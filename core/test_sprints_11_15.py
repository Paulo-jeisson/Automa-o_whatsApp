import hashlib
import hmac
import json
import time
from datetime import time as dt_time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.access import company_for_user
from core.models import (
    Agendamento, AppointmentReminder, CompanyInvitation, CompanyMembership,
    CompanyOnboarding, Contato, DisponibilidadeSemanal, EmpresaCliente,
    PaymentEvent, PaymentHistory, Plan, ReminderConfiguration, Servico,
    Subscription, UsageCounter, WhatsAppIntegration, WhatsAppSession,
)
from core.services.billing import StripeBillingService
from core.services.entitlements import EntitlementService
from core.services.reminders import ReminderService


class TeamAndPermissionsTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            'owner11', password='secure-pass', email='owner@example.com',
        )
        self.company = EmpresaCliente.objects.create(usuario=self.owner, nome='Equipe A')
        CompanyMembership.objects.create(
            empresa=self.company, user=self.owner, role=CompanyMembership.Role.OWNER,
        )
        self.agent = get_user_model().objects.create_user(
            'agent11', password='secure-pass', email='agent@example.com',
        )
        CompanyMembership.objects.create(
            empresa=self.company, user=self.agent, role=CompanyMembership.Role.AGENT,
        )

    def test_member_resolves_same_company_and_agent_permissions_are_limited(self):
        self.assertEqual(company_for_user(self.agent), self.company)
        self.client.login(username='agent11', password='secure-pass')
        self.assertEqual(self.client.get(reverse('atendimentos')).status_code, 200)
        self.assertEqual(self.client.get(reverse('minha_empresa')).status_code, 403)
        self.assertEqual(self.client.get(reverse('equipe')).status_code, 403)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_owner_invites_and_matching_user_accepts(self):
        self.client.login(username='owner11', password='secure-pass')
        response = self.client.post(reverse('equipe'), {
            'email': 'new@example.com', 'role': CompanyMembership.Role.RECEPTIONIST,
        })
        self.assertRedirects(response, reverse('equipe'))
        invitation = CompanyInvitation.objects.get(email='new@example.com')
        self.assertNotEqual(invitation.token_hash, '')

        new_user = get_user_model().objects.create_user(
            'new11', password='secure-pass', email='new@example.com',
        )
        # O token bruto só existe no e-mail; para o teste, criamos um convite conhecido.
        raw = 'known-safe-token'
        invitation.token_hash = hashlib.sha256(raw.encode()).hexdigest()
        invitation.save(update_fields=['token_hash'])
        self.client.logout()
        self.client.login(username='new11', password='secure-pass')
        self.client.get(reverse('aceitar_convite', args=[raw]))
        self.assertTrue(CompanyMembership.objects.filter(
            empresa=self.company, user=new_user,
            role=CompanyMembership.Role.RECEPTIONIST, is_active=True,
        ).exists())


class PlansAndOnboardingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'plan12', password='secure-pass',
        )
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Plano A')
        self.plan = Plan.objects.create(
            name='Básico', code='basic', operator_limit=1,
            attendance_limit=1, message_limit=1, ai_call_limit=1,
        )
        self.subscription = Subscription.objects.create(
            empresa=self.company, plan=self.plan, status=Subscription.Status.ACTIVE,
        )

    def test_limits_are_enforced_and_usage_is_counted(self):
        EntitlementService.consume(self.company, 'messages')
        self.assertEqual(UsageCounter.objects.get().messages, 1)
        with self.assertRaises(PermissionDenied):
            EntitlementService.consume(self.company, 'messages')
        with self.assertRaises(PermissionDenied):
            EntitlementService.require_limit(self.company, 'operators')

    def test_expired_trial_is_suspended(self):
        self.subscription.status = Subscription.Status.TRIAL
        self.subscription.trial_ends_at = timezone.now() - timedelta(seconds=1)
        self.subscription.save()
        self.assertEqual(
            EntitlementService.subscription(self.company).status,
            Subscription.Status.SUSPENDED,
        )

    def test_public_registration_creates_company_trial_and_onboarding(self):
        response = self.client.post(reverse('cadastro'), {
            'username': 'new-company',
            'email': 'new-company@example.com',
            'company_name': 'Nova Clínica',
            'segment': EmpresaCliente.SEGMENTO_CLINICA,
            'password1': 'Strong-password-2026',
            'password2': 'Strong-password-2026',
        })
        self.assertRedirects(response, reverse('onboarding'))
        company = EmpresaCliente.objects.get(nome='Nova Clínica')
        self.assertEqual(company.subscription.status, Subscription.Status.TRIAL)
        self.assertIsNotNone(company.onboarding)

    def test_onboarding_activates_only_after_all_steps(self):
        CompanyOnboarding.objects.create(empresa=self.company, test_completed=True)
        Servico.objects.create(empresa=self.company, nome='Consulta')
        DisponibilidadeSemanal.objects.create(
            empresa=self.company, dia_semana=0,
            hora_inicio=dt_time(8), hora_fim=dt_time(9),
        )
        from core.models import AIConfiguration
        AIConfiguration.objects.create(empresa=self.company, enabled=True)
        WhatsAppIntegration.objects.create(
            company=self.company, phone_number_id='123456',
            whatsapp_business_account_id='654321',
        )
        self.client.login(username='plan12', password='secure-pass')
        self.client.get(reverse('onboarding'))
        self.company.onboarding.refresh_from_db()
        self.assertIsNotNone(self.company.onboarding.activated_at)


