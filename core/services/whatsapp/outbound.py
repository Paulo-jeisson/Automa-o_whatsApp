import logging
import time
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.domain.exceptions import ProviderUnavailable
from core.infrastructure.evolution import EvolutionProvider
from core.models import (
    AIConfiguration, AIPromptProfile, AsyncJob, FluxoAtendimento, Mensagem,
    WhatsAppSession,
)
from core.services.ai.conversation import AIConversationService
from core.services.entitlements import EntitlementService
from core.services.pass_numbers import is_pass_number

from .exceptions import WhatsAppAPIError, WhatsAppProviderError
from .flow_engine import FlowEngine


logger = logging.getLogger('whatsapp.outbound')

LEGACY_TECHNICAL_HANDOFF_REASONS = {
    'Falha no atendimento automático.',
    'Falha inesperada no atendimento automático.',
}


def normalize_legacy_technical_handoff(inbound_message):
    """Safely restore only handoffs created by the old immediate-failure policy."""
    atendimento = inbound_message.atendimento
    handoff_type = (atendimento.conversation_state or {}).get('handoff_type')
    technical_handoff = (
        atendimento.handoff_reason in LEGACY_TECHNICAL_HANDOFF_REASONS
        or handoff_type in {'AI_TEMPORARY_FAILURE', 'AI_PERMANENT_FAILURE'}
    )
    if (
        atendimento.current_step != atendimento.Step.WAITING_HUMAN
        or atendimento.assigned_to_id
        or not technical_handoff
    ):
        return atendimento
    with transaction.atomic():
        locked = type(atendimento).objects.select_for_update().get(
            pk=atendimento.pk, empresa_id=inbound_message.empresa_id,
        )
        locked_handoff_type = (locked.conversation_state or {}).get('handoff_type')
        locked_technical_handoff = (
            locked.handoff_reason in LEGACY_TECHNICAL_HANDOFF_REASONS
            or locked_handoff_type in {'AI_TEMPORARY_FAILURE', 'AI_PERMANENT_FAILURE'}
        )
        if (
            locked.current_step != locked.Step.WAITING_HUMAN
            or locked.assigned_to_id
            or not locked_technical_handoff
        ):
            return locked
        state = dict(locked.conversation_state or {})
        state.pop('handoff_reason', None)
        state.pop('handoff_type', None)
        state.pop('last_ai_failure_type', None)
        locked.current_step = locked.Step.MENU
        locked.automation_enabled = True
        locked.handoff_reason = ''
        locked.conversation_state = state
        locked.save(update_fields=[
            'current_step', 'automation_enabled', 'handoff_reason', 'conversation_state',
        ])
    logger.info(
        'whatsapp.auto_reply.legacy_technical_handoff_normalized company_id=%s attendance_id=%s',
        inbound_message.empresa_id, atendimento.pk,
    )
    return locked


def automatic_reply_ineligibility(inbound_message):
    """Return the precise safety rule preventing an automatic reply."""
    atendimento = inbound_message.atendimento
    empresa = inbound_message.empresa

    if inbound_message.direcao != Mensagem.DIRECAO_ENTRADA:
        return 'message_from_me'
    if atendimento.empresa_id != inbound_message.empresa_id:
        return 'company_mismatch'
    if not empresa.ativa:
        return 'company_inactive'
    sender = atendimento.contato.whatsapp_id if atendimento.contato else ''
    if is_pass_number(inbound_message.empresa_id, sender):
        return 'pass_number'
    if automatic_reply_loop_reached(atendimento):
        return 'loop_protection'
    if atendimento.status == atendimento.STATUS_FINALIZADO or atendimento.current_step == atendimento.Step.FINISHED:
        return 'attendance_closed'
    if atendimento.current_step in {atendimento.Step.WAITING_HUMAN, atendimento.Step.HUMAN} or atendimento.assigned_to_id:
        return 'human_mode'
    if not atendimento.automation_enabled:
        return 'automation_disabled'

    if AsyncJob.objects.filter(
        idempotency_key=f'automatic-reply:{inbound_message.external_message_id}',
        status=AsyncJob.Status.COMPLETED,
    ).exists():
        return 'duplicate'
    if not WhatsAppSession.objects.filter(
        empresa_id=inbound_message.empresa_id, state='CONNECTED',
    ).exists():
        return 'whatsapp_session_disconnected'

    try:
        profile = AIPromptProfile.objects.get(empresa_id=inbound_message.empresa_id)
    except AIPromptProfile.DoesNotExist:
        return 'prompt_missing'
    if not profile.generated_prompt.strip():
        return 'prompt_missing'
    try:
        AIConfiguration.objects.get(empresa_id=inbound_message.empresa_id)
    except AIConfiguration.DoesNotExist:
        return 'ai_unavailable'
    if AIConversationService.is_enabled(atendimento) is None:
        return 'ai_unavailable'
    return None


