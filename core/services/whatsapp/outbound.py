import logging

from django.db import IntegrityError, transaction

from core.models import FluxoAtendimento, Mensagem, WhatsAppIntegration
from core.services.ai.conversation import AIConversationService

from .client import WhatsAppCloudClient
from .exceptions import WhatsAppAPIError, WhatsAppProviderError
from .flow_engine import FlowEngine
from .tokens import access_token_for


logger = logging.getLogger('whatsapp.outbound')


def send_text_for_attendance(atendimento, text):
    contato = atendimento.contato
    if contato is None or contato.empresa_id != atendimento.empresa_id:
        raise WhatsAppProviderError('O atendimento não possui um contato válido.')

    integration = WhatsAppIntegration.objects.filter(
        company_id=atendimento.empresa_id,
        is_active=True,
        company__ativa=True,
    ).first()
    if integration is None:
        raise WhatsAppProviderError('A empresa não possui integração ativa.')

    logger.info(
        'whatsapp.outbound.requested company_id=%s attendance_id=%s',
        atendimento.empresa_id,
        atendimento.pk,
    )
    client = WhatsAppCloudClient(
        phone_number_id=integration.phone_number_id,
        access_token=access_token_for(integration),
    )
    try:
        result = client.send_text(contato.whatsapp_id, text)
    except (WhatsAppAPIError, WhatsAppProviderError) as error:
        logger.warning(
            'whatsapp.outbound.failed company_id=%s attendance_id=%s status_code=%s error_code=%s',
            atendimento.empresa_id,
            atendimento.pk,
            getattr(error, 'status_code', None),
            getattr(error, 'error_code', ''),
        )
        raise

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
        or inbound_message.tipo != 'text'
        or not inbound_message.texto
        or not atendimento.automation_enabled
    ):
        logger.info(
            'whatsapp.auto_reply.skipped company_id=%s attendance_id=%s reason=not_eligible',
            inbound_message.empresa_id,
            atendimento.pk,
        )
        return None

    response_text = AIConversationService().reply(inbound_message=inbound_message)
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
    try:
        with transaction.atomic():
            locked = type(atendimento).objects.select_for_update().get(pk=atendimento.pk)
            if (
                locked.current_step in {locked.Step.HUMAN, locked.Step.FINISHED}
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
            'Falha ao enviar resposta pela WhatsApp Cloud API.',
        )
        return None

    logger.info(
        'whatsapp.auto_reply.sent company_id=%s attendance_id=%s message_id=%s',
        inbound_message.empresa_id,
        atendimento.pk,
        outbound_message.external_message_id,
    )

    try:
        integration = atendimento.empresa.whatsapp_integration
        WhatsAppCloudClient(
            phone_number_id=integration.phone_number_id,
            access_token=access_token_for(integration),
        ).mark_as_read(inbound_message.external_message_id)
    except (WhatsAppAPIError, WhatsAppProviderError):
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
