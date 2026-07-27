"""Integração com WhatsApp, mantendo compatibilidade com o fluxo wa.me atual."""

from .exceptions import (
    InvalidWebhookPayload,
    InvalidWebhookSignature,
    WhatsAppError,
    WhatsAppProviderError,
)
from .legacy import (
    OfficialApiProvider,
    WaMeProvider,
    WhatsAppResult,
    build_attendance_message,
    build_contact_url,
    get_provider,
    notify_attendance,
)

__all__ = [
    'InvalidWebhookPayload',
    'InvalidWebhookSignature',
    'OfficialApiProvider',
    'WaMeProvider',
    'WhatsAppError',
    'WhatsAppProviderError',
    'WhatsAppResult',
    'build_attendance_message',
    'build_contact_url',
    'get_provider',
    'notify_attendance',
]
