import hashlib
import hmac

from .exceptions import InvalidWebhookSignature


SIGNATURE_PREFIX = 'sha256='


def validate_webhook_signature(raw_body, signature_header, app_secret):
    if not app_secret:
        raise InvalidWebhookSignature('App Secret não configurado.')
    if not signature_header or not signature_header.startswith(SIGNATURE_PREFIX):
        raise InvalidWebhookSignature('Assinatura ausente ou malformada.')

    received_signature = signature_header[len(SIGNATURE_PREFIX):]
    expected_signature = hmac.new(
        app_secret.encode('utf-8'),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_signature, expected_signature):
        raise InvalidWebhookSignature('Assinatura inválida.')
    return True
