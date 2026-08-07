from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.infrastructure.evolution import EvolutionSendResult
from core.models import (
    AIConfiguration, AIPromptProfile, AsyncJob, Atendimento, Contato,
    EmpresaCliente, Mensagem, WhatsAppSession,
)
from core.services.ai.exceptions import AIPermanentError, AITemporaryError
from core.services.ai.transcription import (
    AudioTranscriptionService, FALLBACK_TEXT, OpenAITranscriptionClient, prepare_audio,
)
from core.services.queue import enqueue, process_job


@override_settings(
    AI_ENABLED=True, OPENAI_API_KEY='test-only',
    AI_AUDIO_TRANSCRIPTION_ENABLED=True, AI_AUDIO_TRANSCRIPTION_MODEL='gpt-transcribe',
    AI_AUDIO_MAX_BYTES=1024, TASK_QUEUE_EAGER=False,
)
class AudioTranscriptionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('audio-owner')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa Áudio')
        AIConfiguration.objects.create(empresa=self.company, enabled=True)
        AIPromptProfile.objects.create(
            empresa=self.company, generated_prompt='# Prompt ativo', response_delay_seconds=0,
        )
        self.session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='audio-instance', state='CONNECTED',
        )
        self.contact = Contato.objects.create(
            empresa=self.company, whatsapp_id='5511999991234', nome='Cliente Áudio',
        )
        self.attendance = Atendimento.objects.create(
            empresa=self.company, contato=self.contact, nome_cliente='Cliente Áudio',
            telefone_cliente='5511999991234', opcao_escolhida='WhatsApp', necessidade='Áudio',
        )
        self.message = Mensagem.objects.create(
            empresa=self.company, atendimento=self.attendance, contato=self.contact,
            external_message_id='audio-in-1', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='audio', transcription_status=Mensagem.TranscriptionStatus.PENDING,
        )
        self.provider = Mock()
        self.provider.download_media.return_value = b'valid-mp3'
        self.transcription_client = Mock()
        self.transcription_client.transcribe.return_value = 'Quero marcar uma consulta amanhã.'
        self.payload = {'message': {'audioMessage': {'mimetype': 'audio/mpeg'}}}

    def service(self):
        return AudioTranscriptionService(provider=self.provider, client=self.transcription_client)

    def process(self, **overrides):
        params = {
            'message_id': self.message.pk, 'company_id': self.company.pk,
            'session_id': self.session.pk, 'media_payload': self.payload,
            'mime_type': 'audio/mpeg',
        }
        params.update(overrides)
        return self.service().process(**params)

    def test_valid_audio_is_transcribed_and_normal_reply_job_is_created(self):
        self.process()
        self.message.refresh_from_db()
        self.assertEqual(self.message.tipo, 'audio')
        self.assertEqual(self.message.transcription_status, Mensagem.TranscriptionStatus.COMPLETED)
        self.assertEqual(self.message.ai_text, 'Quero marcar uma consulta amanhã.')
        self.assertEqual(self.message.transcription_model, 'gpt-transcribe')
        self.provider.download_media.assert_called_once_with(self.session.instance_name, self.payload)
        self.transcription_client.transcribe.assert_called_once()
        self.assertTrue(AsyncJob.objects.filter(
            task_name='whatsapp.automatic_reply', payload__company_id=self.company.pk,
            payload__message_id=self.message.pk,
        ).exists())

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_transcript_uses_normal_ai_pipeline_and_sends_response(self, respond, send, _mark):
        respond.return_value = SimpleNamespace(
            text='Claro, vou verificar os horários.', provider_response_id='response-audio-1',
            input_tokens=2, output_tokens=4, tool_calls=0,
        )
        send.return_value = EvolutionSendResult('audio-out-1')
        reply_job = self.process()

        process_job(reply_job.pk)

        self.assertEqual(
            respond.call_args.kwargs['user_input'], 'Quero marcar uma consulta amanhã.',
        )
        send.assert_called_once_with(
            self.session.instance_name, self.contact.whatsapp_id,
            'Claro, vou verificar os horários.',
        )
        self.assertTrue(Mensagem.objects.filter(
            empresa=self.company, external_message_id='audio-out-1', direcao=Mensagem.DIRECAO_SAIDA,
        ).exists())

    def test_existing_transcription_is_not_charged_again(self):
        self.process()
        self.process()
        self.transcription_client.transcribe.assert_called_once()
        self.assertEqual(AsyncJob.objects.filter(
            idempotency_key=f'automatic-reply:{self.message.external_message_id}',
        ).count(), 1)

    def test_wrong_company_cannot_access_message_or_media(self):
        other_user = get_user_model().objects.create_user('audio-other')
        other = EmpresaCliente.objects.create(usuario=other_user, nome='Outra Empresa')
        with self.assertRaises(Mensagem.DoesNotExist):
            self.process(company_id=other.pk)
        self.provider.download_media.assert_not_called()
        self.transcription_client.transcribe.assert_not_called()

    @override_settings(SUBSCRIPTION_ENFORCEMENT_ENABLED=True)
    def test_blocked_subscription_never_downloads_or_calls_openai(self):
        self.assertIsNone(self.process())
        self.provider.download_media.assert_not_called()
        self.transcription_client.transcribe.assert_not_called()

    @override_settings(AI_AUDIO_TRANSCRIPTION_ENABLED=False)
    def test_disabled_feature_uses_safe_fallback_without_openai(self):
        reply_job = self.process()
        self.message.refresh_from_db()
        self.assertEqual(self.message.transcription_status, Mensagem.TranscriptionStatus.DISABLED)
        self.provider.download_media.assert_not_called()
        self.transcription_client.transcribe.assert_not_called()
        self.assertEqual(reply_job.task_name, 'whatsapp.automatic_reply')

    def test_empty_and_oversized_media_use_permanent_fallback(self):
        for index, audio in enumerate((b'', b'x' * 1025), start=1):
            with self.subTest(size=len(audio)):
                self.message.transcription_status = Mensagem.TranscriptionStatus.PENDING
                self.message.transcription_error = ''
                self.message.external_message_id = f'audio-size-{index}'
                self.message.save(update_fields=[
                    'transcription_status', 'transcription_error', 'external_message_id',
                ])
                self.provider.reset_mock()
                self.provider.download_media.return_value = audio
                job = self.process()
                self.message.refresh_from_db()
                self.assertEqual(self.message.transcription_status, Mensagem.TranscriptionStatus.FAILED)
                self.assertEqual(job.task_name, 'whatsapp.automatic_reply')
        self.transcription_client.transcribe.assert_not_called()

    def test_empty_transcript_uses_fallback_without_calling_agent(self):
        self.transcription_client.transcribe.return_value = '   '
        job = self.process()
        self.message.refresh_from_db()
        self.assertEqual(self.message.transcription_status, Mensagem.TranscriptionStatus.FAILED)
        self.assertEqual(job.task_name, 'whatsapp.automatic_reply')

    def test_temporary_failure_is_retried_and_does_not_enqueue_reply(self):
        self.transcription_client.transcribe.side_effect = AITemporaryError('temporário')
        with self.assertRaises(AITemporaryError):
            self.process()
        self.message.refresh_from_db()
        self.assertEqual(self.message.transcription_status, Mensagem.TranscriptionStatus.PENDING)
        self.assertFalse(AsyncJob.objects.filter(task_name='whatsapp.automatic_reply').exists())

    def test_permanent_failure_enqueues_fallback(self):
        self.transcription_client.transcribe.side_effect = AIPermanentError('arquivo inválido')
        job = self.process()
        self.message.refresh_from_db()
        self.assertEqual(self.message.transcription_status, Mensagem.TranscriptionStatus.FAILED)
        self.assertEqual(job.task_name, 'whatsapp.automatic_reply')

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    def test_definitive_failure_sends_friendly_fallback(self, send, _mark):
        self.transcription_client.transcribe.side_effect = AIPermanentError('arquivo inválido')
        send.return_value = EvolutionSendResult('audio-fallback-out')
        job = self.process()
        process_job(job.pk)
        send.assert_called_once_with(
            self.session.instance_name, self.contact.whatsapp_id, FALLBACK_TEXT,
        )

    @patch('core.services.ai.transcription.OpenAITranscriptionClient.transcribe')
    @patch('core.infrastructure.evolution.EvolutionProvider.download_media')
    def test_queue_retry_and_two_workers_do_not_duplicate_transcription(self, download, transcribe):
        download.return_value = b'valid-mp3'
        transcribe.side_effect = [AITemporaryError('temporário'), 'Texto após retry']
        job = enqueue(
            'whatsapp.audio_transcription',
            {
                'message_id': self.message.pk, 'company_id': self.company.pk,
                'session_id': self.session.pk, 'media_payload': self.payload,
                'mime_type': 'audio/mpeg',
            },
            idempotency_key='audio-transcription:audio-in-1', queue='whatsapp',
            conversation_key=f'company:{self.company.pk}:attendance:{self.attendance.pk}',
        )
        first = process_job(job.pk)
        self.assertEqual(first.status, AsyncJob.Status.RETRY)
        AsyncJob.objects.filter(pk=job.pk).update(available_at='2000-01-01T00:00:00Z')
        second = process_job(job.pk)
        self.assertEqual(second.status, AsyncJob.Status.COMPLETED)
        process_job(job.pk)  # A second worker cannot reclaim a completed job.
        self.assertEqual(transcribe.call_count, 2)
        self.assertEqual(AsyncJob.objects.filter(task_name='whatsapp.automatic_reply').count(), 1)

    @patch('core.infrastructure.evolution.EvolutionProvider.download_media')
    @patch('core.services.ai.transcription.OpenAITranscriptionClient.transcribe')
    def test_exhausted_retry_marks_failure_and_enqueues_fallback(self, transcribe, download):
        download.return_value = b'valid-mp3'
        transcribe.side_effect = AITemporaryError('temporário')
        job = enqueue(
            'whatsapp.audio_transcription',
            {
                'message_id': self.message.pk, 'company_id': self.company.pk,
                'session_id': self.session.pk, 'media_payload': self.payload,
                'mime_type': 'audio/mpeg',
            },
            idempotency_key='audio-transcription:exhausted', queue='whatsapp', max_attempts=1,
            conversation_key=f'company:{self.company.pk}:attendance:{self.attendance.pk}',
        )
        result = process_job(job.pk)
        self.message.refresh_from_db()
        self.assertEqual(result.status, AsyncJob.Status.DEAD)
        self.assertEqual(self.message.transcription_status, Mensagem.TranscriptionStatus.FAILED)
        self.assertTrue(AsyncJob.objects.filter(task_name='whatsapp.automatic_reply').exists())

    def test_operational_logs_do_not_include_transcript_or_api_key(self):
        secret_transcript = 'conteúdo sigiloso do cliente'
        self.transcription_client.transcribe.return_value = secret_transcript
        with self.assertLogs('ai.transcription', level='INFO') as captured:
            self.process()
        output = '\n'.join(captured.output)
        self.assertNotIn(secret_transcript, output)
        self.assertNotIn('test-only', output)

    @patch('core.services.ai.transcription.subprocess.run')
    def test_ogg_opus_and_aac_are_converted_in_memory_to_webm(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout=b'webm-audio')
        for mime in ('audio/ogg; codecs=opus', 'audio/opus', 'audio/aac'):
            with self.subTest(mime=mime):
                content, filename, output_mime = prepare_audio(b'input', mime)
                self.assertEqual((content, filename, output_mime), (
                    b'webm-audio', 'audio.webm', 'audio/webm',
                ))
        self.assertEqual(run.call_count, 3)

    def test_direct_formats_do_not_invoke_conversion(self):
        for mime in ('audio/mpeg', 'audio/mp4', 'audio/x-m4a', 'audio/wav', 'audio/webm'):
            with self.subTest(mime=mime), patch(
                'core.services.ai.transcription.subprocess.run',
            ) as run:
                content, _filename, _output_mime = prepare_audio(b'direct', mime)
                self.assertEqual(content, b'direct')
                run.assert_not_called()

    def test_openai_adapter_uses_stable_idempotency_key(self):
        sdk = Mock()
        sdk.audio.transcriptions.create.return_value = SimpleNamespace(text='Texto final')
        result = OpenAITranscriptionClient(sdk_client=sdk).transcribe(
            audio_bytes=b'audio', filename='audio.mp3', mime_type='audio/mpeg',
            idempotency_key='audio-transcription:stable-id',
        )
        self.assertEqual(result, 'Texto final')
        self.assertEqual(
            sdk.audio.transcriptions.create.call_args.kwargs['extra_headers'],
            {'Idempotency-Key': 'audio-transcription:stable-id'},
        )

    def test_prompt_editor_reports_real_feature_flag_state(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('prompt_editor'))
        self.assertContains(response, 'Transcrição de áudio ativa')
        with override_settings(AI_AUDIO_TRANSCRIPTION_ENABLED=False):
            response = self.client.get(reverse('prompt_editor'))
        self.assertContains(response, 'Transcrição de áudio desativada')
