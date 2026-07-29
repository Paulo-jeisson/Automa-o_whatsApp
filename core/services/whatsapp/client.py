import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

from .exceptions import WhatsAppAPIError, WhatsAppProviderError


logger = logging.getLogger('whatsapp.api')


@dataclass(frozen=True)
class SendTextResult:
    message_id: str


class WhatsAppCloudClient:
    """Comunicação de saída com a Cloud API oficial, sem retries automáticos."""

    def __init__(
        self,
        phone_number_id='',
        access_token=None,
        api_version=None,
        timeout=10,
    ):
        self.phone_number_id = str(phone_number_id).strip()
        self.access_token = (
            settings.META_ACCESS_TOKEN.strip()
            if access_token is None
            else str(access_token).strip()
        )
        self.api_version = api_version or settings.META_GRAPH_API_VERSION
        self.timeout = timeout

    def send_text(self, to, text):
        self._validate_configuration(require_phone_number_id=True)
        recipient = re.sub(r'\D', '', str(to or ''))
        if not recipient:
            raise WhatsAppProviderError('Destinatário do WhatsApp inválido.')
        body = str(text or '').strip()
        if not body:
            raise WhatsAppProviderError('A mensagem de texto não pode ser vazia.')
        if len(body) > 4096:
            raise WhatsAppProviderError('A mensagem excede o limite de 4096 caracteres.')

        data = self._request_json(
            method='POST',
            path=f'{self.phone_number_id}/messages',
            payload={
                'messaging_product': 'whatsapp',
                'recipient_type': 'individual',
                'to': recipient,
                'type': 'text',
                'text': {'preview_url': False, 'body': body},
            },
        )
        messages = data.get('messages')
        message_id = (
            str(messages[0].get('id', ''))
            if isinstance(messages, list) and messages and isinstance(messages[0], dict)
            else ''
        )
        if not message_id:
            raise WhatsAppAPIError('A Meta não retornou o ID da mensagem.')
        return SendTextResult(message_id=message_id)

    def send_template(self, to, *, template_name, language_code, parameters):
        self._validate_configuration(require_phone_number_id=True)
        recipient = re.sub(r'\D', '', str(to or ''))
        if not recipient or not re.fullmatch(r'[a-z0-9_]+', template_name):
            raise WhatsAppProviderError('Template ou destinatário inválido.')
        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': recipient,
            'type': 'template',
            'template': {
                'name': template_name,
                'language': {'code': language_code},
                'components': [{
                    'type': 'body',
                    'parameters': [
                        {'type': 'text', 'text': str(value)[:1024]}
                        for value in parameters
                    ],
                }],
            },
        }
        data = self._request_json(
            method='POST', path=f'{self.phone_number_id}/messages', payload=payload,
        )
        messages = data.get('messages')
        message_id = str(messages[0].get('id', '')) if isinstance(messages, list) and messages else ''
        if not message_id:
            raise WhatsAppAPIError('A Meta não retornou o ID da mensagem.')
        return SendTextResult(message_id=message_id)

    def mark_as_read(self, message_id):
        self._validate_configuration(require_phone_number_id=True)
        if not message_id:
            raise WhatsAppProviderError('ID da mensagem não informado.')
        self._request_json(
            method='POST',
            path=f'{self.phone_number_id}/messages',
            payload={
                'messaging_product': 'whatsapp',
                'status': 'read',
                'message_id': str(message_id),
            },
        )
        return True

    def test_configuration(self, phone_number_id=None):
        target_phone_number_id = str(phone_number_id or self.phone_number_id)
        self._validate_configuration(phone_number_id=target_phone_number_id)
        data = self._request_json(
            method='GET',
            path=target_phone_number_id,
            query={'fields': 'id,display_phone_number,verified_name'},
        )
        if str(data.get('id', '')) != target_phone_number_id:
            raise WhatsAppProviderError('A Meta retornou uma configuração inesperada.')
        return data

    def _validate_configuration(self, require_phone_number_id=False, phone_number_id=None):
        if not self.access_token:
            raise WhatsAppProviderError('A credencial do WhatsApp não está configurada.')
        if not re.fullmatch(r'v\d+\.\d+', self.api_version):
            raise WhatsAppProviderError('Versão da Graph API inválida.')
        target = self.phone_number_id if phone_number_id is None else phone_number_id
        if (require_phone_number_id or phone_number_id is not None) and not str(target).isdigit():
            raise WhatsAppProviderError('Phone Number ID inválido.')

    def _request_json(self, *, method, path, payload=None, query=None):
        url = f'https://graph.facebook.com/{self.api_version}/{path}'
        if query:
            url = f'{url}?{urlencode(query)}'
        body = json.dumps(payload).encode('utf-8') if payload is not None else None
        headers = {'Authorization': f'Bearer {self.access_token}'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(url, data=body, headers=headers, method=method)

        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except HTTPError as error:
            details = _parse_meta_error(error, access_token=self.access_token)
            logger.warning(
                'whatsapp.api.rejected status=%s type=%s code=%s subcode=%s fbtrace_id=%s',
                error.code,
                details['error_type'],
                details['error_code'],
                details['error_subcode'],
                details['fbtrace_id'],
            )
            raise WhatsAppAPIError(
                'A Meta rejeitou a requisição.',
                status_code=error.code,
                **details,
            ) from error
        except (URLError, TimeoutError) as error:
            raise WhatsAppAPIError('A Meta não respondeu dentro do esperado.') from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WhatsAppAPIError('A Meta retornou uma resposta inválida.') from error


def _parse_meta_error(error, *, access_token=''):
    empty = {
        'error_code': '',
        'error_subcode': '',
        'error_type': '',
        'fbtrace_id': '',
        'meta_message': '',
    }
    try:
        raw = error.read()
        data = json.loads(raw)
        error_data = data.get('error', {})
        if not isinstance(error_data, dict):
            return empty
        message = str(error_data.get('message', ''))
        if access_token:
            message = message.replace(str(access_token), '[REDACTED]')
        message = re.sub(
            r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+',
            'Bearer [REDACTED]',
            message,
        )
        return {
            'error_code': error_data.get('code', ''),
            'error_subcode': error_data.get('error_subcode', ''),
            'error_type': error_data.get('type', ''),
            'fbtrace_id': error_data.get('fbtrace_id', ''),
            'meta_message': message,
        }
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return empty
