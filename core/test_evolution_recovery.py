import io
import json
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from django.test import SimpleTestCase, override_settings

from core.infrastructure.evolution import EvolutionProvider, EvolutionRequestError


@override_settings(
    EVOLUTION_API_URL='https://evolution.test', EVOLUTION_API_KEY='api-secret',
    EVOLUTION_WEBHOOK_SECRET='webhook-secret', PUBLIC_BASE_URL='https://iaatende.test',
)
class EvolutionFailureClassificationTests(SimpleTestCase):
    def setUp(self):
        EvolutionProvider._failures = 0
        EvolutionProvider._circuit_opened_at = 0

    @staticmethod
    def http_error(status, payload):
        return HTTPError('https://evolution.test/instance/create', status, 'error', None,
                         io.BytesIO(json.dumps(payload).encode()))

    @patch('core.infrastructure.evolution.urlopen')
    def test_403_is_sanitized_not_retried_and_does_not_open_breaker(self, urlopen_mock):
        urlopen_mock.side_effect = self.http_error(403, {
            'statusCode': 403, 'message': 'Forbidden by instance policy',
            'apikey': 'api-secret', 'qrcode': 'sensitive-qr',
        })
        with self.assertLogs('evolution.provider', 'WARNING') as captured:
            with self.assertRaises(EvolutionRequestError) as raised:
                EvolutionProvider().create('instance-a')
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.provider_code, '403')
        self.assertEqual(urlopen_mock.call_count, 1)
        self.assertEqual(EvolutionProvider._failures, 0)
        output = ' '.join(captured.output)
        self.assertIn('Forbidden by instance policy', output)
        self.assertNotIn('api-secret', output)
        self.assertNotIn('sensitive-qr', output)

    @patch('core.infrastructure.evolution.time.sleep')
    @patch('core.infrastructure.evolution.urlopen')
    def test_only_transient_http_failures_retry_and_count_for_breaker(self, urlopen_mock, sleep_mock):
        for status in (429, 500):
            with self.subTest(status=status):
                EvolutionProvider._failures = 0
                urlopen_mock.reset_mock()
                urlopen_mock.side_effect = [self.http_error(status, {'message': 'temporary'})] * 3
                with self.assertRaises(EvolutionRequestError):
                    EvolutionProvider()._request('POST', '/instance/create', {})
                self.assertEqual(urlopen_mock.call_count, 3)
                self.assertEqual(EvolutionProvider._failures, 1)

    @patch('core.infrastructure.evolution.time.sleep')
    @patch('core.infrastructure.evolution.urlopen', side_effect=URLError('timeout'))
    def test_transport_timeout_retries_with_bounded_attempts(self, urlopen_mock, _sleep_mock):
        with self.assertRaises(EvolutionRequestError):
            EvolutionProvider()._request('GET', '/instance/fetchInstances')
        self.assertEqual(urlopen_mock.call_count, 3)

