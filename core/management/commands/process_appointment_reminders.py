from django.core.management.base import BaseCommand

from core.services.reminders import ReminderService


class Command(BaseCommand):
    help = 'Agenda e envia lembretes de consultas que estão no prazo.'

    def handle(self, *args, **options):
        sent = ReminderService.process_due()
        self.stdout.write(self.style.SUCCESS(f'{sent} lembrete(s) enviado(s).'))
