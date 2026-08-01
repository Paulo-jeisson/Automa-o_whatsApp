from django.core.management.base import BaseCommand

from core.services.queue import process_next


class Command(BaseCommand):
    help = 'Processa a fila persistente do ZapFluxo.'

    def add_arguments(self, parser):
        parser.add_argument('--queue', default='default')
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        processed = 0
        while processed < options['limit'] and process_next(queue=options['queue']):
            processed += 1
        self.stdout.write(self.style.SUCCESS(f'{processed} job(s) processado(s).'))
