import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


logger = logging.getLogger('billing.asaas.http')


class AsaasError(RuntimeError):
    pass


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
        return f'https://{host}/checkoutSession/show/{checkout_id}'

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
                detail = error.read(2048).decode(errors='replace')
                if error.code >= 500 and attempt + 1 < attempts:
                    time.sleep(0.2 * (2 ** attempt))
                    continue
                logger.warning('asaas.http_error method=%s path=%s status=%s', method, path.split('?')[0], error.code)
                raise AsaasError(f'Asaas recusou a operação (HTTP {error.code}): {detail[:300]}') from error
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                if attempt + 1 < attempts:
                    time.sleep(0.2 * (2 ** attempt))
                    continue
                logger.warning('asaas.unavailable method=%s path=%s', method, path.split('?')[0])
                raise AsaasUnavailable('Asaas temporariamente indisponível.') from error
