import json
import logging
import re
import time
from datetime import UTC, datetime

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.domain.exceptions import ProviderUnavailable
from core.infrastructure.evolution import EvolutionProvider
from core.infrastructure.repositories import WhatsAppSessionRepository
from core.models import Atendimento, Contato, Mensagem
from core.services.entitlements import EntitlementService
from core.services.observability import record_metric
from core.services.queue import enqueue


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
        enqueue(
            'evolution.webhook', {'session_id': session.pk, 'payload': payload},
            idempotency_key=f'evolution:{instance_name}:{event_name}:{event_id}',
            queue='whatsapp', max_attempts=5,
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
        if 'qrcode' in event_name:
            self._qr_update(session, data)
        elif 'connection' in event_name:
            self._connection_update(session, data)
        elif 'messages.update' in event_name or 'send.message' in event_name:
            self._status_update(session, data)
        elif 'messages.upsert' in event_name or event_name in {'message', 'messages'}:
            self._message(session, data)
        session.events.create(
            kind=(event_name or 'WEBHOOK').upper()[:40],
            message='Evento Evolution processado.', payload=self._safe_event_payload(payload),
        )
        elapsed = (time.monotonic() - started) * 1000
        record_metric('evolution.webhook', empresa=session.empresa, value=elapsed, labels={'event': event_name, 'unit': 'ms'})
        logger.info('evolution.webhook.processed company_id=%s instance=%s event=%s latency_ms=%s', session.empresa_id, session.instance_name, event_name, round(elapsed))

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
        if key.get('fromMe'):
            return None
        message_id = str(key.get('id') or data.get('id') or '')
        remote_jid = str(key.get('remoteJid') or data.get('sender') or '')
        if not message_id or '@g.us' in remote_jid:
            return None
        whatsapp_id = re.sub(r'\D', '', remote_jid.split('@')[0])[:32]
        if not whatsapp_id:
            raise EvolutionWebhookError('Remetente inválido.')
        message = data.get('message') or {}
        message_type, text = self._message_content(message, data)
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
        enqueue(
            'whatsapp.automatic_reply', {'message_id': inbound.pk, 'company_id': session.empresa_id},
            idempotency_key=f'automatic-reply:{message_id}', queue='whatsapp', max_attempts=5,
        )
        logger.info('evolution.message.persisted company_id=%s instance=%s contact_id=%s attendance_id=%s message_id=%s type=%s', session.empresa_id, session.instance_name, contato.pk, atendimento.pk, message_id, message_type)
        return inbound

    @staticmethod
    def _message_content(message, data):
        if message.get('conversation'):
            return 'text', str(message['conversation'])
        extended = message.get('extendedTextMessage') or {}
        if extended.get('text'):
            return 'text', str(extended['text'])
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
                return kind, str(caption)
        return str(data.get('messageType') or 'unknown').replace('Message', '').lower()[:32], ''

    @staticmethod
    def _timestamp(value):
        try:
            return datetime.fromtimestamp(int(value), tz=UTC)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
