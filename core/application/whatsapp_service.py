import logging
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from core.domain.exceptions import ProviderUnavailable
from core.domain.whatsapp import SessionState
from core.infrastructure.evolution import EvolutionProvider
from core.models import WhatsAppSession, WhatsAppSessionEvent

logger = logging.getLogger('whatsapp.web_session')


class WhatsAppSessionService:
    def __init__(self, provider=None):
        self.provider = provider or EvolutionProvider()

    @staticmethod
    def _event(session, kind, message='', payload=None):
        return WhatsAppSessionEvent.objects.create(
            session=session, kind=kind, message=message, payload=payload or {},
        )

    @transaction.atomic
    def ensure(self, empresa):
        session, _ = WhatsAppSession.objects.get_or_create(
            empresa=empresa,
            defaults={'instance_name': f'zapfluxo-{empresa.pk}-{uuid4().hex[:8]}'},
        )
        return session

    def connect(self, empresa):
        session = self.ensure(empresa)
        session.state = SessionState.INITIALIZING
        session.last_error = ''
        session.save(update_fields=['state', 'last_error', 'updated_at'])
        try:
            snapshot = self.provider.create(session.instance_name)
            if not snapshot.qr_code and snapshot.state != SessionState.CONNECTED:
                snapshot = self.provider.qr_code(session.instance_name)
            self._apply(session, snapshot)
            self._event(session, 'QR_GENERATED', 'QR Code atualizado.')
        except ProviderUnavailable as exc:
            session.state = SessionState.ERROR
            session.last_error = str(exc)
            session.save(update_fields=['state', 'last_error', 'updated_at'])
            self._event(session, 'ERROR', str(exc))
        return session

    def refresh(self, empresa):
        session = self.ensure(empresa)
        try:
            snapshot = self.provider.status(session.instance_name)
            self._apply(session, snapshot)
            self._event(session, 'HEARTBEAT', 'Sessão sincronizada.', {'ping_ms': snapshot.ping_ms})
        except ProviderUnavailable as exc:
            session.state = SessionState.ERROR
            session.last_error = str(exc)
            session.save(update_fields=['state', 'last_error', 'updated_at'])
        return session

    def reconnect(self, empresa):
        session = self.ensure(empresa)
        session.state = SessionState.RECONNECTING
        session.reconnect_attempts += 1
        session.save(update_fields=['state', 'reconnect_attempts', 'updated_at'])
        try:
            self._apply(session, self.provider.reconnect(session.instance_name))
            self._event(session, 'RECONNECT', 'Reconexão solicitada.')
        except ProviderUnavailable as exc:
            session.state = SessionState.ERROR
            session.last_error = str(exc)
            session.save(update_fields=['state', 'last_error', 'updated_at'])
        return session

    def clear(self, empresa):
        session = self.ensure(empresa)
        try:
            self.provider.logout(session.instance_name)
        except ProviderUnavailable as exc:
            logger.warning('whatsapp.session.logout_failed company_id=%s', empresa.pk)
        session.state = SessionState.OFFLINE
        session.qr_code = ''
        session.phone_number = ''
        session.device_name = ''
        session.connected_at = None
        session.save()
        self._event(session, 'SESSION_CLEARED', 'Sessão local limpa.')
        return session

    @staticmethod
    def _apply(session, snapshot):
        was_connected = session.state == SessionState.CONNECTED
        session.state = snapshot.state
        session.qr_code = snapshot.qr_code or session.qr_code
        session.phone_number = snapshot.phone_number or session.phone_number
        session.device_name = snapshot.device_name or session.device_name
        session.ping_ms = snapshot.ping_ms
        session.last_sync_at = timezone.now()
        session.last_heartbeat_at = timezone.now()
        session.last_error = snapshot.error
        if snapshot.state == SessionState.CONNECTED and not was_connected:
            session.connected_at = timezone.now()
            session.qr_code = ''
        session.save()
