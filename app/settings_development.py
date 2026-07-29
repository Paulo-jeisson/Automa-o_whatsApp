"""Configurações do ambiente local de desenvolvimento."""

import os

from .settings import *  # noqa: F403


DEBUG = os.environ.get('DEBUG', 'True').lower() in {'1', 'true', 'yes', 'on'}
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'ZapFluxo <nao-responda@localhost>'
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', '86400'))
RATELIMIT_TRUST_PROXY = False

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get('SQLITE_NAME', BASE_DIR / 'db.sqlite3'),  # noqa: F405
    }
}
