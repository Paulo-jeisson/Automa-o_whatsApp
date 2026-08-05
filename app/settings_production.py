"""Configurações obrigatórias para o ambiente de produção."""

import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403


DEBUG = False


def required(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'{name} é obrigatória em produção.')
    return value


SECRET_KEY = required('DJANGO_SECRET_KEY')
ALLOWED_HOSTS = [item.strip() for item in required('ALLOWED_HOSTS').split(',') if item.strip()]
CSRF_TRUSTED_ORIGINS = [
    item.strip() for item in required('CSRF_TRUSTED_ORIGINS').split(',') if item.strip()
]

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get(
    'SECURE_HSTS_INCLUDE_SUBDOMAINS', 'True'
).lower() in {'1', 'true', 'yes', 'on'}
SECURE_HSTS_PRELOAD = os.environ.get(
    'SECURE_HSTS_PRELOAD', 'False'
).lower() in {'1', 'true', 'yes', 'on'}
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', '3600'))
SESSION_SAVE_EVERY_REQUEST = True

EMAIL_BACKEND = required('EMAIL_BACKEND')
EMAIL_HOST = required('EMAIL_HOST')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_HOST_USER = required('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = required('EMAIL_HOST_PASSWORD')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() in {'1', 'true', 'yes', 'on'}
DEFAULT_FROM_EMAIL = required('DEFAULT_FROM_EMAIL')
EMAIL_TIMEOUT = int(os.environ.get('EMAIL_TIMEOUT', '20'))
PASSWORD_RESET_TIMEOUT = int(os.environ.get('PASSWORD_RESET_TIMEOUT', '3600'))
PUBLIC_BASE_URL = required('PUBLIC_BASE_URL').rstrip('/')
if EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend':
    raise ImproperlyConfigured('EMAIL_BACKEND deve usar smtp.EmailBackend em produção.')
if EMAIL_USE_TLS and os.environ.get('EMAIL_USE_SSL', 'False').lower() in {'1', 'true', 'yes', 'on'}:
    raise ImproperlyConfigured('EMAIL_USE_TLS e EMAIL_USE_SSL não podem estar ativos ao mesmo tempo.')
if PUBLIC_BASE_URL.startswith(('http://127.0.0.1', 'https://127.0.0.1', 'http://localhost', 'https://localhost')):
    raise ImproperlyConfigured('PUBLIC_BASE_URL deve usar o domínio público em produção.')

RATELIMIT_TRUST_PROXY = True
PASSWORD_RESET_USE_REQUEST_DOMAIN = False

if AI_ENABLED and not OPENAI_API_KEY:  # noqa: F405
    raise ImproperlyConfigured('OPENAI_API_KEY é obrigatória quando AI_ENABLED=True.')

ASAAS_API_KEY = required('ASAAS_API_KEY')
ASAAS_WEBHOOK_TOKEN = required('ASAAS_WEBHOOK_TOKEN')
SUBSCRIPTION_ENFORCEMENT_ENABLED = True
if ASAAS_ENVIRONMENT not in {'sandbox', 'production'}:  # noqa: F405
    raise ImproperlyConfigured('ASAAS_ENVIRONMENT deve ser sandbox ou production.')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': required('POSTGRES_DB'),
        'USER': required('POSTGRES_USER'),
        'PASSWORD': required('POSTGRES_PASSWORD'),
        'HOST': required('POSTGRES_HOST'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'CONN_MAX_AGE': int(os.environ.get('POSTGRES_CONN_MAX_AGE', '60')),
        'CONN_HEALTH_CHECKS': True,
        'OPTIONS': {
            'connect_timeout': int(os.environ.get('POSTGRES_CONNECT_TIMEOUT', '5')),
        },
    }
}
