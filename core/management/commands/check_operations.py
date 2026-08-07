from django.core.management.base import BaseCommand

from core.services.observability import run_operational_checks


class Command(BaseCommand):
    help = 'Verifica banco, fila, Meta e IA e gera alertas operacionais.'

    def handle(self, *args, **options):
        alerts = run_operational_checks()
        from core.models import AsyncJob
        counts = {
            status.lower(): AsyncJob.objects.filter(queue='whatsapp', status=status).count()
            for status in (
                AsyncJob.Status.PENDING, AsyncJob.Status.PROCESSING,
                AsyncJob.Status.RETRY, AsyncJob.Status.DEAD,
            )
        }
        self.stdout.write(
            'Fila whatsapp: ' + ', '.join(f'{key}={value}' for key, value in counts.items())
        )
        self.stdout.write(f'{len(alerts)} alerta(s) operacional(is).')
