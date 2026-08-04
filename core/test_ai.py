from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    AIConfiguration,
    AIPromptProfile,
    AIPromptVersion,
    AuditEvent,
    EmpresaCliente,
)
from .services.ai import AIAgent, AIConfigurationError, AIProviderError
from .services.ai.client import OpenAIClient


class OpenAIClientTests(TestCase):
    @override_settings(
        AI_ENABLED=True,
        OPENAI_API_KEY='test-key-not-real',
        AI_MODEL='gpt-test',
        AI_TIMEOUT=7,
    )
    def test_responses_api_is_called_through_isolated_client(self):
        sdk = Mock()
        sdk.responses.create.return_value = SimpleNamespace(
            id='resp_123',
            output_text='Olá, como posso ajudar?',
        )
        client = OpenAIClient(sdk_client=sdk)

        result = client.generate(
            instructions='Instruções da empresa',
            user_input='Olá',
        )

        self.assertEqual(result.text, 'Olá, como posso ajudar?')
        self.assertEqual(result.response_id, 'resp_123')
        sdk.responses.create.assert_called_once_with(
            model='gpt-test',
            instructions='Instruções da empresa',
            input='Olá',
        )

    @override_settings(AI_ENABLED=False, OPENAI_API_KEY='test-key-not-real')
    def test_global_kill_switch_prevents_provider_call(self):
        sdk = Mock()
        with self.assertRaises(AIConfigurationError):
            OpenAIClient(sdk_client=sdk).generate(
                instructions='Instruções',
                user_input='Mensagem',
            )
        sdk.responses.create.assert_not_called()

    @override_settings(AI_ENABLED=True, OPENAI_API_KEY='test-key-not-real')
    def test_empty_provider_response_is_sanitized(self):
        sdk = Mock()
        sdk.responses.create.return_value = SimpleNamespace(id='resp_empty', output_text='')
        with self.assertRaises(AIProviderError):
            OpenAIClient(sdk_client=sdk).generate(
                instructions='Instruções',
                user_input='Mensagem',
            )


class AIAgentTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('empresa-ai')
        self.empresa = EmpresaCliente.objects.create(usuario=user, nome='Clínica A')
        self.configuration = AIConfiguration.objects.create(
            empresa=self.empresa,
            enabled=True,
            assistant_name='Lia',
            tone='acolhedor',
            business_description='Clínica de saúde.',
        )

    def test_agent_builds_company_context_without_business_operations(self):
        provider = Mock()
        provider.generate.return_value = SimpleNamespace(
            text='Resposta controlada',
            response_id='resp_1',
        )

        reply = AIAgent(client=provider).respond(
            configuration=self.configuration,
            user_input='Quero informações',
        )

        self.assertEqual(reply.text, 'Resposta controlada')
        call = provider.generate.call_args.kwargs
        self.assertIn('Clínica A', call['instructions'])
        self.assertIn('Lia', call['instructions'])
        self.assertEqual(call['user_input'], 'Quero informações')

    def test_legacy_company_flag_does_not_disable_provider(self):
        self.configuration.enabled = False
        provider = Mock()
        provider.generate.return_value = SimpleNamespace(
            text='Resposta automática', response_id='resp_legacy',
            input_tokens=1, output_tokens=1, tool_calls=0,
        )
        reply = AIAgent(client=provider).respond(
            configuration=self.configuration, user_input='Olá',
        )
        self.assertEqual(reply.text, 'Resposta automática')
        provider.generate.assert_called_once()


    def test_agent_loads_generated_prompt_and_logs_its_provenance(self):
        profile = AIPromptProfile.objects.create(
            empresa=self.empresa,
            generated_prompt='PROMPT COMPILADO ATUAL',
            draft_prompt='RASCUNHO VISIVEL DIFERENTE',
        )
        AIPromptVersion.objects.create(
            profile=profile,
            version=3,
            content='PROMPT COMPILADO ATUAL',
            is_active=True,
        )
        provider = Mock()
        provider.generate.return_value = SimpleNamespace(
            text='Resposta controlada', response_id='resp_prompt',
        )

        with self.assertLogs('whatsapp.ai.prompt', level='INFO') as captured:
            AIAgent(client=provider).respond(
                configuration=self.configuration,
                user_input='Mensagem real',
            )

        instructions = provider.generate.call_args.kwargs['instructions']
        self.assertIn('PROMPT COMPILADO ATUAL', instructions)
        self.assertNotIn('RASCUNHO VISIVEL DIFERENTE', instructions)
        log = '\n'.join(captured.output)
        self.assertIn(f'company_id={self.empresa.pk}', log)
        self.assertIn(f'prompt_id={profile.pk}', log)
        self.assertIn('prompt_version=3', log)
        self.assertIn('configured_matches_visible=False', log)


class AIConfigurationViewTests(TestCase):
    def setUp(self):
        self.user_a = get_user_model().objects.create_user('dono-a', password='senha-segura')
        self.company_a = EmpresaCliente.objects.create(usuario=self.user_a, nome='Empresa A')
        self.user_b = get_user_model().objects.create_user('dono-b', password='senha-segura')
        self.company_b = EmpresaCliente.objects.create(usuario=self.user_b, nome='Empresa B')
        self.config_b = AIConfiguration.objects.create(
            empresa=self.company_b,
            enabled=True,
            assistant_name='Assistente B',
        )
        self.client.login(username='dono-a', password='senha-segura')

    def test_configuration_is_created_only_for_logged_company(self):
        response = self.client.post(reverse('configuracao_ia'), {
            'enabled': 'on',
            'assistant_name': 'Assistente A',
            'greeting': 'Olá',
            'tone': 'objetivo',
            'business_description': 'Empresa de testes',
            'additional_information': 'Informação A',
            'human_handoff_rules': 'Quando solicitado.',
        })

        self.assertRedirects(response, reverse('configuracao_ia'))
        config_a = AIConfiguration.objects.get(empresa=self.company_a)
        self.assertEqual(config_a.assistant_name, 'Assistente A')
        self.config_b.refresh_from_db()
        self.assertEqual(self.config_b.assistant_name, 'Assistente B')
        self.assertTrue(
            AuditEvent.objects.filter(
                empresa=self.company_a,
                action='ai.configuration_updated',
            ).exists()
        )

    def test_page_never_exposes_api_key(self):
        with override_settings(OPENAI_API_KEY='super-secret-key', AI_ENABLED=True):
            response = self.client.get(reverse('configuracao_ia'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'super-secret-key')
