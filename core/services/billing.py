import hmac
import logging
import re
from calendar import monthrange
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from core.models import AuditEvent, BillingCheckoutAttempt, PaymentEvent, PaymentHistory, Plan, Subscription
from core.services.billing_providers.asaas import AsaasClient, AsaasError


logger = logging.getLogger('billing.asaas')
PLANS = MappingProxyType({
    'monthly': MappingProxyType({
        'name': 'IAATENDE 2.0 - Plano Mensal',
        'value': Decimal('147.00'),
        'cycle': 'MONTHLY',
    }),
    'annual': MappingProxyType({
        'name': 'IAATENDE 2.0 - Plano Anual',
        'value': Decimal('997.00'),
        'cycle': 'YEARLY',
    }),
})
CONFIRMED_EVENTS = {'PAYMENT_CONFIRMED', 'PAYMENT_RECEIVED'}
PENDING_EVENTS = {'PAYMENT_CREATED', 'PAYMENT_AWAITING_RISK_ANALYSIS'}
OVERDUE_EVENTS = {'PAYMENT_OVERDUE'}
REVERSAL_EVENTS = {
    'PAYMENT_REFUNDED', 'PAYMENT_REFUND_IN_PROGRESS', 'PAYMENT_CHARGEBACK_REQUESTED',
    'PAYMENT_CHARGEBACK_DISPUTE', 'PAYMENT_DELETED', 'PAYMENT_CREDIT_CARD_CAPTURE_REFUSED',
}
IGNORED_EVENTS = {
    'PAYMENT_UPDATED', 'PAYMENT_BANK_SLIP_VIEWED', 'PAYMENT_CHECKOUT_VIEWED',
    'PAYMENT_DUNNING_RECEIVED', 'PAYMENT_DUNNING_REQUESTED',
}


class BillingValidationError(ValueError):
    pass


def commercial_plan(code):
    definition = PLANS.get(code)
    if definition is None:
        raise BillingValidationError('Plano inválido.')
    display_name = 'Mensal' if code == 'monthly' else 'Anual'
    model_cycle = Plan.Cycle.MONTHLY if definition['cycle'] == 'MONTHLY' else Plan.Cycle.YEARLY
    plan, _ = Plan.objects.update_or_create(
        code=code,
        defaults={
            'name': display_name, 'price_cents': int(definition['value'] * 100),
            'billing_cycle': model_cycle, 'is_active': True,
        },
    )
    return plan


def external_reference(subscription, plan, attempt=None):
    reference = f'iaatende:company:{subscription.billing_reference}:plan:{plan.code}'
    return f'{reference}:attempt:{attempt}' if attempt else reference


def verify_webhook_token(received):
    expected = settings.ASAAS_WEBHOOK_TOKEN
    if not expected:
        raise ImproperlyConfigured('Webhook Asaas não configurado.')
    if not received or not hmac.compare_digest(expected.encode(), received.encode()):
        raise BillingValidationError('Token de webhook inválido.')


def _parse_date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        try:
            parsed = datetime.strptime(str(value), '%Y-%m-%d')
        except ValueError:
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _period_end(start, cycle):
    months = 12 if cycle == Plan.Cycle.YEARLY else 1
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def _valid_tax_id(value):
    if len(value) not in {11, 14} or value == value[0] * len(value):
        return False
    weights = ([10, 9, 8, 7, 6, 5, 4, 3, 2], [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]) if len(value) == 11 else ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    digits = [int(item) for item in value]
    for index, factors in enumerate(weights):
        total = sum(number * factor for number, factor in zip(digits, factors))
        check = 0 if total % 11 < 2 else 11 - total % 11
        if digits[len(factors)] != check:
            return False
    return True


