import logging
import hashlib
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

from django.db import OperationalError, connection, transaction
from django.utils import timezone

from core.domain.exceptions import ProviderUnavailable
from core.domain.whatsapp import SessionState
from core.infrastructure.evolution import EvolutionProvider, EvolutionRequestError
from core.models import WhatsAppSession, WhatsAppSessionEvent

logger = logging.getLogger('whatsapp.web_session')
_local_locks_guard = threading.Lock()
_local_locks = {}


class WhatsAppOperationInProgress(ProviderUnavailable):
    pass


@contextmanager
def _company_operation_lock(company_id):
    lock_key = int.from_bytes(hashlib.sha256(f'whatsapp-session:{company_id}'.encode()).digest()[:8], 'big', signed=True)
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [lock_key])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise WhatsAppOperationInProgress('Conexão do WhatsApp já está sendo atualizada. Aguarde alguns segundos.')
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [lock_key])
        return
    with _local_locks_guard:
        lock = _local_locks.setdefault(company_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise WhatsAppOperationInProgress('Conexão do WhatsApp já está sendo atualizada. Aguarde alguns segundos.')
    try:
        yield
    finally:
        lock.release()


def session_operation(method):
    def wrapped(self, empresa, *args, **kwargs):
        with _company_operation_lock(empresa.pk):
            return method(self, empresa, *args, **kwargs)
    return wrapped


class WhatsAppSessionService:
    deletion_poll_delays = (0.1, 0.2, 0.4, 0.8, 1.0)
    create_retry_delays = (0.1, 0.25, 0.5)
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
            defaults={'instance_name': f'iaatende-{empresa.pk}-{uuid4().hex[:8]}'},
        )
        return session

    def _persist(self, session, **kwargs):
        for attempt in range(3):
            try:
                session.save(**kwargs)
                return
            except OperationalError as exc:
                locked = connection.vendor == 'sqlite' and 'database is locked' in str(exc).lower()
                if not locked or attempt == 2:
                    raise
                logger.warning('whatsapp.session.db_lock_retry company_id=%s instance=%s attempt=%s', session.empresa_id, session.instance_name, attempt + 1)
                time.sleep(0.05 * (attempt + 1))

    @staticmethod
    def _confirmed_conflict(exc):
        details = f'{exc.provider_message} {exc.safe_payload}'.lower()
        return exc.status_code in {400, 403, 409} and any(
            marker in details for marker in ('already exists', 'already in use', 'name is in use', 'name "')
        )

    def _provision_missing(self, session):
        snapshot = None
        for attempt, delay in enumerate(self.create_retry_delays, 1):
            try:
                snapshot = self.provider.create(session.instance_name)
                logger.info('whatsapp.session.created company_id=%s instance=%s attempt=%s', session.empresa_id, session.instance_name, attempt)
                break
            except EvolutionRequestError as exc:
                if not self._confirmed_conflict(exc):
                    raise
                logger.info('whatsapp.session.create_retry company_id=%s instance=%s attempt=%s status_code=%s', session.empresa_id, session.instance_name, attempt, exc.status_code)
                try:
                    snapshot = self.provider.status(session.instance_name)
                    break
                except EvolutionRequestError as status_error:
                    if status_error.status_code != 404 or attempt == len(self.create_retry_delays):
                        raise
                    time.sleep(delay)
        if snapshot is None:
            raise ProviderUnavailable('Não foi possível recriar a instância do WhatsApp.')
        self.provider.configure_webhook(session.instance_name)
        if not snapshot.qr_code and snapshot.state != SessionState.CONNECTED:
            snapshot = self.provider.qr_code(session.instance_name)
        if not snapshot.qr_code and snapshot.state != SessionState.CONNECTED:
            raise ProviderUnavailable('Evolution API não retornou um novo QR Code.')
        return snapshot

    def _wait_until_deleted(self, session):
        for attempt, delay in enumerate(self.deletion_poll_delays, 1):
            try:
                remote = self.provider.status(session.instance_name)
                logger.info('whatsapp.session.waiting_remote_deletion company_id=%s instance=%s attempt=%s remote_state=%s', session.empresa_id, session.instance_name, attempt, remote.state)
            except EvolutionRequestError as exc:
                if exc.status_code == 404:
                    logger.info('whatsapp.session.remote_deleted company_id=%s instance=%s attempt=%s', session.empresa_id, session.instance_name, attempt)
                    return
                raise
            time.sleep(delay)
        raise ProviderUnavailable('Estamos finalizando a sessão anterior. Tente novamente em alguns segundos.')

    @session_operation
    def connect(self, empresa):
        session = self.ensure(empresa)
        try:
            snapshot = self.provider.status(session.instance_name)
            if snapshot.state == SessionState.CONNECTED:
                self._apply(session, snapshot, preserve_existing=False)
                self._event(session, 'ALREADY_CONNECTED', 'WhatsApp já está conectado.')
                return session
            snapshot = self.provider.reconnect(session.instance_name)
            if snapshot.state not in {SessionState.CONNECTED, SessionState.CONNECTING} and not snapshot.qr_code:
                snapshot = self.provider.qr_code(session.instance_name)
        except EvolutionRequestError as exc:
            if exc.status_code != 404:
                # A resposta "já existe" nunca prevalece sobre o estado real.
                if exc.status_code in {400, 409}:
                    snapshot = self.provider.status(session.instance_name)
                    self._apply(session, snapshot)
                    return session
                return self._connection_failed(session, exc)
            try:
                snapshot = self.provider.create(session.instance_name)
                if not snapshot.qr_code and snapshot.state != SessionState.CONNECTED:
                    snapshot = self.provider.qr_code(session.instance_name)
            except EvolutionRequestError as create_error:
                if create_error.status_code in {400, 409}:
                    snapshot = self.provider.status(session.instance_name)
                    self._apply(session, snapshot)
                    return session
                return self._connection_failed(session, create_error)
            except ProviderUnavailable as create_error:
                return self._connection_failed(session, create_error)
        except ProviderUnavailable as exc:
            return self._connection_failed(session, exc)
        try:
            self._apply(session, snapshot)
            self._event(
                session,
                'QR_GENERATED' if session.qr_code else 'CONNECTION_SYNCED',
                'QR Code atualizado.' if session.qr_code else 'Estado da conexão atualizado.',
            )
        except EvolutionRequestError as exc:
            if exc.status_code == 404:
                return self.connect(empresa)
            session.state = SessionState.ERROR
            session.last_error = str(exc)
            self._persist(session, update_fields=['state', 'last_error', 'updated_at'])
        except ProviderUnavailable as exc:
            return self._connection_failed(session, exc)
        return session

    def _connection_failed(self, session, exc):
        session.state = SessionState.ERROR
        session.last_error = str(exc)
        self._persist(session, update_fields=['state', 'last_error', 'updated_at'])
        self._event(session, 'ERROR', str(exc))
        return session

    @session_operation
    def refresh(self, empresa):
        session = self.ensure(empresa)
        previous_state = session.state
        try:
            snapshot = self.provider.status(session.instance_name)
            self._apply(session, snapshot)
            if previous_state != session.state:
                self._event(session, 'STATE_CHANGED', f'{previous_state} → {session.state}', {
                    'previous_state': previous_state, 'state': session.state,
                })
            self._event(session, 'HEARTBEAT', 'Sessão sincronizada.', {'ping_ms': snapshot.ping_ms})
        except ProviderUnavailable as exc:
            session.state = SessionState.ERROR
            session.last_error = str(exc)
            self._persist(session, update_fields=['state', 'last_error', 'updated_at'])
        return session

    @session_operation
    def reconnect(self, empresa):
        session = self.ensure(empresa)
        session.state = SessionState.RECONNECTING
        session.reconnect_attempts += 1
        self._persist(session, update_fields=['state', 'reconnect_attempts', 'updated_at'])
        try:
            snapshot = self.provider.status(session.instance_name)
            if snapshot.state != SessionState.CONNECTED:
                snapshot = self.provider.reconnect(session.instance_name)
            if snapshot.state not in {SessionState.CONNECTED, SessionState.CONNECTING} and not snapshot.qr_code:
                snapshot = self.provider.qr_code(session.instance_name)
            self._apply(session, snapshot, preserve_existing=False)
            self._event(session, 'RECONNECT', 'Reconexão solicitada.')
        except EvolutionRequestError as exc:
            if exc.status_code == 404:
                try:
                    snapshot = self._provision_missing(session)
                    self._apply(session, snapshot, preserve_existing=False)
                    self._event(session, 'QR_GENERATED', 'Instância ausente recriada durante reconexão.')
                    return session
                except ProviderUnavailable as create_error:
                    return self._connection_failed(session, create_error)
            return self._connection_failed(session, exc)
        except ProviderUnavailable as exc:
            session.state = SessionState.ERROR
            session.last_error = str(exc)
            self._persist(session, update_fields=['state', 'last_error', 'updated_at'])
        return session

    @session_operation
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
            logger.info('whatsapp.session.remote_delete_requested company_id=%s instance=%s', empresa.pk, instance_name)
            logger.info('whatsapp.session.delete_succeeded company_id=%s instance=%s', empresa.pk, instance_name)
        except EvolutionRequestError as exc:
            if exc.status_code != 404:
                return self._reset_failed(session, empresa, instance_name, exc)
            logger.info(
                'whatsapp.session.delete_tolerated company_id=%s instance=%s status_code=404',
                empresa.pk, instance_name,
            )
        except ProviderUnavailable as exc:
            return self._reset_failed(session, empresa, instance_name, exc)

        try:
            self._wait_until_deleted(session)
        except ProviderUnavailable as exc:
            return self._reset_failed(session, empresa, instance_name, exc)

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
        self._persist(session)
        self._event(session, 'SESSION_CLEARED', 'Sessão local invalidada para recriação.')

        try:
            fresh = self._provision_missing(session)
            logger.info(
                'whatsapp.session.webhook_configured company_id=%s instance=%s',
                empresa.pk, instance_name,
            )
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
        self._persist(session, update_fields=['state', 'qr_code', 'last_error', 'updated_at'])
        self._event(session, 'ERROR', str(exc), {'operation': 'session_reset'})
        logger.error(
            'whatsapp.session.reset_failed company_id=%s instance=%s type=%s',
            empresa.pk, instance_name, type(exc).__name__,
        )
        return session

    def _apply(self, session, snapshot, preserve_existing=True):
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
        self._persist(session)
        if snapshot.state == SessionState.CONNECTED:
            from core.services.ai.activation import auto_enable_company_ai
            auto_enable_company_ai(session.empresa_id)
