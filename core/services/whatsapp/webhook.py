import logging
from datetime import UTC, datetime

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.core.exceptions import PermissionDenied

from core.models import Atendimento, Contato, Mensagem, WhatsAppIntegration
from core.services.entitlements import EntitlementService
from core.domain.exceptions import SubscriptionAccessDenied

from .parser import NormalizedWebhookEvent


logger = logging.getLogger('whatsapp.webhook')


def process_webhook_events(events):
    processed = []
    for event in events:
        processed.append(process_webhook_event(event))
    return processed


def process_webhook_event(event: NormalizedWebhookEvent):
    started_at = timezone.now()
    if not event.phone_number_id:
        logger.warning('whatsapp.webhook.phone_number_id_missing event_type=%s', event.event_type)
        return None

    integration = WhatsAppIntegration.objects.select_related('company').filter(
        phone_number_id=event.phone_number_id,
        is_active=True,
        company__ativa=True,
    ).first()
    if integration is None:
        logger.warning(
            'whatsapp.integration.not_found phone_number_id=%s event_type=%s',
            event.phone_number_id,
            event.event_type,
        )
        return None

    if event.event_type in {'message', 'status'}:
        integration.last_communication_at = timezone.now()
        integration.save(update_fields=['last_communication_at', 'updated_at'])

    if event.event_type == 'message':
        try:
            EntitlementService.require_company_access(integration.company)
        except SubscriptionAccessDenied:
            logger.info('whatsapp.message.skipped company_id=%s reason=subscription_blocked', integration.company_id)
            return integration
        inbound_message, created = _persist_inbound_message(integration, event)
        if created:
            from core.services.whatsapp.outbound import prequeue_auto_reply_reason
            reason = prequeue_auto_reply_reason(
                company_id=inbound_message.empresa_id,
                phone_number=inbound_message.contato.whatsapp_id,
                atendimento=inbound_message.atendimento,
            )
            if reason:
                logger.info(
                    'whatsapp.auto_reply.skipped company_id=%s attendance_id=%s message_id=%s reason=%s',
                    inbound_message.empresa_id, inbound_message.atendimento_id,
                    inbound_message.external_message_id, reason,
                )
                return integration
            from core.services.queue import enqueue
            enqueue(
                'whatsapp.automatic_reply', {
                    'message_id': inbound_message.pk,
                    'company_id': inbound_message.empresa_id,
                },
                idempotency_key=f'automatic-reply:{inbound_message.external_message_id}',
                queue='whatsapp',
            )
        logger.info(
            'whatsapp.message.received company_id=%s phone_number_id=%s message_id=%s type=%s',
            integration.company_id,
            integration.phone_number_id,
            event.message_id,
            event.message_type,
        )
    elif event.event_type == 'status':
        _update_message_status(integration, event)
        logger.info(
            'whatsapp.status.received company_id=%s phone_number_id=%s message_id=%s status=%s',
            integration.company_id,
            integration.phone_number_id,
            event.message_id,
            event.status,
        )
    else:
        logger.info(
            'whatsapp.event.unknown company_id=%s phone_number_id=%s',
            integration.company_id,
            integration.phone_number_id,
        )
    from core.services.observability import record_metric
    record_metric(
        'webhook.event', empresa=integration.company,
        value=(timezone.now() - started_at).total_seconds() * 1000,
        labels={'type': event.event_type, 'unit': 'ms'},
    )
    return integration


