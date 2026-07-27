from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Atendimento, Mensagem
from core.services.whatsapp.exceptions import WhatsAppAPIError, WhatsAppProviderError
from core.services.whatsapp.outbound import send_text_for_attendance


class Command(BaseCommand):
    help = 'Envia manualmente uma mensagem controlada para o contato de um atendimento.'

    def add_arguments(self, parser):
        parser.add_argument('--atendimento', type=int, required=True)
        parser.add_argument(
            '--mensagem',
            default='Teste de integração do ZapFluxo.',
            help='Texto controlado, com no máximo 4096 caracteres.',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirma explicitamente que uma mensagem real será enviada.',
        )

    def handle(self, *args, **options):
        if not options['confirm']:
            raise CommandError('Use --confirm para autorizar explicitamente o envio real.')

        atendimento = Atendimento.objects.select_related(
            'empresa',
            'contato',
        ).filter(pk=options['atendimento']).first()
        if atendimento is None or atendimento.contato is None:
            raise CommandError('Atendimento ou contato válido não encontrado.')

        inside_service_window = Mensagem.objects.filter(
            atendimento=atendimento,
            direcao=Mensagem.DIRECAO_ENTRADA,
            criado_em__gte=timezone.now() - timedelta(hours=24),
        ).exists()
        if not inside_service_window:
            raise CommandError(
                'O atendimento não possui mensagem recebida nas últimas 24 horas. '
                'Texto livre não será enviado.'
            )

        try:
            mensagem = send_text_for_attendance(atendimento, options['mensagem'])
        except (WhatsAppAPIError, WhatsAppProviderError) as error:
            raise CommandError(f'Falha sanitizada no envio: {error}') from error

        self.stdout.write(self.style.SUCCESS(
            f'Mensagem aceita pela Meta. ID registrado: {mensagem.external_message_id}'
        ))
