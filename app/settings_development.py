"""Configurações do ambiente local de desenvolvimento."""

import os

from .settings import *  # noqa: F403


DEBUG = os.environ.get('DEBUG', 'True').lower() in {'1', 'true', 'yes', 'on'}
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', '86400'))
RATELIMIT_TRUST_PROXY = False
PASSWORD_RESET_USE_REQUEST_DOMAIN = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get('SQLITE_NAME', BASE_DIR / 'db.sqlite3'),  # noqa: F405
        'OPTIONS': {'timeout': 20},
    }
}
