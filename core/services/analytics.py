from decimal import Decimal

from django.db.models import Avg, Count, DecimalField, FloatField, Q, Sum
from django.db.models.functions import Coalesce

from core.models import Agendamento, AIUsageRecord, Atendimento, Mensagem


def company_metrics(empresa):
    attendances = Atendimento.objects.filter(empresa=empresa)
    total = attendances.count()
    human = attendances.filter(
        Q(current_step__in=[Atendimento.Step.WAITING_HUMAN, Atendimento.Step.HUMAN])
        | Q(handoff_reason__gt='')
    ).distinct().count()
    usage = AIUsageRecord.objects.filter(empresa=empresa).aggregate(
        ai_calls=Count('id'),
        input_tokens=Coalesce(Sum('input_tokens'), 0),
        output_tokens=Coalesce(Sum('output_tokens'), 0),
        tool_calls=Coalesce(Sum('tool_calls'), 0),
        errors=Count('id', filter=Q(succeeded=False)),
        cost=Coalesce(
            Sum('estimated_cost_usd'), Decimal('0'),
            output_field=DecimalField(max_digits=12, decimal_places=6),
        ),
        avg_latency=Coalesce(Avg('latency_ms'), 0.0, output_field=FloatField()),
    )
    messages = Mensagem.objects.filter(empresa=empresa).aggregate(
        received=Count('id', filter=Q(direcao=Mensagem.DIRECAO_ENTRADA)),
        sent=Count('id', filter=Q(direcao=Mensagem.DIRECAO_SAIDA)),
        failed=Count('id', filter=Q(status=Mensagem.STATUS_FALHA)),
    )
    return {
        'attendances': total,
        'ai_resolved': max(0, total - human),
        'human': human,
        'automation_rate': round(((total - human) / total * 100), 1) if total else 0,
        'appointments': Agendamento.objects.filter(
            empresa=empresa, origem=Agendamento.Origem.WHATSAPP,
        ).count(),
        **usage, **messages,
    }
