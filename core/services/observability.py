import hashlib
import json
import logging
from datetime import timedelta
from urllib import request as urllib_request

from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.utils import timezone

from core.models import AsyncJob, Mensagem, OperationalAlert, OperationalMetric, WhatsAppSession


logger = logging.getLogger('observability')


def record_metric(name, *, value=1, empresa=None, labels=None):
    return OperationalMetric.objects.create(
        name=name, value=value, empresa=empresa, labels=labels or {},
    )


def raise_alert(kind, message, *, severity=OperationalAlert.Severity.WARNING, fingerprint=None):
    key = fingerprint or hashlib.sha256(f'{kind}:{message}'.encode()).hexdigest()[:40]
    alert, created = OperationalAlert.objects.get_or_create(
        fingerprint=key,
        defaults={'kind': kind, 'message': message[:500], 'severity': severity},
    )
    if not created:
        alert.is_open = True
        alert.occurrences += 1
        alert.message = message[:500]
        alert.severity = severity
        alert.resolved_at = None
        alert.save(update_fields=['is_open', 'occurrences', 'message', 'severity', 'resolved_at', 'last_seen_at'])
    _notify(alert)
    return alert


def run_operational_checks(now=None):
    now = now or timezone.now()
    alerts = []
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        alerts.append(raise_alert('database_unavailable', 'Banco de dados indisponível.', severity='CRITICAL', fingerprint='database'))

    queued_jobs = AsyncJob.objects.filter(
        status__in=[AsyncJob.Status.PENDING, AsyncJob.Status.RETRY],
        queue='whatsapp',
    )
    queued = queued_jobs.count()
    processing = AsyncJob.objects.filter(
        queue='whatsapp', status=AsyncJob.Status.PROCESSING,
    ).count()
    oldest = queued_jobs.order_by('created_at').values_list('created_at', flat=True).first()
    oldest_age = max(0, int((now - oldest).total_seconds())) if oldest else 0
    record_metric('queue.depth', value=queued, labels={'queue': 'whatsapp'})
    record_metric('queue.processing', value=processing, labels={'queue': 'whatsapp'})
    record_metric('queue.oldest_age_seconds', value=oldest_age, labels={'queue': 'whatsapp'})
    if (
        queued >= settings.TASK_QUEUE_BACKLOG_WARNING_COUNT
        or oldest_age >= settings.TASK_QUEUE_BACKLOG_MAX_AGE_SECONDS
    ):
        alerts.append(raise_alert(
            'queue_backlog',
            f'Fila whatsapp com {queued} job(s); mais antigo aguarda {oldest_age}s.',
            fingerprint='queue-backlog',
        ))

    stuck = AsyncJob.objects.filter(
        status=AsyncJob.Status.PROCESSING, lease_expires_at__lt=now,
    ).count()
    if stuck:
        alerts.append(raise_alert(
            'queue_stuck', f'{stuck} job(s) com lease expirado.',
            severity='CRITICAL', fingerprint='queue-stuck',
        ))

    offline = WhatsAppSession.objects.filter(
        empresa__ativa=True, state__in=['OFFLINE', 'ERROR'],
    ).count()
    if offline:
        alerts.append(raise_alert(
            'whatsapp_sessions_offline', f'{offline} sessão(ões) WhatsApp offline.',
            fingerprint='whatsapp-sessions-offline',
        ))

    dead = AsyncJob.objects.filter(status=AsyncJob.Status.DEAD, created_at__gte=now - timedelta(hours=1)).count()
    if dead:
        alerts.append(raise_alert('dead_letter', f'{dead} job(s) em falha permanente na última hora.', severity='CRITICAL', fingerprint='dead-letter'))

    failed_messages = Mensagem.objects.filter(status=Mensagem.STATUS_FALHA, criado_em__gte=now - timedelta(minutes=15)).count()
    if failed_messages >= 3:
        alerts.append(raise_alert('meta_rejections', f'Meta recusou {failed_messages} mensagens em 15 minutos.', severity='CRITICAL', fingerprint='meta-rejections'))

    ai_failures = OperationalMetric.objects.filter(
        name='ai.failure', recorded_at__gte=now - timedelta(minutes=15),
    ).count()
    if ai_failures >= 3:
        alerts.append(raise_alert('openai_failures', f'{ai_failures} falhas de IA em 15 minutos.', fingerprint='openai-failures'))
    return alerts


def _notify(alert):
    if not settings.OPERATIONAL_ALERT_WEBHOOK:
        logger.warning('operational.alert kind=%s severity=%s message=%s', alert.kind, alert.severity, alert.message)
        return
    body = json.dumps({'kind': alert.kind, 'severity': alert.severity, 'message': alert.message}).encode()
    try:
        urllib_request.urlopen(urllib_request.Request(
            settings.OPERATIONAL_ALERT_WEBHOOK, data=body,
            headers={'Content-Type': 'application/json'}, method='POST',
        ), timeout=5).close()
    except Exception:
        logger.exception('operational.alert.notification_failed kind=%s', alert.kind)
