from core.access import company_for_user
from core.models import WhatsAppSession
from core.public_routes import PUBLIC_HTML_URL_NAMES


def system_header(request):
    if not request.user.is_authenticated:
        return {}
    resolver_match = getattr(request, 'resolver_match', None)
    if resolver_match and resolver_match.url_name in PUBLIC_HTML_URL_NAMES:
        # Páginas públicas nunca recebem estado interno ou financeiro.
        return {}
    empresa = company_for_user(request.user)
    if empresa is None:
        return {'system_header_company': None, 'system_header_session': None, 'system_header_ai_count': 0}
    session = WhatsAppSession.objects.filter(empresa=empresa).first()
    ai_count = 1 if hasattr(empresa, 'prompt_profile') else 0
    from core.services.entitlements import EntitlementService
    subscription = EntitlementService.subscription(empresa)
    return {
        'system_header_company': empresa,
        'system_header_session': session,
        'system_header_ai_count': ai_count,
        'system_subscription': subscription,
        'system_subscription_grace': bool(subscription and subscription.status == subscription.Status.GRACE and subscription.has_access),
    }
