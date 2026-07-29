from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Atendimento, AuditEvent, EmpresaCliente, RateLimitBucket


class RateLimitTests(TestCase):
    def test_login_is_rate_limited_by_ip(self):
        for _ in range(10):
            response = self.client.post(
                reverse('login'),
                {'username': 'inexistente', 'password': 'incorreta'},
                REMOTE_ADDR='198.51.100.10',
            )
            self.assertNotEqual(response.status_code, 429)

        response = self.client.post(
            reverse('login'),
            {'username': 'inexistente', 'password': 'incorreta'},
            REMOTE_ADDR='198.51.100.10',
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(RateLimitBucket.objects.count(), 1)
        self.assertNotIn('198.51.100.10', RateLimitBucket.objects.get().key)

    @override_settings(META_VERIFY_TOKEN='verify-token')
    def test_webhook_rate_limit_returns_json(self):
        for _ in range(120):
            response = self.client.get(
                reverse('whatsapp_webhook'),
                REMOTE_ADDR='198.51.100.20',
            )
            self.assertNotEqual(response.status_code, 429)

        response = self.client.get(
            reverse('whatsapp_webhook'),
            REMOTE_ADDR='198.51.100.20',
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['detail'], 'Muitas requisições.')


class PasswordResetTests(TestCase):
    def test_password_reset_sends_email_without_revealing_account_existence(self):
        get_user_model().objects.create_user(
            username='dono',
            email='dono@example.com',
            password='senha-segura',
        )

        response = self.client.post(
            reverse('password_reset'),
            {'email': 'dono@example.com'},
        )

        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/senha/redefinir/', mail.outbox[0].body)

        response = self.client.post(
            reverse('password_reset'),
            {'email': 'nao-existe@example.com'},
        )
        self.assertRedirects(response, reverse('password_reset_done'))


class AuditTests(TestCase):
    def test_sensitive_change_creates_sanitized_audit_event(self):
        user = get_user_model().objects.create_user('dono', password='senha-segura')
        empresa = EmpresaCliente.objects.create(usuario=user, nome='Empresa')
        atendimento = Atendimento.objects.create(
            empresa=empresa,
            nome_cliente='Cliente',
            telefone_cliente='5511999999999',
            opcao_escolhida='Atendimento',
            necessidade='Teste',
        )
        self.client.login(username='dono', password='senha-segura')

        response = self.client.post(
            reverse('atualizar_status_atendimento', args=[atendimento.pk]),
            {'status': Atendimento.STATUS_FINALIZADO},
            REMOTE_ADDR='203.0.113.50',
        )

        self.assertEqual(response.status_code, 302)
        event = AuditEvent.objects.get(action='attendance.status_changed')
        self.assertEqual(event.actor, user)
        self.assertEqual(event.empresa, empresa)
        self.assertEqual(event.target_id, str(atendimento.pk))
        self.assertEqual(
            event.metadata,
            {'from': Atendimento.STATUS_NOVO, 'to': Atendimento.STATUS_FINALIZADO},
        )
        self.assertNotEqual(event.ip_hash, '203.0.113.50')
