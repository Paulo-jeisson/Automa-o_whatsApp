from django.core.management.base import BaseCommand

from core.application.whatsapp_service import WhatsAppSessionService
from core.models import WhatsAppSession


class Command(BaseCommand):
    help = 'Executa heartbeat e tenta reconectar sessões WhatsApp Web.'

    def handle(self, *args, **options):
        service = WhatsAppSessionService()
        checked = reconnected = 0
        for session in WhatsAppSession.objects.select_related('empresa'):
            checked += 1
            refreshed = service.refresh(session.empresa)
            if refreshed.state in {'ERROR', 'OFFLINE'} and refreshed.reconnect_attempts < 5:
                service.reconnect(session.empresa)
                reconnected += 1
        self.stdout.write(self.style.SUCCESS(
            f'Heartbeat concluído: {checked} sessões; {reconnected} reconexões.',
        ))
