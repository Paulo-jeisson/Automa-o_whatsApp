import hashlib

from django.conf import settings


def client_ip(request):
    if getattr(settings, 'RATELIMIT_TRUST_PROXY', False):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',', 1)[0].strip()
    return request.META.get('REMOTE_ADDR', '') or 'unknown'


def hash_identifier(value):
    material = f'{settings.SECRET_KEY}:{value}'.encode()
    return hashlib.sha256(material).hexdigest()
