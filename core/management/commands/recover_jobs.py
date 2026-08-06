from django.core.management.base import BaseCommand

from core.services.queue import recover_expired_jobs


class Command(BaseCommand):
    help = 'Recupera jobs cujo lease PROCESSING expirou.'

    def add_arguments(self, parser):
        parser.add_argument('--queue', default=None)

    def handle(self, *args, **options):
        recovered, dead = recover_expired_jobs(queue=options['queue'])
        self.stdout.write(self.style.SUCCESS(
            f'{recovered} job(s) recuperado(s); {dead} movido(s) para dead letter.',
        ))
