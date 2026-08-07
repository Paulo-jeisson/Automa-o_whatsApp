from core.services.entitlements import EntitlementService

from .conversation import AIConversationService


class CompanyAIGateway:
    """The only production facade allowed to initiate a company AI request."""

    def __init__(self, service=None):
        self.service = service or AIConversationService()

    def reply(self, *, inbound_message):
        EntitlementService.require_company_access(inbound_message.empresa)
        return self.service.reply(
            inbound_message=inbound_message, user_input=inbound_message.ai_text,
        )
