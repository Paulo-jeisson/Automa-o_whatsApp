import json
from io import BytesIO
from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.base import Message
from django.contrib.messages.storage.cookie import MessageEncoder
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import BillingCheckoutAttempt, EmpresaCliente, PaymentEvent, PaymentHistory, Plan, Subscription
from core.services.billing import AsaasBillingService, external_reference
from core.services.billing_providers.asaas import AsaasClient, AsaasError
from core.services.entitlements import EntitlementService


@override_settings(SUBSCRIPTION_ENFORCEMENT_ENABLED=True, ASAAS_WEBHOOK_TOKEN='strong-test-token')
class SubscriptionSecurityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('asaas-owner', password='safe-password', email='owner@example.com')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa Asaas')
        self.plan = Plan.objects.create(name='Trial', code='trial')
        self.now = timezone.now()
        self.subscription = Subscription.objects.create(
            empresa=self.company, plan=self.plan, status=Subscription.Status.TRIAL,
            trial_started_at=self.now, trial_ends_at=self.now + timedelta(days=3),
        )
        self.client.force_login(self.user)

    def test_valid_trial_accesses_and_expired_trial_redirects(self):
        self.assertNotEqual(self.client.get(reverse('dashboard'))['Location'], reverse('assinatura_bloqueada'))
        self.subscription.trial_ends_at = timezone.now() - timedelta(seconds=1)
        self.subscription.save(update_fields=['trial_ends_at'])
        response = self.client.get(reverse('dashboard'))
        self.assertRedirects(response, reverse('assinatura_bloqueada'))

    def test_missing_subscription_is_fail_closed_for_html_and_api(self):
        self.subscription.delete()
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)
        response = self.client.get(reverse('api_me'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'subscription_required')

    def test_grace_is_date_bound(self):
        self.subscription.status = Subscription.Status.GRACE
        self.subscription.overdue_since = self.now
        self.subscription.grace_period_ends_at = self.now + timedelta(days=3)
        self.subscription.save()
        self.assertTrue(EntitlementService.access_state(self.company)[0])
        self.subscription.grace_period_ends_at = self.now - timedelta(seconds=1)
        self.subscription.save()
        self.assertFalse(EntitlementService.access_state(self.company)[0])

    def test_monthly_checkout_uses_server_values_and_new_attempt_each_time(self):
        provider = Mock()
        provider.create_checkout.side_effect = [{'id': 'checkout-one'}, {'id': 'checkout-two'}]
        provider.checkout_url.side_effect = lambda checkout_id: f'https://sandbox.asaas.com/checkoutSession/show?id={checkout_id}'
        service = AsaasBillingService(provider)
        first = service.create_checkout(
            empresa=self.company, plan_code='monthly', success_url='https://example.test/success',
            cancel_url='https://example.test/cancel', expired_url='https://example.test/expired',
        )
        second = service.create_checkout(
            empresa=self.company, plan_code='monthly', success_url='https://example.test/success',
            cancel_url='https://example.test/cancel', expired_url='https://example.test/expired',
        )
        self.assertEqual(provider.create_checkout.call_args.args[0]['items'][0]['value'], 147.0)
        payload = provider.create_checkout.call_args.args[0]
        self.assertEqual(payload['billingTypes'], ['CREDIT_CARD'])
        self.assertEqual(payload['chargeTypes'], ['RECURRENT'])
        self.assertEqual(payload['minutesToExpire'], 60)
        self.assertEqual(payload['items'], [{
            'name': 'IAATENDE 2.0 - Plano Mensal',
            'description': 'Assinatura mensal do IAATENDE 2.0',
            'quantity': 1, 'value': 147.0,
        }])
        self.assertEqual(payload['subscription']['cycle'], 'MONTHLY')
        self.assertRegex(payload['subscription']['nextDueDate'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')
        self.assertNotIn('customerData', payload)
        self.assertEqual(first['id'], 'checkout-one')
        self.assertEqual(second['id'], 'checkout-two')
        first_payload = provider.create_checkout.call_args_list[0].args[0]
        second_payload = provider.create_checkout.call_args_list[1].args[0]
        self.assertNotEqual(first_payload['externalReference'], second_payload['externalReference'])
        self.assertIn(':plan:monthly:attempt:', first_payload['externalReference'])
        self.assertEqual(provider.create_checkout.call_count, 2)

    def test_annual_checkout_uses_server_catalog(self):
        provider = Mock()
        provider.create_checkout.return_value = {'id': 'checkout-annual'}
        provider.checkout_url.return_value = 'https://sandbox.asaas.com/checkoutSession/show?id=checkout-annual'
        result = AsaasBillingService(provider).create_checkout(
            empresa=self.company, plan_code='annual',
            success_url='https://example.test/success', cancel_url='https://example.test/cancel',
            expired_url='https://example.test/expired',
        )
        payload = provider.create_checkout.call_args.args[0]
        self.assertEqual(payload['items'][0]['value'], 997.0)
        self.assertEqual(payload['items'][0]['name'], 'IAATENDE 2.0 - Plano Anual')
        self.assertEqual(payload['items'][0]['description'], 'Assinatura anual do IAATENDE 2.0')
        self.assertEqual(payload['subscription']['cycle'], 'YEARLY')
        self.assertEqual(result['link'], 'https://sandbox.asaas.com/checkoutSession/show?id=checkout-annual')

    def test_annual_never_reuses_pending_monthly_checkout(self):
        provider = Mock()
        provider.create_checkout.side_effect = [{'id': 'monthly-id'}, {'id': 'annual-id'}]
        provider.checkout_url.side_effect = lambda checkout_id: f'https://sandbox.asaas.com/checkoutSession/show?id={checkout_id}'
        service = AsaasBillingService(provider)
        monthly = service.create_checkout(
            empresa=self.company, plan_code='monthly', success_url='https://example.test/success',
            cancel_url='https://example.test/cancel', expired_url='https://example.test/expired',
        )
        annual = service.create_checkout(
            empresa=self.company, plan_code='annual', success_url='https://example.test/success',
            cancel_url='https://example.test/cancel', expired_url='https://example.test/expired',
        )
        monthly_payload = provider.create_checkout.call_args_list[0].args[0]
        annual_payload = provider.create_checkout.call_args_list[1].args[0]
        self.assertEqual(monthly['id'], 'monthly-id')
        self.assertEqual(annual['id'], 'annual-id')
        self.assertEqual(monthly_payload['items'][0]['value'], 147.0)
        self.assertEqual(annual_payload['items'][0]['value'], 997.0)
        self.assertEqual(monthly_payload['subscription']['cycle'], 'MONTHLY')
        self.assertEqual(annual_payload['subscription']['cycle'], 'YEARLY')
        self.assertNotEqual(monthly_payload['externalReference'], annual_payload['externalReference'])
        provider.cancel_checkout.assert_called_once_with('monthly-id')

    def test_incompatible_checkout_is_abandoned_when_remote_cancel_is_not_possible(self):
        provider = Mock()
        provider.create_checkout.side_effect = [{'id': 'monthly-id'}, {'id': 'annual-id'}]
        provider.checkout_url.side_effect = lambda checkout_id: f'https://sandbox.asaas.com/checkoutSession/show?id={checkout_id}'
        provider.cancel_checkout.side_effect = AsaasError(
            'Checkout já encerrado.', status_code=400,
            errors=[{'code': 'invalid_status', 'description': 'Checkout não pode ser cancelado.'}],
        )
        service = AsaasBillingService(provider)
        service.create_checkout(
            empresa=self.company, plan_code='monthly', success_url='https://example.test/success',
            cancel_url='https://example.test/cancel', expired_url='https://example.test/expired',
        )
        annual = service.create_checkout(
            empresa=self.company, plan_code='annual', success_url='https://example.test/success',
            cancel_url='https://example.test/cancel', expired_url='https://example.test/expired',
        )
        self.assertEqual(annual['id'], 'annual-id')
        self.assertEqual(provider.create_checkout.call_count, 2)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.provider_checkout_id, 'annual-id')
        self.assertEqual(self.subscription.plan.code, 'annual')

    def test_plan_forms_target_distinct_backend_routes(self):
        response = self.client.get(reverse('planos'))
        self.assertContains(response, 'action="/assinatura/checkout/monthly/"', html=False)
        self.assertContains(response, 'action="/assinatura/checkout/annual/"', html=False)
        self.assertEqual(response.content.count(b'/assinatura/checkout/monthly/'), 1)
        self.assertEqual(response.content.count(b'/assinatura/checkout/annual/'), 1)

    @patch('core.views.AsaasBillingService')
    def test_frontend_price_and_cycle_are_ignored(self, service_class):
        service_class.return_value.create_checkout.return_value = {
            'id': 'safe-checkout', 'link': 'https://sandbox.asaas.com/safe', 'reused': False,
        }
        response = self.client.post(
            reverse('subscription_checkout', args=['annual']),
            {'price': '0.01', 'value': '0.01', 'cycle': 'MONTHLY', 'plan': 'monthly'},
        )
        self.assertRedirects(response, 'https://sandbox.asaas.com/safe', fetch_redirect_response=False)
        kwargs = service_class.return_value.create_checkout.call_args.kwargs
        self.assertEqual(kwargs['plan_code'], 'annual')
        self.assertEqual(kwargs['billing_type'], 'HOSTED')
        self.assertNotIn('price', kwargs)
        self.assertNotIn('value', kwargs)
        self.assertNotIn('cycle', kwargs)

    def test_invalid_plan_is_rejected(self):
        response = self.client.post(reverse('subscription_checkout', args=['enterprise']))
        self.assertEqual(response.status_code, 404)

    def test_return_does_not_activate_but_valid_webhook_does(self):
        self.subscription.status = Subscription.Status.BLOCKED
        self.subscription.save()
        self.assertEqual(self.client.get(reverse('assinatura_retorno')).status_code, 200)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.BLOCKED)
        monthly = Plan.objects.get(code='monthly')
        self.subscription.plan = monthly
        self.subscription.provider_customer_id = 'cus-safe'
        self.subscription.save()
        event = {'id': 'evt-safe', 'event': 'PAYMENT_CONFIRMED', 'payment': {
            'id': 'pay-safe', 'customer': 'cus-safe', 'subscription': 'sub-safe',
            'externalReference': external_reference(self.subscription, monthly),
            'value': 147, 'dueDate': timezone.localdate().isoformat(),
        }}
        response = self.client.post(
            reverse('asaas_webhook'), json.dumps(event), content_type='application/json',
            HTTP_ASAAS_ACCESS_TOKEN='strong-test-token',
        )
        self.assertEqual(response.status_code, 200)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_confirmed_checkout_unlocks_expired_trial_immediately(self):
        monthly = Plan.objects.get(code='monthly')
        self.subscription.plan = monthly
        self.subscription.status = Subscription.Status.BLOCKED
        self.subscription.trial_ends_at = timezone.now() - timedelta(days=1)
        self.subscription.blocked_at = timezone.now()
        self.subscription.save()
        reference = external_reference(self.subscription, monthly, '11111111-1111-4111-8111-111111111111')
        BillingCheckoutAttempt.objects.create(
            empresa=self.company, subscription=self.subscription,
            provider_checkout_id='checkout-paid-a', external_reference=reference,
            plan_code='monthly', amount_cents=14700, cycle='MONTHLY',
        )
        event = {'id': 'evt-unlock-a', 'event': 'PAYMENT_CONFIRMED', 'payment': {
            'id': 'pay-unlock-a', 'checkoutSession': 'checkout-paid-a',
            'customer': 'cus-a', 'subscription': 'sub-a', 'externalReference': None,
            'value': 147, 'dueDate': timezone.localdate().isoformat(),
            'confirmedDate': timezone.localdate().isoformat(),
        }}
        response = self.client.post(
            reverse('asaas_webhook'), json.dumps(event), content_type='application/json',
            HTTP_ASAAS_ACCESS_TOKEN='strong-test-token',
        )
        self.assertEqual(response.status_code, 200)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertIsNone(self.subscription.overdue_since)
        self.assertIsNone(self.subscription.grace_period_ends_at)
        self.assertIsNone(self.subscription.blocked_at)
        self.assertTrue(self.subscription.has_access)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)
        self.assertNotEqual(self.client.get(reverse('dashboard'))['Location'], reverse('assinatura_bloqueada'))
        self.assertTrue(PaymentHistory.objects.filter(external_id='pay-unlock-a', empresa=self.company).exists())

    def test_payment_for_company_a_never_unlocks_company_b(self):
        monthly = Plan.objects.get(code='monthly')
        other_user = get_user_model().objects.create_user('other-owner', password='safe-password')
        other_company = EmpresaCliente.objects.create(usuario=other_user, nome='Empresa B')
        other_subscription = Subscription.objects.create(
            empresa=other_company, plan=monthly, status=Subscription.Status.BLOCKED,
            trial_started_at=timezone.now() - timedelta(days=4),
            trial_ends_at=timezone.now() - timedelta(days=1), blocked_at=timezone.now(),
        )
        reference = external_reference(self.subscription, monthly, '22222222-2222-4222-8222-222222222222')
        BillingCheckoutAttempt.objects.create(
            empresa=self.company, subscription=self.subscription,
            provider_checkout_id='checkout-company-a', external_reference=reference,
            plan_code='monthly', amount_cents=14700, cycle='MONTHLY',
        )
        event = {'id': 'evt-company-a', 'event': 'PAYMENT_RECEIVED', 'payment': {
            'id': 'pay-company-a', 'checkoutSession': 'checkout-company-a',
            'customer': 'cus-a', 'subscription': 'sub-a', 'value': 147,
            'dueDate': timezone.localdate().isoformat(),
        }}
        response = self.client.post(
            reverse('asaas_webhook'), json.dumps(event), content_type='application/json',
            HTTP_ASAAS_ACCESS_TOKEN='strong-test-token',
        )
        self.assertEqual(response.status_code, 200)
        self.subscription.refresh_from_db()
        other_subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertEqual(other_subscription.status, Subscription.Status.BLOCKED)
        self.assertFalse(PaymentHistory.objects.filter(external_id='pay-company-a', empresa=other_company).exists())
        self.client.post(
            reverse('asaas_webhook'), json.dumps(event), content_type='application/json',
            HTTP_ASAAS_ACCESS_TOKEN='strong-test-token',
        )
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_wrong_webhook_token_is_rejected(self):
        response = self.client.post(reverse('asaas_webhook'), '{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    @override_settings(ASAAS_API_KEY='')
    def test_checkout_failure_returns_to_public_pricing_not_internal_plans(self):
        response = self.client.post(reverse('subscription_checkout', args=['monthly']))
        self.assertRedirects(response, reverse('landing_page') + '#planos', fetch_redirect_response=False)

    def test_confirmed_payment_finishes_session_at_login(self):
        self.subscription.status = Subscription.Status.ACTIVE
        self.subscription.current_period_start = timezone.now() - timedelta(minutes=1)
        self.subscription.current_period_end = timezone.now() + timedelta(days=30)
        self.subscription.save()
        response = self.client.post(reverse('assinatura_finalizar'))
        self.assertRedirects(response, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_public_site_never_renders_billing_or_internal_messages(self):
        self.subscription.status = Subscription.Status.GRACE
        self.subscription.overdue_since = timezone.now() - timedelta(days=1)
        self.subscription.grace_period_ends_at = timezone.now() + timedelta(days=2)
        self.subscription.save()
        session = self.client.session
        session['_messages'] = MessageEncoder().encode([Message(40, 'Plano vencido')])
        session.save()
        response = self.client.get(reverse('landing_page'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Pagamento pendente')
        self.assertNotContains(response, 'Regularize agora')
        self.assertNotContains(response, 'Plano vencido')
        self.assertNotContains(response, 'subscription-warning')


@override_settings(ASAAS_ENVIRONMENT='sandbox')
class AsaasClientDiagnosticsTests(TestCase):
    @patch('core.services.billing_providers.asaas.urlopen')
    def test_http_400_keeps_only_sanitized_error_codes_and_descriptions(self, mocked_urlopen):
        body = json.dumps({'errors': [{
            'code': 'invalid_customer',
            'description': 'CPF 12345678901 e email owner@example.com; access_token=secret-value',
            'cpfCnpj': '12345678901',
        }], 'apiKey': 'must-not-leak', 'customer': {'name': 'Pessoa'}}).encode()
        mocked_urlopen.side_effect = HTTPError(
            'https://api-sandbox.asaas.com/v3/checkouts', 400, 'Bad Request', {}, BytesIO(body),
        )
        client = AsaasClient(api_key='api-key-must-not-leak')
        with self.assertLogs('billing.asaas.http', 'WARNING') as captured:
            with self.assertRaises(AsaasError) as raised:
                client.create_checkout({'customerData': {'cpfCnpj': '12345678901'}})
        error = raised.exception
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.errors[0]['code'], 'invalid_customer')
        combined = ' '.join(captured.output) + str(error.response) + str(error)
        self.assertNotIn('12345678901', combined)
        self.assertNotIn('owner@example.com', combined)
        self.assertNotIn('secret-value', combined)
        self.assertNotIn('api-key-must-not-leak', combined)
        self.assertNotIn('must-not-leak', combined)

    def test_checkout_url_is_built_from_id(self):
        client = AsaasClient(api_key='safe-test-key')
        self.assertEqual(
            client.checkout_url('chk_123'),
            'https://sandbox.asaas.com/checkoutSession/show?id=chk_123',
        )
