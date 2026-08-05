from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from core.models import Atendimento


class PRDSprintTwelveTests(TestCase):
    def test_liveness_and_readiness_probes(self):
        self.assertEqual(self.client.get(reverse('health_live')).json()['application'], 'alive')
        self.assertEqual(self.client.get(reverse('health_ready')).status_code, 200)

    def test_production_artifacts_exist(self):
        root = Path(settings.BASE_DIR)
        for name in ('Dockerfile', 'compose.yaml', '.dockerignore', '.github/workflows/ci.yml', '.github/workflows/release.yml'):
            self.assertTrue((root / name).exists(), name)

    def test_container_runs_as_unprivileged_user_and_has_healthcheck(self):
        dockerfile = (Path(settings.BASE_DIR) / 'Dockerfile').read_text(encoding='utf-8')
        self.assertIn('USER iaatende', dockerfile)
        self.assertIn('HEALTHCHECK', dockerfile)

    def test_release_check_blocks_insecure_production_environment(self):
        with self.assertRaises(CommandError):
            call_command('release_check')

    def test_commercial_cta_points_to_login(self):
        response = self.client.get(reverse('landing_page'))
        self.assertContains(response, reverse('login'))

    def test_conversation_indexes_are_declared(self):
        fields = [tuple(index.fields) for index in Atendimento._meta.indexes]
        self.assertIn(('empresa', 'status', '-last_message_at'), fields)
