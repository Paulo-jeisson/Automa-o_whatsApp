import logging
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from core.domain.exceptions import ProviderUnavailable
from core.domain.whatsapp import SessionState
from core.infrastructure.evolution import EvolutionProvider, EvolutionRequestError
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
        instance_name = session.instance_name
        logger.info(
            'whatsapp.session.reset_started company_id=%s instance=%s local_state=%s',
            empresa.pk, instance_name, session.state,
        )

        try:
            remote = self.provider.status(instance_name)
            logger.info(
                'whatsapp.session.remote_state company_id=%s instance=%s state=%s',
                empresa.pk, instance_name, remote.state,
            )
        except EvolutionRequestError as exc:
            logger.info(
                'whatsapp.session.state_unavailable company_id=%s instance=%s status_code=%s',
                empresa.pk, instance_name, exc.status_code,
            )
        except ProviderUnavailable:
            logger.warning(
                'whatsapp.session.state_failed company_id=%s instance=%s',
                empresa.pk, instance_name,
            )

        try:
            self.provider.logout(instance_name)
            logger.info('whatsapp.session.logout_succeeded company_id=%s instance=%s', empresa.pk, instance_name)
        except EvolutionRequestError as exc:
            logger.log(
                logging.INFO if exc.status_code in {401, 404, 409} else logging.WARNING,
                'whatsapp.session.logout_tolerated company_id=%s instance=%s status_code=%s',
                empresa.pk, instance_name, exc.status_code,
            )
        except ProviderUnavailable:
            logger.warning(
                'whatsapp.session.logout_tolerated company_id=%s instance=%s status_code=transport',
                empresa.pk, instance_name,
            )

        try:
            self.provider.delete(instance_name)
            logger.info('whatsapp.session.delete_succeeded company_id=%s instance=%s', empresa.pk, instance_name)
        except EvolutionRequestError as exc:
            logger.log(
                logging.INFO if exc.status_code == 404 else logging.WARNING,
                'whatsapp.session.delete_tolerated company_id=%s instance=%s status_code=%s',
                empresa.pk, instance_name, exc.status_code,
            )
        except ProviderUnavailable:
            logger.warning(
                'whatsapp.session.delete_tolerated company_id=%s instance=%s status_code=transport',
                empresa.pk, instance_name,
            )

        # Invalida o QR anterior antes de qualquer tentativa de recriação remota.
        session.state = SessionState.OFFLINE
        session.qr_code = ''
        session.phone_number = ''
        session.device_name = ''
        session.connected_at = None
        session.last_sync_at = None
        session.last_heartbeat_at = None
        session.ping_ms = None
        session.reconnect_attempts = 0
        session.last_error = ''
        session.save()
        self._event(session, 'SESSION_CLEARED', 'Sessão local invalidada para recriação.')

        try:
            self.provider.create(instance_name)
        except EvolutionRequestError as exc:
            # Timeout na exclusão pode deixar a mesma instância ativa. Reusar o
            # mesmo nome evita duplicidade e ainda permite solicitar um QR novo.
            if exc.status_code not in {400, 403, 409}:
                return self._reset_failed(session, empresa, instance_name, exc)
            logger.info(
                'whatsapp.session.create_existing company_id=%s instance=%s status_code=%s',
                empresa.pk, instance_name, exc.status_code,
            )
        except ProviderUnavailable as exc:
            return self._reset_failed(session, empresa, instance_name, exc)

        try:
            self.provider.configure_webhook(instance_name)
            logger.info(
                'whatsapp.session.webhook_configured company_id=%s instance=%s',
                empresa.pk, instance_name,
            )
            fresh = self.provider.qr_code(instance_name)
            if not fresh.qr_code and fresh.state != SessionState.CONNECTED:
                raise ProviderUnavailable('Evolution API não retornou um novo QR Code.')
            self._apply(session, fresh, preserve_existing=False)
            self._event(session, 'QR_GENERATED', 'Novo QR Code gerado após limpar a sessão.')
            logger.info(
                'whatsapp.session.reset_completed company_id=%s instance=%s state=%s has_qr=%s',
                empresa.pk, instance_name, session.state, bool(session.qr_code),
            )
        except ProviderUnavailable as exc:
            return self._reset_failed(session, empresa, instance_name, exc)
        return session

    def _reset_failed(self, session, empresa, instance_name, exc):
        session.state = SessionState.ERROR
        session.qr_code = ''
        session.last_error = str(exc)
        session.save(update_fields=['state', 'qr_code', 'last_error', 'updated_at'])
        self._event(session, 'ERROR', str(exc), {'operation': 'session_reset'})
        logger.error(
            'whatsapp.session.reset_failed company_id=%s instance=%s type=%s',
            empresa.pk, instance_name, type(exc).__name__,
        )
        return session

    @staticmethod
    def _apply(session, snapshot, preserve_existing=True):
        was_connected = session.state == SessionState.CONNECTED
        session.state = snapshot.state
        session.qr_code = snapshot.qr_code or (session.qr_code if preserve_existing else '')
        session.phone_number = snapshot.phone_number or (session.phone_number if preserve_existing else '')
        session.device_name = snapshot.device_name or (session.device_name if preserve_existing else '')
        session.ping_ms = snapshot.ping_ms
        session.last_sync_at = timezone.now()
        session.last_heartbeat_at = timezone.now()
        session.last_error = snapshot.error
        if snapshot.state == SessionState.CONNECTED and not was_connected:
            session.connected_at = timezone.now()
            session.qr_code = ''
        session.save()
        if snapshot.state == SessionState.CONNECTED:
            from core.services.ai.activation import auto_enable_company_ai
            auto_enable_company_ai(session.empresa_id)
