from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class HealthCheckTests(TestCase):
    def test_health_reports_application_and_database(self):
        response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertEqual(response.json()['application'], 'ok')
        self.assertEqual(response.json()['database'], 'ok')

    def test_health_returns_503_when_database_is_unavailable(self):
        with patch('app.health.connection.cursor', side_effect=OSError('database offline')):
            response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'unavailable')
        self.assertEqual(response.json()['database'], 'unavailable')

    def test_health_only_accepts_get(self):
        self.assertEqual(self.client.post(reverse('health')).status_code, 405)
