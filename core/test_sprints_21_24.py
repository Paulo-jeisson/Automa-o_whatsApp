from datetime import time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AIConfiguration, AIUsageRecord, Agendamento, Atendimento, Contato,
    DataSubjectRequest, EmpresaCliente, Mensagem, MetaOnboardingVerification,
    Servico, WhatsAppIntegration,
)
from core.services.ai.conversation import AIConversationService
from core.services.analytics import company_metrics
from core.services.meta_readiness import meta_production_readiness
from core.services.privacy import PrivacyService


class Sprint2124Fixtures(TestCase):
    def setUp(self):
        self.user_a = get_user_model().objects.create_user('lgpd-a', password='pass')
        self.user_b = get_user_model().objects.create_user('lgpd-b', password='pass')
        self.company_a = EmpresaCliente.objects.create(usuario=self.user_a, nome='Empresa A')
        self.company_b = EmpresaCliente.objects.create(usuario=self.user_b, nome='Empresa B')
        self.contact_a = Contato.objects.create(empresa=self.company_a, whatsapp_id='551100000001', nome='Ana')
        self.contact_b = Contato.objects.create(empresa=self.company_b, whatsapp_id='551100000002', nome='Bia')
        self.attendance_a = Atendimento.objects.create(
            empresa=self.company_a, contato=self.contact_a, nome_cliente='Ana',
            telefone_cliente=self.contact_a.whatsapp_id, opcao_escolhida='', necessidade='Consulta',
        )
        self.message_a = Mensagem.objects.create(
            empresa=self.company_a, contato=self.contact_a, atendimento=self.attendance_a,
            external_message_id='privacy-a', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto='Dado pessoal sensível',
        )
        self.service_a = Servico.objects.create(empresa=self.company_a, nome='Consulta')
        self.appointment_a = Agendamento.objects.create(
            empresa=self.company_a, contato=self.contact_a, atendimento=self.attendance_a,
            servico=self.service_a, data=timezone.localdate() + timedelta(days=1),
            hora_inicio=time(9), hora_fim=time(10), observacao='Dado clínico',
        )


class PrivacyLifecycleTests(Sprint2124Fixtures):
    def test_export_and_anonymization_are_tenant_scoped_and_auditable(self):
        request = DataSubjectRequest.objects.create(
            empresa=self.company_a, contact=self.contact_a,
            whatsapp_id=self.contact_a.whatsapp_id,
            request_type=DataSubjectRequest.RequestType.DELETION,
            status=DataSubjectRequest.Status.APPROVED,
        )
        exported = PrivacyService.export_subject_data(request)
        self.assertIn('Dado pessoal sensível', str(exported))
        self.assertNotIn(self.contact_b.whatsapp_id, str(exported))
        PrivacyService.execute_deletion(request)
        self.contact_a.refresh_from_db()
        self.message_a.refresh_from_db()
        request.refresh_from_db()
        self.assertTrue(self.contact_a.whatsapp_id.startswith('anon-'))
        self.assertEqual(self.message_a.texto, '')
        self.assertEqual(request.status, DataSubjectRequest.Status.COMPLETED)
        self.assertIsNotNone(request.completed_at)

    def test_idor_cannot_export_another_company_subject(self):
        request = DataSubjectRequest.objects.create(
            empresa=self.company_b, contact=self.contact_b, whatsapp_id=self.contact_b.whatsapp_id,
            request_type=DataSubjectRequest.RequestType.ACCESS,
        )
        self.client.force_login(self.user_a)
        self.assertEqual(
            self.client.get(reverse('privacidade_exportar', args=[request.pk])).status_code,
            404,
        )


@override_settings(
    META_APP_ID='123', META_APP_SECRET='secret', META_VERIFY_TOKEN='verify',
    META_EMBEDDED_SIGNUP_CONFIG_ID='config', PUBLIC_BASE_URL='https://zap.example',
)
class MetaProductionTests(Sprint2124Fixtures):
    def test_readiness_requires_real_external_verification(self):
        integration = WhatsAppIntegration.objects.create(
            company=self.company_a, phone_number_id='111',
            whatsapp_business_account_id='222',
            onboarding_status=WhatsAppIntegration.OnboardingStatus.CONNECTED,
        )
        integration.set_access_token('tenant-token')
        integration.save()
        report = meta_production_readiness(self.company_a)
        self.assertFalse(report['ready'])
        MetaOnboardingVerification.objects.create(
            empresa=self.company_a, integration=integration,
            inbound_verified=True, outbound_verified=True, tenant_isolation_verified=True,
            templates_verified=True, permissions_verified=True,
        )
        report = meta_production_readiness(self.company_a)
        self.assertTrue(report['ready'])


class MetricsAndCostTests(Sprint2124Fixtures):
    @override_settings(
        AI_ENABLED=True, OPENAI_API_KEY='test', AI_MODEL='test-model',
        AI_INPUT_COST_PER_MILLION=Decimal('2'),
        AI_OUTPUT_COST_PER_MILLION=Decimal('8'),
    )
    def test_ai_usage_records_tokens_tools_latency_and_cost(self):
        AIConfiguration.objects.create(empresa=self.company_a, enabled=True)
        agent = Mock()
        agent.respond.return_value = SimpleNamespace(
            text='Resposta', provider_response_id='resp-1',
            input_tokens=1000, output_tokens=500, tool_calls=2,
        )
        service = AIConversationService(agent=agent)
        self.assertEqual(service.reply(inbound_message=self.message_a), 'Resposta')
        usage = AIUsageRecord.objects.get()
        self.assertEqual(usage.input_tokens, 1000)
        self.assertEqual(usage.output_tokens, 500)
        self.assertEqual(usage.tool_calls, 2)
        self.assertEqual(usage.estimated_cost_usd, Decimal('0.006000'))
        metrics = company_metrics(self.company_a)
        self.assertEqual(metrics['ai_calls'], 1)
        self.assertEqual(metrics['appointments'], 0)

    def test_metrics_dashboard_is_tenant_scoped(self):
        AIUsageRecord.objects.create(
            empresa=self.company_b, model='other', input_tokens=999, output_tokens=999,
        )
        self.client.force_login(self.user_a)
        response = self.client.get(reverse('metricas_ia'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '999')
