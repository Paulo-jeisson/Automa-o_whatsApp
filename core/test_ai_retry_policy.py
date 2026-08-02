from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.infrastructure.evolution import EvolutionSendResult
from core.models import (
    AIConfiguration, AIPromptProfile, AsyncJob, Atendimento, Contato,
    EmpresaCliente, Mensagem, WhatsAppSession,
)
from core.services.ai.exceptions import AIPermanentError, AITemporaryError
from core.services.queue import enqueue, process_job


@override_settings(
    AI_ENABLED=True, OPENAI_API_KEY='test-only', TASK_QUEUE_EAGER=False,
    TASK_QUEUE_BACKOFF=1, TASK_QUEUE_MAX_BACKOFF=10,
)
class AIRetryPolicyTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('retry-owner')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Empresa Retry')
        AIConfiguration.objects.create(empresa=self.company, enabled=True)
        AIPromptProfile.objects.create(
            empresa=self.company, generated_prompt='# Prompt empresa retry',
            response_delay_seconds=0,
        )
        self.session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='retry-instance', state='CONNECTED',
        )
        self.contact = Contato.objects.create(
            empresa=self.company, whatsapp_id='5511777770001', nome='Cliente',
        )
        self.attendance = Atendimento.objects.create(
            empresa=self.company, contato=self.contact, nome_cliente='Cliente',
            telefone_cliente='5511777770001', opcao_escolhida='WhatsApp', necessidade='Ajuda',
        )
        self.inbound = Mensagem.objects.create(
            empresa=self.company, atendimento=self.attendance, contato=self.contact,
            external_message_id='retry-in-1', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto='Mensagem normal',
        )

    def make_job(self, max_attempts=3):
        return enqueue(
            'whatsapp.automatic_reply',
            {'message_id': self.inbound.pk, 'company_id': self.company.pk},
            idempotency_key=f'automatic-reply:{self.inbound.external_message_id}',
            queue='whatsapp', max_attempts=max_attempts,
        )

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_temporary_failure_retries_then_sends_successfully(self, respond_mock, send_mock, _read_mock):
        respond_mock.side_effect = [
            AITemporaryError('timeout'),
            SimpleNamespace(
                text='Resposta depois do retry', provider_response_id='retry-ai-1',
                input_tokens=1, output_tokens=1, tool_calls=0,
            ),
        ]
        send_mock.return_value = EvolutionSendResult('retry-out-1')
        job = self.make_job()

        first = process_job(job.pk)

        self.assertEqual(first.status, AsyncJob.Status.RETRY)
        self.assertEqual(first.attempts, 1)
        self.attendance.refresh_from_db()
        self.assertTrue(self.attendance.automation_enabled)
        self.assertEqual(self.attendance.current_step, Atendimento.Step.MENU)
        send_mock.assert_not_called()

        AsyncJob.objects.filter(pk=job.pk).update(available_at=timezone.now() - timedelta(seconds=1))
        second = process_job(job.pk)

        self.assertEqual(second.status, AsyncJob.Status.COMPLETED)
        self.assertEqual(second.attempts, 2)
        self.assertTrue(Mensagem.objects.filter(external_message_id='retry-out-1').exists())
        send_mock.assert_called_once()

    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_exhausted_temporary_failures_transfer_to_human(self, respond_mock):
        respond_mock.side_effect = AITemporaryError('timeout')
        job = self.make_job(max_attempts=2)

        first = process_job(job.pk)
        self.assertEqual(first.status, AsyncJob.Status.RETRY)
        self.attendance.refresh_from_db()
        self.assertTrue(self.attendance.automation_enabled)
        AsyncJob.objects.filter(pk=job.pk).update(available_at=timezone.now() - timedelta(seconds=1))

        second = process_job(job.pk)

        self.assertEqual(second.status, AsyncJob.Status.DEAD)
        self.attendance.refresh_from_db()
        self.assertFalse(self.attendance.automation_enabled)
        self.assertEqual(self.attendance.current_step, Atendimento.Step.WAITING_HUMAN)
        self.assertEqual(self.attendance.conversation_state['handoff_type'], 'AI_PERMANENT_FAILURE')

    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_permanent_failure_transfers_only_after_retry_limit(self, respond_mock):
        respond_mock.side_effect = AIPermanentError('invalid request')
        job = self.make_job(max_attempts=2)

        first = process_job(job.pk)
        self.assertEqual(first.status, AsyncJob.Status.RETRY)
        self.attendance.refresh_from_db()
        self.assertTrue(self.attendance.automation_enabled)
        self.assertEqual(self.attendance.current_step, Atendimento.Step.MENU)
        AsyncJob.objects.filter(pk=job.pk).update(available_at=timezone.now() - timedelta(seconds=1))

        result = process_job(job.pk)

        self.assertEqual(result.status, AsyncJob.Status.DEAD)
        self.assertEqual(result.attempts, 2)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.current_step, Atendimento.Step.WAITING_HUMAN)

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_legacy_technical_handoff_is_normalized_on_next_message(self, respond_mock, send_mock, _read_mock):
        self.attendance.current_step = Atendimento.Step.WAITING_HUMAN
        self.attendance.automation_enabled = False
        self.attendance.handoff_reason = 'Falha no atendimento automático.'
        self.attendance.save(update_fields=['current_step', 'automation_enabled', 'handoff_reason'])
        respond_mock.return_value = SimpleNamespace(
            text='Automação normalizada', provider_response_id='normalized-ai-1',
            input_tokens=1, output_tokens=1, tool_calls=0,
        )
        send_mock.return_value = EvolutionSendResult('normalized-out-1')

        result = process_job(self.make_job().pk)

        self.assertEqual(result.status, AsyncJob.Status.COMPLETED)
        self.attendance.refresh_from_db()
        self.assertTrue(self.attendance.automation_enabled)
        self.assertEqual(self.attendance.current_step, Atendimento.Step.MENU)
        self.assertEqual(self.attendance.handoff_reason, '')

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_exhausted_technical_handoff_retries_again_on_a_new_message(self, respond_mock, send_mock, _read_mock):
        self.attendance.current_step = Atendimento.Step.WAITING_HUMAN
        self.attendance.automation_enabled = False
        self.attendance.handoff_reason = 'Falhas da IA esgotaram a política de novas tentativas.'
        self.attendance.conversation_state = {
            'handoff_reason': self.attendance.handoff_reason,
            'handoff_type': 'AI_PERMANENT_FAILURE',
        }
        self.attendance.save(update_fields=[
            'current_step', 'automation_enabled', 'handoff_reason', 'conversation_state',
        ])
        respond_mock.return_value = SimpleNamespace(
            text='Resposta na nova mensagem', provider_response_id='new-cycle-ai-1',
            input_tokens=1, output_tokens=1, tool_calls=0,
        )
        send_mock.return_value = EvolutionSendResult('new-cycle-out-1')

        result = process_job(self.make_job().pk)

        self.assertEqual(result.status, AsyncJob.Status.COMPLETED)
        self.attendance.refresh_from_db()
        self.assertTrue(self.attendance.automation_enabled)
        self.assertEqual(self.attendance.current_step, Atendimento.Step.MENU)
        self.assertTrue(Mensagem.objects.filter(external_message_id='new-cycle-out-1').exists())

    def test_manual_handoff_is_never_automatically_normalized(self):
        self.attendance.current_step = Atendimento.Step.HUMAN
        self.attendance.automation_enabled = False
        self.attendance.assigned_to = self.company.usuario
        self.attendance.handoff_reason = 'Assumido manualmente.'
        self.attendance.conversation_state = {'handoff_type': 'HANDOFF_MANUAL_BY_AGENT'}
        self.attendance.save(update_fields=[
            'current_step', 'automation_enabled', 'assigned_to', 'handoff_reason', 'conversation_state',
        ])

        result = process_job(self.make_job().pk)

        self.assertEqual(result.status, AsyncJob.Status.COMPLETED)
        self.attendance.refresh_from_db()
        self.assertFalse(self.attendance.automation_enabled)
        self.assertEqual(self.attendance.current_step, Atendimento.Step.HUMAN)
