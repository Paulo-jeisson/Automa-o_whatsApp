import json
from datetime import timedelta
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import EmpresaCliente, PaymentEvent, Plan, Subscription
from core.services.billing import AsaasBillingService, external_reference
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

    def test_checkout_prices_are_server_side_and_reused(self):
        provider = Mock()
        provider.list_customers.return_value = [{'id': 'cus-safe'}]
        provider.create_checkout.return_value = {'id': 'checkout-safe', 'link': 'https://sandbox.asaas.com/checkoutSession/show/checkout-safe'}
        provider.checkout_url.return_value = 'https://sandbox.asaas.com/checkoutSession/show/checkout-safe'
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
        self.assertEqual(first['id'], second['id'])
        provider.create_checkout.assert_called_once()

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
        self.client.post(
            reverse('asaas_webhook'), json.dumps(event), content_type='application/json',
            HTTP_ASAAS_ACCESS_TOKEN='strong-test-token',
        )
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_wrong_webhook_token_is_rejected(self):
        response = self.client.post(reverse('asaas_webhook'), '{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)
