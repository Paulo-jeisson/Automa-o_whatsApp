from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    label: str
    ready: bool
    detail: str


def meta_production_readiness(empresa):
    integration = getattr(empresa, 'whatsapp_integration', None)
    base_url = urlparse(settings.PUBLIC_BASE_URL)
    items = [
        ReadinessItem('app', 'Aplicativo Meta configurado', bool(settings.META_APP_ID and settings.META_APP_SECRET), 'META_APP_ID e META_APP_SECRET'),
        ReadinessItem('embedded_signup', 'Embedded Signup configurado', bool(settings.META_EMBEDDED_SIGNUP_CONFIG_ID), 'Configuration ID'),
        ReadinessItem('webhook_https', 'Webhook público HTTPS', base_url.scheme == 'https' and bool(base_url.netloc), f'{settings.PUBLIC_BASE_URL}/webhooks/whatsapp/'),
        ReadinessItem('webhook_security', 'Webhook assinado', bool(settings.META_VERIFY_TOKEN and settings.META_APP_SECRET), 'Verify token e App Secret'),
        ReadinessItem('integration', 'Número conectado', bool(integration and integration.is_connected), getattr(integration, 'display_phone_number', '') or 'Não conectado'),
        ReadinessItem('token', 'Token empresarial válido', bool(integration and integration.access_token_encrypted and integration.is_active), 'Token criptografado por tenant'),
        ReadinessItem('privacy', 'Páginas legais publicadas', bool(settings.PUBLIC_BASE_URL), '/politica-de-privacidade/ e /exclusao-de-dados/'),
    ]
    latest = empresa.meta_verifications.order_by('-verified_at').first()
    items.extend([
        ReadinessItem('inbound_real', 'Mensagem real recebida', bool(latest and latest.inbound_verified), 'Validação manual auditada'),
        ReadinessItem('outbound_real', 'Mensagem real enviada', bool(latest and latest.outbound_verified), 'Validação manual auditada'),
        ReadinessItem('tenant_isolation', 'Isolamento real validado', bool(latest and latest.tenant_isolation_verified), 'Dois números e duas empresas'),
        ReadinessItem('templates', 'Templates aprovados validados', bool(latest and latest.templates_verified), 'Meta Business Manager'),
        ReadinessItem('permissions', 'Advanced Access/permissões validados', bool(latest and latest.permissions_verified), 'Meta App Review'),
    ])
    return {'ready': all(item.ready for item in items), 'items': items}
