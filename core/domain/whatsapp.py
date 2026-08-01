from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class SessionState(StrEnum):
    OFFLINE = 'OFFLINE'
    INITIALIZING = 'INITIALIZING'
    WAITING_QR = 'WAITING_QR'
    CONNECTING = 'CONNECTING'
    CONNECTED = 'CONNECTED'
    ERROR = 'ERROR'
    RECONNECTING = 'RECONNECTING'


@dataclass(frozen=True)
class SessionSnapshot:
    state: SessionState
    qr_code: str = ''
    phone_number: str = ''
    device_name: str = ''
    ping_ms: int | None = None
    error: str = ''


class WhatsAppWebProvider(Protocol):
    def create(self, instance_name: str) -> SessionSnapshot: ...
    def status(self, instance_name: str) -> SessionSnapshot: ...
    def reconnect(self, instance_name: str) -> SessionSnapshot: ...
    def logout(self, instance_name: str) -> None: ...
    def health(self) -> bool: ...
