"""Idempotent audio transcription for inbound Evolution messages."""

import io
import logging
import subprocess

from django.conf import settings
from django.db import transaction
from openai import APIConnectionError, APITimeoutError, OpenAI, OpenAIError

from core.domain.exceptions import ProviderUnavailable, SubscriptionAccessDenied
from core.models import Mensagem, WhatsAppSession
from core.services.ai.conversation import AIConversationService
from core.services.ai.exceptions import AIPermanentError, AITemporaryError
from core.services.entitlements import EntitlementService
from core.services.queue import enqueue


logger = logging.getLogger('ai.transcription')
FALLBACK_TEXT = 'Não consegui entender esse áudio. Você pode enviar novamente ou escrever sua mensagem?'
DIRECT_FORMATS = {
    'audio/mpeg': ('audio.mp3', 'audio/mpeg'),
    'audio/mp3': ('audio.mp3', 'audio/mpeg'),
    'audio/mp4': ('audio.m4a', 'audio/mp4'),
    'audio/x-m4a': ('audio.m4a', 'audio/mp4'),
    'audio/m4a': ('audio.m4a', 'audio/mp4'),
    'audio/wav': ('audio.wav', 'audio/wav'),
    'audio/x-wav': ('audio.wav', 'audio/wav'),
    'audio/webm': ('audio.webm', 'audio/webm'),
}
CONVERT_FORMATS = {'audio/ogg', 'audio/opus', 'audio/aac', 'audio/x-aac'}


def _enqueue_reply(message):
    return enqueue(
        'whatsapp.automatic_reply',
        {'message_id': message.pk, 'company_id': message.empresa_id},
        idempotency_key=f'automatic-reply:{message.external_message_id}',
        queue='whatsapp', max_attempts=5,
        conversation_key=f'company:{message.empresa_id}:attendance:{message.atendimento_id}',
    )


class OpenAITranscriptionClient:
    """Provider adapter without ORM, WhatsApp, or company awareness."""

    def __init__(self, *, sdk_client=None):
        self._sdk_client = sdk_client

    def transcribe(self, *, audio_bytes, filename, mime_type, idempotency_key):
        if not settings.AI_ENABLED or not settings.OPENAI_API_KEY:
            raise AIPermanentError('A transcrição de áudio não está configurada.')
        client = self._sdk_client or OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=settings.AI_TIMEOUT,
            max_retries=0,
        )
        stream = io.BytesIO(audio_bytes)
        stream.name = filename
        try:
            response = client.audio.transcriptions.create(
                model=settings.AI_AUDIO_TRANSCRIPTION_MODEL,
                file=(filename, stream, mime_type),
                extra_headers={'Idempotency-Key': idempotency_key},
            )
        except (APITimeoutError, APIConnectionError) as error:
            raise AITemporaryError('Serviço de transcrição temporariamente indisponível.') from error
        except OpenAIError as error:
            status_code = getattr(error, 'status_code', None)
            if status_code in {400, 401, 403, 404, 413, 415, 422}:
                raise AIPermanentError('O provedor rejeitou o arquivo de áudio.') from error
            raise AITemporaryError('Serviço de transcrição temporariamente indisponível.') from error
        text = str(getattr(response, 'text', '') or '').strip()
        if not text:
            raise AIPermanentError('A transcrição retornou vazia.')
        return text


