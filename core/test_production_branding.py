from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ProductionBrandingRegressionTests(SimpleTestCase):
    """Impede que identidade, domínios e endereços locais vazem para produção."""

    root = Path(settings.BASE_DIR)
    text_suffixes = {'.conf', '.css', '.env', '.html', '.md', '.py', '.service', '.sh', '.timer', '.yaml', '.yml'}
    named_text_files = {'Dockerfile', 'design_system', 'iaatende-backup'}

    def _files(self, paths):
        for relative in paths:
            target = self.root / relative
            candidates = target.rglob('*') if target.is_dir() else (target,)
            for candidate in candidates:
                if candidate.resolve() == Path(__file__).resolve():
                    continue
                if candidate.is_file() and (
                    candidate.suffix.lower() in self.text_suffixes
                    or candidate.name in self.named_text_files
                ):
                    yield candidate

    def _assert_absent(self, paths, forbidden):
        failures = []
        for path in self._files(paths):
            content = path.read_text(encoding='utf-8').casefold()
            matches = [token for token in forbidden if token.casefold() in content]
            if matches:
                failures.append(f'{path.relative_to(self.root)}: {", ".join(matches)}')
        self.assertFalse(failures, 'Referências proibidas encontradas:\n' + '\n'.join(failures))

    def test_legacy_brand_paths_and_domains_are_absent(self):
        self._assert_absent(
            [
                '.env.example', '.github', 'Dockerfile', 'PRD.MD', 'README.md',
                'app', 'compose.yaml', 'core', 'deploy', 'design_system', 'docs',
                'scripts', 'static', 'templates',
            ],
            [
                'zap' + 'fluxo', 'zap' + 'fluxo.conf', 'Automação_' + 'whatzzap',
                'operacao' + 'ia' + '.com', 'atendeia2.0.' + 'operacao' + 'ia' + '.com',
                'neural' + 'foco.com.br', 'buy.' + 'stripe.com',
            ],
        )

    def test_local_addresses_do_not_appear_on_production_surfaces(self):
        # README, .env.example, settings base, testes e o healthcheck do container
        # são deliberadamente locais; nenhum deles é renderizado ou instalado como
        # configuração de produção.
        self._assert_absent(
            ['deploy', 'docs', 'scripts', 'static', 'templates'],
            ['localhost', '127.0.0.1', 'ngrok'],
        )

    def test_production_artifacts_use_canonical_identity(self):
        production_settings = (self.root / 'app/settings_production.py').read_text(encoding='utf-8')
        nginx = (self.root / 'deploy/nginx/iaatende.conf').read_text(encoding='utf-8')
        self.assertIn("PUBLIC_BASE_URL != 'https://iaatende.app'", production_settings)
        self.assertIn('server_name iaatende.app;', nginx)
        self.assertTrue((self.root / 'deploy/systemd/iaatende.service').is_file())
        self.assertTrue((self.root / 'deploy/cron/iaatende-backup').is_file())
