from core.access import company_for_user
from core.models import WhatsAppSession


def system_header(request):
    if not request.user.is_authenticated:
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