def prepare_audio(audio_bytes, mime_type):
    """Return an API-supported in-memory file, converting only WhatsApp formats."""
    normalized = str(mime_type or '').split(';', 1)[0].strip().lower()
    if normalized in DIRECT_FORMATS:
        filename, output_mime = DIRECT_FORMATS[normalized]
        return audio_bytes, filename, output_mime
    if normalized not in CONVERT_FORMATS:
        raise AIPermanentError('Formato de áudio não suportado.')
    try:
        result = subprocess.run(
            [
                'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-i', 'pipe:0', '-vn', '-c:a', 'libopus', '-f', 'webm', 'pipe:1',
            ],
            input=audio_bytes, capture_output=True, check=False,
            timeout=settings.AI_AUDIO_CONVERSION_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise AIPermanentError('Não foi possível converter o áudio.') from error
    if result.returncode or not result.stdout:
        raise AIPermanentError('Arquivo de áudio inválido.')
    return result.stdout, 'audio.webm', 'audio/webm'


class AudioTranscriptionService:
    def __init__(self, *, provider, client=None):
        self.provider = provider
        self.client = client or OpenAITranscriptionClient()

    def process(self, *, message_id, company_id, session_id, media_payload, mime_type):
        message = Mensagem.objects.select_related('empresa', 'atendimento').get(
            pk=message_id, empresa_id=company_id, direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='audio',
        )
        session = WhatsAppSession.objects.get(pk=session_id, empresa_id=company_id)

        # Subscription and company AI access are checked before download or paid API use.
        try:
            EntitlementService.require_company_access(message.empresa)
        except SubscriptionAccessDenied:
            logger.info(
                'audio.transcription.skipped company_id=%s attendance_id=%s message_id=%s reason=subscription_blocked',
                company_id, message.atendimento_id, message.pk,
            )
            return None
        if AIConversationService.is_enabled(message.atendimento) is None:
            return None
        if message.transcription_status == Mensagem.TranscriptionStatus.COMPLETED:
            return _enqueue_reply(message)
        if not settings.AI_AUDIO_TRANSCRIPTION_ENABLED:
            self._finish(message, Mensagem.TranscriptionStatus.DISABLED, error='feature_disabled')
            return _enqueue_reply(message)

        with transaction.atomic():
            locked = Mensagem.objects.select_for_update().get(
                pk=message.pk, empresa_id=company_id, tipo='audio',
            )
            if locked.transcription_status == Mensagem.TranscriptionStatus.COMPLETED:
                return _enqueue_reply(locked)
            locked.transcription_status = Mensagem.TranscriptionStatus.PROCESSING
            locked.transcription_error = ''
            locked.save(update_fields=['transcription_status', 'transcription_error'])

        logger.info(
            'audio.transcription.started company_id=%s attendance_id=%s message_id=%s instance_id=%s',
            company_id, message.atendimento_id, message.pk, session.pk,
        )
        try:
            audio_bytes = self.provider.download_media(session.instance_name, media_payload)
            if not audio_bytes:
                raise AIPermanentError('A mídia de áudio está vazia.')
            if len(audio_bytes) > settings.AI_AUDIO_MAX_BYTES:
                raise AIPermanentError('O áudio excede o limite permitido.')
            prepared, filename, output_mime = prepare_audio(audio_bytes, mime_type)
            if len(prepared) > settings.AI_AUDIO_MAX_BYTES:
                raise AIPermanentError('O áudio convertido excede o limite permitido.')
            transcript = self.client.transcribe(
                audio_bytes=prepared, filename=filename, mime_type=output_mime,
                idempotency_key=f'audio-transcription:{message.external_message_id}',
            )
            transcript = str(transcript or '').strip()
            if not transcript:
                raise AIPermanentError('A transcrição retornou vazia.')
        except AITemporaryError:
            self._finish(message, Mensagem.TranscriptionStatus.PENDING, error='temporary_provider_error')
            logger.warning(
                'audio.transcription.failed company_id=%s attendance_id=%s message_id=%s retryable=true',
                company_id, message.atendimento_id, message.pk,
            )
            raise
        except ProviderUnavailable as error:
            self._finish(message, Mensagem.TranscriptionStatus.PENDING, error='media_download_failed')
            logger.warning(
                'audio.transcription.failed company_id=%s attendance_id=%s message_id=%s retryable=true',
                company_id, message.atendimento_id, message.pk,
            )
            raise AITemporaryError('Não foi possível baixar a mídia temporariamente.') from error
        except AIPermanentError as error:
            self._finish(message, Mensagem.TranscriptionStatus.FAILED, error=type(error).__name__)
            logger.warning(
                'audio.transcription.failed company_id=%s attendance_id=%s message_id=%s retryable=false',
                company_id, message.atendimento_id, message.pk,
            )
            return _enqueue_reply(message)

        with transaction.atomic():
            locked = Mensagem.objects.select_for_update().get(pk=message.pk, empresa_id=company_id)
            locked.transcription_status = Mensagem.TranscriptionStatus.COMPLETED
            locked.transcription_text = transcript
            locked.transcription_model = settings.AI_AUDIO_TRANSCRIPTION_MODEL
            locked.transcription_error = ''
            locked.save(update_fields=[
                'transcription_status', 'transcription_text',
                'transcription_model', 'transcription_error',
            ])
        logger.info(
            'audio.transcription.completed company_id=%s attendance_id=%s message_id=%s',
            company_id, message.atendimento_id, message.pk,
        )
        return _enqueue_reply(locked)

    @staticmethod
    def _finish(message, status, *, error):
        Mensagem.objects.filter(pk=message.pk, empresa_id=message.empresa_id).update(
            transcription_status=status, transcription_error=error,
        )


def finalize_exhausted_transcription(*, message_id, company_id, error_type):
    message = Mensagem.objects.get(
        pk=message_id, empresa_id=company_id, direcao=Mensagem.DIRECAO_ENTRADA, tipo='audio',
    )
    Mensagem.objects.filter(pk=message.pk, empresa_id=company_id).update(
        transcription_status=Mensagem.TranscriptionStatus.FAILED,
        transcription_error=str(error_type or 'retry_exhausted')[:80],
    )
    logger.warning(
        'audio.transcription.failed company_id=%s attendance_id=%s message_id=%s retryable=false exhausted=true',
        company_id, message.atendimento_id, message.pk,
    )
    return _enqueue_reply(message)
