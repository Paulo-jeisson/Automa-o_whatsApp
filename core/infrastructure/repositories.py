from core.models import AIPromptProfile, WhatsAppSession


class WhatsAppSessionRepository:
    def for_company(self, empresa):
        return WhatsAppSession.objects.filter(empresa=empresa).first()

    def save(self, session, fields=None):
        session.save(update_fields=fields)
        return session


class PromptProfileRepository:
    def for_company(self, empresa):
        return AIPromptProfile.objects.filter(empresa=empresa).first()
