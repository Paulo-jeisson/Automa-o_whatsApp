import hashlib
import logging
import threading
from datetime import timedelta

from django.db import IntegrityError, OperationalError, transaction
from django.http import JsonResponse
from django.utils import timezone

from .models import RateLimitBucket
from .security import client_ip

logger = logging.getLogger('security.rate_limit')


class RateLimitMiddleware:
    _fallback_lock = threading.Lock()
    _fallback_buckets = {}
    POLICIES = (
        ('login', lambda r: r.path == '/login/' and r.method == 'POST', 10, 300),
        ('api_token', lambda r: r.path.startswith('/api/auth/') and r.method == 'POST', 20, 300),
        ('webhook', lambda r: r.path in {'/webhooks/whatsapp/', '/webhooks/evolution/'}, 120, 60),
        (
            'public_attendance',
            lambda r: r.path.startswith('/atendimento/') and r.method == 'POST',
            20,
            60,
        ),
        (
            'sensitive',
            lambda r: r.method == 'POST' and (
                r.path.startswith('/configuracoes/')
                or r.path.startswith('/agenda/')
                or r.path.startswith('/fluxo/')
                or r.path.startswith('/atendimentos/')
            ),
            60,
            60,
        ),
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        policy = next((item for item in self.POLICIES if item[1](request)), None)
        if policy and self._exceeded(request, policy[0], policy[2], policy[3]):
            if policy[0] == 'webhook':
                return JsonResponse({'detail': 'Muitas requisições.'}, status=429)
            return JsonResponse({'detail': 'Muitas tentativas. Aguarde e tente novamente.'}, status=429)
        return self.get_response(request)

    @staticmethod
    def _exceeded(request, name, limit, seconds):
        identity = (
            f'user:{request.user.pk}'
            if request.user.is_authenticated
            else f'ip:{client_ip(request)}'
        )
        key = hashlib.sha256(f'{name}:{identity}'.encode()).hexdigest()
        now = timezone.now()
        window_floor = now - timedelta(seconds=seconds)

        for attempt in range(2):
            try:
                with transaction.atomic():
                    bucket = RateLimitBucket.objects.select_for_update().filter(key=key).first()
                    if bucket is None:
                        RateLimitBucket.objects.create(
                            key=key,
                            window_started_at=now,
                            count=1,
                        )
                        return False
                    if bucket.window_started_at <= window_floor:
                        bucket.window_started_at = now
                        bucket.count = 1
                    else:
                        bucket.count += 1
                    bucket.save(update_fields=['window_started_at', 'count', 'updated_at'])
                    return bucket.count > limit
            except IntegrityError:
                if attempt:
                    return True
            except OperationalError as error:
                if 'locked' not in str(error).lower():
                    raise
                logger.warning(
                    'rate_limit.database_locked policy=%s fallback=memory', name,
                )
                return RateLimitMiddleware._fallback_exceeded(
                    key=key, limit=limit, seconds=seconds, now=now,
                )
        return True

    @classmethod
    def _fallback_exceeded(cls, *, key, limit, seconds, now):
        with cls._fallback_lock:
            window_started_at, count = cls._fallback_buckets.get(key, (now, 0))
            if window_started_at <= now - timedelta(seconds=seconds):
                window_started_at, count = now, 0
            count += 1
            cls._fallback_buckets[key] = (window_started_at, count)
            # Remove janelas expiradas para o fallback não crescer indefinidamente.
            if len(cls._fallback_buckets) > 2048:
                cutoff = now - timedelta(seconds=max(seconds, 300))
                cls._fallback_buckets = {
                    item_key: value
                    for item_key, value in cls._fallback_buckets.items()
                    if value[0] > cutoff
                }
            return count > limit


class SecurityHeadersMiddleware:
    """Headers defensivos centralizados para HTML e APIs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault('X-Content-Type-Options', 'nosniff')
        response.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=()')
        response.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
        response.setdefault('Content-Security-Policy', "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; connect-src 'self'")
        user = getattr(request, 'user', None)
        response.setdefault('Cache-Control', 'no-store' if user and user.is_authenticated else response.get('Cache-Control', ''))
        return response
