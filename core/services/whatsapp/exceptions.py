class WhatsAppError(Exception):
    """Erro base da integração oficial do WhatsApp."""


class WhatsAppProviderError(WhatsAppError):
    """Erro de configuração ou operação de um provider."""


class InvalidWebhookSignature(WhatsAppError):
    """A assinatura do webhook não corresponde ao App Secret."""


class InvalidWebhookPayload(WhatsAppError):
    """O corpo recebido não representa um payload JSON suportado."""


class WhatsAppAPIError(WhatsAppError):
    """Falha sanitizada ao comunicar com a Graph API."""

    def __init__(self, message, *, status_code=None, error_code=''):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = str(error_code or '')[:32]
