import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.services.queue import process_next, recover_expired_jobs


class Command(BaseCommand):
    help = 'Executa a fila persistentemente com shutdown gracioso e recuperação de leases.'

    def add_arguments(self, parser):
        parser.add_argument('--queue', default='default')

    def handle(self, *args, **options):
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        queue = options['queue']
        last_recovery = 0.0
        self.stdout.write(f'Worker iniciado para queue={queue}.')
        while not stopping:
            now = time.monotonic()
            if now - last_recovery >= settings.TASK_QUEUE_HEARTBEAT_SECONDS:
                recover_expired_jobs(queue=queue)
                last_recovery = now
            close_old_connections()
            if process_next(queue=queue) is None:
                time.sleep(settings.TASK_QUEUE_IDLE_SECONDS)
        close_old_connections()
        self.stdout.write('Worker encerrado com segurança.')