class AsaasBillingService:
    def __init__(self, client=None):
        self.client = client or AsaasClient()

    def create_checkout(self, *, empresa, plan_code, success_url, cancel_url, expired_url, billing_type='HOSTED', tax_id=''):
        if billing_type not in {'HOSTED', 'BOLETO'}:
            raise BillingValidationError('Forma de pagamento inválida.')
        plan = commercial_plan(plan_code)
        with transaction.atomic():
            subscription, _ = Subscription.objects.select_for_update().get_or_create(
                empresa=empresa,
                defaults={
                    'plan': plan, 'status': Subscription.Status.BLOCKED,
                    'provider': Subscription.Provider.ASAAS,
                },
            )
            now = timezone.now()
            reference = external_reference(subscription, plan)
            if billing_type == 'BOLETO':
                customer = self._ensure_customer(empresa, subscription, reference, tax_id=tax_id)
                return self._create_boleto(subscription, plan, customer, reference)
            previous_checkout_id = subscription.provider_checkout_id
            previous_plan_code = subscription.plan.code if subscription.plan_id else ''
            if previous_checkout_id:
                try:
                    self.client.cancel_checkout(previous_checkout_id)
                    logger.info(
                        'asaas.checkout.canceled checkout_id=%s previous_plan=%s requested_plan=%s',
                        previous_checkout_id, previous_plan_code, plan_code,
                    )
                except AsaasError as error:
                    # Sessões concluídas, canceladas, expiradas ou pertencentes a
                    # credencial antiga podem não aceitar cancelamento. Elas são
                    # abandonadas localmente e jamais reutilizadas.
                    logger.warning(
                        'asaas.checkout.abandoned checkout_id=%s previous_plan=%s requested_plan=%s status=%s errors=%s',
                        previous_checkout_id, previous_plan_code, plan_code,
                        error.status_code, error.errors,
                    )
            attempt_id = uuid4()
            reference = external_reference(subscription, plan, attempt_id)
            definition = PLANS[plan_code]
            payload = {
                # O Checkout recorrente do Asaas aceita somente cartão. Pix exige
                # cobrança DETACHED e não pode ser combinado com RECURRENT.
                'billingTypes': ['CREDIT_CARD'],
                'chargeTypes': ['RECURRENT'],
                'minutesToExpire': settings.ASAAS_CHECKOUT_EXPIRES_IN,
                'externalReference': reference,
                'callback': {
                    'successUrl': success_url, 'cancelUrl': cancel_url,
                    'expiredUrl': expired_url,
                },
                'items': [{
                    'name': definition['name'],
                    'description': f'Assinatura {plan.name.lower()} do IAATENDE 2.0',
                    'quantity': 1, 'value': float(definition['value']),
                }],
                'subscription': {
                    'cycle': definition['cycle'],
                    'nextDueDate': timezone.localtime(now).strftime('%Y-%m-%d %H:%M:%S'),
                },
            }
            logger.info(
                'asaas.checkout.request payload=%s',
                {
                    'billingTypes': payload['billingTypes'], 'chargeTypes': payload['chargeTypes'],
                    'minutesToExpire': payload['minutesToExpire'], 'callback': payload['callback'],
                    'items': payload['items'], 'subscription': payload['subscription'],
                    'externalReference': payload['externalReference'],
                },
            )
            data = self.client.create_checkout(payload)
            checkout_id = str(data.get('id') or '')
            if not checkout_id:
                raise AsaasError('O Asaas não retornou o identificador do checkout.')
            checkout_link = data.get('link') or self.client.checkout_url(checkout_id)
            logger.info(
                'asaas.checkout.response response=%s redirect_url=%s',
                {'id': checkout_id, 'status': data.get('status'), 'link': data.get('link')},
                checkout_link,
            )
            subscription.plan = plan
            subscription.provider = Subscription.Provider.ASAAS
            subscription.provider_checkout_id = checkout_id
            subscription.checkout_expires_at = now + timedelta(minutes=settings.ASAAS_CHECKOUT_EXPIRES_IN)
            subscription.save()
            if previous_checkout_id:
                BillingCheckoutAttempt.objects.filter(
                    provider_checkout_id=previous_checkout_id,
                    status=BillingCheckoutAttempt.Status.PENDING,
                ).update(status=BillingCheckoutAttempt.Status.ABANDONED)
            BillingCheckoutAttempt.objects.create(
                empresa=empresa, subscription=subscription,
                provider=Subscription.Provider.ASAAS,
                provider_checkout_id=checkout_id, external_reference=reference,
                plan_code=plan.code, amount_cents=plan.price_cents,
                cycle=definition['cycle'], expires_at=subscription.checkout_expires_at,
            )
            return {'id': checkout_id, 'link': checkout_link, 'reused': False}

    def _create_boleto(self, subscription, plan, customer, reference):
        if subscription.provider_subscription_id and subscription.plan_id == plan.id:
            payments = self.client.list_subscription_payments(subscription.provider_subscription_id)
            items = payments.get('data', []) if isinstance(payments, dict) else []
            if items and items[0].get('invoiceUrl'):
                return {'id': subscription.provider_subscription_id, 'link': items[0]['invoiceUrl'], 'reused': True}
        data = self.client.create_subscription({
            'customer': customer, 'billingType': 'BOLETO',
            'value': float(Decimal(plan.price_cents) / 100),
            'nextDueDate': timezone.localdate().isoformat(),
            'cycle': PLANS[plan.code]['cycle'],
            'externalReference': reference,
        })
        subscription_id = str(data.get('id') or '')
        if not subscription_id:
            raise AsaasError('O Asaas não retornou a assinatura do boleto.')
        subscription.plan = plan
        subscription.provider_subscription_id = subscription_id
        subscription.save()
        payments = self.client.list_subscription_payments(subscription_id)
        items = payments.get('data', []) if isinstance(payments, dict) else []
        link = items[0].get('invoiceUrl') if items else ''
        if not link:
            raise AsaasError('O Asaas não retornou o boleto da assinatura.')
        return {'id': subscription_id, 'link': link, 'reused': False}

    def _ensure_customer(self, empresa, subscription, reference, *, tax_id):
        if subscription.provider_customer_id:
            return subscription.provider_customer_id
        tax_id = re.sub(r'\D', '', tax_id or '')
        if not _valid_tax_id(tax_id):
            raise BillingValidationError('CPF ou CNPJ válido é obrigatório para boleto.')
        matches = self.client.list_customers(external_reference=reference.split(':plan:')[0])
        if matches:
            customer_id = str(matches[0]['id'])
        else:
            data = self.client.create_customer({
                'name': empresa.nome_dono or empresa.nome,
                'email': empresa.usuario.email,
                'mobilePhone': empresa.whatsapp_dono,
                'cpfCnpj': tax_id,
                'externalReference': reference.split(':plan:')[0],
            })
            customer_id = str(data.get('id') or '')
        if not customer_id:
            raise AsaasError('O Asaas não retornou o identificador do cliente.')
        subscription.provider_customer_id = customer_id
        subscription.save(update_fields=['provider_customer_id', 'updated_at'])
        return customer_id

    @classmethod
    def process_event(cls, payload):
        event_id = str(payload.get('id') or '')
        event_type = str(payload.get('event') or '')
        payment = payload.get('payment') or payload.get('subscription') or {}
        if not event_id or not event_type or not isinstance(payment, dict):
            raise BillingValidationError('Evento inválido.')
        safe_payload = cls._safe_payload(payload)
        event, created = PaymentEvent.objects.get_or_create(
            provider=Subscription.Provider.ASAAS, provider_event_id=event_id,
            defaults={'event_type': event_type, 'payload': safe_payload},
        )
        if not created and event.status in {PaymentEvent.Status.PROCESSED, PaymentEvent.Status.IGNORED}:
            return False
        try:
            with transaction.atomic():
                event = PaymentEvent.objects.select_for_update().get(pk=event.pk)
                event.status = PaymentEvent.Status.PROCESSING
                event.attempts += 1
                event.save(update_fields=['status', 'attempts'])
                cls._apply_event(event, event_type, payment)
        except Exception as error:
            PaymentEvent.objects.filter(pk=event.pk).update(
                status=PaymentEvent.Status.FAILED, failed_at=timezone.now(),
                last_error=f'{type(error).__name__}: {error}'[:2000],
            )
            raise
        return True

    @classmethod
    def _apply_event(cls, event, event_type, payment):
        reference = str(payment.get('externalReference') or '')
        payment_id = str(payment.get('id') or '')
        if event_type.startswith('PAYMENT_') and not payment_id:
            raise BillingValidationError('Pagamento sem identificador.')
        checkout_id = str(payment.get('checkoutSession') or '')
        if not checkout_id and payment_id and not reference:
            remote_payment = AsaasClient().get_payment(payment_id)
            if str(remote_payment.get('id') or '') != payment_id:
                raise BillingValidationError('Pagamento divergente.')
            checkout_id = str(remote_payment.get('checkoutSession') or '')
            for field in ('customer', 'subscription', 'value'):
                if payment.get(field) not in (None, '') and remote_payment.get(field) != payment.get(field):
                    raise BillingValidationError(f'{field} divergente.')
            payment = {**remote_payment, **payment, 'checkoutSession': checkout_id}

        attempt = None
        if checkout_id:
            attempt = BillingCheckoutAttempt.objects.select_for_update().select_related(
                'subscription__plan', 'empresa',
            ).filter(provider=Subscription.Provider.ASAAS, provider_checkout_id=checkout_id).first()
            if attempt is None:
                raise BillingValidationError('Checkout não reconhecido.')
            reference = attempt.external_reference
        match = re.fullmatch(
            r'iaatende:company:([0-9a-fA-F-]{36}):plan:(monthly|annual)(?::attempt:([0-9a-fA-F-]{36}))?',
            reference,
        )
        if not match:
            raise BillingValidationError('Referência externa inválida.')
        billing_reference, plan_code = match.group(1), match.group(2)
        subscription = Subscription.objects.select_for_update().select_related('plan', 'empresa').filter(
            billing_reference=billing_reference, provider=Subscription.Provider.ASAAS,
        ).first()
        if subscription is None:
            raise BillingValidationError('Assinatura não encontrada.')
        plan = commercial_plan(plan_code)
        if attempt and (
            attempt.subscription_id != subscription.pk or attempt.empresa_id != subscription.empresa_id
            or attempt.plan_code != plan_code or attempt.amount_cents != plan.price_cents
            or attempt.cycle != PLANS[plan_code]['cycle']
        ):
            raise BillingValidationError('Checkout divergente.')
        customer_id = str(payment.get('customer') or '')
        provider_subscription_id = str(payment.get('subscription') or '')
        if subscription.provider_customer_id and customer_id != subscription.provider_customer_id:
            raise BillingValidationError('Cliente divergente.')
        if subscription.provider_subscription_id and provider_subscription_id != subscription.provider_subscription_id:
            raise BillingValidationError('Assinatura divergente.')
        amount_cents = plan.price_cents
        if event_type.startswith('PAYMENT_'):
            try:
                amount_cents = int((Decimal(str(payment.get('value'))) * 100).quantize(Decimal('1')))
            except (InvalidOperation, TypeError):
                raise BillingValidationError('Valor inválido.')
            if amount_cents != plan.price_cents:
                raise BillingValidationError('Valor divergente.')
        if subscription.plan_id != plan.id and subscription.provider_subscription_id:
            raise BillingValidationError('Plano divergente.')
        subscription.plan = plan
        subscription.provider_customer_id = customer_id or subscription.provider_customer_id
        subscription.provider_subscription_id = provider_subscription_id or subscription.provider_subscription_id
        event.empresa = subscription.empresa
        event.subscription = subscription
        event.payment_external_id = str(payment.get('id') or '')
        now = timezone.now()
        if event_type in CONFIRMED_EVENTS:
            paid_at = _parse_date(payment.get('confirmedDate') or payment.get('paymentDate') or payment.get('clientPaymentDate')) or now
            due_at = _parse_date(payment.get('dueDate')) or paid_at
            period_start = max(paid_at, due_at)
            period_end = _period_end(period_start, plan.billing_cycle)
            PaymentHistory.objects.update_or_create(
                external_id=event.payment_external_id,
                defaults={
                    'empresa': subscription.empresa, 'subscription': subscription,
                    'provider': Subscription.Provider.ASAAS, 'plan_code': plan.code,
                    'status': 'paid', 'amount_cents': amount_cents, 'currency': 'brl',
                    'due_at': due_at, 'paid_at': paid_at,
                },
            )
            subscription.status = Subscription.Status.ACTIVE
            subscription.current_period_start = period_start
            subscription.current_period_end = period_end
            subscription.last_payment_at = paid_at
            subscription.overdue_since = subscription.grace_period_ends_at = subscription.blocked_at = None
            if attempt:
                attempt.status = BillingCheckoutAttempt.Status.PAID
                attempt.paid_at = paid_at
                attempt.save(update_fields=['status', 'paid_at', 'updated_at'])
        elif event_type in OVERDUE_EVENTS:
            due_at = _parse_date(payment.get('dueDate')) or now
            subscription.status = Subscription.Status.GRACE
            subscription.overdue_since = due_at
            subscription.grace_period_ends_at = due_at + timedelta(days=3)
        elif event_type in REVERSAL_EVENTS:
            subscription.status = Subscription.Status.BLOCKED
            subscription.blocked_at = now
        elif event_type in {'SUBSCRIPTION_DELETED', 'SUBSCRIPTION_INACTIVATED'}:
            subscription.status = Subscription.Status.CANCELED
            subscription.canceled_at = now
        elif event_type not in PENDING_EVENTS | IGNORED_EVENTS:
            event.status = PaymentEvent.Status.IGNORED
            event.processed_at = now
            event.save()
            return
        subscription.save()
        AuditEvent.objects.create(
            empresa=subscription.empresa, action={
                **{item: 'billing.payment_confirmed' for item in CONFIRMED_EVENTS},
                **{item: 'billing.grace_started' for item in OVERDUE_EVENTS},
                **{item: 'billing.payment_reversed' for item in REVERSAL_EVENTS},
                'SUBSCRIPTION_DELETED': 'billing.subscription_canceled',
                'SUBSCRIPTION_INACTIVATED': 'billing.subscription_canceled',
            }.get(event_type, 'billing.webhook_processed'),
            target_type='core.subscription', target_id=str(subscription.pk),
            metadata={
                'provider': 'ASAAS', 'event_id': event.provider_event_id,
                'event_type': event_type, 'payment_id': event.payment_external_id,
            },
        )
        event.status = PaymentEvent.Status.PROCESSED
        event.processed_at = now
        event.last_error = ''
        event.save()

    @staticmethod
    def _safe_payload(payload):
        payment = payload.get('payment') or payload.get('subscription') or {}
        return {
            'id': payload.get('id'), 'event': payload.get('event'),
            'payment': {key: payment.get(key) for key in (
                'id', 'customer', 'subscription', 'externalReference', 'value',
                'status', 'billingType', 'dueDate', 'paymentDate', 'confirmedDate',
                'checkoutSession',
            )},
        }
