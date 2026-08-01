from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = 'Valida prontidão da release sem alterar dados.'

    def handle(self, *args, **options):
        call_command('check', deploy=not settings.DEBUG)
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            raise CommandError(f'{len(pending)} migração(ões) pendente(s).')
        checks = {
            'debug_disabled': not settings.DEBUG,
            'secret_not_development': 'development-only' not in settings.SECRET_KEY,
            'allowed_hosts': bool(settings.ALLOWED_HOSTS),
            'public_url': bool(settings.PUBLIC_BASE_URL),
            'database': connection.vendor in {'postgresql', 'sqlite'},
        }
        for name, ok in checks.items():
            self.stdout.write(f'{"OK" if ok else "WARN"} {name}')
        if not settings.DEBUG and not all(checks.values()):
            raise CommandError('Configuração de produção incompleta.')
        self.stdout.write(self.style.SUCCESS(f'Release {settings.APP_VERSION} pronta.'))
