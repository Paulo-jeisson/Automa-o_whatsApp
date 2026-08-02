from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.application.dto import PromptGeneratorInput
from core.application.prompt_compiler_service import PromptCompilerService
from core.models import AIPromptProfile, EmpresaCliente
from core.services.ai.context import build_company_context
from core.services.ai.prompts import build_instructions


class PromptCompilerFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('prompt-owner', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa A')
        self.client.force_login(self.user)

    def payload(self):
        return {
            'agent_name': 'Lia', 'company_name': 'Empresa A', 'segment': 'Saúde',
            'calendar_usage': 'Ofereça agendamentos.',
            'profession': 'Recepcionista', 'personality': 'Acolhedora e cordial',
            'additional_information': 'Atendimento em português.',
        }

    def test_generator_compiles_versions_and_redirects_to_editor(self):
        response = self.client.post(reverse('prompt_generator'), self.payload())
        self.assertRedirects(response, reverse('prompt_editor'))
        profile = AIPromptProfile.objects.get(empresa=self.company)
        self.assertIn('# [IDENTIDADE]', profile.generated_prompt)
        self.assertIn('Lia', profile.generated_prompt)
        self.assertIn('Empresa A', profile.generated_prompt)
        self.assertEqual(profile.versions.count(), 1)
        page = self.client.get(reverse('prompt_editor'))
        self.assertContains(page, 'Lia')
        self.assertContains(page, 'CALENDÁRIO')
        self.assertContains(page, 'NÚMEROS PASS')
        self.assertContains(page, 'VER CONVERSAS')
        self.assertContains(page, 'PROMPT DA IA')
        self.assertNotContains(page, 'Central da IA')

    def test_agent_name_ferreira_is_compiled_into_ai_prompt(self):
        payload = self.payload()
        payload['agent_name'] = 'Ferreira'
        response = self.client.post(reverse('prompt_generator'), payload)
        self.assertRedirects(response, reverse('prompt_editor'))
        profile = AIPromptProfile.objects.get(empresa=self.company)
        self.assertIn('**Ferreira**', profile.generated_prompt)
        self.assertNotIn('**Paulo**', profile.generated_prompt)
        self.assertContains(self.client.get(reverse('prompt_editor')), '**Ferreira**')

    def test_new_buyer_receives_fixed_default_prompt_and_response_delay(self):
        page = self.client.get(reverse('prompt_editor'))
        profile = AIPromptProfile.objects.get(empresa=self.company)
        self.assertContains(page, '# [IDENTIDADE]')
        self.assertIn('Pj.Advocacia', profile.generated_prompt)
        self.assertEqual(profile.response_delay_seconds, 3)
        self.assertEqual(profile.versions.count(), 1)

        response = self.client.post(reverse('prompt_editor'), {
            'prompt_content': profile.generated_prompt,
            'response_delay_seconds': 5,
        })
        self.assertRedirects(response, reverse('prompt_editor'))
        profile.refresh_from_db()
        self.assertEqual(profile.response_delay_seconds, 5)
        self.assertEqual(profile.versions.count(), 2)

    def test_other_company_cannot_export_prompt(self):
        PromptCompilerService.compile_and_save(
            empresa=self.company, user=self.user,
            data=PromptGeneratorInput(
                **self.payload(), uses_calendar=True,
                objective='Atender', service_style='', tone='', products='', services='',
            ),
        )
        other_user = get_user_model().objects.create_user('other-owner', password='safe-password')
        EmpresaCliente.objects.create(usuario=other_user, nome='Empresa B')
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(reverse('prompt_export')).status_code, 404)
        self.assertEqual(self.client.post(reverse('prompt_restore', args=[
            AIPromptProfile.objects.get(empresa=self.company).versions.first().id
        ])).status_code, 404)

    def test_settings_exposes_only_evolution_session_summary(self):
        page = self.client.get(reverse('configuracoes'))
        self.assertContains(page, 'Gerenciar WhatsApp')
        self.assertNotContains(page, 'Phone Number ID')
        self.assertNotContains(page, 'WABA ID')
        self.assertNotContains(page, 'Embedded Signup')
        self.assertNotContains(page, 'Testar integração')

    def test_runtime_prefers_saved_prompt_over_legacy_configuration_fields(self):
        class Configuration:
            empresa = self.company
            empresa_id = self.company.id
            assistant_name = 'Nome antigo'
            greeting = ''
            tone = 'Antigo'
            business_description = 'Descrição antiga'
            additional_information = ''
            human_handoff_rules = ''
            faq = ''
            policies = ''
            guidance = ''
            cancellation_rules = ''
            service_rules = ''
            allowed_information = ''

        AIPromptProfile.objects.create(empresa=self.company, generated_prompt='# Identidade\nPrompt final exclusivo')
        instructions = build_instructions(build_company_context(Configuration()))
        self.assertIn('Prompt final exclusivo', instructions)
        self.assertNotIn('Descrição antiga', instructions)
