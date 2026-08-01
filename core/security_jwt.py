import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import APIRefreshToken


class JWTError(ValueError):
    pass


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def _decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def _key():
    return hashlib.sha256(f'{settings.SECRET_KEY}:zapfluxo-jwt-v1'.encode()).digest()


def encode(payload):
    header = _b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
    body = _b64(json.dumps(payload, separators=(',', ':')).encode())
    signature = _b64(hmac.new(_key(), f'{header}.{body}'.encode(), hashlib.sha256).digest())
    return f'{header}.{body}.{signature}'


def decode(token, expected_type='access'):
    try:
        header, body, signature = token.split('.')
        expected = _b64(hmac.new(_key(), f'{header}.{body}'.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature): raise JWTError('Assinatura inválida.')
        payload = json.loads(_decode(body))
        if payload.get('exp', 0) <= int(time.time()): raise JWTError('Token expirado.')
        if payload.get('type') != expected_type: raise JWTError('Tipo de token inválido.')
        return payload
    except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        if isinstance(exc, JWTError): raise
        raise JWTError('Token inválido.') from exc


def issue_pair(user):
    now = int(time.time()); refresh_jti = uuid.uuid4().hex
    access = encode({'sub': str(user.pk), 'type': 'access', 'iat': now, 'exp': now + settings.JWT_ACCESS_SECONDS})
    refresh = encode({'sub': str(user.pk), 'type': 'refresh', 'jti': refresh_jti, 'iat': now, 'exp': now + settings.JWT_REFRESH_SECONDS})
    APIRefreshToken.objects.create(user=user, jti_hash=hashlib.sha256(refresh_jti.encode()).hexdigest(), expires_at=timezone.now() + timedelta(seconds=settings.JWT_REFRESH_SECONDS))
    return {'access': access, 'refresh': refresh, 'token_type': 'Bearer', 'expires_in': settings.JWT_ACCESS_SECONDS}


def rotate(refresh_token):
    payload = decode(refresh_token, 'refresh')
    record = APIRefreshToken.objects.select_related('user').filter(jti_hash=hashlib.sha256(payload['jti'].encode()).hexdigest(), revoked_at__isnull=True, expires_at__gt=timezone.now()).first()
    if not record: raise JWTError('Refresh token revogado.')
    record.revoked_at = timezone.now(); record.last_used_at = timezone.now(); record.save(update_fields=['revoked_at', 'last_used_at'])
    return issue_pair(record.user)


def user_from_access(token):
    payload = decode(token)
    user = get_user_model().objects.filter(pk=payload['sub'], is_active=True).first()
    if not user: raise JWTError('Usuário inválido.')
    return user
