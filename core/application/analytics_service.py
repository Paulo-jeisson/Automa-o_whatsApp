from datetime import timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from core.models import Agendamento, AIUsageRecord, Atendimento, Contato, Mensagem


class DashboardAnalyticsService:
    @staticmethod
    def build(empresa, days=14):
        start = timezone.localdate() - timedelta(days=days - 1)
        qs = Atendimento.objects.filter(empresa=empresa)
        daily_rows = qs.filter(criado_em__date__gte=start).annotate(day=TruncDate('criado_em')).values('day').annotate(total=Count('id')).order_by('day')
        daily_map = {row['day']: row['total'] for row in daily_rows}
        daily = [{'date': (start + timedelta(days=i)).isoformat(), 'total': daily_map.get(start + timedelta(days=i), 0)} for i in range(days)]
        duration = ExpressionWrapper(F('closed_at') - F('criado_em'), output_field=DurationField())
        avg_time = qs.filter(closed_at__isnull=False).aggregate(value=Avg(duration))['value']
        funnel = [
            {'label': 'Conversas', 'value': qs.count()},
            {'label': 'Atendidas por IA', 'value': qs.filter(automation_enabled=True).count()},
            {'label': 'Transferidas', 'value': qs.filter(Q(current_step='WAITING_HUMAN') | Q(current_step='HUMAN')).count()},
            {'label': 'Finalizadas', 'value': qs.filter(status=Atendimento.STATUS_FINALIZADO).count()},
        ]
        return {
            'conversations': qs.count(), 'customers': Contato.objects.filter(empresa=empresa).count(),
            'ai_messages': Mensagem.objects.filter(empresa=empresa, direcao=Mensagem.DIRECAO_SAIDA, sent_by__isnull=True).count(),
            'human_messages': Mensagem.objects.filter(empresa=empresa, direcao=Mensagem.DIRECAO_SAIDA, sent_by__isnull=False).count(),
            'appointments': Agendamento.objects.filter(empresa=empresa).count(),
            'avg_minutes': round(avg_time.total_seconds() / 60, 1) if avg_time else 0,
            'daily': daily, 'funnel': funnel,
        }
