from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.infrastructure.evolution import EvolutionSendResult
from core.models import (
    AIConfiguration, AIPromptProfile, AsyncJob, Atendimento, Contato,
    EmpresaCliente, IgnoredPhoneNumber, Mensagem, WhatsAppSession,
)
from core.services.queue import enqueue, process_job
from core.services.whatsapp.outbound import automatic_reply_ineligibility


@override_settings(AI_ENABLED=True, OPENAI_API_KEY='test-only', TASK_QUEUE_EAGER=False)
class AutomaticReplyEligibilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('eligibility-owner')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa Elegível')
        self.configuration = AIConfiguration.objects.create(empresa=self.company, enabled=True)
        self.profile = AIPromptProfile.objects.create(
            empresa=self.company, generated_prompt='# Prompt ativo', response_delay_seconds=0,
        )
        self.session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='eligibility-instance', state='CONNECTED',
        )
        self.contact = Contato.objects.create(
            empresa=self.company, whatsapp_id='5511999990001', nome='Cliente',
        )
        self.attendance = Atendimento.objects.create(
            empresa=self.company, contato=self.contact, nome_cliente='Cliente',
            telefone_cliente='5511999990001', opcao_escolhida='WhatsApp', necessidade='Dúvida',
        )
        self.inbound = Mensagem.objects.create(
            empresa=self.company, atendimento=self.attendance, contato=self.contact,
            external_message_id='eligibility-in-1', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto='Olá, preciso de ajuda',
        )

    def assert_reason(self, expected):
        self.inbound.refresh_from_db()
        self.assertEqual(automatic_reply_ineligibility(self.inbound), expected)

    def test_common_customer_message_is_eligible(self):
        self.assertIsNone(automatic_reply_ineligibility(self.inbound))

    def test_message_from_me_reason(self):
        self.inbound.direcao = Mensagem.DIRECAO_SAIDA
        self.inbound.save(update_fields=['direcao'])
        self.assert_reason('message_from_me')

    def test_company_inactive_reason(self):
        self.company.ativa = False
        self.company.save(update_fields=['ativa'])
        self.assert_reason('company_inactive')

    def test_attendance_closed_reason(self):
        self.attendance.status = Atendimento.STATUS_FINALIZADO
        self.attendance.save(update_fields=['status'])
        self.assert_reason('attendance_closed')

    def test_human_mode_and_assigned_reasons(self):
        for step in (Atendimento.Step.WAITING_HUMAN, Atendimento.Step.HUMAN):
            with self.subTest(step=step):
                self.attendance.current_step = step
                self.attendance.save(update_fields=['current_step'])
                self.assert_reason('human_mode')
        self.attendance.current_step = Atendimento.Step.MENU
        self.attendance.assigned_to = self.user
        self.attendance.save(update_fields=['current_step', 'assigned_to'])
        self.assert_reason('human_mode')

    def test_automation_disabled_reason(self):
        self.attendance.automation_enabled = False
        self.attendance.save(update_fields=['automation_enabled'])
        self.assert_reason('automation_disabled')

    def test_blocked_number_reason(self):
        IgnoredPhoneNumber.objects.create(
            empresa=self.company, phone_number=self.contact.whatsapp_id, name='Bloqueado',
        )
        self.assert_reason('blocked_number')

    def test_duplicate_reason(self):
        AsyncJob.objects.create(
            task_name='whatsapp.automatic_reply', payload={}, queue='whatsapp',
            idempotency_key=f'automatic-reply:{self.inbound.external_message_id}',
            status=AsyncJob.Status.COMPLETED,
        )
        self.assert_reason('duplicate')

    def test_disconnected_session_reason(self):
        self.session.state = 'OFFLINE'
        self.session.save(update_fields=['state'])
        self.assert_reason('whatsapp_session_disconnected')

    def test_no_active_prompt_reason(self):
        self.profile.generated_prompt = ''
        self.profile.save(update_fields=['generated_prompt'])
        self.assert_reason('no_active_prompt')

    def test_ai_disabled_and_unavailable_reasons(self):
        self.configuration.enabled = False
        self.configuration.save(update_fields=['enabled'])
        self.assert_reason('ai_disabled')
        self.configuration.enabled = True
        self.configuration.save(update_fields=['enabled'])
        with override_settings(AI_ENABLED=False):
            self.assert_reason('ai_unavailable')

    def test_company_mismatch_reason(self):
        other_user = get_user_model().objects.create_user('eligibility-other')
        other = EmpresaCliente.objects.create(usuario=other_user, nome='Outra')
        Mensagem.objects.filter(pk=self.inbound.pk).update(empresa=other)
        self.assert_reason('company_mismatch')

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_worker_generates_sends_and_persists_ai_reply(self, respond_mock, send_mock, _read_mock):
        respond_mock.return_value = SimpleNamespace(
            text='Resposta normal da IA', provider_response_id='ai-response-1',
            input_tokens=2, output_tokens=3, tool_calls=0,
        )
        send_mock.return_value = EvolutionSendResult('eligibility-out-1')
        job = enqueue(
            'whatsapp.automatic_reply',
            {'message_id': self.inbound.pk, 'company_id': self.company.pk},
            idempotency_key=f'automatic-reply:{self.inbound.external_message_id}',
            queue='whatsapp',
        )

        completed = process_job(job.pk)

        self.assertEqual(completed.status, AsyncJob.Status.COMPLETED)
        outbound = Mensagem.objects.get(external_message_id='eligibility-out-1')
        self.assertEqual(outbound.direcao, Mensagem.DIRECAO_SAIDA)
        self.assertEqual(outbound.texto, 'Resposta normal da IA')
        send_mock.assert_called_once_with(
            self.session.instance_name, self.contact.whatsapp_id, 'Resposta normal da IA',
        )

