from django.core.exceptions import PermissionDenied

from .models import CompanyMembership, EmpresaCliente


ROLE_PERMISSIONS = {
    CompanyMembership.Role.OWNER: {'manage_company', 'manage_team', 'manage_billing', 'manage_schedule', 'attend'},
    CompanyMembership.Role.ADMIN: {'manage_team', 'manage_schedule', 'attend'},
    CompanyMembership.Role.RECEPTIONIST: {'manage_schedule', 'attend'},
    CompanyMembership.Role.AGENT: {'attend'},
}


def company_for_user(user):
    owned = EmpresaCliente.objects.filter(usuario=user).first()
    if owned:
        return owned
    membership = CompanyMembership.objects.select_related('empresa').filter(
        user=user, is_active=True, empresa__ativa=True,
    ).first()
    return membership.empresa if membership else None


def ensure_company_for_user(user):
    empresa = company_for_user(user)
    if empresa is not None:
        return empresa
    display_name = user.get_full_name().strip() or user.email or user.username
    empresa, created = EmpresaCliente.objects.get_or_create(
        usuario=user,
        defaults={
            'nome': f'Espaço de {display_name}'[:120],
            'segmento': EmpresaCliente.SEGMENTO_COMERCIO,
            'nome_dono': display_name[:120],
            'ativa': True,
        },
    )
    if created:
        from datetime import timedelta
        from django.conf import settings
        from django.utils import timezone
        from .models import Plan, Subscription
        now = timezone.now()
        plan, _ = Plan.objects.get_or_create(code='trial', defaults={'name': 'Trial', 'price_cents': 0})
        Subscription.objects.create(
            empresa=empresa, plan=plan, status=Subscription.Status.TRIAL,
            trial_started_at=now, trial_ends_at=now + timedelta(days=settings.TRIAL_DAYS),
        )
    return empresa


def role_for_user(user, empresa):
    if empresa.usuario_id == user.id:
        return CompanyMembership.Role.OWNER
    membership = CompanyMembership.objects.filter(
        empresa=empresa, user=user, is_active=True,
    ).first()
    return membership.role if membership else None


def require_permission(user, empresa, permission):
    role = role_for_user(user, empresa)
    if not role or permission not in ROLE_PERMISSIONS.get(role, set()):
        raise PermissionDenied
    return role


class RolePermissionMiddleware:
    PERMISSIONS = {
        'minha_empresa': 'manage_company',
        'configuracao_ia': 'manage_company',
        'trocar_senha': 'manage_company',
        'dados_negocio': 'manage_company',
        'dados_negocio_status': 'manage_company',
        'dados_negocio_excluir': 'manage_company',
        'whatsapp_onboarding': 'manage_company',
        'whatsapp_desconectar': 'manage_company',
        'fluxo': 'manage_company',
        'aplicar_template_fluxo': 'manage_company',
        'agenda_configuracao': 'manage_schedule',
        'agenda_configuracao_excluir': 'manage_schedule',
        'agendamento_novo': 'manage_schedule',
        'agendamento_editar': 'manage_schedule',
        'agendamento_status': 'manage_schedule',
        'lembretes_configuracao': 'manage_schedule',
        'atendimentos': 'attend',
        'atendimento_detalhe': 'attend',
        'assumir_atendimento': 'attend',
        'enviar_mensagem_atendimento': 'attend',
        'finalizar_atendimento': 'attend',
        'atendimento_eventos': 'attend',
        'inbox_eventos': 'attend',
        'security_center': 'manage_company',
        'revoke_api_tokens': 'manage_company',
        'revoke_other_sessions': 'manage_company',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        permission = self.PERMISSIONS.get(request.resolver_match.url_name)
        if permission and request.user.is_authenticated:
            empresa = company_for_user(request.user)
            if not empresa:
                return None
            require_permission(request.user, empresa, permission)
