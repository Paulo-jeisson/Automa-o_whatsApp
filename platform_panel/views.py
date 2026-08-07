import json
from datetime import timedelta

from django.db.models import Count, Sum
from django.views.generic import DetailView, ListView, TemplateView

from core.models import AIUsageRecord, EmpresaCliente, Subscription

from .forms import DateRangeFilterForm
from .permissions import PlatformAdminPermission
from .selectors import (
    companies_queryset, filtered_logs, infrastructure_snapshot, period_bounds,
    usage_by_company, usage_series, usage_summary,
)
from .services import dashboard_metrics, financial_metrics, readonly_settings, to_brl


class PlatformContextMixin(PlatformAdminPermission):
    """Shared context for the isolated master-panel layout."""

    section = ''

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['platform_section'] = self.section
        return context


class DashboardView(PlatformContextMixin, TemplateView):
    template_name = 'platform/dashboard.html'
    section = 'dashboard'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['metrics'] = dashboard_metrics()
        return context


class CompanyListView(PlatformContextMixin, ListView):
    template_name = 'platform/companies.html'
    context_object_name = 'companies'
    paginate_by = 50
    section = 'companies'

    def get_queryset(self):
        _now, _day, month = period_bounds()
        return companies_queryset(month)


class CompanyDetailView(PlatformContextMixin, DetailView):
    template_name = 'platform/company_detail.html'
    context_object_name = 'company'
    section = 'companies'

    def get_queryset(self):
        _now, _day, month = period_bounds()
        return companies_queryset(month)


class OpenAIUsageView(PlatformContextMixin, TemplateView):
    template_name = 'platform/openai.html'
    section = 'openai'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now, day, month = period_bounds()
        today = usage_summary(day)
        current_month = usage_summary(month)
        current_month['total_tokens'] = current_month['input_tokens'] + current_month['output_tokens']
        context.update({
            'today': today,
            'month': current_month,
            'today_brl': to_brl(today['cost_usd']),
            'month_brl': to_brl(current_month['cost_usd']),
            'companies': usage_by_company(month),
            'daily_json': json.dumps(usage_series(month), default=str),
            'monthly_json': json.dumps(usage_series(now - timedelta(days=365), monthly=True), default=str),
        })
        return context


class FinanceView(PlatformContextMixin, TemplateView):
    template_name = 'platform/finance.html'
    section = 'finance'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['finance'] = financial_metrics()
        return context


class InfrastructureView(PlatformContextMixin, TemplateView):
    template_name = 'platform/infrastructure.html'
    section = 'infrastructure'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['infra'] = infrastructure_snapshot()
        return context


class LogView(PlatformContextMixin, TemplateView):
    template_name = 'platform/logs.html'
    section = 'logs'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = DateRangeFilterForm(self.request.GET or None)
        start = end = None
        if form.is_valid():
            start, end = form.cleaned_data.get('start'), form.cleaned_data.get('end')
        alerts, evolution_events, failed_jobs, ai_errors = filtered_logs(start, end)
        context.update({
            'filter_form': form, 'alerts': alerts,
            'evolution_events': evolution_events, 'failed_jobs': failed_jobs,
            'ai_errors': ai_errors,
        })
        return context


class SubscriptionListView(PlatformContextMixin, ListView):
    template_name = 'platform/subscriptions.html'
    context_object_name = 'subscriptions'
    paginate_by = 50
    section = 'subscriptions'
    queryset = Subscription.objects.select_related('empresa', 'plan').order_by('-updated_at')


class PlatformSettingsView(PlatformContextMixin, TemplateView):
    template_name = 'platform/settings.html'
    section = 'settings'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['settings_data'] = readonly_settings()
        return context
