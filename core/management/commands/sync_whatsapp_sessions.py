from django.core.management.base import BaseCommand

from core.application.whatsapp_service import WhatsAppSessionService
from core.models import WhatsAppSession


class Command(BaseCommand):
    help = 'Executa heartbeat e tenta reconectar sessões WhatsApp Web.'

    def add_arguments(self, parser):
        parser.add_argument('--reconnect', action='store_true')

    def handle(self, *args, **options):
        service = WhatsAppSessionService()
        checked = reconnected = 0
        for session in WhatsAppSession.objects.select_related('empresa'):
            checked += 1
            refreshed = service.refresh(session.empresa)
            if (
                options['reconnect']
                and refreshed.state in {'ERROR', 'OFFLINE'}
                and refreshed.reconnect_attempts < 5
            ):
                service.reconnect(session.empresa)
                reconnected += 1
        self.stdout.write(self.style.SUCCESS(
            f'Heartbeat concluído: {checked} sessões; {reconnected} reconexões.',
        ))
