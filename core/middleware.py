import hashlib
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone

from .models import RateLimitBucket
from .security import client_ip


class RateLimitMiddleware:
    POLICIES = (
        ('login', lambda r: r.path == '/login/' and r.method == 'POST', 10, 300),
        ('webhook', lambda r: r.path == '/webhooks/whatsapp/', 120, 60),
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
        return True
