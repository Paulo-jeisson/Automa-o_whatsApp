import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from .exceptions import WhatsAppProviderError


def _fernet():
    configured = settings.WHATSAPP_TOKEN_ENCRYPTION_KEY.strip().encode()
    if configured:
        try:
            return Fernet(configured)
        except (ValueError, TypeError) as error:
            raise ImproperlyConfigured(
                'WHATSAPP_TOKEN_ENCRYPTION_KEY deve ser uma chave Fernet válida.'
            ) from error
    if not settings.DEBUG:
        raise ImproperlyConfigured(
            'WHATSAPP_TOKEN_ENCRYPTION_KEY é obrigatória quando DEBUG=False.'
        )
    derived = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(derived)


def encrypt_token(token):
    value = str(token or '').strip()
    if not value:
        raise WhatsAppProviderError('A Meta não retornou uma credencial válida.')
    return _fernet().encrypt(value.encode()).decode()


def decrypt_token(encrypted):
    try:
        return _fernet().decrypt(str(encrypted).encode()).decode()
    except (InvalidToken, ValueError, TypeError) as error:
        raise WhatsAppProviderError('A credencial armazenada não pôde ser lida.') from error


def access_token_for(integration):
    if integration.token_expires_at and integration.token_expires_at <= timezone.now():
        integration.is_active = False
        integration.onboarding_status = integration.OnboardingStatus.EXPIRED
        integration.save(update_fields=['is_active', 'onboarding_status', 'updated_at'])
        raise WhatsAppProviderError(
            'A autorização do WhatsApp expirou. Reconecte o número nas configurações.'
        )
    if integration.access_token_encrypted:
        return integration.get_access_token()
    if integration.onboarding_status == integration.OnboardingStatus.CONNECTED:
        raise WhatsAppProviderError(
            'A credencial desta empresa está ausente. Reconecte o WhatsApp.'
        )
    legacy_token = settings.META_ACCESS_TOKEN.strip()
    if legacy_token:
        return legacy_token
    raise WhatsAppProviderError(
        'A empresa ainda não concluiu a autorização oficial do WhatsApp.'
    )
