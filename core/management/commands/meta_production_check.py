from django.core.management.base import BaseCommand, CommandError

from core.models import EmpresaCliente
from core.services.meta_readiness import meta_production_readiness


class Command(BaseCommand):
    help = 'Audita a prontidão Meta de uma empresa sem exibir tokens.'

    def add_arguments(self, parser):
        parser.add_argument('company_id', type=int)

    def handle(self, *args, **options):
        try:
            company = EmpresaCliente.objects.get(pk=options['company_id'])
        except EmpresaCliente.DoesNotExist as error:
            raise CommandError('Empresa não encontrada.') from error
        report = meta_production_readiness(company)
        for item in report['items']:
            self.stdout.write(f'[{"OK" if item.ready else "PENDENTE"}] {item.label}: {item.detail}')
        if not report['ready']:
            raise CommandError('A integração ainda não está pronta para produção.')
        self.stdout.write(self.style.SUCCESS('Integração Meta pronta para produção.'))
