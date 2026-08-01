from django.core.management.base import BaseCommand

from core.models import EmpresaCliente
from core.services.privacy import PrivacyService


class Command(BaseCommand):
    help = 'Aplica as políticas de retenção de dados de cada empresa.'

    def handle(self, *args, **options):
        affected = sum(PrivacyService.apply_retention(company) for company in EmpresaCliente.objects.iterator())
        self.stdout.write(self.style.SUCCESS(f'{affected} registro(s) tratado(s).'))
