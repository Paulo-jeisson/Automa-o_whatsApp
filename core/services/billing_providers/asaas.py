import json
import logging
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


logger = logging.getLogger('billing.asaas.http')


class AsaasError(RuntimeError):
    def __init__(self, message, *, status_code=None, errors=None, response=None):
        super().__init__(message)
        self.status_code = status_code
        self.errors = errors or []
        self.response = response or {}


class AsaasUnavailable(AsaasError):
    pass


class AsaasClient:
    def __init__(self, *, api_url=None, api_key=None, timeout=15):
        self.api_url = (api_url or settings.ASAAS_API_URL).rstrip('/')
        self.api_key = api_key if api_key is not None else settings.ASAAS_API_KEY
        self.timeout = timeout
        if not self.api_key:
            raise ImproperlyConfigured('ASAAS_API_KEY não configurada.')

    def create_customer(self, payload):
        return self._request('POST', '/customers', payload)

    def list_customers(self, *, external_reference):
        data = self._request('GET', f'/customers?{urlencode({"externalReference": external_reference})}')
        return data.get('data', []) if isinstance(data, dict) else []

    def get_customer(self, customer_id):
        return self._request('GET', f'/customers/{customer_id}')

    def update_customer(self, customer_id, payload):
        return self._request('PUT', f'/customers/{customer_id}', payload)

    def create_checkout(self, payload):
        return self._request('POST', '/checkouts', payload)

    def cancel_checkout(self, checkout_id):
        return self._request('POST', f'/checkouts/{quote(str(checkout_id), safe="")}/cancel')

    def create_subscription(self, payload):
        return self._request('POST', '/subscriptions', payload)

    def get_subscription(self, subscription_id):
        return self._request('GET', f'/subscriptions/{subscription_id}')

    def cancel_subscription(self, subscription_id):
        return self._request('DELETE', f'/subscriptions/{subscription_id}')

    def get_payment(self, payment_id):
        return self._request('GET', f'/payments/{payment_id}')

    def list_subscription_payments(self, subscription_id):
        return self._request('GET', f'/subscriptions/{subscription_id}/payments')

    def checkout_url(self, checkout_id):
        host = 'sandbox.asaas.com' if settings.ASAAS_ENVIRONMENT == 'sandbox' else 'asaas.com'
        return f'https://{host}/checkoutSession/show?id={quote(str(checkout_id), safe="")}'

    @staticmethod
    def _safe_error_response(raw_body):
        """Retém somente o diagnóstico público da API e remove possíveis PII."""
        try:
            decoded = json.loads(raw_body)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {'errors': [{'code': 'invalid_error_response', 'description': 'Resposta de erro não estruturada.'}]}
        errors = decoded.get('errors', []) if isinstance(decoded, dict) else []
        safe_errors = []
        for item in errors[:20]:
            if not isinstance(item, dict):
                continue
            code = re.sub(r'[^A-Za-z0-9_.-]', '', str(item.get('code') or 'unknown'))[:100]
            description = str(item.get('description') or 'Erro sem descrição.')
            description = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL]', description)
            description = re.sub(r'(?<!\d)\d{11,16}(?!\d)', '[DOCUMENTO]', description)
            description = re.sub(r'(?i)(access[_ -]?token|api[_ -]?key|token)\s*[:=]\s*\S+', r'\1=[REDACTED]', description)
            safe_errors.append({'code': code, 'description': description[:500]})
        return {'errors': safe_errors or [{'code': 'unknown', 'description': 'Erro não detalhado pelo Asaas.'}]}

    def _request(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            f'{self.api_url}{path}', data=body, method=method,
            headers={
                'accept': 'application/json', 'content-type': 'application/json',
                'access_token': self.api_key, 'User-Agent': 'IAATENDE/2.0',
            },
        )
        attempts = 3 if method == 'GET' else 1
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read() or b'{}')
            except HTTPError as error:
                detail = error.read(16 * 1024).decode(errors='replace')
                if error.code >= 500 and attempt + 1 < attempts:
                    time.sleep(0.2 * (2 ** attempt))
                    continue
                safe_response = self._safe_error_response(detail)
                logger.warning(
                    'asaas.http_error method=%s path=%s status=%s response=%s',
                    method, path.split('?')[0], error.code,
                    json.dumps(safe_response, ensure_ascii=False, separators=(',', ':')),
                )
                raise AsaasError(
                    f'Asaas recusou a operação (HTTP {error.code}).',
                    status_code=error.code,
                    errors=safe_response['errors'],
                    response=safe_response,
                ) from error
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                if attempt + 1 < attempts:
                    time.sleep(0.2 * (2 ** attempt))
                    continue
                logger.warning('asaas.unavailable method=%s path=%s', method, path.split('?')[0])
                raise AsaasUnavailable('Asaas temporariamente indisponível.') from error
