from django.core.management.base import BaseCommand

from core.services.observability import run_operational_checks


class Command(BaseCommand):
    help = 'Verifica banco, fila, Meta e IA e gera alertas operacionais.'

    def handle(self, *args, **options):
        alerts = run_operational_checks()
        self.stdout.write(f'{len(alerts)} alerta(s) operacional(is).')
