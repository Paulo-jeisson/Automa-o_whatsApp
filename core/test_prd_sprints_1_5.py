import json
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.application.dto import PromptGeneratorInput
from core.application.prompt_service import PromptGeneratorService
from core.application.whatsapp_service import WhatsAppSessionService
from core.domain.whatsapp import SessionSnapshot, SessionState
from core.models import AIPromptVersion, EmpresaCliente, WhatsAppSession


class PRDSprintsOneToFiveTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user('prd-owner', password='safe-password')
        self.other_user = user_model.objects.create_user('prd-other', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa PRD')
        self.other_company = EmpresaCliente.objects.create(usuario=self.other_user, nome='Outra Empresa')
        self.client.force_login(self.user)

    def test_whatsapp_module_is_tenant_isolated(self):
        own = WhatsAppSessionService(provider=Mock()).ensure(self.company)
        other = WhatsAppSessionService(provider=Mock()).ensure(self.other_company)
        own.qr_code = 'data:image/png;base64,b3du'
        own.save(update_fields=['qr_code'])
        other.qr_code = 'data:image/png;base64,b3RoZXI='
        other.save(update_fields=['qr_code'])
        response = self.client.get(reverse('conversations_crm'))
        self.assertContains(response, own.qr_code)
        self.assertNotContains(response, other.qr_code)

    def test_connect_persists_qr_and_state(self):
        provider = Mock()
        provider.create.return_value = SessionSnapshot(
            state=SessionState.WAITING_QR, qr_code='data:image/png;base64,abc', ping_ms=12,
        )
        session = WhatsAppSessionService(provider=provider).connect(self.company)
        self.assertEqual(session.state, 'WAITING_QR')
        self.assertEqual(session.qr_code, 'data:image/png;base64,abc')
        self.assertEqual(session.events.first().kind, 'QR_GENERATED')

    @override_settings(EVOLUTION_WEBHOOK_SECRET='secret')
    def test_evolution_webhook_updates_only_named_instance(self):
        session = WhatsAppSessionService(provider=Mock()).ensure(self.company)
        response = self.client.post(
            reverse('evolution_webhook'),
            data=json.dumps({'instance': session.instance_name, 'event': 'connection.update', 'data': {'state': 'open'}}),
            content_type='application/json', HTTP_X_ZAPFLUXO_SECRET='secret',
        )
        self.assertEqual(response.status_code, 202)
        session.refresh_from_db()
        self.assertEqual(session.state, 'CONNECTED')

    def test_ai_hub_and_generator_are_available(self):
        self.assertEqual(self.client.get(reverse('ai_dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('prompt_generator')).status_code, 200)

    def test_prompt_generation_saves_sequential_versions(self):
        data = PromptGeneratorInput(
            agent_name='Lia', company_name=self.company.nome, segment='Clínica',
            uses_calendar=True, profession='Recepcionista', personality='Empática',
            objective='Agendar consultas', service_style='Uma pergunta por vez',
            tone='Acolhedor', forbidden_words='garantia', limitations='Não diagnosticar',
            business_hours='Segunda a sexta', products='', services='Consultas', notes='',
        )
        first = PromptGeneratorService.save_version(empresa=self.company, user=self.user, data=data)
        second = PromptGeneratorService.save_version(empresa=self.company, user=self.user, data=data)
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertIn('# [IDENTIDADE]', second.content)
        self.assertIn('# [REGRAS DE CONDUTA]', second.content)
        self.assertEqual(AIPromptVersion.objects.filter(profile__empresa=self.company).count(), 2)
