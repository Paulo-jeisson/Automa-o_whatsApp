from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.application.whatsapp_service import WhatsAppSessionService
from core.domain.whatsapp import SessionState, SessionSnapshot
from core.infrastructure.evolution import EvolutionRequestError, EvolutionSendResult
from core.models import (
    AIConfiguration, AIPromptProfile, AIResponseDraft, AsyncJob, Atendimento,
    BlockedInboundMessage, Contato, EmpresaCliente, Mensagem, Plan, Subscription,
    WhatsAppSession,
)
from core.services.evolution_webhook import EvolutionWebhookService
from core.services.queue import enqueue, process_job, process_next, recover_expired_jobs


def snapshot(state, qr=''):
    return SessionSnapshot(state=state, qr_code=qr)


class EvolutionConnectionReliabilityTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('connection-reliability')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Conexão confiável')
        self.session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='existing-instance', state='WAITING_QR',
            qr_code='old-qr',
        )
        self.provider = Mock()
        self.service = WhatsAppSessionService(provider=self.provider)

    def test_connected_instance_never_creates_or_generates_qr(self):
        self.provider.status.return_value = snapshot(SessionState.CONNECTED)
        result = self.service.connect(self.company)
        self.assertEqual(result.state, SessionState.CONNECTED)
        self.assertEqual(result.qr_code, '')
        self.provider.create.assert_not_called()
        self.provider.qr_code.assert_not_called()
        self.provider.reconnect.assert_not_called()

    def test_missing_instance_is_the_only_case_that_calls_create(self):
        self.provider.status.side_effect = EvolutionRequestError('missing', status_code=404)
        self.provider.create.return_value = snapshot(SessionState.WAITING_QR, 'new-qr')
        result = self.service.connect(self.company)
        self.assertEqual(result.qr_code, 'new-qr')
        self.provider.create.assert_called_once_with('existing-instance')

    def test_existing_conflict_rechecks_remote_state_before_error(self):
        self.provider.status.side_effect = [
            EvolutionRequestError('exists', status_code=409),
            snapshot(SessionState.CONNECTED),
        ]
        result = self.service.connect(self.company)
        self.assertEqual(result.state, SessionState.CONNECTED)
        self.assertEqual(self.provider.status.call_count, 2)
        self.provider.create.assert_not_called()


@override_settings(SUBSCRIPTION_ENFORCEMENT_ENABLED=True)
class SubscriptionWebhookReliabilityTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('blocked-reliability')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Empresa bloqueada')
        self.plan = Plan.objects.create(name='Plano', code='blocked-plan')
        Subscription.objects.create(
            empresa=self.company, plan=self.plan, status=Subscription.Status.BLOCKED,
            blocked_at=timezone.now(),
        )
        self.session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='blocked-instance', state='CONNECTED',
        )

    @patch('core.services.ai.agent.AIAgent.respond')
    def test_blocked_message_creates_minimal_record_without_ai_or_disconnect(self, respond):
        payload = {
            'event': 'messages.upsert',
            'data': {
                'key': {'id': 'blocked-message-1', 'remoteJid': '5511999999999@s.whatsapp.net'},
                'message': {'conversation': 'conteúdo não deve ser persistido'},
                'messageTimestamp': str(int(timezone.now().timestamp())),
            },
        }
        provider = Mock()
        EvolutionWebhookService(provider=provider).process(self.session.pk, payload)
        audit = BlockedInboundMessage.objects.get(external_message_id='blocked-message-1')
        self.assertEqual(audit.empresa, self.company)
        self.assertFalse(Mensagem.objects.exists())
        respond.assert_not_called()
        provider.logout.assert_not_called()
        provider.delete.assert_not_called()


