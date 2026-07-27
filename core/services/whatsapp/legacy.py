from dataclasses import dataclass
from urllib.parse import quote

from django.conf import settings

from .exceptions import WhatsAppProviderError


@dataclass(frozen=True)
class WhatsAppResult:
    provider: str
    redirect_url: str = ''


class WaMeProvider:
    name = 'wa.me'

    def send_message(self, phone, message):
        normalized_phone = ''.join(character for character in phone if character.isdigit())
        if not normalized_phone:
            raise WhatsAppProviderError('O telefone do WhatsApp não foi informado.')
        return WhatsAppResult(
            provider=self.name,
            redirect_url=f'https://wa.me/{normalized_phone}?text={quote(message)}',
        )


class OfficialApiProvider:
    name = 'official'

    def send_message(self, phone, message):
        raise WhatsAppProviderError(
            'O envio pela Cloud API será habilitado em uma fase posterior.'
        )


PROVIDERS = {
    WaMeProvider.name: WaMeProvider,
    OfficialApiProvider.name: OfficialApiProvider,
}


def get_provider(provider_name=None):
    name = (provider_name or settings.WHATSAPP_PROVIDER).strip().lower()
    provider_class = PROVIDERS.get(name)
    if provider_class is None:
        valid_providers = ', '.join(PROVIDERS)
        raise WhatsAppProviderError(
            f'Provider "{name}" inválido. Use um destes valores: {valid_providers}.'
        )
    return provider_class()


def build_attendance_message(attendance):
    lines = [
        'Novo atendimento recebido:',
        f'Cliente: {attendance.nome_cliente}',
        f'Telefone: {attendance.telefone_cliente}',
        f'Opção: {attendance.opcao_escolhida}',
        f'Segmento: {attendance.empresa.get_segmento_display()}',
        f'Necessidade: {attendance.necessidade}',
    ]
    if attendance.observacao:
        lines.append(f'Observação: {attendance.observacao}')
    return '\n'.join(lines)


def notify_attendance(attendance, provider_name=None):
    provider = get_provider(provider_name)
    message = build_attendance_message(attendance)
    return provider.send_message(attendance.empresa.whatsapp_dono, message)


def build_contact_url(phone, message):
    provider = get_provider()
    if not isinstance(provider, WaMeProvider) or not phone:
        return ''
    return provider.send_message(phone, message).redirect_url