def automatic_reply_loop_reached(atendimento):
    since = timezone.now() - timedelta(minutes=2)
    reached = Mensagem.objects.filter(
        empresa_id=atendimento.empresa_id,
        atendimento_id=atendimento.pk,
        direcao=Mensagem.DIRECAO_SAIDA,
        sent_by__isnull=True,
        criado_em__gte=since,
    ).count() >= 5
    if reached and atendimento.automation_enabled:
        type(atendimento).objects.filter(
            pk=atendimento.pk, empresa_id=atendimento.empresa_id,
        ).update(automation_enabled=False)
        atendimento.automation_enabled = False
    return reached


def prequeue_auto_reply_reason(*, company_id, phone_number, atendimento):
    if is_pass_number(company_id, phone_number):
        return 'pass_number'
    if automatic_reply_loop_reached(atendimento):
        return 'loop_protection'
    return None


def log_reply_skip(*, company_id, attendance_id=None, message_id='', reason, stage):
    logger.info(
        'whatsapp.reply.skip company_id=%s attendance_id=%s message_id=%s stage=%s',
        company_id, attendance_id, message_id, stage,
    )
    logger.info(
        'whatsapp.reply.reason company_id=%s attendance_id=%s message_id=%s reason=%s stage=%s',
        company_id, attendance_id, message_id, reason, stage,
    )
    logger.info(
        'whatsapp.reply.end company_id=%s attendance_id=%s message_id=%s outcome=skipped reason=%s stage=%s',
        company_id, attendance_id, message_id, reason, stage,
    )


def send_text_for_attendance(atendimento, text):
    EntitlementService.require_company_access(atendimento.empresa)
    contato = atendimento.contato
    if contato is None or contato.empresa_id != atendimento.empresa_id:
        raise WhatsAppProviderError('O atendimento não possui um contato válido.')

    try:
        session = WhatsAppSession.objects.get(
            empresa_id=atendimento.empresa_id,
            empresa__ativa=True,
            state='CONNECTED',
        )
    except WhatsAppSession.DoesNotExist:
        raise WhatsAppProviderError('A empresa não possui sessão Evolution conectada.')

    logger.info(
        'whatsapp.outbound.requested company_id=%s attendance_id=%s',
        atendimento.empresa_id,
        atendimento.pk,
    )
    try:
        result = EvolutionProvider().send_text(session.instance_name, contato.whatsapp_id, text)
    except (ProviderUnavailable, WhatsAppAPIError) as error:
        logger.warning(
            'whatsapp.outbound.failed company_id=%s attendance_id=%s status_code=%s error_code=%s',
            atendimento.empresa_id,
            atendimento.pk,
            getattr(error, 'status_code', None),
            getattr(error, 'error_code', ''),
        )
        if isinstance(error, WhatsAppAPIError):
            raise
        raise WhatsAppProviderError('Falha ao enviar mensagem pela Evolution API.') from error

    try:
        with transaction.atomic():
            mensagem, _created = Mensagem.objects.get_or_create(
                external_message_id=result.message_id,
                defaults={
                    'empresa': atendimento.empresa,
                    'atendimento': atendimento,
                    'contato': contato,
                    'direcao': Mensagem.DIRECAO_SAIDA,
                    'tipo': 'text',
                    'texto': text,
                    'status': Mensagem.STATUS_ACEITA,
                },
            )
            atendimento.last_message_at = mensagem.criado_em
            atendimento.save(update_fields=['last_message_at'])
    except IntegrityError:
        mensagem = Mensagem.objects.get(external_message_id=result.message_id)

    logger.info(
        'whatsapp.outbound.sent company_id=%s attendance_id=%s message_id=%s',
        atendimento.empresa_id,
        atendimento.pk,
        result.message_id,
    )
    return mensagem


