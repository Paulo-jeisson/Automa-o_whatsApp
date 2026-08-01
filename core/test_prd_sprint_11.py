import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import APIRefreshToken, AuditEvent, EmpresaCliente


class PRDSprintElevenTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('security-owner', password='Strong-password-123')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Secure Company')

    def test_jwt_access_refresh_rotation_and_replay_protection(self):
        response = self.client.post(reverse('api_token_create'), data=json.dumps({'username': 'security-owner', 'password': 'Strong-password-123'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        pair = response.json()
        me = self.client.get(reverse('api_me'), HTTP_AUTHORIZATION=f"Bearer {pair['access']}")
        self.assertEqual(me.json()['company_id'], self.company.pk)
        rotated = self.client.post(reverse('api_token_refresh'), data=json.dumps({'refresh': pair['refresh']}), content_type='application/json')
        self.assertEqual(rotated.status_code, 200)
        replay = self.client.post(reverse('api_token_refresh'), data=json.dumps({'refresh': pair['refresh']}), content_type='application/json')
        self.assertEqual(replay.status_code, 401)

    def test_security_headers_are_present(self):
        response = self.client.get(reverse('landing_page'))
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn("default-src 'self'", response['Content-Security-Policy'])
        self.assertEqual(response['Referrer-Policy'], 'strict-origin-when-cross-origin')

    def test_security_center_requires_owner_permission(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('security_center'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Central de segurança')

    def test_token_revocation_is_audited(self):
        APIRefreshToken.objects.create(user=self.user, jti_hash='a' * 64, expires_at='2099-01-01T00:00:00Z')
        self.client.force_login(self.user)
        self.client.post(reverse('revoke_api_tokens'))
        self.assertTrue(AuditEvent.objects.filter(empresa=self.company, action='security.api_tokens_revoked').exists())
