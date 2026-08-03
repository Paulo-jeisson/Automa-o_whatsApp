SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
CONSOLE_BACKEND = 'django.core.mail.backends.console.EmailBackend'


def resolve_email_backend(environ):
    requested = environ.get('EMAIL_BACKEND', '').strip()
    complete = all(environ.get(name, '').strip() for name in (
        'EMAIL_HOST', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD', 'DEFAULT_FROM_EMAIL',
    ))
    return SMTP_BACKEND if requested == SMTP_BACKEND and complete else CONSOLE_BACKEND
