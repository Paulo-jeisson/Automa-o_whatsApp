from core.models import AIPromptProfile, WhatsAppSession


class WhatsAppSessionRepository:
    def for_company(self, empresa):
        return WhatsAppSession.objects.filter(empresa=empresa).first()

    def save(self, session, fields=None):
        session.save(update_fields=fields)
        return session

    def for_instance(self, instance_name):
        return WhatsAppSession.objects.select_related('empresa').filter(
            instance_name=instance_name, empresa__ativa=True,
        ).first()

    def by_id(self, session_id):
        return WhatsAppSession.objects.select_related('empresa').get(pk=session_id)


class PromptProfileRepository:
    def for_company(self, empresa):
        return AIPromptProfile.objects.filter(empresa=empresa).first()