@override_settings(
    STRIPE_SECRET_KEY='sk_test_safe',
    STRIPE_WEBHOOK_SECRET='whsec_safe',
)
class BillingTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('billing14')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Billing')
        self.plan = Plan.objects.create(name='Pro', code='pro', stripe_price_id='price_safe')
        self.subscription = Subscription.objects.create(
            empresa=self.company, plan=self.plan,
        )

    def _signed(self, event):
        payload = json.dumps(event, separators=(',', ':')).encode()
        timestamp = int(time.time())
        digest = hmac.new(
            b'whsec_safe', f'{timestamp}.'.encode() + payload, hashlib.sha256,
        ).hexdigest()
        return payload, f't={timestamp},v1={digest}'

    def test_signed_webhook_activates_subscription_and_is_idempotent(self):
        event = {
            'id': 'evt_checkout_1', 'type': 'checkout.session.completed',
            'data': {'object': {
                'client_reference_id': str(self.company.pk),
                'customer': 'cus_1', 'subscription': 'sub_1',
                'metadata': {'empresa_id': str(self.company.pk)},
            }},
        }
        payload, signature = self._signed(event)
        first = self.client.post(
            reverse('stripe_webhook'), data=payload,
            content_type='application/json', HTTP_STRIPE_SIGNATURE=signature,
        )
        second = self.client.post(
            reverse('stripe_webhook'), data=payload,
            content_type='application/json', HTTP_STRIPE_SIGNATURE=signature,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertEqual(PaymentEvent.objects.filter(external_id='evt_checkout_1').count(), 1)

    def test_paid_invoice_creates_financial_history(self):
        StripeBillingService.process_event({
            'id': 'evt_invoice_1', 'type': 'invoice.paid',
            'data': {'object': {
                'id': 'in_1', 'amount_paid': 9900, 'currency': 'brl',
                'metadata': {'empresa_id': str(self.company.pk)},
            }},
        })
        history = PaymentHistory.objects.get(external_id='in_1')
        self.assertEqual(history.amount_cents, 9900)

    def test_invalid_signature_is_rejected(self):
        response = self.client.post(
            reverse('stripe_webhook'), data=b'{}',
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=invalid',
        )
        self.assertEqual(response.status_code, 400)


@override_settings(META_ACCESS_TOKEN='meta-test')
class ReminderTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('reminder15')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Lembretes')
        ReminderConfiguration.objects.create(
            empresa=self.company, enabled=True, offsets_hours=[24, 2],
            template_name='lembrete_agendamento', language_code='pt_BR',
        )
        WhatsAppIntegration.objects.create(
            company=self.company, phone_number_id='123456',
            whatsapp_business_account_id='654321',
        )
        WhatsAppSession.objects.create(
            empresa=self.company, instance_name='reminder-evolution', state='CONNECTED',
        )
        contact = Contato.objects.create(
            empresa=self.company, whatsapp_id='5511999999999', nome='Ana',
        )
        service = Servico.objects.create(empresa=self.company, nome='Consulta')
        self.appointment = Agendamento.objects.create(
            empresa=self.company, contato=contact, servico=service,
            data=timezone.localdate() + timedelta(days=1),
            hora_inicio=dt_time(10), hora_fim=dt_time(11),
            status=Agendamento.Status.CONFIRMED,
        )

    def test_schedule_creates_configured_offsets_idempotently(self):
        ReminderService.schedule(self.appointment)
        ReminderService.schedule(self.appointment)
        self.assertEqual(self.appointment.reminders.count(), 2)

    @patch('core.services.reminders.EvolutionProvider.send_text')
    def test_due_reminder_uses_approved_template_and_persists_result(self, send_mock):
        send_mock.return_value = SimpleNamespace(message_id='wamid.reminder.1')
        reminder = AppointmentReminder.objects.create(
            appointment=self.appointment, offset_hours=24,
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )
        self.assertTrue(ReminderService.send(reminder))
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, AppointmentReminder.Status.SENT)
        call = send_mock.call_args
        self.assertEqual(call.args[0], 'reminder-evolution')
        self.assertIn('Consulta', call.args[2])
