import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

from core.domain.exceptions import ProviderUnavailable
from core.domain.whatsapp import SessionSnapshot, SessionState


logger = logging.getLogger('evolution.provider')

STATE_MAP = {
    'open': SessionState.CONNECTED,
    'connected': SessionState.CONNECTED,
    'connecting': SessionState.CONNECTING,
    'close': SessionState.OFFLINE,
    'disconnected': SessionState.OFFLINE,
}


@dataclass(frozen=True)
class EvolutionSendResult:
    message_id: str


class EvolutionRequestError(ProviderUnavailable):
    """Evolution failure with safe HTTP context for service orchestration."""

    def __init__(self, message, *, status_code=None, path=''):
        super().__init__(message)
        self.status_code = status_code
        self.path = path


class EvolutionProvider:
    """Único ponto de comunicação HTTP com a Evolution API v2."""

    timeout = 12
    max_attempts = 3
    circuit_failure_threshold = 5
    circuit_open_seconds = 30
    _lock = threading.Lock()
    _failures = 0
    _circuit_opened_at = 0.0

    def __init__(self, base_url=None, api_key=None):
        self.base_url = (base_url or settings.EVOLUTION_API_URL).rstrip('/')
        self.api_key = api_key or settings.EVOLUTION_API_KEY

    @classmethod
    def _circuit_allows_request(cls):
        with cls._lock:
            if cls._failures < cls.circuit_failure_threshold:
                return True
            if time.monotonic() - cls._circuit_opened_at >= cls.circuit_open_seconds:
                cls._failures = 0
                return True
            return False

    @classmethod
    def _record_success(cls):
        with cls._lock:
            cls._failures = 0

    @classmethod
    def _record_failure(cls):
        with cls._lock:
            cls._failures += 1
            if cls._failures >= cls.circuit_failure_threshold:
                cls._circuit_opened_at = time.monotonic()

    def _request(self, method, path, payload=None):
        if not self.base_url or not self.api_key:
            raise ProviderUnavailable('Evolution API não configurada.')
        if not self._circuit_allows_request():
            raise ProviderUnavailable('Evolution API temporariamente indisponível.')
        body = json.dumps(payload).encode('utf-8') if payload is not None else None
        last_error = None
        status_code = None
        for attempt in range(self.max_attempts):
            request = Request(
                f'{self.base_url}{path}', data=body, method=method,
                headers={'apikey': self.api_key, 'Content-Type': 'application/json'},
            )
            started = time.monotonic()
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read() or b'{}'
                    result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError('invalid provider response')
                result['_ping_ms'] = round((time.monotonic() - started) * 1000)
                self._record_success()
                return result
            except HTTPError as exc:
                last_error = exc
                status_code = exc.code
                if exc.code < 500 and exc.code != 429:
                    break
            except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
            if attempt + 1 < self.max_attempts:
                time.sleep(0.2 * (2 ** attempt))
        self._record_failure()
        logger.warning(
            'evolution.request.failed path=%s method=%s status_code=%s type=%s',
            path, method, status_code, type(last_error).__name__,
        )
        raise EvolutionRequestError(
            'Falha de comunicação com Evolution API.', status_code=status_code, path=path,
        ) from last_error

    @staticmethod
    def _snapshot(data, fallback=SessionState.OFFLINE):
        instance = data.get('instance', data)
        raw_state = instance.get('state') or instance.get('status') or ''
        qr_data = data.get('base64') or data.get('qrcode', {}).get('base64') or ''
        if qr_data and not qr_data.startswith('data:'):
            qr_data = f'data:image/png;base64,{qr_data}'
        return SessionSnapshot(
            state=STATE_MAP.get(str(raw_state).lower(), SessionState.WAITING_QR if qr_data else fallback),
            qr_code=qr_data,
            phone_number=str(instance.get('ownerJid') or '').split('@')[0],
            device_name=str(instance.get('profileName') or ''),
            ping_ms=data.get('_ping_ms'),
        )

    @staticmethod
    def _webhook_config():
        if not settings.EVOLUTION_WEBHOOK_SECRET:
            raise ProviderUnavailable('EVOLUTION_WEBHOOK_SECRET não configurado.')
        webhook_url = settings.EVOLUTION_WEBHOOK_URL or (
            f"{settings.PUBLIC_BASE_URL.rstrip('/')}/webhooks/evolution/"
            if settings.PUBLIC_BASE_URL else ''
        )
        if not webhook_url:
            raise ProviderUnavailable('EVOLUTION_WEBHOOK_URL não configurada.')
        return {
            'enabled': True,
            'url': webhook_url,
            'webhook_by_events': False,
            'webhook_base64': False,
            'headers': {'x-iaatende-secret': settings.EVOLUTION_WEBHOOK_SECRET},
            'events': [
                'QRCODE_UPDATED', 'CONNECTION_UPDATE', 'MESSAGES_UPSERT',
                'MESSAGES_UPDATE', 'SEND_MESSAGE',
            ],
        }

    def create(self, instance_name):
        return self._snapshot(self._request('POST', '/instance/create', {
            'instanceName': instance_name, 'qrcode': True,
            'integration': 'WHATSAPP-BAILEYS',
            'webhook': self._webhook_config(),
        }), SessionState.INITIALIZING)

    def configure_webhook(self, instance_name):
        self._request('POST', f'/webhook/set/{instance_name}', {
            'webhook': self._webhook_config(),
        })

    def status(self, instance_name):
        return self._snapshot(self._request('GET', f'/instance/connectionState/{instance_name}'))

    def qr_code(self, instance_name):
        return self._snapshot(self._request('GET', f'/instance/connect/{instance_name}'), SessionState.WAITING_QR)

    def connect(self, instance_name):
        return self.qr_code(instance_name)

    def reconnect(self, instance_name):
        return self.restart(instance_name)

    def restart(self, instance_name):
        self._request('PUT', f'/instance/restart/{instance_name}')
        return self.qr_code(instance_name)

    def logout(self, instance_name):
        self._request('DELETE', f'/instance/logout/{instance_name}')

    def disconnect(self, instance_name):
        return self.logout(instance_name)

    def delete(self, instance_name):
        self._request('DELETE', f'/instance/delete/{instance_name}')

    def renew_session(self, instance_name):
        return self.restart(instance_name)

    def send_text(self, instance_name, number, text):
        result = self._request('POST', f'/message/sendText/{instance_name}', {
            'number': str(number), 'text': str(text),
        })
        key = result.get('key') or {}
        message_id = str(key.get('id') or result.get('messageId') or result.get('id') or '')
        if not message_id:
            raise ProviderUnavailable('Evolution API não retornou o ID da mensagem.')
        return EvolutionSendResult(message_id=message_id)

    def mark_as_read(self, instance_name, message_id, remote_jid):
        self._request('POST', f'/chat/markMessageAsRead/{instance_name}', {
            'readMessages': [{'remoteJid': remote_jid, 'fromMe': False, 'id': message_id}],
        })

    def download_media(self, instance_name, message):
        result = self._request('POST', f'/chat/getBase64FromMediaMessage/{instance_name}', {
            'message': message, 'convertToMp4': False,
        })
        encoded = result.get('base64') or ''
        try:
            return base64.b64decode(encoded, validate=True) if encoded else b''
        except (ValueError, TypeError):
            raise ProviderUnavailable('Mídia inválida retornada pela Evolution API.')

    @staticmethod
    def validate_webhook(raw_body, headers):
        secret = settings.EVOLUTION_WEBHOOK_SECRET
        if not secret:
            raise ProviderUnavailable('EVOLUTION_WEBHOOK_SECRET não configurado.')
        supplied = headers.get('x-iaatende-secret', '')
        authorization = headers.get('authorization', '')
        signature = headers.get('x-evolution-signature', '')
        if hmac.compare_digest(supplied, secret) or hmac.compare_digest(authorization, f'Bearer {secret}'):
            return True
        if signature.startswith('sha256='):
            expected = hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(signature[7:], expected):
                return True
        raise ProviderUnavailable('Assinatura do webhook inválida.')

    def health(self):
        try:
            self._request('GET', '/instance/fetchInstances')
            return True
        except ProviderUnavailable:
            return False
