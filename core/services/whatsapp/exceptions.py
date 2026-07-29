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

    def __init__(
        self,
        message,
        *,
        status_code=None,
        error_code='',
        error_subcode='',
        error_type='',
        fbtrace_id='',
        meta_message='',
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = str(error_code or '')[:32]
        self.error_subcode = str(error_subcode or '')[:32]
        self.error_type = str(error_type or '')[:80]
        self.fbtrace_id = str(fbtrace_id or '')[:128]
        self.meta_message = str(meta_message or '')[:500]
