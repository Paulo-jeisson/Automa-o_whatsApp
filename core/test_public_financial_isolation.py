import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.base import Message
from django.contrib.messages.storage.cookie import MessageEncoder
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import EmpresaCliente, Plan, Subscription
from core.public_routes import PUBLIC_HTML_URL_NAMES, PUBLIC_TEMPLATE_NAMES


@override_settings(SUBSCRIPTION_ENFORCEMENT_ENABLED=True)
class PublicFinancialIsolationRegressionTests(TestCase):
    """Contrato permanente: páginas públicas não conhecem estado financeiro interno."""

    FORBIDDEN_CONTEXT_KEYS = {
        'system_subscription', 'system_subscription_grace', 'subscription',
        'checkout', 'billing', 'payment', 'payment_event',
    }
    FORBIDDEN_RENDERED_SENTINELS = {
        'INTERNAL_FINANCIAL_MESSAGE_SENTINEL',
        'INTERNAL_PLAN_SENTINEL',
        'INTERNAL_CHECKOUT_SENTINEL',
        'INTERNAL_CUSTOMER_SENTINEL',
        'INTERNAL_SUBSCRIPTION_SENTINEL',
        'subscription-warning',
        'Pagamento pendente. Regularize',
        'ASSINATURA NECESSÁRIA',
        'Seu teste de 3 dias acabou',
    }
    FORBIDDEN_TEMPLATE_TOKENS = {
        'system_subscription', 'system_subscription_grace',
        'subscription.status', 'subscription.get_status_display',
        'provider_checkout_id', 'provider_subscription_id',
        'payment_events', 'subscription-warning',
        '{% for message in messages %}', '{{ message }}',
    }

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            'public-isolation-owner', password='safe-password', email='public@example.com',
        )
        cls.company = EmpresaCliente.objects.create(
            usuario=cls.user, nome='Empresa Pública', public_slug='empresa-publica',
        )
        cls.plan = Plan.objects.create(
            name='INTERNAL_PLAN_SENTINEL', code='internal-regression-plan', price_cents=987654,
        )
        now = timezone.now()
        cls.subscription = Subscription.objects.create(
            empresa=cls.company, plan=cls.plan, status=Subscription.Status.GRACE,
            overdue_since=now - timedelta(days=5),
            grace_period_ends_at=now - timedelta(days=2),
            provider_customer_id='INTERNAL_CUSTOMER_SENTINEL',
            provider_subscription_id='INTERNAL_SUBSCRIPTION_SENTINEL',
            provider_checkout_id='INTERNAL_CHECKOUT_SENTINEL',
        )

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['_messages'] = MessageEncoder().encode([
            Message(40, 'INTERNAL_FINANCIAL_MESSAGE_SENTINEL'),
        ])
        session.save()

    def public_cases(self):
        return {
            'landing_page': reverse('landing_page'),
            'cadastro': reverse('cadastro'),
            'login': reverse('login'),
            'password_reset': reverse('password_reset'),
            'password_reset_done': reverse('password_reset_done'),
            'password_reset_confirm': reverse('password_reset_confirm', kwargs={
                'uidb64': 'invalid', 'token': 'invalid-token',
            }),
            'password_reset_complete': reverse('password_reset_complete'),
            'politica_privacidade': reverse('politica_privacidade'),
            'termos_servico': reverse('termos_servico'),
            'exclusao_dados': reverse('exclusao_dados'),
            'atendimento_publico': reverse('atendimento_publico', kwargs={
                'public_slug': self.company.public_slug,
            }),
        }

    def test_every_catalogued_public_route_is_runtime_isolated(self):
        cases = self.public_cases()
        self.assertEqual(set(cases), set(PUBLIC_HTML_URL_NAMES))
        for name, url in cases.items():
            with self.subTest(public_route=name):
                response = self.client.get(url)
                self.assertLess(response.status_code, 400)
                content = response.content.decode(errors='replace')
                for sentinel in self.FORBIDDEN_RENDERED_SENTINELS:
                    self.assertNotIn(sentinel, content)
                contexts = response.context or []
                if not isinstance(contexts, (list, tuple)):
                    contexts = [contexts]
                for context in contexts:
                    flattened = context.flatten()
                    self.assertFalse(
                        self.FORBIDDEN_CONTEXT_KEYS.intersection(flattened),
                        f'{name} recebeu contexto financeiro interno.',
                    )

    def test_public_template_dependency_tree_has_no_internal_financial_component(self):
        templates_root = Path(settings.BASE_DIR) / 'templates'
        dependency_pattern = re.compile(
            r'{%\s*(?:extends|include)\s+["\']([^"\']+)["\']',
        )
        expected = set(PUBLIC_TEMPLATE_NAMES)
        discovered = set()
        pending = list(expected)
        while pending:
            template_name = pending.pop()
            if template_name in discovered:
                continue
            discovered.add(template_name)
            source = (templates_root / template_name).read_text(encoding='utf-8')
            self.assertIn(
                '{% extends "public_base.html" %}', source,
                f'{template_name} deixou de herdar o layout público isolado.',
            ) if template_name in expected else None
            for token in self.FORBIDDEN_TEMPLATE_TOKENS:
                self.assertNotIn(token, source, f'{template_name} contém componente financeiro interno.')
            for dependency in dependency_pattern.findall(source):
                if dependency not in discovered:
                    pending.append(dependency)
        self.assertIn('public_base.html', discovered)
        self.assertNotIn('base.html', discovered)
