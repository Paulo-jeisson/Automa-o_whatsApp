import logging
import time
import re

from django.db import IntegrityError, transaction

from core.domain.exceptions import ProviderUnavailable
from core.infrastructure.evolution import EvolutionProvider
from core.models import FluxoAtendimento, Mensagem, WhatsAppSession, IgnoredPhoneNumber
from core.services.ai.conversation import AIConversationService

from .exceptions import WhatsAppAPIError, WhatsAppProviderError
from .flow_engine import FlowEngine


logger = logging.getLogger('whatsapp.outbound')


def send_text_for_attendance(atendimento, text):
    contato = atendimento.contato
    if contato is None or contato.empresa_id != atendimento.empresa_id:
        raise WhatsAppProviderError('O atendimento não possui um contato válido.')

    session = WhatsAppSession.objects.filter(
        empresa_id=atendimento.empresa_id,
        empresa__ativa=True,
        state='CONNECTED',
    ).first()
    if session is None:
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
    atendimento = inbound_message.atendimento
    if (
        inbound_message.direcao != Mensagem.DIRECAO_ENTRADA
        or not atendimento.automation_enabled
    ):
        logger.info(
            'whatsapp.auto_reply.skipped company_id=%s attendance_id=%s reason=not_eligible',
            inbound_message.empresa_id,
            atendimento.pk,
        )
        return None

    sender = re.sub(r'\D', '', atendimento.contato.whatsapp_id if atendimento.contato else '')
    if sender and IgnoredPhoneNumber.objects.filter(
        empresa_id=inbound_message.empresa_id, phone_number=sender,
    ).exists():
        logger.info(
            'whatsapp.auto_reply.skipped company_id=%s attendance_id=%s reason=pass_number',
            inbound_message.empresa_id, atendimento.pk,
        )
        return None

    supported_text = inbound_message.tipo == 'text' and bool(inbound_message.texto)
    response_text = AIConversationService().reply(inbound_message=inbound_message) if supported_text else None
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
        fluxo = FluxoAtendimento.objects.filter(empresa_id=inbound_message.empresa_id).first()
        if fluxo is None:
            logger.info(
                'whatsapp.auto_reply.skipped company_id=%s attendance_id=%s reason=no_flow',
                inbound_message.empresa_id,
                atendimento.pk,
            )
            return None
        response_text = FlowEngine.process(atendimento, inbound_message)
    if not response_text:
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
                    'whatsapp.auto_reply.skipped company_id=%s attendance_id=%s reason=human_state',
                    inbound_message.empresa_id, atendimento.pk,
                )
                return None
            outbound_message = send_text_for_attendance(locked, response_text)
    except (WhatsAppAPIError, WhatsAppProviderError):
        AIConversationService._handoff(
            atendimento,
            'Falha ao enviar resposta pela Evolution API.',
        )
        return None

    logger.info(
        'whatsapp.auto_reply.sent company_id=%s attendance_id=%s message_id=%s',
        inbound_message.empresa_id,
        atendimento.pk,
        outbound_message.external_message_id,
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
    return outbound_message


def _build_initial_response(fluxo):
    menu = '\n'.join(
        f'{position} - {option}'
        for position, option in enumerate(fluxo.opcoes, start=1)
    )
    parts = [fluxo.saudacao, fluxo.pergunta_menu, menu]
    return '\n\n'.join(part for part in parts if part)
