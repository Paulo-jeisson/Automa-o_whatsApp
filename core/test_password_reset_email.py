import re
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from app.email_settings import CONSOLE_BACKEND, SMTP_BACKEND, resolve_email_backend


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    ALLOWED_HOSTS=['testserver', 'dev.example.test', 'internal-container'],
)
class PasswordResetEmailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='reset-owner', email='owner@example.com', password='senha-antiga-segura',
        )

    def request_reset(self, email='owner@example.com', host='dev.example.test'):
        return self.client.post(reverse('password_reset'), {'email': email}, HTTP_HOST=host)

    def test_sends_one_html_email_with_official_token_and_dynamic_development_domain(self):
        response = self.request_reset()
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, 'Redefinição de senha — IAATENDE')
        self.assertEqual(message.to, ['owner@example.com'])
        self.assertIn('http://dev.example.test/senha/redefinir/', message.body)
        self.assertNotIn('localhost', message.body)
        self.assertNotIn('127.0.0.1', message.body)
        self.assertTrue(message.alternatives)
        self.assertEqual(message.alternatives[0].mimetype, 'text/html')
        uid, token = re.search(r'/senha/redefinir/([^/]+)/([^/]+)/', message.body).groups()
        self.assertTrue(default_token_generator.check_token(self.user, token))
        self.assertTrue(uid)

    @override_settings(DEBUG=False, PUBLIC_BASE_URL='https://app.iaatende.example', PASSWORD_RESET_USE_REQUEST_DOMAIN=False)
    def test_production_link_uses_configured_public_domain(self):
        self.request_reset(host='internal-container:8000')
        self.assertIn('https://app.iaatende.example/senha/redefinir/', mail.outbox[0].body)
        self.assertNotIn('internal-container', mail.outbox[0].body)

    def test_unknown_email_has_same_redirect_and_sends_nothing(self):
        existing = self.request_reset()
        mail.outbox.clear()
        unknown = self.request_reset('missing@example.com')
        self.assertEqual(existing.status_code, unknown.status_code)
        self.assertEqual(existing.url, unknown.url)
        self.assertEqual(len(mail.outbox), 0)

    def test_legacy_duplicate_email_still_sends_a_single_message(self):
        get_user_model().objects.create_user(
            username='legacy-duplicate', email='owner@example.com', password='outra-senha-segura',
        )
        self.request_reset()
        self.assertEqual(len(mail.outbox), 1)

    def test_invalid_token_does_not_change_password(self):
        response = self.client.get(reverse('password_reset_confirm', kwargs={
            'uidb64': 'MQ', 'token': 'token-invalido',
        }))
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('senha-antiga-segura'))

    def test_expired_token_is_rejected_by_django_generator(self):
        created_at = datetime.now()
        with patch.object(default_token_generator, '_now', return_value=created_at):
            token = default_token_generator.make_token(self.user)
        with patch.object(default_token_generator, '_now', return_value=created_at + timedelta(seconds=3601)):
            with override_settings(PASSWORD_RESET_TIMEOUT=3600):
                self.assertFalse(default_token_generator.check_token(self.user, token))

    def test_complete_password_reset_changes_password_and_invalidates_token(self):
        self.request_reset()
        path = re.search(r'(http://[^\s]+/senha/redefinir/[^/]+/[^/]+/)', mail.outbox[0].body).group(1)
        issued_token = path.rstrip('/').rsplit('/', 1)[-1]
        initial = self.client.get(path)
        self.assertEqual(initial.status_code, 302)
        confirmation_path = initial['Location']
        response = self.client.post(confirmation_path, {
            'new_password1': 'nova-senha-segura-987',
            'new_password2': 'nova-senha-segura-987',
        })
        self.assertRedirects(response, reverse('password_reset_complete'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('nova-senha-segura-987'))
        self.assertFalse(default_token_generator.check_token(self.user, issued_token))


class EmailBackendSelectionTests(TestCase):
    def test_complete_gmail_environment_selects_smtp(self):
        environment = {
            'EMAIL_BACKEND': SMTP_BACKEND, 'EMAIL_HOST': 'smtp.gmail.com',
            'EMAIL_HOST_USER': 'sender@gmail.com', 'EMAIL_HOST_PASSWORD': 'app-password',
            'DEFAULT_FROM_EMAIL': 'ZapFluxo <sender@gmail.com>',
        }
        self.assertEqual(resolve_email_backend(environment), SMTP_BACKEND)

    def test_incomplete_environment_falls_back_to_console(self):
        self.assertEqual(resolve_email_backend({'EMAIL_BACKEND': SMTP_BACKEND}), CONSOLE_BACKEND)
        self.assertEqual(resolve_email_backend({}), CONSOLE_BACKEND)
