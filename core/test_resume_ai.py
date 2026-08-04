from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from core.application.whatsapp_service import WhatsAppSessionService
from core.domain.whatsapp import SessionSnapshot, SessionState
from core.infrastructure.evolution import EvolutionSendResult
from core.models import (
    AIConfiguration, AIPromptProfile, AsyncJob, Atendimento, Contato,
    EmpresaCliente, Mensagem, WhatsAppSession,
)
from core.services.evolution_webhook import EvolutionWebhookService
from core.services.queue import enqueue, process_job
from core.services.whatsapp.outbound import send_automatic_reply


@override_settings(AI_ENABLED=True, OPENAI_API_KEY='test-only')
class AutomaticAIActivationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('auto-owner', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Empresa Automática')
        other_user = get_user_model().objects.create_user('auto-other')
        self.other = EmpresaCliente.objects.create(usuario=other_user, nome='Outra Empresa')
        self.session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='auto-instance', state='WAITING_QR',
        )
        self.other_session = WhatsAppSession.objects.create(
            empresa=self.other, instance_name='other-instance', state='WAITING_QR',
        )
        self.profile = AIPromptProfile.objects.create(
            empresa=self.company, generated_prompt='# Prompt utilizável', response_delay_seconds=0,
        )

    def test_connected_snapshot_auto_enables_only_session_company(self):
        provider = Mock()
        provider.status.return_value = SessionSnapshot(state=SessionState.CONNECTED)

        WhatsAppSessionService(provider=provider).refresh(self.company)

        self.assertTrue(AIConfiguration.objects.get(empresa=self.company).enabled)
        self.assertFalse(AIConfiguration.objects.filter(empresa=self.other).exists())

    def test_connection_webhook_auto_enables_only_named_company(self):
        EvolutionWebhookService(provider=Mock()).process(
            self.session.pk,
            {'event': 'connection.update', 'data': {'state': 'open'}},
        )
        self.session.refresh_from_db()
        self.other_session.refresh_from_db()
        self.assertEqual(self.session.state, 'CONNECTED')
        self.assertEqual(self.other_session.state, 'WAITING_QR')
        self.assertTrue(AIConfiguration.objects.get(empresa=self.company).enabled)
        self.assertFalse(AIConfiguration.objects.filter(empresa=self.other).exists())

    def test_disconnect_preserves_prompt_and_configuration(self):
        AIConfiguration.objects.create(empresa=self.company, enabled=True, assistant_name='Lia')
        EvolutionWebhookService(provider=Mock()).process(
            self.session.pk,
            {'event': 'connection.update', 'data': {'state': 'disconnected'}},
        )
        self.assertTrue(AIPromptProfile.objects.filter(pk=self.profile.pk).exists())
        self.assertTrue(AIConfiguration.objects.filter(
            empresa=self.company, assistant_name='Lia',
        ).exists())

    @patch('core.services.queue._dispatch')
    def test_enqueue_only_persists_pending_job(self, dispatch):
        job = enqueue(
            'evolution.webhook', {'session_id': self.session.pk, 'payload': {}},
            idempotency_key='enqueue-only', queue='whatsapp',
        )
        self.assertEqual(job.status, AsyncJob.Status.PENDING)
        self.assertEqual(job.attempts, 0)
        dispatch.assert_not_called()

    @patch('core.services.queue._dispatch')
    def test_worker_processes_pending_job_only_once(self, dispatch):
        job = enqueue(
            'evolution.webhook', {'session_id': self.session.pk, 'payload': {}},
            idempotency_key='worker-once', queue='whatsapp',
        )
        first = process_job(job.pk)
        second = process_job(job.pk)
        self.assertEqual(first.status, AsyncJob.Status.COMPLETED)
        self.assertEqual(second.status, AsyncJob.Status.COMPLETED)
        self.assertEqual(second.attempts, 1)
        dispatch.assert_called_once()

    def test_missing_prompt_uses_precise_structured_reason(self):
        self.session.state = 'CONNECTED'
        self.session.save(update_fields=['state'])
        AIConfiguration.objects.create(empresa=self.company, enabled=True)
        self.profile.generated_prompt = ''
        self.profile.save(update_fields=['generated_prompt'])
        contact = Contato.objects.create(empresa=self.company, whatsapp_id='5511999990001')
        attendance = Atendimento.objects.create(
            empresa=self.company, contato=contact, nome_cliente='Cliente',
            telefone_cliente=contact.whatsapp_id, opcao_escolhida='WhatsApp', necessidade='Ajuda',
        )
        inbound = Mensagem.objects.create(
            empresa=self.company, atendimento=attendance, contato=contact,
            external_message_id='prompt-missing-in', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto='Olá',
        )
        with self.assertLogs('whatsapp.outbound', level='INFO') as logs:
            self.assertIsNone(send_automatic_reply(inbound))
        self.assertTrue(any('reason=prompt_missing' in line for line in logs.output))
        self.assertFalse(any('reason=ai_disabled' in line for line in logs.output))

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_message_gets_automatic_reply_after_connection(self, respond, send, _mark):
        EvolutionWebhookService(provider=Mock()).process(
            self.session.pk,
            {'event': 'connection.update', 'data': {'state': 'connected'}},
        )
        EvolutionWebhookService(provider=Mock()).process(
            self.session.pk,
            {
                'event': 'messages.upsert',
                'data': {
                    'key': {'id': 'auto-in-1', 'remoteJid': '5511988887777@s.whatsapp.net', 'fromMe': False},
                    'message': {'conversation': 'Quero atendimento'},
                },
            },
        )
        respond.return_value = SimpleNamespace(
            text='Resposta IAATENDE', provider_response_id='ai-1',
            input_tokens=1, output_tokens=1, tool_calls=0,
        )
        send.return_value = EvolutionSendResult('auto-out-1')
        reply_job = AsyncJob.objects.get(task_name='whatsapp.automatic_reply')
        process_job(reply_job.pk)
        self.assertTrue(Mensagem.objects.filter(
            empresa=self.company, external_message_id='auto-out-1', texto='Resposta IAATENDE',
        ).exists())
        self.assertFalse(Mensagem.objects.filter(empresa=self.other).exists())

    def test_manual_ai_resume_control_no_longer_exists(self):
        with self.assertRaises(NoReverseMatch):
            reverse('devolver_atendimento_ia', args=[1])
