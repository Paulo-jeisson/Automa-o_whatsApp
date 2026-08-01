class DomainError(Exception):
    """Erro esperado de regra de negócio."""


class ProviderUnavailable(DomainError):
    """Provider externo indisponível."""


class InvalidSessionTransition(DomainError):
    """Transição inválida de sessão WhatsApp."""
