import json
import os
import subprocess
import sys
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import EmpresaCliente, Plan, Subscription


class DevelopmentDomainTests(SimpleTestCase):
    @override_settings(ALLOWED_HOSTS=['127.0.0.1', 'localhost'])
    def test_development_accepts_local_hosts_and_rejects_unknown(self):
        self.assertEqual(self.client.get('/', HTTP_HOST='127.0.0.1').status_code, 200)
        self.assertEqual(self.client.get('/', HTTP_HOST='localhost').status_code, 200)
        self.assertEqual(self.client.get('/', HTTP_HOST='malicious.example').status_code, 400)

    @override_settings(
        ALLOWED_HOSTS=['iaatende.app', 'www.iaatende.app'],
        CSRF_TRUSTED_ORIGINS=['https://iaatende.app', 'https://www.iaatende.app'],
    )
    def test_production_hosts_and_csrf_origin_allowlist(self):
        self.assertEqual(self.client.get('/', HTTP_HOST='iaatende.app', secure=True).status_code, 200)
        self.assertEqual(self.client.get('/', HTTP_HOST='www.iaatende.app', secure=True).status_code, 200)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.get(reverse('cadastro'), HTTP_HOST='iaatende.app', secure=True)
        token = csrf_client.cookies['csrftoken'].value
        accepted = csrf_client.post(
            reverse('cadastro'), {'csrfmiddlewaretoken': token},
            HTTP_HOST='iaatende.app', HTTP_ORIGIN='https://iaatende.app', secure=True,
        )
        rejected = csrf_client.post(
            reverse('cadastro'), {'csrfmiddlewaretoken': token},
            HTTP_HOST='iaatende.app', HTTP_ORIGIN='https://evil.example', secure=True,
        )
        self.assertNotEqual(accepted.status_code, 403)
        self.assertEqual(rejected.status_code, 403)


class ProductionSettingsImportTests(SimpleTestCase):
    def test_official_production_domain_and_integrations(self):
        environment = os.environ.copy()
        environment.update({
            'APP_ENV': 'production', 'DEBUG': 'False',
            'DJANGO_SECRET_KEY': 'production-test-secret-' * 4,
            'ALLOWED_HOSTS': 'iaatende.app,www.iaatende.app',
            'CSRF_TRUSTED_ORIGINS': 'https://iaatende.app,https://www.iaatende.app',
            'PUBLIC_BASE_URL': 'https://iaatende.app', 'SITE_URL': 'https://iaatende.app',
            'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
            'EMAIL_HOST': 'smtp.example.test', 'EMAIL_HOST_USER': 'user',
            'EMAIL_HOST_PASSWORD': 'password', 'DEFAULT_FROM_EMAIL': 'IAATENDE <noreply@iaatende.app>',
            'AI_ENABLED': 'False', 'ASAAS_ENVIRONMENT': 'sandbox',
            'ASAAS_API_URL': 'https://api-sandbox.asaas.com/v3',
            'ASAAS_API_KEY': 'sandbox-key', 'ASAAS_WEBHOOK_TOKEN': 'webhook-token',
            'ASAAS_CHECKOUT_SUCCESS_URL': 'https://iaatende.app/assinatura/retorno/',
            'ASAAS_CHECKOUT_CANCEL_URL': 'https://iaatende.app/planos/',
            'EVOLUTION_WEBHOOK_URL': 'https://iaatende.app/webhooks/evolution/',
            'POSTGRES_DB': 'iaatende', 'POSTGRES_USER': 'iaatende',
            'POSTGRES_PASSWORD': 'database-password', 'POSTGRES_HOST': '127.0.0.1',
        })
        code = (
            'import json; import app.settings_production as s; '
            'print(json.dumps({"debug":s.DEBUG,"hosts":s.ALLOWED_HOSTS,'
            '"csrf":s.CSRF_TRUSTED_ORIGINS,"public":s.PUBLIC_BASE_URL,'
            '"site":s.SITE_URL,"asaas":s.ASAAS_API_URL,"evolution":s.EVOLUTION_WEBHOOK_URL,'
            '"ssl":s.SECURE_SSL_REDIRECT,"proxy":s.SECURE_PROXY_SSL_HEADER}))'
        )
        result = subprocess.run(
            [sys.executable, '-c', code], cwd=settings.BASE_DIR, env=environment,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip())
        self.assertFalse(data['debug'])
        self.assertEqual(set(data['hosts']), {'iaatende.app', 'www.iaatende.app'})
        self.assertEqual(data['public'], 'https://iaatende.app')
        self.assertEqual(data['site'], 'https://iaatende.app')
        self.assertEqual(data['evolution'], 'https://iaatende.app/webhooks/evolution/')
        self.assertTrue(data['ssl'])
        self.assertEqual(data['proxy'], ['HTTP_X_FORWARDED_PROTO', 'https'])


@override_settings(SITE_URL='https://iaatende.app', PUBLIC_BASE_URL='https://iaatende.app')
class PublicDomainOutputTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'domain-owner', password='safe-password', email='owner@example.com',
        )
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Domain Company')
        plan = Plan.objects.create(name='Ativo', code='domain-active')
        now = timezone.now()
        Subscription.objects.create(
            empresa=self.company, plan=plan, status=Subscription.Status.ACTIVE,
            current_period_start=now - timedelta(days=1), current_period_end=now + timedelta(days=30),
        )

    def test_canonical_exists_only_on_landing_and_sitemap_is_public_only(self):
        landing = self.client.get(reverse('landing_page'))
        self.assertContains(landing, '<link rel="canonical" href="https://iaatende.app/">', html=True)
        self.client.force_login(self.user)
        internal = self.client.get(reverse('prompt_generator'))
        self.assertNotContains(internal, 'rel="canonical"')
        sitemap = self.client.get(reverse('sitemap_xml')).content.decode()
        self.assertIn('https://iaatende.app/', sitemap)
        for internal_path in ('/login/', '/cadastro/', '/assinatura/', '/admin/', '/api/'):
            self.assertNotIn(f'<loc>https://iaatende.app{internal_path}</loc>', sitemap)

    def test_robots_blocks_internal_routes(self):
        robots = self.client.get(reverse('robots_txt')).content.decode()
        for path in ('/login/', '/cadastro/', '/assinatura/', '/webhooks/', '/admin/', '/api/'):
            self.assertIn(f'Disallow: {path}', robots)
        self.assertIn('Sitemap: https://iaatende.app/sitemap.xml', robots)

    @override_settings(
        DEBUG=False, PASSWORD_RESET_USE_REQUEST_DOMAIN=False,
        PUBLIC_BASE_URL='https://iaatende.app',
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    )
    def test_production_password_reset_email_uses_official_https_domain(self):
        self.client.post(reverse('password_reset'), {'email': self.user.email})
        self.assertIn('https://iaatende.app/senha/redefinir/', mail.outbox[0].body)
        self.assertNotIn('localhost', mail.outbox[0].body)
