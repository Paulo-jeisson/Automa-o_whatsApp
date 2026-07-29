from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Atendimento, Contato, EmpresaCliente, WhatsAppIntegration
from .services.whatsapp.client import SendTextResult
from .services.whatsapp.embedded_signup import EmbeddedSignupService
from .services.whatsapp.exceptions import WhatsAppAPIError, WhatsAppProviderError
from .services.whatsapp.outbound import send_text_for_attendance


TEST_KEY = Fernet.generate_key().decode()
META_SETTINGS = {
    'META_APP_ID': '1461814772658592',
    'META_APP_SECRET': 'segredo-de-teste',
    'META_EMBEDDED_SIGNUP_CONFIG_ID': 'config-de-teste',
    'META_GRAPH_API_VERSION': 'v25.0',
    'WHATSAPP_TOKEN_ENCRYPTION_KEY': TEST_KEY,
}


class FakeGraph:
    def __init__(self):
        self.access_token = ''
        self.subscribed = []
        self.unsubscribed = []

    def exchange_code(self, code):
        if code != 'codigo-valido':
            return {}
        return {'access_token': 'token-secreto-da-empresa', 'expires_in': 3600}

    def debug_token(self, token):
        return {
            'data': {
                'is_valid': True,
                'app_id': META_SETTINGS['META_APP_ID'],
                'scopes': [
                    'whatsapp_business_management',
                    'whatsapp_business_messaging',
                ],
            },
        }

    def get_waba_phones(self, waba_id):
        return {
            'data': [{
                'id': '1226385717231981',
                'display_phone_number': '+55 11 99999-9999',
                'verified_name': 'Clínica Teste',
            }],
        }

    def get_phone(self, phone_number_id):
        return {
            'id': phone_number_id,
            'display_phone_number': '+55 11 99999-9999',
            'verified_name': 'Clínica Teste',
        }

    def subscribe_app(self, waba_id):
        self.subscribed.append(waba_id)
        return {'success': True}

    def unsubscribe_app(self, waba_id):
        self.unsubscribed.append(waba_id)
        return {'success': True}


@override_settings(**META_SETTINGS)
class EmbeddedSignupServiceTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('clinica', password='senha-segura')
        self.empresa = EmpresaCliente.objects.create(usuario=user, nome='Clínica Teste')

    def test_connect_encrypts_token_and_subscribes_waba(self):
        graph = FakeGraph()

        result = EmbeddedSignupService.connect(
            empresa=self.empresa,
            code='codigo-valido',
            waba_id='1608450217682644',
            phone_number_id='1226385717231981',
            graph_client=graph,
        )

        integration = result.integration
        self.assertTrue(result.created)
        self.assertTrue(integration.is_connected)
        self.assertNotEqual(integration.access_token_encrypted, 'token-secreto-da-empresa')
        self.assertEqual(integration.get_access_token(), 'token-secreto-da-empresa')
        self.assertEqual(graph.subscribed, ['1608450217682644'])

    def test_same_phone_cannot_be_connected_to_another_company(self):
        EmbeddedSignupService.connect(
            empresa=self.empresa,
            code='codigo-valido',
            waba_id='1608450217682644',
            phone_number_id='1226385717231981',
            graph_client=FakeGraph(),
        )
        other_user = get_user_model().objects.create_user('outra', password='senha-segura')
        other = EmpresaCliente.objects.create(usuario=other_user, nome='Outra empresa')

        with self.assertRaisesMessage(
            WhatsAppProviderError, 'Este número já está conectado a outra empresa.'
        ):
            EmbeddedSignupService.connect(
                empresa=other,
                code='codigo-valido',
                waba_id='1608450217682644',
                phone_number_id='1226385717231981',
                graph_client=FakeGraph(),
            )

    def test_rejects_phone_outside_authorized_waba(self):
        with self.assertRaisesMessage(
            WhatsAppProviderError, 'O número não pertence à conta WhatsApp autorizada.'
        ):
            EmbeddedSignupService.connect(
                empresa=self.empresa,
                code='codigo-valido',
                waba_id='1608450217682644',
                phone_number_id='999999999',
                graph_client=FakeGraph(),
            )

    def test_disconnect_removes_local_credential(self):
        graph = FakeGraph()
        integration = EmbeddedSignupService.connect(
            empresa=self.empresa,
            code='codigo-valido',
            waba_id='1608450217682644',
            phone_number_id='1226385717231981',
            graph_client=graph,
        ).integration

        EmbeddedSignupService.disconnect(integration, graph_client=graph)

        integration.refresh_from_db()
        self.assertFalse(integration.is_active)
        self.assertEqual(integration.access_token_encrypted, '')
        self.assertEqual(
            integration.onboarding_status,
            WhatsAppIntegration.OnboardingStatus.DISCONNECTED,
        )
        self.assertEqual(graph.unsubscribed, ['1608450217682644'])