def _persist_inbound_message(integration, event):
    if not event.message_id:
        logger.warning(
            'whatsapp.message.id_missing company_id=%s phone_number_id=%s type=%s',
            integration.company_id,
            integration.phone_number_id,
            event.message_type,
        )
        return None, False

    whatsapp_id = _normalize_whatsapp_id(event.wa_id)
    if not whatsapp_id:
        logger.warning(
            'whatsapp.message.sender_missing company_id=%s message_id=%s type=%s',
            integration.company_id,
            event.message_id,
            event.message_type,
        )
        return None, False

    try:
        with transaction.atomic():
            existing_message = Mensagem.objects.filter(
                external_message_id=event.message_id,
            ).first()
            if existing_message is not None:
                logger.info(
                    'whatsapp.message.duplicate company_id=%s message_id=%s',
                    integration.company_id,
                    event.message_id,
                )
                return existing_message, False

            contato, created = Contato.objects.get_or_create(
                empresa=integration.company,
                whatsapp_id=whatsapp_id,
                defaults={'nome': event.contact_name[:120]},
            )
            if not created and event.contact_name and not contato.nome:
                contato.nome = event.contact_name[:120]
                contato.save(update_fields=['nome', 'atualizado_em'])

            atendimento = Atendimento.objects.filter(
                empresa=integration.company,
                contato=contato,
            ).exclude(
                status=Atendimento.STATUS_FINALIZADO,
            ).first()
            if atendimento is None:
                EntitlementService.consume(integration.company, 'attendances')
                atendimento = Atendimento.objects.create(
                    empresa=integration.company,
                    contato=contato,
                    nome_cliente=contato.nome or whatsapp_id,
                    telefone_cliente=whatsapp_id[:13],
                    opcao_escolhida='WhatsApp',
                    necessidade=_attendance_summary(event),
                    observacao='',
                    status=Atendimento.STATUS_NOVO,
                )

            EntitlementService.consume(integration.company, 'messages')
            mensagem = Mensagem.objects.create(
                empresa=integration.company,
                atendimento=atendimento,
                contato=contato,
                external_message_id=event.message_id,
                direcao=Mensagem.DIRECAO_ENTRADA,
                tipo=(event.message_type or 'unknown')[:32],
                texto=event.text,
                timestamp_meta=_parse_meta_timestamp(event.timestamp),
            )
            atendimento.last_message_at = mensagem.timestamp_meta or mensagem.criado_em
            atendimento.save(update_fields=['last_message_at'])
    except PermissionDenied:
        logger.warning('whatsapp.plan_limit company_id=%s', integration.company_id)
        return None, False
    except IntegrityError:
        # Uma entrega concorrente pode vencer a constraint única. A transação
        # atual é revertida e recuperamos a mensagem já persistida.
        logger.info(
            'whatsapp.message.duplicate company_id=%s message_id=%s',
            integration.company_id,
            event.message_id,
        )
        return Mensagem.objects.filter(external_message_id=event.message_id).first(), False

    logger.info(
        'whatsapp.message.persisted company_id=%s message_id=%s contact_id=%s attendance_id=%s',
        integration.company_id,
        event.message_id,
        contato.pk,
        atendimento.pk,
    )
    return mensagem, True


def _normalize_whatsapp_id(value):
    from core.services.phone_numbers import normalize_phone_number
    return normalize_phone_number(value)


def _parse_meta_timestamp(value):
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _attendance_summary(event):
    if event.message_type == 'text' and event.text:
        return event.text[:180]
    message_type = (event.message_type or 'desconhecido')[:32]
    return f'Mensagem recebida pelo WhatsApp ({message_type}).'


STATUS_ORDER = {
    Mensagem.STATUS_ACEITA: 0,
    Mensagem.STATUS_ENVIADA: 1,
    Mensagem.STATUS_ENTREGUE: 2,
    Mensagem.STATUS_LIDA: 3,
}


def _update_message_status(integration, event):
    mensagem = Mensagem.objects.filter(
        external_message_id=event.message_id,
        empresa=integration.company,
        direcao=Mensagem.DIRECAO_SAIDA,
    ).first()
    if mensagem is None:
        logger.info(
            'whatsapp.status.unknown_message company_id=%s message_id=%s status=%s',
            integration.company_id,
            event.message_id,
            event.status,
        )
        return None

    status_map = {
        'sent': Mensagem.STATUS_ENVIADA,
        'delivered': Mensagem.STATUS_ENTREGUE,
        'read': Mensagem.STATUS_LIDA,
        'failed': Mensagem.STATUS_FALHA,
    }
    new_status = status_map.get(event.status)
    if new_status is None:
        return mensagem

    if new_status == Mensagem.STATUS_FALHA:
        if mensagem.status in {Mensagem.STATUS_ENTREGUE, Mensagem.STATUS_LIDA}:
            return mensagem
    elif mensagem.status == Mensagem.STATUS_FALHA:
        return mensagem
    elif STATUS_ORDER.get(new_status, -1) <= STATUS_ORDER.get(mensagem.status, -1):
        return mensagem

    mensagem.status = new_status
    mensagem.erro_codigo = event.error_code if new_status == Mensagem.STATUS_FALHA else ''
    mensagem.save(update_fields=['status', 'erro_codigo'])
    logger.info(
        'whatsapp.status.updated company_id=%s message_id=%s status=%s',
        integration.company_id,
        event.message_id,
        new_status,
    )
    return mensagem