@override_settings(
    AI_ENABLED=True, OPENAI_API_KEY='test-only', SUBSCRIPTION_ENFORCEMENT_ENABLED=False,
    TASK_QUEUE_BACKOFF=0,
)
class AIPipelineReliabilityTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('pipeline-reliability')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Pipeline')
        AIConfiguration.objects.create(empresa=self.company, enabled=True)
        AIPromptProfile.objects.create(
            empresa=self.company, generated_prompt='# Prompt', response_delay_seconds=0,
        )
        WhatsAppSession.objects.create(
            empresa=self.company, instance_name='pipeline-instance', state='CONNECTED',
        )
        contact = Contato.objects.create(empresa=self.company, whatsapp_id='5511888887777')
        attendance = Atendimento.objects.create(
            empresa=self.company, contato=contact, nome_cliente='Cliente',
            telefone_cliente=contact.whatsapp_id, opcao_escolhida='WhatsApp', necessidade='Teste',
        )
        self.message = Mensagem.objects.create(
            empresa=self.company, atendimento=attendance, contato=contact,
            external_message_id='pipeline-in-1', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto='Olá',
        )

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_send_retry_reuses_persisted_ai_response(self, respond, send, _read):
        respond.return_value = SimpleNamespace(
            text='Resposta persistida', provider_response_id='provider-1',
            input_tokens=1, output_tokens=1, tool_calls=0,
        )
        send.side_effect = [Exception('network'), EvolutionSendResult('pipeline-out-1')]
        job = enqueue(
            'whatsapp.automatic_reply',
            {'message_id': self.message.pk, 'company_id': self.company.pk},
            idempotency_key='automatic-reply:pipeline-in-1', queue='whatsapp', max_attempts=2,
            conversation_key=f'company:{self.company.pk}:attendance:{self.message.atendimento_id}',
        )
        # Use the provider exception type handled by the outbound adapter.
        from core.domain.exceptions import ProviderUnavailable
        send.side_effect = [ProviderUnavailable('network'), EvolutionSendResult('pipeline-out-1')]
        first = process_job(job.pk)
        self.assertEqual(first.status, AsyncJob.Status.RETRY)
        AsyncJob.objects.filter(pk=job.pk).update(available_at=timezone.now())
        second = process_job(job.pk)
        self.assertEqual(second.status, AsyncJob.Status.COMPLETED)
        self.assertEqual(respond.call_count, 1)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(
            AIResponseDraft.objects.get(inbound_message=self.message).status,
            AIResponseDraft.Status.SENT,
        )


class QueueLeaseReliabilityTests(TestCase):
    @override_settings(TASK_QUEUE_BACKOFF=0)
    def test_expired_processing_job_returns_to_retry(self):
        job = AsyncJob.objects.create(
            task_name='unknown', idempotency_key='expired', status=AsyncJob.Status.PROCESSING,
            attempts=1, max_attempts=3, locked_at=timezone.now() - timedelta(minutes=10),
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(recover_expired_jobs(), (1, 0))
        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.Status.RETRY)
        self.assertIsNone(job.lease_expires_at)

    def test_valid_lease_is_not_recovered(self):
        job = AsyncJob.objects.create(
            task_name='unknown', idempotency_key='valid', status=AsyncJob.Status.PROCESSING,
            attempts=1, lease_expires_at=timezone.now() + timedelta(minutes=1),
        )
        self.assertEqual(recover_expired_jobs(), (0, 0))
        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.Status.PROCESSING)

    def test_later_message_waits_for_same_conversation(self):
        first = AsyncJob.objects.create(
            task_name='unknown', idempotency_key='order-1', queue='ordered',
            conversation_key='company:1:attendance:1', status=AsyncJob.Status.PROCESSING,
            lease_expires_at=timezone.now() + timedelta(minutes=1),
        )
        AsyncJob.objects.create(
            task_name='unknown', idempotency_key='order-2', queue='ordered',
            conversation_key=first.conversation_key,
        )
        self.assertIsNone(process_next(queue='ordered'))

    def test_different_conversation_can_be_claimed(self):
        AsyncJob.objects.create(
            task_name='unknown', idempotency_key='parallel-1', queue='parallel',
            conversation_key='company:1:attendance:1', status=AsyncJob.Status.PROCESSING,
            lease_expires_at=timezone.now() + timedelta(minutes=1),
        )
        second = AsyncJob.objects.create(
            task_name='unknown', idempotency_key='parallel-2', queue='parallel',
            conversation_key='company:1:attendance:2', max_attempts=1,
        )
        result = process_next(queue='parallel')
        self.assertEqual(result.pk, second.pk)
        self.assertEqual(result.status, AsyncJob.Status.DEAD)