@override_settings(**META_SETTINGS)
class EmbeddedSignupViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('dono', password='senha-segura')
        self.empresa = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa do dono')
        self.client.force_login(self.user)

    def test_page_contains_meta_redirect_without_exposing_secret(self):
        response = self.client.get(reverse('whatsapp_onboarding'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Conectar com a Meta')
        self.assertContains(response, META_SETTINGS['META_APP_ID'])
        self.assertNotContains(response, META_SETTINGS['META_APP_SECRET'])

    @patch('core.views.EmbeddedSignupService.connect')
    def test_post_requires_nonce(self, connect_mock):
        response = self.client.post(reverse('whatsapp_onboarding'), {
            'nonce': 'invalido',
            'code': 'codigo-valido',
            'waba_id': '1608450217682644',
            'phone_number_id': '1226385717231981',
        })

        self.assertEqual(response.status_code, 302)
        connect_mock.assert_not_called()

    @patch('core.views.EmbeddedSignupService.connect')
    def test_successful_post_uses_only_logged_company(self, connect_mock):
        self.client.get(reverse('whatsapp_onboarding'))
        nonce = self.client.session['whatsapp_onboarding_nonce']

        response = self.client.post(reverse('whatsapp_onboarding'), {
            'nonce': nonce,
            'code': 'codigo-valido',
            'waba_id': '1608450217682644',
            'phone_number_id': '1226385717231981',
        })

        self.assertRedirects(response, reverse('configuracoes'))
        self.assertEqual(connect_mock.call_args.kwargs['empresa'], self.empresa)

    @patch('core.views.WhatsAppCloudClient')
    def test_connection_test_uses_encrypted_company_token(self, client_class):
        integration = WhatsAppIntegration.objects.create(
            company=self.empresa,
            phone_number_id='1226385717231981',
            whatsapp_business_account_id='1608450217682644',
            onboarding_status=WhatsAppIntegration.OnboardingStatus.CONNECTED,
        )
        integration.set_access_token('token-da-empresa-a')
        integration.save()

        response = self.client.post(reverse('testar_integracao_whatsapp'))

        self.assertRedirects(response, reverse('configuracoes'))
        self.assertEqual(client_class.call_args.kwargs['access_token'], 'token-da-empresa-a')
        self.assertEqual(
            client_class.call_args.kwargs['phone_number_id'],
            '1226385717231981',
        )

    @patch('core.views.WhatsAppCloudClient')
    def test_company_b_never_uses_company_a_token(self, client_class):
        integration_a = WhatsAppIntegration.objects.create(
            company=self.empresa,
            phone_number_id='111111111',
            whatsapp_business_account_id='222222222',
            onboarding_status=WhatsAppIntegration.OnboardingStatus.CONNECTED,
        )
        integration_a.set_access_token('token-da-empresa-a')
        integration_a.save()
        user_b = get_user_model().objects.create_user('dono-b', password='senha-segura')
        empresa_b = EmpresaCliente.objects.create(usuario=user_b, nome='Empresa B')
        integration_b = WhatsAppIntegration.objects.create(
            company=empresa_b,
            phone_number_id='333333333',
            whatsapp_business_account_id='444444444',
            onboarding_status=WhatsAppIntegration.OnboardingStatus.CONNECTED,
        )
        integration_b.set_access_token('token-da-empresa-b')
        integration_b.save()
        self.client.force_login(user_b)

        self.client.post(reverse('testar_integracao_whatsapp'))

        self.assertEqual(client_class.call_args.kwargs['access_token'], 'token-da-empresa-b')
        self.assertNotEqual(client_class.call_args.kwargs['access_token'], 'token-da-empresa-a')

    def test_api_error_redirects_instead_of_500(self):
        integration = WhatsAppIntegration.objects.create(
            company=self.empresa,
            phone_number_id='1226385717231981',
            whatsapp_business_account_id='1608450217682644',
            onboarding_status=WhatsAppIntegration.OnboardingStatus.CONNECTED,
        )
        integration.set_access_token('token-que-nao-pode-aparecer')
        integration.save()

        with patch(
            'core.views.WhatsAppCloudClient.test_configuration',
            side_effect=WhatsAppAPIError(
                'A Meta rejeitou a requisição.',
                status_code=401,
                error_code='190',
                error_subcode='463',
                meta_message='token-que-nao-pode-aparecer',
            ),
        ):
            response = self.client.post(
                reverse('testar_integracao_whatsapp'),
                follow=True,
            )

        self.assertRedirects(response, reverse('configuracoes'))
        self.assertContains(response, 'token inválido ou expirado')
        self.assertNotContains(response, 'token-que-nao-pode-aparecer')

    @override_settings(META_ACCESS_TOKEN='')
    def test_missing_legacy_token_redirects_safely(self):
        WhatsAppIntegration.objects.create(
            company=self.empresa,
            phone_number_id='1226385717231981',
            whatsapp_business_account_id='1608450217682644',
        )

        response = self.client.post(reverse('testar_integracao_whatsapp'))

        self.assertRedirects(response, reverse('configuracoes'))

    def test_decryption_error_redirects_safely(self):
        WhatsAppIntegration.objects.create(
            company=self.empresa,
            phone_number_id='1226385717231981',
            whatsapp_business_account_id='1608450217682644',
            onboarding_status=WhatsAppIntegration.OnboardingStatus.CONNECTED,
            access_token_encrypted='conteudo-invalido',
        )

        response = self.client.post(reverse('testar_integracao_whatsapp'))

        self.assertRedirects(response, reverse('configuracoes'))


@override_settings(**META_SETTINGS, META_ACCESS_TOKEN='token-global-legado')
class PerTenantOutboundTokenTests(TestCase):
    def test_outbound_uses_company_encrypted_token(self):
        user = get_user_model().objects.create_user('tenant', password='senha-segura')
        empresa = EmpresaCliente.objects.create(usuario=user, nome='Tenant')
        integration = WhatsAppIntegration.objects.create(
            company=empresa,
            phone_number_id='111111111',
            whatsapp_business_account_id='222222222',
            onboarding_status=WhatsAppIntegration.OnboardingStatus.CONNECTED,
        )
        integration.set_access_token('token-exclusivo-tenant')
        integration.save()
        contato = Contato.objects.create(empresa=empresa, whatsapp_id='5511999999999')
        atendimento = Atendimento.objects.create(
            empresa=empresa,
            contato=contato,
            nome_cliente='Cliente',
            telefone_cliente=contato.whatsapp_id,
            opcao_escolhida='1',
            necessidade='Agendar',
        )

        with patch(
            'core.services.whatsapp.outbound.WhatsAppCloudClient'
        ) as client_class:
            client_class.return_value.send_text.return_value = SendTextResult('wamid.1')
            send_text_for_attendance(atendimento, 'Olá')

        self.assertEqual(
            client_class.call_args.kwargs['access_token'],
            'token-exclusivo-tenant',
        )
        self.assertNotEqual(
            client_class.call_args.kwargs['access_token'],
            'token-global-legado',
        )
