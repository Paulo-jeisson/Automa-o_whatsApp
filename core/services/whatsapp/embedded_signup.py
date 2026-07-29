import json
import re
from dataclasses import dataclass
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import EmpresaCliente, WhatsAppIntegration

from .exceptions import WhatsAppAPIError, WhatsAppProviderError


@dataclass(frozen=True)
class EmbeddedSignupResult:
    integration: WhatsAppIntegration
    created: bool


class MetaGraphClient:
    def __init__(self, access_token='', timeout=15):
        self.access_token = access_token
        self.timeout = timeout
        self.version = settings.META_GRAPH_API_VERSION

    def exchange_code(self, code):
        return self._request(
            'GET', 'oauth/access_token',
            query={
                'client_id': settings.META_APP_ID,
                'client_secret': settings.META_APP_SECRET,
                'code': code,
            },
            authenticated=False,
        )

    def debug_token(self, token):
        return self._request(
            'GET', 'debug_token',
            query={'input_token': token},
            access_token=f'{settings.META_APP_ID}|{settings.META_APP_SECRET}',
        )

    def get_phone(self, phone_number_id):
        return self._request(
            'GET', phone_number_id,
            query={'fields': 'id,display_phone_number,verified_name'},
        )

    def get_waba_phones(self, waba_id):
        return self._request(
            'GET', f'{waba_id}/phone_numbers',
            query={'fields': 'id,display_phone_number,verified_name'},
        )

    def subscribe_app(self, waba_id):
        return self._request('POST', f'{waba_id}/subscribed_apps', payload={})

    def unsubscribe_app(self, waba_id):
        return self._request('DELETE', f'{waba_id}/subscribed_apps')

    def _request(
        self, method, path, *, query=None, payload=None,
        authenticated=True, access_token=None,
    ):
        url = f'https://graph.facebook.com/{self.version}/{path}'
        if query:
            url = f'{url}?{urlencode(query)}'
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {}
        token = self.access_token if access_token is None else access_token
        if authenticated:
            if not token:
                raise WhatsAppProviderError('Credencial da Meta ausente.')
            headers['Authorization'] = f'Bearer {token}'
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except HTTPError as error:
            code = _error_code(error)
            raise WhatsAppAPIError(
                'A Meta rejeitou a configuração do WhatsApp.',
                status_code=error.code, error_code=code,
            ) from error
        except (URLError, TimeoutError) as error:
            raise WhatsAppAPIError('A Meta não respondeu durante a configuração.') from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WhatsAppAPIError('A Meta retornou dados inválidos.') from error


class EmbeddedSignupService:
    REQUIRED_SCOPES = {'whatsapp_business_management', 'whatsapp_business_messaging'}

    @classmethod
    def connect(cls, *, empresa, code, waba_id, phone_number_id, graph_client=None):
        cls._validate_local_configuration()
        if not all(re.fullmatch(r'\d+', value or '') for value in (waba_id, phone_number_id)):
            raise WhatsAppProviderError('A Meta retornou identificadores inválidos.')
        graph = graph_client or MetaGraphClient()
        token_data = graph.exchange_code(code)
        access_token = str(token_data.get('access_token', ''))
        if not access_token:
            raise WhatsAppProviderError('A Meta não retornou o token de acesso.')
        graph.access_token = access_token
        cls._validate_token(graph.debug_token(access_token))
        phones = graph.get_waba_phones(waba_id).get('data', [])
        phone = next(
            (item for item in phones if str(item.get('id', '')) == phone_number_id),
            None,
        )
        if phone is None:
            raise WhatsAppProviderError('O número não pertence à conta WhatsApp autorizada.')
        verified_phone = graph.get_phone(phone_number_id)
        graph.subscribe_app(waba_id)
        expires_at = _expiration(token_data)
        with transaction.atomic():
            EmpresaCliente.objects.select_for_update().get(pk=empresa.pk)
            conflict = WhatsAppIntegration.objects.filter(
                phone_number_id=phone_number_id,
            ).exclude(company=empresa).exists()
            if conflict:
                raise WhatsAppProviderError('Este número já está conectado a outra empresa.')
            integration, created = WhatsAppIntegration.objects.get_or_create(
                company=empresa,
                defaults={
                    'phone_number_id': phone_number_id,
                    'whatsapp_business_account_id': waba_id,
                },
            )
            integration.phone_number_id = phone_number_id
            integration.whatsapp_business_account_id = waba_id
            integration.display_phone_number = str(
                verified_phone.get('display_phone_number')
                or phone.get('display_phone_number')
                or ''
            )[:32]
            integration.verified_name = str(
                verified_phone.get('verified_name')
                or phone.get('verified_name')
                or ''
            )[:120]
            integration.set_access_token(access_token)
            integration.token_expires_at = expires_at
            integration.connected_at = timezone.now()
            integration.disconnected_at = None
            integration.last_error_code = ''
            integration.onboarding_status = WhatsAppIntegration.OnboardingStatus.CONNECTED
            integration.is_active = True
            integration.save()
        return EmbeddedSignupResult(integration=integration, created=created)

    @classmethod
    def disconnect(cls, integration, graph_client=None):
        token = integration.get_access_token()
        remote_error = None
        if token:
            other_active = WhatsAppIntegration.objects.filter(
                whatsapp_business_account_id=integration.whatsapp_business_account_id,
                is_active=True,
            ).exclude(pk=integration.pk).exists()
            if not other_active:
                graph = graph_client or MetaGraphClient(access_token=token)
                try:
                    graph.unsubscribe_app(integration.whatsapp_business_account_id)
                except WhatsAppProviderError as error:
                    remote_error = error
        integration.access_token_encrypted = ''
        integration.is_active = False
        integration.onboarding_status = WhatsAppIntegration.OnboardingStatus.DISCONNECTED
        integration.disconnected_at = timezone.now()
        integration.save(update_fields=[
            'access_token_encrypted', 'is_active', 'onboarding_status',
            'disconnected_at', 'updated_at',
        ])
        if remote_error:
            raise WhatsAppProviderError(
                'O número foi desconectado do ZapFluxo, mas a Meta não confirmou '
                'a remoção da assinatura.'
            ) from remote_error
        return integration

    @classmethod
    def _validate_token(cls, response):
        data = response.get('data', {})
        if not data.get('is_valid') or str(data.get('app_id', '')) != settings.META_APP_ID:
            raise WhatsAppProviderError('A autorização não pertence ao aplicativo ZapFluxo.')
        scopes = set(data.get('scopes') or [])
        if not cls.REQUIRED_SCOPES.issubset(scopes):
            raise WhatsAppProviderError('A autorização não concedeu todas as permissões necessárias.')

    @staticmethod
    def _validate_local_configuration():
        missing = [
            name for name, value in (
                ('META_APP_ID', settings.META_APP_ID),
                ('META_APP_SECRET', settings.META_APP_SECRET),
                ('META_EMBEDDED_SIGNUP_CONFIG_ID', settings.META_EMBEDDED_SIGNUP_CONFIG_ID),
            ) if not value
        ]
        if missing:
            raise WhatsAppProviderError(
                f'Configuração incompleta do Embedded Signup: {", ".join(missing)}.'
            )


def _expiration(data):
    seconds = data.get('expires_in')
    try:
        return timezone.now() + timedelta(seconds=int(seconds)) if seconds else None
    except (TypeError, ValueError):
        return None


def _error_code(error):
    try:
        data = json.loads(error.read())
        return str(data.get('error', {}).get('code', ''))[:32]
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return ''
