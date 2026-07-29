import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from core.models import PaymentEvent, PaymentHistory, Subscription


class StripeBillingService:
    endpoint = 'https://api.stripe.com/v1/checkout/sessions'

    def create_checkout(self, *, empresa, plan, success_url, cancel_url):
        if not settings.STRIPE_SECRET_KEY or not plan.stripe_price_id:
            raise ImproperlyConfigured('Cobrança Stripe não configurada.')
        payload = urlencode({
            'mode': 'subscription',
            'line_items[0][price]': plan.stripe_price_id,
            'line_items[0][quantity]': 1,
            'client_reference_id': empresa.pk,
            'metadata[empresa_id]': empresa.pk,
            'metadata[plan_id]': plan.pk,
            'subscription_data[metadata][empresa_id]': empresa.pk,
            'subscription_data[metadata][plan_id]': plan.pk,
            'success_url': success_url,
            'cancel_url': cancel_url,
        }).encode()
        request = Request(self.endpoint, data=payload, method='POST', headers={
            'Authorization': f'Bearer {settings.STRIPE_SECRET_KEY}',
            'Stripe-Version': settings.STRIPE_API_VERSION,
            'Content-Type': 'application/x-www-form-urlencoded',
            'Idempotency-Key': f'checkout-{empresa.pk}-{plan.pk}-{int(time.time() // 300)}',
        })
        try:
            with urlopen(request, timeout=15) as response:
                data = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError('Gateway de cobrança indisponível.') from error
        if not data.get('url'):
            raise RuntimeError('A Stripe não retornou a URL do checkout.')
        return data

    @staticmethod
    def verify_event(payload, signature, tolerance=300):
        if not settings.STRIPE_WEBHOOK_SECRET:
            raise ImproperlyConfigured('Webhook Stripe não configurado.')
        parts = dict(item.split('=', 1) for item in signature.split(',') if '=' in item)
        timestamp = int(parts.get('t', '0'))
        if abs(int(time.time()) - timestamp) > tolerance:
            raise ValueError('Assinatura expirada.')
        signed = f'{timestamp}.'.encode() + payload
        expected = hmac.new(
            settings.STRIPE_WEBHOOK_SECRET.encode(), signed, hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, parts.get('v1', '')):
            raise ValueError('Assinatura inválida.')
        return json.loads(payload)

    @classmethod
    def process_event(cls, event):
        event_id, event_type = str(event.get('id', '')), str(event.get('type', ''))
        obj = event.get('data', {}).get('object', {})
        if not event_id or not isinstance(obj, dict):
            raise ValueError('Evento inválido.')
        with transaction.atomic():
            _, created = PaymentEvent.objects.get_or_create(
                external_id=event_id, defaults={'event_type': event_type},
            )
            if not created:
                return False
            empresa_id = obj.get('metadata', {}).get('empresa_id')
            subscription = Subscription.objects.select_for_update().filter(
                empresa_id=empresa_id,
            ).first()
            if not subscription and obj.get('client_reference_id'):
                subscription = Subscription.objects.select_for_update().filter(
                    empresa_id=obj['client_reference_id'],
                ).first()
            stripe_subscription_id = cls._subscription_id(obj)
            if not subscription and stripe_subscription_id:
                subscription = Subscription.objects.select_for_update().filter(
                    stripe_subscription_id=stripe_subscription_id,
                ).first()
            if not subscription and obj.get('customer'):
                subscription = Subscription.objects.select_for_update().filter(
                    stripe_customer_id=obj.get('customer'),
                ).first()
            if subscription:
                cls._apply(subscription, event_type, obj)
        return True

    @staticmethod
    def _subscription_id(obj):
        direct = obj.get('subscription')
        if isinstance(direct, str):
            return direct
        parent = obj.get('parent', {})
        if isinstance(parent, dict):
            details = parent.get('subscription_details', {})
            if isinstance(details, dict):
                value = details.get('subscription')
                return value if isinstance(value, str) else ''
        return ''

    @staticmethod
    def _apply(subscription, event_type, obj):
        if event_type == 'checkout.session.completed':
            subscription.status = Subscription.Status.ACTIVE
            subscription.stripe_customer_id = str(obj.get('customer', '') or '')
            subscription.stripe_subscription_id = str(obj.get('subscription', '') or '') or None
        elif event_type == 'invoice.paid':
            subscription.status = Subscription.Status.ACTIVE
            PaymentHistory.objects.get_or_create(
                external_id=str(obj.get('id')),
                defaults={
                    'empresa': subscription.empresa, 'status': 'paid',
                    'amount_cents': int(obj.get('amount_paid', 0)),
                    'currency': str(obj.get('currency', 'brl'))[:3],
                },
            )
        elif event_type == 'invoice.payment_failed':
            subscription.status = Subscription.Status.PAST_DUE
        elif event_type == 'customer.subscription.deleted':
            subscription.status = Subscription.Status.CANCELED
        elif event_type == 'customer.subscription.updated':
            status = obj.get('status')
            subscription.status = {
                'active': Subscription.Status.ACTIVE,
                'trialing': Subscription.Status.TRIAL,
                'past_due': Subscription.Status.PAST_DUE,
                'canceled': Subscription.Status.CANCELED,
                'unpaid': Subscription.Status.SUSPENDED,
            }.get(status, subscription.status)
            if obj.get('current_period_end'):
                subscription.current_period_end = datetime.fromtimestamp(
                    obj['current_period_end'], tz=UTC,
                )
        subscription.save()
