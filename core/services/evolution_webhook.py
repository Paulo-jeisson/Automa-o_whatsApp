import json
import logging
import time
from datetime import UTC, datetime

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.domain.exceptions import ProviderUnavailable
from core.infrastructure.evolution import EvolutionProvider
from core.infrastructure.repositories import WhatsAppSessionRepository
from core.models import Atendimento, BlockedInboundMessage, Contato, Mensagem
from core.services.entitlements import EntitlementService
from core.domain.exceptions import SubscriptionAccessDenied
from core.services.observability import record_metric
from core.services.queue import enqueue
from core.services.phone_numbers import brazilian_phone_variants, normalize_phone_number


logger = logging.getLogger('evolution.webhook')
SUPPORTED_MEDIA = {'image', 'audio', 'document', 'video', 'sticker'}


class EvolutionWebhookError(ValueError):
    pass


class EvolutionWebhookService:
    def __init__(self, provider=None, session_repository=None):
        self.provider = provider or EvolutionProvider()
        self.sessions = session_repository or WhatsAppSessionRepository()

    def accept(self, raw_body, headers):
        self.provider.validate_webhook(raw_body, headers)
        try:
            payload = json.loads(raw_body or b'{}')
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise EvolutionWebhookError('Payload JSON inválido.') from exc
        if not isinstance(payload, dict):
            raise EvolutionWebhookError('Payload inválido.')
        instance_name = str(payload.get('instance') or payload.get('instanceName') or '')
        if not instance_name:
            raise EvolutionWebhookError('Instância ausente.')
        session = self.sessions.for_instance(instance_name)
        if session is None:
            raise EvolutionWebhookError('Instância desconhecida.')
        event_name = str(payload.get('event') or 'unknown').lower()
        event_id = self._event_id(payload)
        data = payload.get('data') or {}
        key = data.get('key') or {}
        sender = normalize_phone_number(str(key.get('remoteJid') or data.get('sender') or ''))
        conversation_key = (
            f'company:{session.empresa_id}:whatsapp:{sender}'
            if sender and ('messages.upsert' in event_name or event_name in {'message', 'messages'})
            else ''
        )
        enqueue(
            'evolution.webhook', {'session_id': session.pk, 'payload': payload},
            idempotency_key=f'evolution:{instance_name}:{event_name}:{event_id}',
            queue='whatsapp', max_attempts=5, conversation_key=conversation_key,
        )
        logger.info(
            'whatsapp.webhook.enqueued company_id=%s instance=%s event=%s',
            session.empresa_id, instance_name, event_name,
        )
        return session

    @staticmethod
    def _event_id(payload):
        data = payload.get('data') or {}
        key = data.get('key') or {}
        value = key.get('id') or data.get('id') or payload.get('date_time') or payload.get('datetime')
        if value:
            return str(value)[:180]
        import hashlib
        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(canonical).hexdigest()

    def process(self, session_id, payload):
        started = time.monotonic()
        session = self.sessions.by_id(session_id)
        event_name = str(payload.get('event') or '').lower()
        data = payload.get('data') or {}
        key = data.get('key') or {}
        message_id = str(key.get('id') or data.get('id') or '')
        logger.info(
            'whatsapp.reply.begin company_id=%s message_id=%s event=%s message_type=%s stage=webhook',
            session.empresa_id, message_id, event_name,
            data.get('messageType') or self._detected_message_type(data.get('message') or {}),
        )
        if 'qrcode' in event_name:
            self._qr_update(session, data)
            self._reply_skip(session, message_id, 'internal_qr_event', event_name)
        elif 'connection' in event_name:
            self._connection_update(session, data)
            self._reply_skip(session, message_id, 'internal_connection_event', event_name)
        elif 'messages.update' in event_name or 'send.message' in event_name:
            self._status_update(session, data)
            status = str(data.get('status') or data.get('update', {}).get('status') or 'status').lower()
            self._reply_skip(session, message_id, f'status_{status}', event_name)
        elif 'messages.upsert' in event_name or event_name in {'message', 'messages'}:
            self._message(session, data)
        else:
            reason = 'presence_event' if 'presence' in event_name else 'internal_evolution_event'
            self._reply_skip(session, message_id, reason, event_name)
        session.events.create(
            kind=(event_name or 'WEBHOOK').upper()[:40],
            message='Evento Evolution processado.', payload=self._safe_event_payload(payload),
        )
        elapsed = (time.monotonic() - started) * 1000
        record_metric('evolution.webhook', empresa=session.empresa, value=elapsed, labels={'event': event_name, 'unit': 'ms'})
        logger.info('evolution.webhook.processed company_id=%s instance=%s event=%s latency_ms=%s', session.empresa_id, session.instance_name, event_name, round(elapsed))

    @staticmethod
    def _reply_skip(session, message_id, reason, event_name, attendance_id=None):
        logger.info(
            'whatsapp.reply.skip company_id=%s attendance_id=%s message_id=%s event=%s stage=webhook',
            session.empresa_id, attendance_id, message_id, event_name,
        )
        logger.info(
            'whatsapp.reply.reason company_id=%s attendance_id=%s message_id=%s reason=%s event=%s stage=webhook',
            session.empresa_id, attendance_id, message_id, reason, event_name,
        )
        logger.info(
            'whatsapp.reply.end company_id=%s attendance_id=%s message_id=%s outcome=ignored reason=%s stage=webhook',
            session.empresa_id, attendance_id, message_id, reason,
        )

    @staticmethod
    def _detected_message_type(message):
        return next(iter(message), 'unknown') if isinstance(message, dict) else 'unknown'

    @staticmethod
    def _safe_event_payload(payload):
        data = payload.get('data') or {}
        key = data.get('key') or {}
        return {'event': payload.get('event'), 'message_id': key.get('id') or data.get('id')}

    @staticmethod
    def _qr_update(session, data):
        qr_code = data.get('base64') or (data.get('qrcode') or {}).get('base64') or ''
        if qr_code and not qr_code.startswith('data:'):
            qr_code = f'data:image/png;base64,{qr_code}'
        if qr_code:
            session.qr_code = qr_code
            session.state = 'WAITING_QR'
            session.last_sync_at = timezone.now()
            session.save(update_fields=['qr_code', 'state', 'last_sync_at', 'updated_at'])

    @staticmethod
    def _connection_update(session, data):
        state = str(data.get('state') or data.get('status') or '').lower()
        mapped = {'open': 'CONNECTED', 'connected': 'CONNECTED', 'connecting': 'CONNECTING', 'close': 'OFFLINE', 'disconnected': 'OFFLINE'}.get(state)
        if mapped:
            session.state = mapped
            if mapped == 'CONNECTED' and not session.connected_at:
                session.connected_at = timezone.now()
                session.qr_code = ''
            session.last_sync_at = timezone.now()
            session.save(update_fields=['state', 'connected_at', 'qr_code', 'last_sync_at', 'updated_at'])
            if mapped == 'CONNECTED':
                from core.services.ai.activation import auto_enable_company_ai
                auto_enable_company_ai(session.empresa_id)

    @staticmethod
    def _status_update(session, data):
        key = data.get('key') or {}
        message_id = str(key.get('id') or data.get('id') or '')
        if not message_id:
            return
        status = str(data.get('status') or data.get('update', {}).get('status') or '').lower()
        status_map = {
            'server_ack': Mensagem.STATUS_ENVIADA, 'sent': Mensagem.STATUS_ENVIADA,
            'delivery_ack': Mensagem.STATUS_ENTREGUE, 'delivered': Mensagem.STATUS_ENTREGUE,
            'read': Mensagem.STATUS_LIDA, 'played': Mensagem.STATUS_LIDA,
            'error': Mensagem.STATUS_FALHA, 'failed': Mensagem.STATUS_FALHA,
        }
        new_status = status_map.get(status)
        if new_status:
            Mensagem.objects.filter(
                empresa=session.empresa, external_message_id=message_id,
                direcao=Mensagem.DIRECAO_SAIDA,
            ).update(status=new_status, erro_codigo='evolution' if new_status == Mensagem.STATUS_FALHA else '')

    def _message(self, session, data):
        key = data.get('key') or {}
        message_id = str(key.get('id') or data.get('id') or '')
        if key.get('fromMe') or data.get('fromMe'):
            self._reply_skip(session, message_id, 'message_from_me', 'messages.upsert')
            return None
        remote_jid = str(key.get('remoteJid') or data.get('sender') or '')
        if not message_id:
            self._reply_skip(session, message_id, 'message_id_missing', 'messages.upsert')
            return None
        if '@g.us' in remote_jid:
            self._reply_skip(session, message_id, 'group_message', 'messages.upsert')
            return None
        whatsapp_id = normalize_phone_number(remote_jid)
        if not whatsapp_id:
            raise EvolutionWebhookError('Remetente inválido.')
        try:
            EntitlementService.require_company_access(session.empresa)
        except SubscriptionAccessDenied:
            message = data.get('message') or {}
            BlockedInboundMessage.objects.get_or_create(
                external_message_id=message_id,
                defaults={
                    'empresa': session.empresa,
                    'contact_identifier': whatsapp_id[:64],
                    'message_type': str(
                        data.get('messageType') or self._detected_message_type(message) or 'unknown'
                    )[:32],
                    'reason': 'subscription_blocked',
                    'received_at': self._timestamp(data.get('messageTimestamp')) or timezone.now(),
                },
            )
            self._reply_skip(session, message_id, 'subscription_blocked', 'messages.upsert')
            return None
        if (
            session.phone_number
            and brazilian_phone_variants(whatsapp_id)
            & brazilian_phone_variants(session.phone_number)
        ):
            self._reply_skip(session, message_id, 'connected_number_message', 'messages.upsert')
            return None
        message = data.get('message') or {}
        message_type, text, ignored_reason = self._message_content(message, data)
        if ignored_reason:
            self._reply_skip(session, message_id, ignored_reason, 'messages.upsert')
            return None
        if message_type in SUPPORTED_MEDIA:
            try:
                media = self.provider.download_media(session.instance_name, data)
                logger.info(
                    'evolution.media.downloaded company_id=%s instance=%s message_id=%s type=%s bytes=%s',
                    session.empresa_id, session.instance_name, message_id, message_type, len(media),
                )
            except ProviderUnavailable:
                logger.warning(
                    'evolution.media.download_failed company_id=%s instance=%s message_id=%s type=%s',
                    session.empresa_id, session.instance_name, message_id, message_type,
                )
        try:
            with transaction.atomic():
                existing = Mensagem.objects.filter(external_message_id=message_id).first()
                if existing:
                    return existing
                contato, created = Contato.objects.get_or_create(
                    empresa=session.empresa, whatsapp_id=whatsapp_id,
                    defaults={'nome': str(data.get('pushName') or '')[:120]},
                )
                if not created and not contato.nome and data.get('pushName'):
                    contato.nome = str(data['pushName'])[:120]
                    contato.save(update_fields=['nome', 'atualizado_em'])
                atendimento = Atendimento.objects.filter(
                    empresa=session.empresa, contato=contato,
                ).exclude(status=Atendimento.STATUS_FINALIZADO).first()
                if atendimento is None:
                    EntitlementService.consume(session.empresa, 'attendances')
                    atendimento = Atendimento.objects.create(
                        empresa=session.empresa, contato=contato,
                        nome_cliente=contato.nome or whatsapp_id,
                        telefone_cliente=whatsapp_id[:13], opcao_escolhida='WhatsApp',
                        necessidade=(text or f'Mensagem recebida ({message_type})')[:180],
                        observacao='', status=Atendimento.STATUS_NOVO,
                    )
                EntitlementService.consume(session.empresa, 'messages')
                inbound = Mensagem.objects.create(
                    empresa=session.empresa, atendimento=atendimento, contato=contato,
                    external_message_id=message_id, direcao=Mensagem.DIRECAO_ENTRADA,
                    tipo=message_type[:32], texto=text,
                    timestamp_meta=self._timestamp(data.get('messageTimestamp')),
                )
                atendimento.last_message_at = inbound.timestamp_meta or inbound.criado_em
                atendimento.save(update_fields=['last_message_at'])
        except IntegrityError:
            return Mensagem.objects.filter(external_message_id=message_id).first()
        except PermissionDenied:
            logger.warning('evolution.plan_limit company_id=%s', session.empresa_id)
            return None
        from core.services.whatsapp.outbound import prequeue_auto_reply_reason
        reason = prequeue_auto_reply_reason(
            company_id=session.empresa_id,
            phone_number=whatsapp_id,
            atendimento=atendimento,
        )
        if reason:
            logger.info(
                'whatsapp.auto_reply.skipped company_id=%s attendance_id=%s message_id=%s reason=%s',
                session.empresa_id, atendimento.pk, message_id, reason,
            )
            self._reply_skip(
                session, message_id, reason, 'messages.upsert', attendance_id=atendimento.pk,
            )
            return inbound
        enqueue(
            'whatsapp.automatic_reply', {'message_id': inbound.pk, 'company_id': session.empresa_id},
            idempotency_key=f'automatic-reply:{message_id}', queue='whatsapp', max_attempts=5,
            conversation_key=f'company:{session.empresa_id}:attendance:{atendimento.pk}',
        )
        logger.info(
            'whatsapp.webhook.enqueued company_id=%s instance=%s event=automatic_reply message_id=%s',
            session.empresa_id, session.instance_name, message_id,
        )
        logger.info(
            'whatsapp.reply.reason company_id=%s attendance_id=%s message_id=%s reason=eligible_user_message type=%s stage=webhook',
            session.empresa_id, atendimento.pk, message_id, message_type,
        )
        logger.info(
            'whatsapp.reply.end company_id=%s attendance_id=%s message_id=%s outcome=enqueued stage=webhook',
            session.empresa_id, atendimento.pk, message_id,
        )
        logger.info('evolution.message.persisted company_id=%s instance=%s contact_id=%s attendance_id=%s message_id=%s type=%s', session.empresa_id, session.instance_name, contato.pk, atendimento.pk, message_id, message_type)
        return inbound

    @staticmethod
    def _message_content(message, data):
        if message.get('conversation'):
            return 'text', str(message['conversation']), None
        extended = message.get('extendedTextMessage') or {}
        if extended.get('text'):
            return 'text', str(extended['text']), None
        for key in ('listMessage', 'listResponseMessage', 'buttonsResponseMessage'):
            item = message.get(key) or {}
            if key in message:
                selected = item.get('singleSelectReply') or {}
                text = (
                    item.get('title') or item.get('description')
                    or item.get('selectedDisplayText') or selected.get('selectedRowId') or ''
                )
                return ('text', str(text), None) if text else (None, '', 'empty_list_message')
        if 'protocolMessage' in message:
            return None, '', 'protocol_message'
        if 'reactionMessage' in message:
            return None, '', 'reaction_message'
        mapping = {
            'imageMessage': 'image', 'audioMessage': 'audio',
            'documentMessage': 'document', 'videoMessage': 'video',
            'stickerMessage': 'sticker', 'locationMessage': 'location',
            'contactMessage': 'contact', 'contactsArrayMessage': 'contact',
        }
        for key, kind in mapping.items():
            if key in message:
                item = message[key] or {}
                caption = item.get('caption') or item.get('displayName') or ''
                if kind == 'location':
                    caption = f"Localização: {item.get('degreesLatitude')}, {item.get('degreesLongitude')}"
                return kind, str(caption), None
        raw_type = str(data.get('messageType') or '').lower()
        if raw_type in {'protocolmessage', 'reactionmessage'}:
            return None, '', raw_type.replace('message', '_message')
        return None, '', 'unsupported_or_empty_message'

    @staticmethod
    def _timestamp(value):
        try:
            return datetime.fromtimestamp(int(value), tz=UTC)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
