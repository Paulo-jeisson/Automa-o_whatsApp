from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse


class PlatformSecurityTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.customer = user_model.objects.create_user('platform-customer', password='test-pass')
        self.admin = user_model.objects.create_superuser(
            'platform-root', email='root@example.com', password='test-pass',
        )

    def test_common_user_receives_403(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('platform:dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse('platform:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_explicit_platform_permission_grants_access_without_company(self):
        operator = get_user_model().objects.create_user('platform-operator')
        operator.user_permissions.add(Permission.objects.get(codename='access_platform_panel'))
        self.client.force_login(operator)
        self.assertEqual(self.client.get(reverse('platform:dashboard')).status_code, 200)


class PlatformRenderingTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            'render-root', email='render@example.com', password='test-pass',
        )
        self.client.force_login(self.admin)

    def test_superuser_accesses_dashboard_and_sidebar(self):
        response = self.client.get(reverse('platform:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PLATFORM MASTER')
        self.assertContains(response, 'Consumo OpenAI')
        self.assertNotContains(response, 'system-menu')

    def test_openai_usage_renders_without_api_key(self):
        response = self.client.get(reverse('platform:openai'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Consumo OpenAI')
        self.assertNotContains(response, 'OPENAI_API_KEY')

    def test_companies_render(self):
        response = self.client.get(reverse('platform:companies'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Empresas')

    def test_logs_render(self):
        response = self.client.get(reverse('platform:logs'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Logs operacionais')

    def test_all_platform_pages_render(self):
        names = ('finance', 'infrastructure', 'subscriptions', 'settings')
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f'platform:{name}')).status_code, 200)
