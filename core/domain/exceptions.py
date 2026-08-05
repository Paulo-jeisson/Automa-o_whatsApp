class DomainError(Exception):
    """Erro esperado de regra de negócio."""


class ProviderUnavailable(DomainError):
    """Provider externo indisponível."""


class InvalidSessionTransition(DomainError):
    """Transição inválida de sessão WhatsApp."""


class SubscriptionAccessDenied(DomainError):
    """Bloqueio comercial esperado; não deve causar retry de jobs."""
