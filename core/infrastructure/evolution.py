import base64
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

from core.domain.exceptions import ProviderUnavailable
from core.domain.whatsapp import SessionSnapshot, SessionState


STATE_MAP = {
    'open': SessionState.CONNECTED,
    'connected': SessionState.CONNECTED,
    'connecting': SessionState.CONNECTING,
    'close': SessionState.OFFLINE,
    'disconnected': SessionState.OFFLINE,
}


class EvolutionAPIProvider:
    """Adapter isolating all Evolution API HTTP details from the application."""

    def __init__(self, base_url=None, api_key=None):
        self.base_url = (base_url or settings.EVOLUTION_API_URL).rstrip('/')
        self.api_key = api_key or settings.EVOLUTION_API_KEY

    def _request(self, method, path, payload=None):
        if not self.base_url or not self.api_key:
            raise ProviderUnavailable('Evolution API não configurada.')
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            f'{self.base_url}{path}', data=body, method=method,
            headers={'apikey': self.api_key, 'Content-Type': 'application/json'},
        )
        started = time.monotonic()
        try:
            with urlopen(request, timeout=12) as response:
                result = json.loads(response.read() or b'{}')
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable('Falha de comunicação com Evolution API.') from exc
        result['_ping_ms'] = round((time.monotonic() - started) * 1000)
        return result

    @staticmethod
    def _snapshot(data, fallback=SessionState.OFFLINE):
        instance = data.get('instance', data)
        raw_state = instance.get('state') or instance.get('status') or ''
        qr = data.get('base64') or data.get('qrcode', {}).get('base64') or ''
        if qr and not qr.startswith('data:'):
            qr = f'data:image/png;base64,{qr}'
        return SessionSnapshot(
            state=STATE_MAP.get(str(raw_state).lower(), SessionState.WAITING_QR if qr else fallback),
            qr_code=qr,
            phone_number=instance.get('ownerJid', '').split('@')[0],
            device_name=instance.get('profileName', ''),
            ping_ms=data.get('_ping_ms'),
        )

    def create(self, instance_name):
        data = self._request('POST', '/instance/create', {
            'instanceName': instance_name, 'qrcode': True,
            'integration': 'WHATSAPP-BAILEYS',
        })
        return self._snapshot(data, SessionState.INITIALIZING)

    def status(self, instance_name):
        return self._snapshot(self._request('GET', f'/instance/connectionState/{instance_name}'))

    def qr_code(self, instance_name):
        return self._snapshot(self._request('GET', f'/instance/connect/{instance_name}'), SessionState.WAITING_QR)

    def reconnect(self, instance_name):
        return self.qr_code(instance_name)

    def logout(self, instance_name):
        self._request('DELETE', f'/instance/logout/{instance_name}')

    def delete(self, instance_name):
        self._request('DELETE', f'/instance/delete/{instance_name}')

    def health(self):
        try:
            self._request('GET', '/instance/fetchInstances')
            return True
        except ProviderUnavailable:
            return False
