from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.infrastructure.evolution import EvolutionSendResult
from core.models import (
    AIConfiguration, AIPromptProfile, AsyncJob, Atendimento, AuditEvent,
    Contato, EmpresaCliente, Mensagem, WhatsAppSession,
)
from core.services.queue import enqueue, process_job
from core.services.whatsapp.outbound import automatic_reply_ineligibility


@override_settings(AI_ENABLED=True, OPENAI_API_KEY='test-only', TASK_QUEUE_EAGER=False)
class ResumeAIAttendanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('resume-owner', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa Resume')
        AIConfiguration.objects.create(empresa=self.company, enabled=True)
        AIPromptProfile.objects.create(
            empresa=self.company, generated_prompt='# Prompt ativo', response_delay_seconds=0,
        )
        self.session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='resume-instance', state='CONNECTED',
        )
        self.contact = Contato.objects.create(
            empresa=self.company, whatsapp_id='5511888880001', nome='Cliente',
        )
        self.attendance = Atendimento.objects.create(
            empresa=self.company, contato=self.contact, nome_cliente='Cliente',
            telefone_cliente='5511888880001', opcao_escolhida='WhatsApp', necessidade='Ajuda',
            status=Atendimento.STATUS_EM_ANDAMENTO,
            current_step=Atendimento.Step.WAITING_HUMAN,
            automation_enabled=False,
            handoff_reason='Falha no atendimento automático.',
            conversation_state={'handoff_reason': 'Falha no atendimento automático.', 'dado_preservado': 'sim'},
        )
        self.old_message = Mensagem.objects.create(
            empresa=self.company, atendimento=self.attendance, contato=self.contact,
            external_message_id='resume-old-1', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto='Mensagem anterior',
        )
        self.client.login(username='resume-owner', password='safe-password')

    def resume_url(self, attendance=None):
        return reverse('devolver_atendimento_ia', args=[(attendance or self.attendance).pk])

    def test_action_requires_authenticated_user(self):
        self.client.logout()
        response = self.client.post(self.resume_url())
        self.assertEqual(response.status_code, 302)
        self.attendance.refresh_from_db()
        self.assertFalse(self.attendance.automation_enabled)

    def test_other_tenant_cannot_resume_attendance(self):
        other_user = get_user_model().objects.create_user('resume-other', password='safe-password')
        other_company = EmpresaCliente.objects.create(usuario=other_user, nome='Outra Empresa')
        AIConfiguration.objects.create(empresa=other_company, enabled=True)
        AIPromptProfile.objects.create(empresa=other_company, generated_prompt='# Outro prompt')
        WhatsAppSession.objects.create(
            empresa=other_company, instance_name='resume-other-instance', state='CONNECTED',
        )
        self.client.logout()
        self.client.login(username='resume-other', password='safe-password')

        response = self.client.post(self.resume_url())

        self.assertEqual(response.status_code, 404)
        self.attendance.refresh_from_db()
        self.assertFalse(self.attendance.automation_enabled)

    def test_waiting_human_is_safely_resumed_and_audited(self):
        response = self.client.post(self.resume_url())

        self.assertRedirects(response, reverse('atendimento_detalhe', args=[self.attendance.pk]))
        self.attendance.refresh_from_db()
        self.assertTrue(self.attendance.automation_enabled)
        self.assertEqual(self.attendance.current_step, Atendimento.Step.MENU)
        self.assertEqual(self.attendance.handoff_reason, '')
        self.assertNotIn('handoff_reason', self.attendance.conversation_state)
        self.assertEqual(self.attendance.conversation_state['dado_preservado'], 'sim')
        audit = AuditEvent.objects.get(
            empresa=self.company, action='attendance.returned_to_ai', target_id=str(self.attendance.pk),
        )
        self.assertEqual(audit.actor, self.user)
        self.assertEqual(audit.metadata['previous_step'], Atendimento.Step.WAITING_HUMAN)

    def test_click_does_not_reprocess_or_answer_old_messages(self):
        with patch('core.services.whatsapp.outbound.EvolutionProvider.send_text') as send_mock:
            self.client.post(self.resume_url())
        send_mock.assert_not_called()
        self.assertEqual(self.attendance.mensagens.count(), 1)
        self.assertFalse(AsyncJob.objects.exists())

    def test_non_human_attendance_cannot_be_reopened(self):
        self.attendance.current_step = Atendimento.Step.FINISHED
        self.attendance.status = Atendimento.STATUS_FINALIZADO
        self.attendance.save(update_fields=['current_step', 'status'])

        self.client.post(self.resume_url())

        self.attendance.refresh_from_db()
        self.assertFalse(self.attendance.automation_enabled)
        self.assertEqual(self.attendance.current_step, Atendimento.Step.FINISHED)
        self.assertFalse(AuditEvent.objects.filter(action='attendance.returned_to_ai').exists())

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_only_next_message_runs_worker_ai_and_evolution(self, respond_mock, send_mock, _read_mock):
        respond_mock.return_value = SimpleNamespace(
            text='Resposta após retomada', provider_response_id='resume-ai-1',
            input_tokens=1, output_tokens=2, tool_calls=0,
        )
        send_mock.return_value = EvolutionSendResult('resume-out-1')
        self.client.post(self.resume_url())
        self.attendance.refresh_from_db()
        next_message = Mensagem.objects.create(
            empresa=self.company, atendimento=self.attendance, contato=self.contact,
            external_message_id='resume-next-1', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto='Nova mensagem depois da retomada',
        )
        self.assertIsNone(automatic_reply_ineligibility(next_message))
        job = enqueue(
            'whatsapp.automatic_reply',
            {'message_id': next_message.pk, 'company_id': self.company.pk},
            idempotency_key='automatic-reply:resume-next-1', queue='whatsapp',
        )

        completed = process_job(job.pk)

        self.assertEqual(completed.status, AsyncJob.Status.COMPLETED)
        self.assertTrue(Mensagem.objects.filter(
            atendimento=self.attendance, external_message_id='resume-out-1',
            direcao=Mensagem.DIRECAO_SAIDA, texto='Resposta após retomada',
        ).exists())
        self.assertEqual(Mensagem.objects.filter(external_message_id='resume-old-1').count(), 1)

