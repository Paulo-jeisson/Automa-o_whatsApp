from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    Atendimento,
    EmpresaCliente,
    FluxoAtendimento,
    dados_padrao_fluxo,
)


class Command(BaseCommand):
    help = 'Cria ou atualiza uma conta de demonstração completa do IAATENDE 2.0.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='demo', help='Usuário da conta demo.')
        parser.add_argument('--password', default='demo12345', help='Senha da conta demo.')

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=options['username'],
            defaults={'first_name': 'Cliente', 'last_name': 'Demonstração'},
        )
        if created or not user.has_usable_password():
            user.set_password(options['password'])
            user.save(update_fields=['password'])

        empresa, _ = EmpresaCliente.objects.update_or_create(
            usuario=user,
            defaults={
                'nome': 'Estacionamento Central Demo',
                'segmento': EmpresaCliente.SEGMENTO_ESTACIONAMENTO,
                'nome_dono': 'Paulo Demo',
                'whatsapp_dono': '5511999999999',
                'endereco': 'Rua das Flores, 100 - Centro',
                'horario_funcionamento': 'Segunda a sábado, das 7h às 22h',
                'mensagem_inicial': 'Olá! Como podemos ajudar com seu veículo?',
                'ativa': True,
            },
        )
        FluxoAtendimento.objects.update_or_create(
            empresa=empresa,
            defaults=dados_padrao_fluxo(empresa),
        )

        examples = [
            ('Marina Souza', '11988880001', 'Saber preco', 'Quer saber o valor da diária.', '', Atendimento.STATUS_NOVO),
            ('Rafael Costa', '11988880002', 'Ver disponibilidade de vaga', 'Precisa de vaga para hoje.', 'Carro pequeno.', Atendimento.STATUS_NOVO),
            ('Beatriz Lima', '11988880003', 'Falar com atendente', 'Quer contratar uma mensalidade.', '', Atendimento.STATUS_EM_ANDAMENTO),
            ('João Santos', '11988880004', 'Informar entrada de veiculo', 'Entrada de veículo para pernoite.', 'Placa DEM-1020.', Atendimento.STATUS_EM_ANDAMENTO),
            ('Camila Alves', '11988880005', 'Saber preco', 'Consultou o valor por hora.', '', Atendimento.STATUS_FINALIZADO),
            ('Lucas Rocha', '11988880006', 'Ver disponibilidade de vaga', 'Reservou uma vaga para a tarde.', 'SUV.', Atendimento.STATUS_FINALIZADO),
        ]
        for name, phone, option, need, note, status in examples:
            attendance, _ = Atendimento.objects.update_or_create(
                empresa=empresa,
                telefone_cliente=phone,
                defaults={
                    'nome_cliente': name,
                    'opcao_escolhida': option,
                    'necessidade': need,
                    'observacao': note,
                    'status': status,
                },
            )
            if status == Atendimento.STATUS_FINALIZADO and attendance.avisado_em is None:
                attendance.avisado_em = timezone.now()
                attendance.save(update_fields=['avisado_em'])

        self.stdout.write(self.style.SUCCESS(
            f'Demonstração pronta: usuário "{user.username}", empresa "{empresa.nome}" '
            f'e {empresa.atendimentos.count()} atendimentos.'
        ))