def send_automatic_reply(inbound_message):
    logger.info(
        'whatsapp.reply.begin company_id=%s attendance_id=%s message_id=%s type=%s stage=worker',
        inbound_message.empresa_id, inbound_message.atendimento_id,
        inbound_message.external_message_id, inbound_message.tipo,
    )
    from core.domain.exceptions import SubscriptionAccessDenied
    try:
        EntitlementService.require_company_access(inbound_message.empresa)
    except SubscriptionAccessDenied:
        log_reply_skip(
            company_id=inbound_message.empresa_id,
            attendance_id=inbound_message.atendimento_id,
            message_id=inbound_message.external_message_id,
            reason='subscription_blocked', stage='worker_eligibility',
        )
        return None
    atendimento = normalize_legacy_technical_handoff(inbound_message)
    inbound_message.atendimento = atendimento
    reason = automatic_reply_ineligibility(inbound_message)
    if reason:
        logger.info(
            'whatsapp.auto_reply.skipped company_id=%s attendance_id=%s message_id=%s reason=%s',
            inbound_message.empresa_id,
            atendimento.pk,
            inbound_message.external_message_id,
            reason,
        )
        log_reply_skip(
            company_id=inbound_message.empresa_id,
            attendance_id=atendimento.pk,
            message_id=inbound_message.external_message_id,
            reason=reason,
            stage='worker_eligibility',
        )
        return None

    supported_text = inbound_message.tipo == 'text' and bool(inbound_message.texto)
    try:
        response_text = AIConversationService().reply(inbound_message=inbound_message) if supported_text else None
    except Exception:
        logger.info(
            'whatsapp.reply.end company_id=%s attendance_id=%s message_id=%s outcome=error stage=ai',
            inbound_message.empresa_id, atendimento.pk, inbound_message.external_message_id,
        )
        raise
    ai_generated = response_text is not None
    if not supported_text:
        response_text = {
            'image': 'Recebi sua imagem. Vou encaminhar para análise da equipe.',
            'audio': 'Recebi seu áudio. No momento preciso encaminhá-lo para análise da equipe.',
            'document': 'Recebi seu documento. Vou encaminhar para análise segura da equipe.',
            'video': 'Recebi seu vídeo. Vou encaminhar para análise da equipe.',
            'location': 'Recebi sua localização. Vou encaminhar para a equipe.',
            'contact': 'Recebi o contato compartilhado. Vou encaminhar para a equipe.',
        }.get(inbound_message.tipo, 'Recebi sua mensagem. Vou encaminhar para análise da equipe.')
        logger.info(
            'evolution.media.fallback company_id=%s attendance_id=%s type=%s',
            inbound_message.empresa_id, atendimento.pk, inbound_message.tipo,
        )
    if response_text is None:
        try:
            fluxo = FluxoAtendimento.objects.get(empresa_id=inbound_message.empresa_id)
        except FluxoAtendimento.DoesNotExist:
            logger.info(
                'whatsapp.auto_reply.skipped company_id=%s attendance_id=%s reason=no_flow',
                inbound_message.empresa_id,
                atendimento.pk,
            )
            log_reply_skip(
                company_id=inbound_message.empresa_id,
                attendance_id=atendimento.pk,
                message_id=inbound_message.external_message_id,
                reason='no_flow',
                stage='worker_fallback',
            )
            return None
        response_text = FlowEngine.process(atendimento, inbound_message)
    if not response_text:
        log_reply_skip(
            company_id=inbound_message.empresa_id,
            attendance_id=atendimento.pk,
            message_id=inbound_message.external_message_id,
            reason='empty_response',
            stage='worker_response',
        )
        return None
    if ai_generated:
        profile = getattr(inbound_message.empresa, 'prompt_profile', None)
        if profile and profile.response_delay_seconds:
            time.sleep(profile.response_delay_seconds)
    try:
        with transaction.atomic():
            locked = type(atendimento).objects.select_for_update().get(pk=atendimento.pk)
            if (
                locked.current_step in {locked.Step.WAITING_HUMAN, locked.Step.HUMAN, locked.Step.FINISHED}
                or locked.assigned_to_id
            ):
                logger.info(
                    'whatsapp.auto_reply.skipped company_id=%s attendance_id=%s reason=human_mode',
                    inbound_message.empresa_id, atendimento.pk,
                )
                log_reply_skip(
                    company_id=inbound_message.empresa_id,
                    attendance_id=atendimento.pk,
                    message_id=inbound_message.external_message_id,
                    reason='human_mode',
                    stage='worker_pre_send',
                )
                return None
            outbound_message = send_text_for_attendance(locked, response_text)
    except (WhatsAppAPIError, WhatsAppProviderError):
        AIConversationService._handoff(
            atendimento,
            'Falha ao enviar resposta pela Evolution API.',
        )
        log_reply_skip(
            company_id=inbound_message.empresa_id,
            attendance_id=atendimento.pk,
            message_id=inbound_message.external_message_id,
            reason='provider_send_failed',
            stage='worker_send',
        )
        return None

    logger.info(
        'whatsapp.auto_reply.sent company_id=%s attendance_id=%s message_id=%s',
        inbound_message.empresa_id,
        atendimento.pk,
        outbound_message.external_message_id,
    )
    logger.info(
        'whatsapp.reply.reason company_id=%s attendance_id=%s message_id=%s reason=eligible_user_message stage=worker',
        inbound_message.empresa_id, atendimento.pk, inbound_message.external_message_id,
    )

    try:
        session = WhatsAppSession.objects.get(empresa_id=atendimento.empresa_id)
        remote_jid = f'{atendimento.contato.whatsapp_id}@s.whatsapp.net'
        EvolutionProvider().mark_as_read(
            session.instance_name, inbound_message.external_message_id, remote_jid,
        )
    except (ProviderUnavailable, WhatsAppSession.DoesNotExist):
        logger.info(
            'whatsapp.mark_as_read.failed company_id=%s message_id=%s',
            inbound_message.empresa_id,
            inbound_message.external_message_id,
        )
    logger.info(
        'whatsapp.reply.end company_id=%s attendance_id=%s message_id=%s outcome=sent outbound_message_id=%s stage=worker',
        inbound_message.empresa_id, atendimento.pk, inbound_message.external_message_id,
        outbound_message.external_message_id,
    )
    return outbound_message


def _build_initial_response(fluxo):
    menu = '\n'.join(
        f'{position} - {option}'
        for position, option in enumerate(fluxo.opcoes, start=1)
    )
    parts = [fluxo.saudacao, fluxo.pergunta_menu, menu]
    return '\n\n'.join(part for part in parts if part)
