from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.utils import timezone

from core.models import (
    AIUsageRecord, AsyncJob, Atendimento, EmpresaCliente, Mensagem,
    OperationalAlert, Subscription, WhatsAppSession, WhatsAppSessionEvent,
)


def period_bounds():
    now = timezone.now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return now, day, month


def usage_summary(since):
    return AIUsageRecord.objects.filter(created_at__gte=since).aggregate(
        requests=Count('id'), input_tokens=Coalesce(Sum('input_tokens'), 0),
        output_tokens=Coalesce(Sum('output_tokens'), 0),
        cost_usd=Coalesce(Sum('estimated_cost_usd'), Decimal('0')),
        latency_ms=Coalesce(Sum('latency_ms'), 0),
    )


def usage_by_company(since):
    return AIUsageRecord.objects.filter(created_at__gte=since).values(
        'empresa_id', 'empresa__nome', 'model',
    ).annotate(
        requests=Count('id'), input_tokens=Coalesce(Sum('input_tokens'), 0),
        output_tokens=Coalesce(Sum('output_tokens'), 0),
        cost_usd=Coalesce(Sum('estimated_cost_usd'), Decimal('0')),
        last_used=Max('created_at'),
    ).order_by('-cost_usd')


def usage_series(since, *, monthly=False):
    trunc = TruncMonth('created_at') if monthly else TruncDate('created_at')
    return list(AIUsageRecord.objects.filter(created_at__gte=since).annotate(
        period=trunc,
    ).values('period').annotate(
        requests=Count('id'), tokens=Sum('input_tokens') + Sum('output_tokens'),
        cost=Sum('estimated_cost_usd'),
    ).order_by('period'))


def companies_queryset(month_start):
    return EmpresaCliente.objects.select_related(
        'usuario', 'subscription', 'subscription__plan', 'whatsapp_session',
    ).annotate(
        messages_month=Count('mensagens', filter=Q(mensagens__criado_em__gte=month_start), distinct=True),
        ai_cost_month=Coalesce(Sum(
            'ai_usage_records__estimated_cost_usd',
            filter=Q(ai_usage_records__created_at__gte=month_start),
        ), Decimal('0')),
    ).order_by('nome')


def filtered_logs(start=None, end=None):
    alert_qs = OperationalAlert.objects.all().order_by('-last_seen_at')
    event_qs = WhatsAppSessionEvent.objects.select_related('session__empresa').order_by('-created_at')
    job_qs = AsyncJob.objects.exclude(last_error='').order_by('-created_at')
    ai_qs = AIUsageRecord.objects.filter(succeeded=False).select_related('empresa').order_by('-created_at')
    if start:
        alert_qs = alert_qs.filter(last_seen_at__date__gte=start)
        event_qs = event_qs.filter(created_at__date__gte=start)
        job_qs = job_qs.filter(created_at__date__gte=start)
        ai_qs = ai_qs.filter(created_at__date__gte=start)
    if end:
        alert_qs = alert_qs.filter(last_seen_at__date__lte=end)
        event_qs = event_qs.filter(created_at__date__lte=end)
        job_qs = job_qs.filter(created_at__date__lte=end)
        ai_qs = ai_qs.filter(created_at__date__lte=end)
    return alert_qs[:100], event_qs[:100], job_qs[:100], ai_qs[:100]


def infrastructure_snapshot():
    now = timezone.now()
    queue = AsyncJob.objects.aggregate(
        pending=Count('id', filter=Q(status__in=[AsyncJob.Status.PENDING, AsyncJob.Status.RETRY])),
        processing=Count('id', filter=Q(status=AsyncJob.Status.PROCESSING)),
        dead=Count('id', filter=Q(status=AsyncJob.Status.DEAD)),
    )
    return {
        'checked_at': now,
        'database': 'online',
        'redis': 'não configurado',
        'evolution_connected': WhatsAppSession.objects.filter(state='CONNECTED').count(),
        'evolution_total': WhatsAppSession.objects.count(),
        'workers_busy': queue['processing'],
        **queue,
    }
