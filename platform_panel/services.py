import platform
from decimal import Decimal

import django
from django.conf import settings
from django.db import connection
from django.db.models import Count, Q, Sum
from django.utils import timezone

from core.models import AIUsageRecord, Atendimento, EmpresaCliente, Mensagem, PaymentHistory, Plan, Subscription, WhatsAppSession

from .selectors import period_bounds, usage_summary


USD_BRL_ESTIMATE = Decimal('5.00')


def to_brl(usd):
    return (Decimal(usd or 0) * USD_BRL_ESTIMATE).quantize(Decimal('0.01'))


def dashboard_metrics():
    now, day, month = period_bounds()
    subscriptions = Subscription.objects.all()
    today_usage = usage_summary(day)
    month_usage = usage_summary(month)
    return {
        'companies': EmpresaCliente.objects.count(),
        'active_companies': EmpresaCliente.objects.filter(ativa=True).count(),
        'trial_companies': subscriptions.filter(status=Subscription.Status.TRIAL).count(),
        'blocked_companies': subscriptions.filter(status=Subscription.Status.BLOCKED).count(),
        'conversations_today': Atendimento.objects.filter(criado_em__gte=day).count(),
        'messages_today': Mensagem.objects.filter(criado_em__gte=day).count(),
        'ai_messages_today': today_usage['requests'],
        'openai_today_brl': to_brl(today_usage['cost_usd']),
        'openai_month_brl': to_brl(month_usage['cost_usd']),
        'monthly_cost_estimate_brl': to_brl(
            month_usage['cost_usd'] * max(Decimal(30) / Decimal(now.day), Decimal(1))
        ),
        'online_clients': WhatsAppSession.objects.filter(state='CONNECTED').count(),
    }


def financial_metrics():
    active = Subscription.objects.select_related('plan').filter(status__in=[Subscription.Status.ACTIVE, Subscription.Status.GRACE])
    monthly_cents = sum(
        item.plan.price_cents if item.plan.billing_cycle == Plan.Cycle.MONTHLY else item.plan.price_cents / 12
        for item in active
    )
    count = active.count()
    by_plan = active.values('plan__name').annotate(clients=Count('id'), revenue_cents=Sum('plan__price_cents')).order_by('-revenue_cents')
    return {
        'monthly_revenue': Decimal(monthly_cents) / 100,
        'annual_revenue': Decimal(monthly_cents * 12) / 100,
        'overdue': Subscription.objects.filter(status=Subscription.Status.GRACE).count(),
        'trial': Subscription.objects.filter(status=Subscription.Status.TRIAL).count(),
        'active': count,
        'average_ticket': Decimal(monthly_cents / count) / 100 if count else Decimal('0'),
        'by_plan': by_plan,
    }


def readonly_settings():
    connection.ensure_connection()
    if connection.vendor == 'postgresql':
        database_version = str(connection.connection.info.server_version)
    else:
        database_version = str(getattr(connection.Database, 'sqlite_version', 'não disponível'))
    return {
        'openai_model': settings.AI_MODEL,
        'openai_timeout': settings.AI_TIMEOUT,
        'queue_retry': settings.TASK_QUEUE_BACKOFF,
        'queue_max_retry': settings.TASK_QUEUE_MAX_BACKOFF,
        'queue_lease': settings.TASK_QUEUE_LEASE_SECONDS,
        'django_version': django.get_version(),
        'python_version': platform.python_version(),
        'database_vendor': connection.vendor,
        'database_version': database_version,
        'system_version': getattr(settings, 'SYSTEM_VERSION', 'development'),
        'evolution_version': getattr(settings, 'EVOLUTION_VERSION', 'não informada'),
        'evolution_url_configured': bool(getattr(settings, 'EVOLUTION_API_URL', '')),
    }
