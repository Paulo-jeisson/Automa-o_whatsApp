from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.application.dto import PromptGeneratorInput
from core.application.prompt_compiler_service import PromptCompilerService
from core.models import AIConfiguration, AIPromptProfile, AIPromptVersion, EmpresaCliente
from core.services.ai import AIAgent
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
        self.assertIn('Empresa A', profile.generated_prompt)
        self.assertNotIn('{{AGENT_NAME}}', profile.generated_prompt)
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
        page = self.client.get(reverse('configuracoes'), follow=True)
        self.assertRedirects(page, reverse('trocar_senha'))
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

    def test_saving_draft_does_not_change_active_prompt(self):
        self.client.get(reverse('prompt_editor'))
        profile = AIPromptProfile.objects.get(empresa=self.company)
        active = profile.generated_prompt
        version_count = profile.versions.count()
        response = self.client.post(reverse('prompt_editor'), {
            'action': 'draft', 'prompt_content': '# Novo rascunho',
            'response_delay_seconds': 4,
        })
        self.assertRedirects(response, reverse('prompt_editor'))
        profile.refresh_from_db()
        self.assertEqual(profile.draft_prompt, '# Novo rascunho')
        self.assertEqual(profile.generated_prompt, active)
        self.assertEqual(profile.versions.count(), version_count)

    def test_publish_updates_active_prompt_creates_version_and_preserves_history(self):
        self.client.get(reverse('prompt_editor'))
        profile = AIPromptProfile.objects.get(empresa=self.company)
        old_version = profile.versions.get(is_active=True)
        with self.assertLogs('whatsapp.ai.prompt', level='INFO') as logs:
            response = self.client.post(reverse('prompt_editor'), {
                'action': 'publish', 'prompt_content': '# Prompt publicado agora',
                'response_delay_seconds': 3,
            })
        self.assertRedirects(response, reverse('prompt_editor'))
        profile.refresh_from_db()
        new_version = profile.versions.get(is_active=True)
        old_version.refresh_from_db()
        self.assertEqual(profile.generated_prompt, '# Prompt publicado agora')
        self.assertEqual(new_version.content, profile.generated_prompt)
        self.assertTrue(new_version.content_hash)
        self.assertIsNotNone(new_version.published_at)
        self.assertFalse(old_version.is_active)
        self.assertTrue(profile.versions.filter(pk=old_version.pk).exists())
        self.assertIn('whatsapp.ai.prompt.published', '\n'.join(logs.output))
        self.assertIn(f'company_id={self.company.pk}', '\n'.join(logs.output))

    def test_ai_uses_newly_published_version_immediately(self):
        self.client.get(reverse('prompt_editor'))
        self.client.post(reverse('prompt_editor'), {
            'action': 'publish', 'prompt_content': '# IDENTIDADE NOVA E ATIVA',
            'response_delay_seconds': 3,
        })
        profile = AIPromptProfile.objects.get(empresa=self.company)

        class Configuration:
            empresa = self.company
            empresa_id = self.company.id
            assistant_name = greeting = tone = business_description = ''
            additional_information = human_handoff_rules = faq = policies = ''
            guidance = cancellation_rules = service_rules = allowed_information = ''

        instructions = build_instructions(build_company_context(Configuration()))
        self.assertIn('# IDENTIDADE NOVA E ATIVA', instructions)
        self.assertEqual(profile.versions.get(is_active=True).content, profile.generated_prompt)

    def test_editor_shows_active_prompt_and_unpublished_warning(self):
        profile = PromptCompilerService.ensure_default_profile(empresa=self.company, user=self.user)
        profile.draft_prompt = '# Alteração ainda não publicada'
        profile.save(update_fields=['draft_prompt'])
        page = self.client.get(reverse('prompt_editor'))
        self.assertEqual(page.context['prompt'], profile.generated_prompt)
        self.assertContains(page, 'PROMPT ATIVO')
        self.assertContains(page, 'Existem alterações não publicadas.')

    def test_publish_is_tenant_isolated(self):
        other_user = get_user_model().objects.create_user('prompt-other')
        other = EmpresaCliente.objects.create(usuario=other_user, nome='Empresa B')
        other_profile = PromptCompilerService.ensure_default_profile(empresa=other, user=other_user)
        other_active = other_profile.generated_prompt
        self.client.post(reverse('prompt_editor'), {
            'action': 'publish', 'prompt_content': '# Exclusivo da empresa A',
            'response_delay_seconds': 3,
        })
        other_profile.refresh_from_db()
        self.assertEqual(other_profile.generated_prompt, other_active)
        self.assertFalse(other_profile.versions.filter(content='# Exclusivo da empresa A').exists())

    def test_empty_prompt_cannot_be_published(self):
        profile = PromptCompilerService.ensure_default_profile(empresa=self.company, user=self.user)
        active = profile.generated_prompt
        version_count = AIPromptVersion.objects.filter(profile=profile).count()
        response = self.client.post(reverse('prompt_editor'), {
            'action': 'publish', 'prompt_content': '   ',
            'response_delay_seconds': 3,
        })
        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.generated_prompt, active)
        self.assertEqual(profile.versions.count(), version_count)
        self.assertContains(response, 'O prompt não pode ficar vazio.')

    def test_published_identity_is_dynamic_and_isolated_between_companies(self):
        user_b = get_user_model().objects.create_user('identity-owner-b')
        company_b = EmpresaCliente.objects.create(usuario=user_b, nome='Empresa B')

        def publish(company, user, agent_name):
            payload = self.payload()
            payload.update({'agent_name': agent_name, 'company_name': company.nome})
            return PromptCompilerService.compile_and_save(
                empresa=company,
                user=user,
                data=PromptGeneratorInput(
                    **payload, uses_calendar=True, objective='Atender',
                    service_style='', tone='', products='', services='',
                ),
            )

        version_a1 = publish(self.company, self.user, 'Paulo')
        version_b1 = publish(company_b, user_b, 'Ana')
        profile_a = AIPromptProfile.objects.get(empresa=self.company)
        profile_b = AIPromptProfile.objects.get(empresa=company_b)
        prompt_b_before = profile_b.generated_prompt

        def sent_instructions(company):
            provider = Mock()
            provider.generate.return_value = SimpleNamespace(text='ok', response_id='response')
            AIAgent(client=provider).respond(
                configuration=AIConfiguration.objects.get(empresa=company),
                user_input='Olá',
            )
            return provider.generate.call_args.kwargs['instructions']

        instructions_a = sent_instructions(self.company)
        instructions_b = sent_instructions(company_b)
        self.assertIn('**Paulo**', instructions_a)
        self.assertNotIn('**Ana**', instructions_a)
        self.assertIn('**Ana**', instructions_b)
        self.assertNotIn('**Paulo**', instructions_b)

        version_a2 = publish(self.company, self.user, 'Carlos')
        profile_a.refresh_from_db()
        profile_b.refresh_from_db()
        instructions_a_updated = sent_instructions(self.company)
        instructions_b_unchanged = sent_instructions(company_b)

        self.assertIn('**Carlos**', instructions_a_updated)
        self.assertNotIn('**Paulo**', instructions_a_updated)
        self.assertIn('**Ana**', instructions_b_unchanged)
        self.assertNotIn('**Carlos**', instructions_b_unchanged)
        self.assertEqual(profile_b.generated_prompt, prompt_b_before)
        self.assertEqual(profile_a.generated_prompt, profile_a.draft_prompt)
        self.assertEqual(profile_b.generated_prompt, profile_b.draft_prompt)
        self.assertTrue(AIPromptVersion.objects.filter(pk=version_a1.pk).exists())
        self.assertFalse(AIPromptVersion.objects.get(pk=version_a1.pk).is_active)
        self.assertTrue(AIPromptVersion.objects.get(pk=version_a2.pk).is_active)
        self.assertTrue(AIPromptVersion.objects.get(pk=version_b1.pk).is_active)
        self.assertEqual(profile_a.versions.count(), 2)
        self.assertEqual(profile_b.versions.count(), 1)

    def test_migrations_do_not_rewrite_published_identity(self):
        migrations_dir = Path(__file__).resolve().parent / 'migrations'
        source = '\n'.join(
            path.read_text(encoding='utf-8')
            for path in migrations_dir.glob('*.py')
        ).lower()
        self.assertNotIn('keyperry', source)
        self.assertNotIn('empresa_id=2', source)
        self.assertNotIn('empresa_id = 2', source)
