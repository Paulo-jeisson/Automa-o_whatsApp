from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import login
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.core.mail import send_mail
from django.core.exceptions import ImproperlyConfigured, ValidationError
from datetime import timedelta
import hashlib
from django.utils.crypto import constant_time_compare
import secrets

from .forms import (
    AgendamentoForm,
    CalendarConfigurationForm,
    AIConfigurationForm,
    CompanyInvitationForm,
    AtendimentoSimuladoForm,
    BloqueioAgendaForm,
    ConfiguracoesContaForm,
    DisponibilidadeSemanalForm,
    EmpresaClienteForm,
    FluxoAtendimentoForm,
    ServicoForm,
    RegistrationForm,
    ReminderConfigurationForm,
    KnowledgeBaseArticleForm,
    DataRetentionPolicyForm,
    DataSubjectRequestForm,
    MetaOnboardingVerificationForm,
    BusinessDataImportForm,
    AccountPasswordChangeForm,
)
from .models import (
    Agendamento,
    AIConfiguration,
    AIPromptProfile,
    Atendimento,
    BloqueioAgenda,
    DisponibilidadeSemanal,
    EmpresaCliente,
    FluxoAtendimento,
    Mensagem,
    Servico,
    TEMPLATES_FLUXO,
    WhatsAppIntegration,
    dados_padrao_fluxo,
    template_fluxo_por_segmento,
    CompanyInvitation,
    CompanyMembership,
    CompanyOnboarding,
    Plan,
    ReminderConfiguration,
    Subscription,
    KnowledgeBaseArticle,
    DataRetentionPolicy,
    DataSubjectRequest,
    MetaOnboardingVerification,
    CalendarConfiguration,
    WhatsAppSession,
    BusinessDataSource,
    BusinessDataRecord,
)
from .services.business_data import import_business_data
from .services.scheduling import SchedulingService, SlotUnavailable
from .services.whatsapp.embedded_signup import EmbeddedSignupService
from .services.whatsapp import (
    WhatsAppProviderError,
    build_contact_url,
    notify_attendance,
)
from .services.whatsapp.client import WhatsAppCloudClient
from .services.whatsapp.exceptions import WhatsAppAPIError
from .services.whatsapp.tokens import access_token_for
from .services.whatsapp.outbound import send_text_for_attendance
from .audit import record_audit
from .access import company_for_user, require_permission
from .services.billing import StripeBillingService
from .services.entitlements import EntitlementService


def _safe_next_url(request, fallback='atendimentos'):
    next_url = request.POST.get('next', '')
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return reverse(fallback)


def _public_attendance_url(request, empresa):
    path = empresa.get_atendimento_url()
    if settings.PUBLIC_BASE_URL:
        return f'{settings.PUBLIC_BASE_URL}{path}'
    return request.build_absolute_uri(path)


def landing_page(request):
    mensagem = 'Olá! Quero conhecer o ZapFluxo e automatizar meus atendimentos.'
    whatsapp_url = build_contact_url(settings.ZAPFLUXO_WHATSAPP, mensagem)
    return render(request, 'core/landing_page.html', {
        'whatsapp_url': whatsapp_url,
    })


def cadastro(request):
    if request.user.is_authenticated:
        return redirect('onboarding')
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = form.save(commit=False)
            user.email = form.cleaned_data['email']
            user.save()
            empresa = EmpresaCliente.objects.create(
                usuario=user,
                nome=form.cleaned_data['company_name'],
                segmento=form.cleaned_data['segment'],
                nome_dono=user.get_full_name() or user.username,
            )
            CompanyMembership.objects.create(
                empresa=empresa, user=user, role=CompanyMembership.Role.OWNER,
            )
            plan, _ = Plan.objects.get_or_create(
                code='trial',
                defaults={'name': 'Trial', 'price_cents': 0},
            )
            Subscription.objects.create(
                empresa=empresa, plan=plan,
                trial_ends_at=timezone.now() + timedelta(days=settings.TRIAL_DAYS),
            )
            CompanyOnboarding.objects.create(empresa=empresa)
        login(request, user)
        return redirect('onboarding')
    return render(request, 'core/cadastro.html', {'form': form})


@login_required
def onboarding(request):
    empresa = _empresa_do_usuario(request)
    progress, _ = CompanyOnboarding.objects.get_or_create(empresa=empresa)
    if request.method == 'POST' and request.POST.get('action') == 'test':
        progress.test_completed = True
        progress.save(update_fields=['test_completed'])
    checks = {
        'Empresa criada': True,
        'Serviços cadastrados': empresa.servicos.filter(ativo=True).exists(),
        'Horários cadastrados': empresa.disponibilidades.filter(ativo=True).exists(),
        'WhatsApp conectado': bool(
            getattr(empresa, 'whatsapp_integration', None)
            and empresa.whatsapp_integration.is_connected
        ),
        'IA configurada': bool(
            getattr(empresa, 'ai_configuration', None)
            and empresa.ai_configuration.enabled
        ),
        'Teste realizado': progress.test_completed,
    }
    if all(checks.values()) and not progress.activated_at:
        progress.activated_at = timezone.now()
        progress.save(update_fields=['activated_at'])
    return render(request, 'core/onboarding.html', {
        'empresa': empresa, 'checks': checks, 'progress': progress,
    })


@login_required
def equipe(request):
    empresa = _empresa_do_usuario(request)
    require_permission(request.user, empresa, 'manage_team')
    EntitlementService.require_access(empresa)
    form = CompanyInvitationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        EntitlementService.require_limit(empresa, 'operators')
        raw_token = secrets.token_urlsafe(32)
        invitation = form.save(commit=False)
        invitation.empresa = empresa
        invitation.invited_by = request.user
        invitation.token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        invitation.expires_at = timezone.now() + timedelta(days=7)
        invitation.save()
        url = request.build_absolute_uri(reverse('aceitar_convite', args=[raw_token]))
        send_mail(
            'Convite para o ZapFluxo',
            f'Você foi convidado para {empresa.nome}. Acesse: {url}',
            settings.DEFAULT_FROM_EMAIL,
            [invitation.email],
        )
        record_audit(request, 'team.invitation_created', empresa=empresa, target=invitation)
        messages.success(request, 'Convite enviado.')
        return redirect('equipe')
    return render(request, 'core/equipe.html', {
        'empresa': empresa, 'form': form,
        'memberships': empresa.memberships.select_related('user'),
        'invitations': empresa.invitations.filter(accepted_at__isnull=True),
    })


@login_required
@require_POST
def membro_status(request, membership_id):
    empresa = _empresa_do_usuario(request)
    require_permission(request.user, empresa, 'manage_team')
    membership = get_object_or_404(CompanyMembership, pk=membership_id, empresa=empresa)
    if membership.role == CompanyMembership.Role.OWNER:
        messages.error(request, 'O proprietário não pode ser desativado.')
    else:
        membership.is_active = request.POST.get('active') == '1'
        membership.save(update_fields=['is_active'])
        record_audit(request, 'team.membership_status_changed', empresa=empresa, target=membership)
    return redirect('equipe')


@login_required
def aceitar_convite(request, token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    invitation = get_object_or_404(
        CompanyInvitation, token_hash=token_hash, accepted_at__isnull=True,
        expires_at__gt=timezone.now(),
    )
    if request.user.email.casefold() != invitation.email.casefold():
        messages.error(request, 'Entre com o e-mail que recebeu o convite.')
        return redirect('prompt_generator')
    CompanyMembership.objects.update_or_create(
        empresa=invitation.empresa, user=request.user,
        defaults={
            'role': invitation.role, 'is_active': True,
            'invited_by': invitation.invited_by,
        },
    )
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=['accepted_at'])
    return redirect('prompt_generator')


@login_required
def planos(request):
    empresa = _empresa_do_usuario(request)
    subscription = EntitlementService.subscription(empresa)
    return render(request, 'core/planos.html', {
        'empresa': empresa, 'plans': Plan.objects.filter(is_active=True),
        'subscription': subscription,
    })


@login_required
@require_POST
def iniciar_checkout(request, plan_id):
    empresa = _empresa_do_usuario(request)
    require_permission(request.user, empresa, 'manage_billing')
    plan = get_object_or_404(Plan, pk=plan_id, is_active=True)
    try:
        session = StripeBillingService().create_checkout(
            empresa=empresa, plan=plan,
            success_url=request.build_absolute_uri(reverse('planos')) + '?checkout=success',
            cancel_url=request.build_absolute_uri(reverse('planos')) + '?checkout=cancel',
        )
    except (ImproperlyConfigured, RuntimeError):
        messages.error(request, 'Cobrança temporariamente indisponível.')
        return redirect('planos')
    subscription, _ = Subscription.objects.get_or_create(
        empresa=empresa, defaults={'plan': plan},
    )
    subscription.plan = plan
    subscription.save(update_fields=['plan', 'updated_at'])
    return redirect(session['url'])


@csrf_exempt
@require_POST
def stripe_webhook(request):
    try:
        event = StripeBillingService.verify_event(
            request.body, request.headers.get('Stripe-Signature', ''),
        )
        StripeBillingService.process_event(event)
    except (ValueError, ImproperlyConfigured):
        return JsonResponse({'detail': 'Evento inválido.'}, status=400)
    return JsonResponse({'received': True})


@login_required
def lembretes_configuracao(request):
    empresa = _empresa_do_usuario(request)
    require_permission(request.user, empresa, 'manage_schedule')
    config, _ = ReminderConfiguration.objects.get_or_create(empresa=empresa)
    form = ReminderConfigurationForm(request.POST or None, instance=config)
    if request.method == 'POST' and form.is_valid():
        form.save()
        record_audit(request, 'reminders.configuration_updated', empresa=empresa, target=config)
        messages.success(request, 'Lembretes configurados.')
        return redirect('lembretes_configuracao')
    return render(request, 'core/lembretes_configuracao.html', {
        'empresa': empresa, 'form': form,
    })


def politica_privacidade(request):
    return render(request, 'core/politica_privacidade.html')


def termos_servico(request):
    return render(request, 'core/termos_servico.html')


def exclusao_dados(request):
    return render(request, 'core/exclusao_dados.html')


@login_required
def dashboard(request):
    company_for_user(request.user)
    return redirect('prompt_generator')


@login_required
def minha_empresa(request):
    empresa = company_for_user(request.user)

    if request.method == 'POST':
        if empresa is not None:
            require_permission(request.user, empresa, 'manage_company')
        form = EmpresaClienteForm(request.POST, instance=empresa)
        if form.is_valid():
            empresa = form.save(commit=False)
            if not empresa.usuario_id:
                empresa.usuario = request.user
            empresa.save()
            record_audit(request, 'company.updated', empresa=empresa, target=empresa)
            messages.success(request, 'Dados da empresa salvos com sucesso.')
            return redirect('minha_empresa')
    else:
        initial = {}
        if empresa is None:
            initial = {'nome_dono': request.user.get_full_name() or request.user.username}
        form = EmpresaClienteForm(instance=empresa, initial=initial)

    context = {
        'empresa': empresa,
        'form': form,
        'status_conta': 'Ativa' if empresa and empresa.ativa else 'Pendente',
    }
    return render(request, 'core/minha_empresa.html', context)


@login_required
def configuracoes(request):
    return redirect('trocar_senha')


@login_required
def trocar_senha(request):
    empresa = company_for_user(request.user)
    if request.method == 'POST':
        form = AccountPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            record_audit(request, 'account.password_changed', empresa=empresa)
            messages.success(request, 'Senha alterada com sucesso.')
            return redirect('trocar_senha')
    else:
        form = AccountPasswordChangeForm(request.user)
    return render(request, 'core/trocar_senha.html', {'form': form})


@login_required
def configuracao_ia(request):
    empresa = _empresa_do_usuario(request)
    configuration, _ = AIConfiguration.objects.get_or_create(empresa=empresa)
    if request.method == 'POST':
        form = AIConfigurationForm(request.POST, instance=configuration)
        if form.is_valid():
            configuration = form.save()
            record_audit(
                request,
                'ai.configuration_updated',
                empresa=empresa,
                target=configuration,
                metadata={'enabled': configuration.enabled},
            )
            messages.success(request, 'Configuração da IA salva com sucesso.')
            return redirect('configuracao_ia')
    else:
        form = AIConfigurationForm(instance=configuration)
    return render(request, 'core/configuracao_ia.html', {
        'empresa': empresa,
        'configuration': configuration,
        'form': form,
        'global_ai_enabled': settings.AI_ENABLED,
        'openai_ready': bool(settings.OPENAI_API_KEY),
        'ai_model': settings.AI_MODEL,
    })


@login_required
def base_conhecimento(request):
    empresa = _empresa_do_usuario(request)
    editing = None
    article_id = request.GET.get('editar')
    if article_id:
        editing = get_object_or_404(KnowledgeBaseArticle, pk=article_id, empresa=empresa)
    if request.method == 'POST':
        target_id = request.POST.get('article_id')
        target = get_object_or_404(KnowledgeBaseArticle, pk=target_id, empresa=empresa) if target_id else None
        form = KnowledgeBaseArticleForm(request.POST, request.FILES, instance=target)
        if form.is_valid():
            article = form.save(commit=False)
            article.empresa = empresa
            article.save()
            record_audit(request, 'knowledge.updated', empresa=empresa, target=article)
            messages.success(request, 'Conteúdo salvo na base de conhecimento.')
            return redirect('base_conhecimento')
    else:
        form = KnowledgeBaseArticleForm(instance=editing)
    return render(request, 'core/base_conhecimento.html', {
        'empresa': empresa, 'form': form, 'editing': editing,
        'articles': KnowledgeBaseArticle.objects.filter(empresa=empresa),
    })


@login_required
@require_POST
def base_conhecimento_excluir(request, article_id):
    empresa = _empresa_do_usuario(request)
    article = get_object_or_404(KnowledgeBaseArticle, pk=article_id, empresa=empresa)
    record_audit(request, 'knowledge.deleted', empresa=empresa, target=article)
    article.delete()
    messages.success(request, 'Conteúdo removido.')
    return redirect('base_conhecimento')


@login_required
def dados_negocio(request):
    empresa = _empresa_do_usuario(request)
    query = request.GET.get('q', '').strip()
    results = BusinessDataRecord.objects.none()
    if query:
        terms = [term.casefold() for term in query.split() if len(term) >= 2][:8]
        filters = Q()
        for term in terms:
            filters &= Q(searchable_text__icontains=term)
        if terms:
            results = BusinessDataRecord.objects.filter(
                filters, empresa=empresa, source__is_active=True,
            ).select_related('source')[:30]

    if request.method == 'POST':
        form = BusinessDataImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                source = import_business_data(
                    empresa=empresa, user=request.user,
                    name=form.cleaned_data['name'], data_type=form.cleaned_data['data_type'],
                    uploaded=form.cleaned_data['spreadsheet'],
                    visible_columns=form.cleaned_data['ai_visible_columns'],
                    replace_existing=form.cleaned_data['replace_existing'],
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                record_audit(request, 'business_data.imported', empresa=empresa, target=source, metadata={'rows': source.row_count})
                messages.success(request, f'{source.row_count} registros importados com sucesso.')
                return redirect('dados_negocio')
    else:
        form = BusinessDataImportForm()
    return render(request, 'core/dados_negocio.html', {
        'empresa': empresa, 'form': form, 'sources': BusinessDataSource.objects.filter(empresa=empresa),
        'query': query, 'results': results,
    })


@login_required
@require_POST
def dados_negocio_status(request, source_id):
    empresa = _empresa_do_usuario(request)
    source = get_object_or_404(BusinessDataSource, pk=source_id, empresa=empresa)
    source.is_active = not source.is_active
    source.save(update_fields=['is_active', 'updated_at'])
    record_audit(request, 'business_data.status_changed', empresa=empresa, target=source, metadata={'active': source.is_active})
    return redirect('dados_negocio')


@login_required
@require_POST
def dados_negocio_excluir(request, source_id):
    empresa = _empresa_do_usuario(request)
    source = get_object_or_404(BusinessDataSource, pk=source_id, empresa=empresa)
    record_audit(request, 'business_data.deleted', empresa=empresa, target=source)
    source.delete()
    messages.success(request, 'Base de dados removida.')
    return redirect('dados_negocio')


@login_required
def privacidade_dados(request):
    empresa = _empresa_do_usuario(request)
    policy, _ = DataRetentionPolicy.objects.get_or_create(empresa=empresa)
    request_form = DataSubjectRequestForm(prefix='subject')
    policy_form = DataRetentionPolicyForm(instance=policy, prefix='policy')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'policy':
            policy_form = DataRetentionPolicyForm(request.POST, instance=policy, prefix='policy')
            if policy_form.is_valid():
                policy_form.save()
                record_audit(request, 'privacy.retention_updated', empresa=empresa, target=policy)
                messages.success(request, 'Política de retenção atualizada.')
                return redirect('privacidade_dados')
        elif action == 'request':
            request_form = DataSubjectRequestForm(request.POST, prefix='subject')
            if request_form.is_valid():
                privacy_request = request_form.save(commit=False)
                privacy_request.empresa = empresa
                privacy_request.created_by = request.user
                privacy_request.contact = empresa.contatos.filter(
                    whatsapp_id=privacy_request.whatsapp_id,
                ).first()
                privacy_request.save()
                record_audit(request, 'privacy.request_created', empresa=empresa, target=privacy_request)
                messages.success(request, 'Solicitação registrada.')
                return redirect('privacidade_dados')
    return render(request, 'core/privacidade_dados.html', {
        'empresa': empresa, 'policy_form': policy_form, 'request_form': request_form,
        'privacy_requests': DataSubjectRequest.objects.filter(empresa=empresa),
    })


@login_required
@require_POST
def privacidade_aprovar(request, request_id):
    empresa = _empresa_do_usuario(request)
    privacy_request = get_object_or_404(DataSubjectRequest, pk=request_id, empresa=empresa)
    privacy_request.status = DataSubjectRequest.Status.APPROVED
    privacy_request.verified_at = timezone.now()
    privacy_request.save(update_fields=['status', 'verified_at'])
    record_audit(request, 'privacy.request_approved', empresa=empresa, target=privacy_request)
    return redirect('privacidade_dados')


@login_required
@require_POST
def privacidade_executar(request, request_id):
    from .services.privacy import PrivacyService
    empresa = _empresa_do_usuario(request)
    privacy_request = get_object_or_404(DataSubjectRequest, pk=request_id, empresa=empresa)
    PrivacyService.execute_deletion(privacy_request)
    record_audit(request, 'privacy.deletion_completed', empresa=empresa, target=privacy_request)
    messages.success(request, 'Anonimização concluída e auditada.')
    return redirect('privacidade_dados')


@login_required
def privacidade_exportar(request, request_id):
    from django.http import HttpResponse
    from .services.privacy import PrivacyService
    empresa = _empresa_do_usuario(request)
    privacy_request = get_object_or_404(DataSubjectRequest, pk=request_id, empresa=empresa)
    payload = PrivacyService.serialize_export(PrivacyService.export_subject_data(privacy_request))
    response = HttpResponse(payload, content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="dados-titular-{privacy_request.pk}.json"'
    record_audit(request, 'privacy.data_exported', empresa=empresa, target=privacy_request)
    return response


@login_required
def whatsapp_onboarding(request):
    empresa = _empresa_do_usuario(request)
    integration = getattr(empresa, 'whatsapp_integration', None)
    if request.method == 'POST':
        if integration is None:
            EntitlementService.require_limit(empresa, 'whatsapps')
        expected_nonce = request.session.pop('whatsapp_onboarding_nonce', '')
        received_nonce = request.POST.get('nonce', '')
        if not expected_nonce or not constant_time_compare(expected_nonce, received_nonce):
            messages.error(request, 'A sessão de conexão expirou. Tente novamente.')
            return redirect('whatsapp_onboarding')
        try:
            EmbeddedSignupService.connect(
                empresa=empresa,
                code=request.POST.get('code', ''),
                waba_id=request.POST.get('waba_id', ''),
                phone_number_id=request.POST.get('phone_number_id', ''),
            )
        except WhatsAppProviderError as error:
            messages.error(request, str(error))
        else:
            integration = getattr(empresa, 'whatsapp_integration', None)
            record_audit(request, 'whatsapp.connected', empresa=empresa, target=integration)
            messages.success(request, 'WhatsApp conectado com sucesso à sua empresa.')
            return redirect('configuracoes')

    nonce = secrets.token_urlsafe(32)
    request.session['whatsapp_onboarding_nonce'] = nonce
    return render(request, 'core/whatsapp_onboarding.html', {
        'empresa': empresa, 'integration': integration, 'nonce': nonce,
        'meta_app_id': settings.META_APP_ID,
        'meta_config_id': settings.META_EMBEDDED_SIGNUP_CONFIG_ID,
        'meta_api_version': settings.META_GRAPH_API_VERSION,
        'embedded_signup_ready': all([
            settings.META_APP_ID,
            settings.META_APP_SECRET,
            settings.META_EMBEDDED_SIGNUP_CONFIG_ID,
        ]),
    })


@login_required
@require_POST
def whatsapp_desconectar(request):
    empresa = _empresa_do_usuario(request)
    integration = get_object_or_404(WhatsAppIntegration, company=empresa)
    try:
        EmbeddedSignupService.disconnect(integration)
    except WhatsAppProviderError as error:
        messages.error(request, str(error))
    else:
        record_audit(request, 'whatsapp.disconnected', empresa=empresa, target=integration)
        messages.success(request, 'WhatsApp desconectado desta empresa.')
    return redirect('configuracoes')


@login_required
def meta_producao(request):
    from .services.meta_readiness import meta_production_readiness
    empresa = _empresa_do_usuario(request)
    integration = getattr(empresa, 'whatsapp_integration', None)
    form = MetaOnboardingVerificationForm()
    if request.method == 'POST':
        if not integration:
            messages.error(request, 'Conecte o WhatsApp antes de registrar o teste real.')
            return redirect('meta_producao')
        form = MetaOnboardingVerificationForm(request.POST)
        if form.is_valid():
            verification = form.save(commit=False)
            verification.empresa = empresa
            verification.integration = integration
            verification.verified_by = request.user
            verification.save()
            record_audit(request, 'meta.production_verified', empresa=empresa, target=verification)
            messages.success(request, 'Verificação real registrada e auditada.')
            return redirect('meta_producao')
    return render(request, 'core/meta_producao.html', {
        'empresa': empresa, 'form': form,
        'report': meta_production_readiness(empresa),
    })


@login_required
def metricas_ia(request):
    from .services.analytics import company_metrics
    empresa = _empresa_do_usuario(request)
    return render(request, 'core/metricas_ia.html', {
        'empresa': empresa, 'metrics': company_metrics(empresa),
    })


@login_required
@require_POST
def testar_integracao_whatsapp(request):
    empresa = company_for_user(request.user)
    integration = WhatsAppIntegration.objects.filter(
        company=empresa,
        is_active=True,
    ).first()
    if integration is None:
        messages.error(
            request,
            'Esta empresa ainda não possui uma integração ativa do WhatsApp.',
        )
        return redirect('configuracoes')
    try:
        WhatsAppCloudClient(
            phone_number_id=integration.phone_number_id,
            access_token=access_token_for(integration),
        ).test_configuration(integration.phone_number_id)
    except (WhatsAppProviderError, WhatsAppAPIError) as error:
        if isinstance(error, WhatsAppAPIError):
            if error.error_code == '190':
                detail = 'token inválido ou expirado; reconecte o WhatsApp.'
            elif error.status_code == 403:
                detail = 'a autorização não possui acesso a este número.'
            elif error.status_code == 400:
                detail = 'o número ou a autorização não foram aceitos.'
            else:
                detail = 'a credencial ou o número não foram aceitos.'
            messages.error(
                request,
                f'Não foi possível validar a conexão com o WhatsApp: {detail}',
            )
            return redirect('configuracoes')
        messages.error(request, str(error))
    else:
        record_audit(request, 'whatsapp.connection_tested', empresa=empresa, target=integration)
        messages.success(request, 'Integração validada com sucesso na Meta.')
    return redirect('configuracoes')


@login_required
def fluxo(request):
    empresa = company_for_user(request.user)
    if empresa is None:
        messages.warning(request, 'Cadastre sua empresa antes de configurar o fluxo.')
        return redirect('minha_empresa')

    fluxo_obj, _ = FluxoAtendimento.objects.get_or_create(
        empresa=empresa,
        defaults=dados_padrao_fluxo(empresa),
    )

    if request.method == 'POST':
        form = FluxoAtendimentoForm(request.POST, instance=fluxo_obj)
        if form.is_valid():
            saved_flow = form.save()
            record_audit(request, 'flow.updated', empresa=empresa, target=saved_flow)
            messages.success(request, 'Fluxo de atendimento salvo com sucesso.')
            return redirect('fluxo')
    else:
        form = FluxoAtendimentoForm(instance=fluxo_obj)

    context = {
        'empresa': empresa,
        'fluxo': fluxo_obj,
        'form': form,
        'public_url': _public_attendance_url(request, empresa),
        'status_conta': 'Ativa' if empresa.ativa else 'Pendente',
        'templates_fluxo': [
            {
                'segmento': segmento,
                'nome': template['nome'],
                'descricao': template['descricao'],
                'opcoes': template['opcoes'],
                'ativo': segmento == empresa.segmento,
            }
            for segmento, template in TEMPLATES_FLUXO.items()
        ],
    }
    return render(request, 'core/fluxo.html', context)


@login_required
@require_POST
def aplicar_template_fluxo(request):
    empresa = company_for_user(request.user)
    if empresa is None:
        messages.warning(request, 'Cadastre sua empresa antes de escolher um modelo.')
        return redirect('minha_empresa')

    segmento = request.POST.get('segmento', '')
    if segmento not in TEMPLATES_FLUXO:
        messages.error(request, 'O modelo de atendimento informado não é válido.')
        return redirect('fluxo')

    template_data = template_fluxo_por_segmento(segmento, empresa.nome)
    with transaction.atomic():
        if empresa.segmento != segmento:
            empresa.segmento = segmento
            empresa.save(update_fields=['segmento', 'atualizada_em'])
        FluxoAtendimento.objects.update_or_create(
            empresa=empresa,
            defaults=template_data,
        )
    record_audit(
        request,
        'flow.template_applied',
        empresa=empresa,
        target=empresa,
        metadata={'segment': segmento},
    )

    messages.success(
        request,
        f'Modelo de {TEMPLATES_FLUXO[segmento]["nome"]} aplicado ao seu fluxo.',
    )
    return redirect('fluxo')


def atendimento_publico(request, public_slug):
    empresa = get_object_or_404(EmpresaCliente, public_slug=public_slug, ativa=True)
    fluxo_obj, _ = FluxoAtendimento.objects.get_or_create(
        empresa=empresa,
        defaults=dados_padrao_fluxo(empresa),
    )
    atendimento_salvo = False

    if request.method == 'POST':
        form = AtendimentoSimuladoForm(request.POST, fluxo=fluxo_obj)
        if form.is_valid():
            atendimento = form.save(commit=False)
            atendimento.empresa = empresa
            atendimento.save()
            atendimento_salvo = True
            form = AtendimentoSimuladoForm(fluxo=fluxo_obj)
    else:
        form = AtendimentoSimuladoForm(fluxo=fluxo_obj)

    context = {
        'empresa': empresa,
        'fluxo': fluxo_obj,
        'form': form,
        'atendimento_salvo': atendimento_salvo,
    }
    return render(request, 'core/atendimento_publico.html', context)


@login_required
def atendimentos(request):
    empresa = company_for_user(request.user)
    if empresa is None:
        messages.warning(request, 'Cadastre sua empresa antes de acompanhar atendimentos.')
        return redirect('minha_empresa')

    hoje = timezone.localdate()
    atendimentos_base = empresa.atendimentos.select_related(
        'empresa', 'contato', 'assigned_to',
    ).prefetch_related('mensagens').order_by('-last_message_at', '-criado_em')
    atendimentos_filtrados = atendimentos_base

    status = request.GET.get('status', '')
    data = request.GET.get('data', '')
    segmento = request.GET.get('segmento', '')
    fila = request.GET.get('fila', '')

    if status:
        atendimentos_filtrados = atendimentos_filtrados.filter(status=status)

    if data:
        atendimentos_filtrados = atendimentos_filtrados.filter(criado_em__date=data)

    if segmento:
        atendimentos_filtrados = atendimentos_filtrados.filter(empresa__segmento=segmento)
    if fila == 'new':
        atendimentos_filtrados = atendimentos_filtrados.filter(
            Q(mensagens__isnull=True) | Q(automation_enabled=False),
            status=Atendimento.STATUS_NOVO,
        ).distinct()
    elif fila == 'ai':
        atendimentos_filtrados = atendimentos_filtrados.filter(
            automation_enabled=True, mensagens__isnull=False,
        ).distinct()
    elif fila == 'waiting_human':
        atendimentos_filtrados = atendimentos_filtrados.filter(
            current_step=Atendimento.Step.WAITING_HUMAN,
        )
    elif fila == 'human':
        atendimentos_filtrados = atendimentos_filtrados.filter(
            current_step=Atendimento.Step.HUMAN,
        )
    elif fila == 'finished':
        atendimentos_filtrados = atendimentos_filtrados.filter(
            status=Atendimento.STATUS_FINALIZADO,
        )

    context = {
        'empresa': empresa,
        'atendimentos': atendimentos_filtrados,
        'total_atendimentos': atendimentos_base.count(),
        'atendimentos_hoje': atendimentos_base.filter(criado_em__date=hoje).count(),
        'novos_atendimentos': atendimentos_base.filter(status=Atendimento.STATUS_NOVO).count(),
        'status_choices': Atendimento.STATUS_CHOICES,
        'segmento_choices': EmpresaCliente.SEGMENTO_CHOICES,
        'filtros': {
            'status': status,
            'data': data,
            'segmento': segmento,
            'fila': fila,
        },
        'fila_choices': [
            ('new', 'Novos'),
            ('ai', 'IA atendendo'),
            ('waiting_human', 'Aguardando humano'),
            ('human', 'Em atendimento humano'),
            ('finished', 'Finalizados'),
        ],
        'status_conta': 'Ativa' if empresa.ativa else 'Pendente',
    }
    return render(request, 'core/atendimentos.html', context)


@login_required
@require_POST
def atualizar_status_atendimento(request, atendimento_id):
    empresa = company_for_user(request.user)
    atendimento = get_object_or_404(Atendimento, pk=atendimento_id, empresa=empresa)
    novo_status = request.POST.get('status')
    status_validos = {status for status, _label in Atendimento.STATUS_CHOICES}

    if novo_status in status_validos:
        previous_status = atendimento.status
        atendimento.status = novo_status
        update_fields = ['status']
        if novo_status == Atendimento.STATUS_FINALIZADO:
            atendimento.current_step = Atendimento.Step.FINISHED
            atendimento.automation_enabled = False
            atendimento.closed_by = request.user
            atendimento.closed_at = timezone.now()
            update_fields.extend([
                'current_step', 'automation_enabled', 'closed_by', 'closed_at',
            ])
        elif previous_status == Atendimento.STATUS_FINALIZADO:
            atendimento.current_step = Atendimento.Step.MENU
            atendimento.closed_by = None
            atendimento.closed_at = None
            update_fields.extend(['current_step', 'closed_by', 'closed_at'])
        atendimento.save(update_fields=update_fields)
        record_audit(
            request,
            'attendance.status_changed',
            empresa=empresa,
            target=atendimento,
            metadata={'from': previous_status, 'to': novo_status},
        )
        messages.success(request, 'Status do atendimento atualizado.')
    else:
        messages.error(request, 'O status informado não é válido.')

    return redirect(_safe_next_url(request))


@login_required
@require_POST
def avisar_whatsapp_atendimento(request, atendimento_id):
    empresa = company_for_user(request.user)
    atendimento = get_object_or_404(Atendimento, pk=atendimento_id, empresa=empresa)

    if not empresa.whatsapp_dono:
        messages.error(request, 'Cadastre o WhatsApp do dono antes de enviar avisos.')
        return redirect(_safe_next_url(request))

    try:
        result = notify_attendance(atendimento)
    except WhatsAppProviderError as error:
        messages.error(request, str(error))
        return redirect(_safe_next_url(request))

    atendimento.avisado_em = timezone.now()
    atendimento.save(update_fields=['avisado_em'])

    if result.redirect_url:
        return redirect(result.redirect_url)

    messages.success(request, 'Aviso enviado pelo WhatsApp.')
    return redirect(_safe_next_url(request))


def _empresa_do_usuario(request):
    empresa = company_for_user(request.user)
    if not empresa:
        from django.http import Http404
        raise Http404
    return empresa


@login_required
def agenda(request):
    empresa = _empresa_do_usuario(request)
    from core.application.calendar_configuration_service import CalendarConfigurationService
    config = getattr(empresa, 'calendar_configuration', None)
    initial = CalendarConfigurationService.initial_for(empresa) if config is None else None
    form = CalendarConfigurationForm(request.POST or None, instance=config, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            config = CalendarConfigurationService.save(empresa=empresa, data=form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            record_audit(request, 'calendar.configuration_saved', empresa=empresa, target=config)
            messages.success(request, 'Configuração do calendário salva.')
            return redirect('agenda')
    appointments = empresa.agendamentos.select_related('contato', 'servico').filter(
        data__gte=timezone.localdate(), status__in=[Agendamento.Status.PENDING, Agendamento.Status.CONFIRMED],
    ).order_by('data', 'hora_inicio')[:20]
    return render(request, 'core/agenda.html', {
        'empresa': empresa, 'agendamentos': appointments, 'calendar_form': form,
        'calendar_configuration': config,
    })


@login_required
def agendamento_novo(request):
    empresa = _empresa_do_usuario(request)
    form = AgendamentoForm(request.POST or None, empresa=empresa)
    if request.method == 'POST' and form.is_valid():
        try:
            appointment = SchedulingService.create_appointment(
                empresa=empresa, contato=form.save_contact(), servico=form.cleaned_data['servico'],
                date=form.cleaned_data['data'], start_time=form.cleaned_data['hora_inicio'],
                origem=Agendamento.Origem.MANUAL, observacao=form.cleaned_data['observacao'],
            )
        except SlotUnavailable as error:
            form.add_error('hora_inicio', error.message)
        else:
            if form.cleaned_data['status'] != Agendamento.Status.CONFIRMED:
                appointment.status = form.cleaned_data['status']
                appointment.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Agendamento criado com sucesso.')
            return redirect('agendamento_detalhe', agendamento_id=appointment.pk)
    return render(request, 'core/agendamento_form.html', {'empresa': empresa, 'form': form, 'titulo': 'Novo agendamento'})


@login_required
def agendamento_detalhe(request, agendamento_id):
    empresa = _empresa_do_usuario(request)
    appointment = get_object_or_404(Agendamento.objects.select_related('contato', 'servico', 'atendimento'), pk=agendamento_id, empresa=empresa)
    return render(request, 'core/agendamento_detalhe.html', {'empresa': empresa, 'agendamento': appointment})


@login_required
def agendamento_editar(request, agendamento_id):
    empresa = _empresa_do_usuario(request)
    appointment = get_object_or_404(Agendamento, pk=agendamento_id, empresa=empresa)
    form = AgendamentoForm(request.POST or None, instance=appointment, empresa=empresa)
    if request.method == 'POST' and form.is_valid():
        old_status = appointment.status
        appointment.status = Agendamento.Status.CANCELLED
        appointment.save(update_fields=['status', 'updated_at'])
        try:
            replacement = SchedulingService.create_appointment(
                empresa=empresa, contato=form.save_contact(), servico=form.cleaned_data['servico'],
                date=form.cleaned_data['data'], start_time=form.cleaned_data['hora_inicio'],
                atendimento=appointment.atendimento, origem=appointment.origem,
                observacao=form.cleaned_data['observacao'],
            )
        except SlotUnavailable as error:
            appointment.status = old_status
            appointment.save(update_fields=['status', 'updated_at'])
            form.add_error('hora_inicio', error.message)
        else:
            replacement.status = form.cleaned_data['status']
            replacement.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Agendamento remarcado com sucesso.')
            return redirect('agendamento_detalhe', agendamento_id=replacement.pk)
    return render(request, 'core/agendamento_form.html', {'empresa': empresa, 'form': form, 'titulo': 'Editar ou remarcar'})


@login_required
@require_POST
def agendamento_status(request, agendamento_id):
    empresa = _empresa_do_usuario(request)
    appointment = get_object_or_404(Agendamento, pk=agendamento_id, empresa=empresa)
    status = request.POST.get('status')
    if status in Agendamento.Status.values:
        appointment.status = status
        if status == Agendamento.Status.CANCELLED:
            appointment.cancelled_at = timezone.now()
        appointment.save(update_fields=['status', 'cancelled_at', 'updated_at'])
        messages.success(request, 'Agendamento atualizado.')
    return redirect('agendamento_detalhe', agendamento_id=appointment.pk)


@login_required
def atendimento_detalhe(request, atendimento_id):
    empresa = _empresa_do_usuario(request)
    atendimento = get_object_or_404(Atendimento.objects.prefetch_related('mensagens', 'agendamentos__servico'), pk=atendimento_id, empresa=empresa)
    return render(request, 'core/atendimento_detalhe.html', {'empresa': empresa, 'atendimento': atendimento})


@login_required
@require_POST
def assumir_atendimento(request, atendimento_id):
    empresa = _empresa_do_usuario(request)
    with transaction.atomic():
        atendimento = get_object_or_404(
            Atendimento.objects.select_for_update(), pk=atendimento_id, empresa=empresa,
        )
        atendimento.current_step = Atendimento.Step.HUMAN
        atendimento.automation_enabled = False
        atendimento.status = Atendimento.STATUS_EM_ANDAMENTO
        atendimento.assigned_to = request.user
        atendimento.assigned_at = timezone.now()
        conversation_state = dict(atendimento.conversation_state or {})
        conversation_state['handoff_type'] = 'HANDOFF_MANUAL_BY_AGENT'
        atendimento.conversation_state = conversation_state
        atendimento.save(update_fields=[
            'current_step', 'automation_enabled', 'status',
            'assigned_to', 'assigned_at', 'conversation_state',
        ])
    record_audit(request, 'attendance.assigned_to_human', empresa=empresa, target=atendimento)
    messages.success(request, 'Atendimento assumido pela equipe.')
    return redirect('atendimento_detalhe', atendimento_id=atendimento.pk)


@login_required
@require_POST
def enviar_mensagem_atendimento(request, atendimento_id):
    empresa = _empresa_do_usuario(request)
    text = request.POST.get('texto', '').strip()
    if not text or len(text) > 4000:
        messages.error(request, 'Informe uma mensagem com até 4.000 caracteres.')
        return redirect('atendimento_detalhe', atendimento_id=atendimento_id)
    try:
        with transaction.atomic():
            atendimento = get_object_or_404(
                Atendimento.objects.select_for_update(),
                pk=atendimento_id,
                empresa=empresa,
            )
            if (
                atendimento.current_step != Atendimento.Step.HUMAN
                or atendimento.assigned_to_id != request.user.id
            ):
                messages.error(request, 'Assuma o atendimento antes de responder.')
                return redirect('atendimento_detalhe', atendimento_id=atendimento_id)
            outbound = send_text_for_attendance(atendimento, text)
            outbound.sent_by = request.user
            outbound.save(update_fields=['sent_by'])
    except (WhatsAppAPIError, WhatsAppProviderError):
        messages.error(request, 'Não foi possível enviar a mensagem pelo WhatsApp.')
    else:
        record_audit(
            request, 'attendance.manual_message_sent',
            empresa=empresa, target=atendimento,
        )
        messages.success(request, 'Mensagem enviada.')
    return redirect('atendimento_detalhe', atendimento_id=atendimento_id)


@login_required
@require_POST
def devolver_atendimento_ia(request, atendimento_id):
    empresa = _empresa_do_usuario(request)
    require_permission(request.user, empresa, 'attend')
    configuration = AIConfiguration.objects.filter(empresa=empresa, enabled=True).first()
    if not configuration or not configuration.is_available:
        messages.error(request, 'A IA desta empresa não está disponível.')
        return redirect('atendimento_detalhe', atendimento_id=atendimento_id)
    if not AIPromptProfile.objects.filter(
        empresa=empresa, generated_prompt__regex=r'\S',
    ).exists():
        messages.error(request, 'Ative o Prompt da IA antes de retomar a automação.')
        return redirect('atendimento_detalhe', atendimento_id=atendimento_id)
    if not WhatsAppSession.objects.filter(empresa=empresa, state='CONNECTED').exists():
        messages.error(request, 'Conecte a sessão do WhatsApp antes de retomar a automação.')
        return redirect('atendimento_detalhe', atendimento_id=atendimento_id)
    with transaction.atomic():
        atendimento = get_object_or_404(
            Atendimento.objects.select_for_update(), pk=atendimento_id, empresa=empresa,
        )
        if atendimento.current_step not in {
            Atendimento.Step.WAITING_HUMAN, Atendimento.Step.HUMAN,
        }:
            messages.error(request, 'Somente atendimentos em modo humano podem retomar a IA.')
            return redirect('atendimento_detalhe', atendimento_id=atendimento.pk)
        previous_step = atendimento.current_step
        conversation_state = dict(atendimento.conversation_state or {})
        conversation_state.pop('handoff_reason', None)
        conversation_state.pop('handoff_type', None)
        atendimento.current_step = Atendimento.Step.MENU
        atendimento.automation_enabled = True
        atendimento.status = Atendimento.STATUS_EM_ANDAMENTO
        atendimento.assigned_to = None
        atendimento.assigned_at = None
        atendimento.handoff_reason = ''
        atendimento.conversation_state = conversation_state
        atendimento.save(update_fields=[
            'current_step', 'automation_enabled', 'status', 'assigned_to',
            'assigned_at', 'handoff_reason', 'conversation_state',
        ])
    record_audit(
        request, 'attendance.returned_to_ai', empresa=empresa, target=atendimento,
        metadata={'previous_step': previous_step, 'new_step': Atendimento.Step.MENU},
    )
    messages.success(request, 'IA retomada. Ela responderá somente à próxima mensagem recebida.')
    return redirect('atendimento_detalhe', atendimento_id=atendimento.pk)


@login_required
@require_POST
def finalizar_atendimento(request, atendimento_id):
    empresa = _empresa_do_usuario(request)
    with transaction.atomic():
        atendimento = get_object_or_404(
            Atendimento.objects.select_for_update(), pk=atendimento_id, empresa=empresa,
        )
        atendimento.current_step = Atendimento.Step.FINISHED
        atendimento.automation_enabled = False
        atendimento.status = Atendimento.STATUS_FINALIZADO
        atendimento.closed_by = request.user
        atendimento.closed_at = timezone.now()
        atendimento.save(update_fields=[
            'current_step', 'automation_enabled', 'status',
            'closed_by', 'closed_at',
        ])
    record_audit(request, 'attendance.finished', empresa=empresa, target=atendimento)
    messages.success(request, 'Atendimento finalizado.')
    return redirect('atendimento_detalhe', atendimento_id=atendimento.pk)


@login_required
def atendimento_eventos(request, atendimento_id):
    empresa = _empresa_do_usuario(request)
    atendimento = get_object_or_404(
        Atendimento.objects.select_related('assigned_to'),
        pk=atendimento_id,
        empresa=empresa,
    )
    try:
        after_id = max(int(request.GET.get('after', '0')), 0)
    except ValueError:
        after_id = 0
    message_items = atendimento.mensagens.filter(pk__gt=after_id).order_by('pk')[:100]
    return JsonResponse({
        'attendance': {
            'id': atendimento.pk,
            'state': atendimento.inbox_state,
            'step': atendimento.current_step,
            'automation_enabled': atendimento.automation_enabled,
            'assigned_to': atendimento.assigned_to.get_username() if atendimento.assigned_to else '',
        },
        'messages': [
            {
                'id': item.pk,
                'direction': item.direcao,
                'text': item.texto,
                'status': item.status,
                'created_at': item.criado_em.isoformat(),
                'sent_by': item.sent_by.get_username() if item.sent_by else '',
            }
            for item in message_items.select_related('sent_by')
        ],
    })


@login_required
def inbox_eventos(request):
    empresa = _empresa_do_usuario(request)
    items = empresa.atendimentos.select_related('assigned_to').prefetch_related(
        'mensagens',
    ).order_by('-last_message_at', '-criado_em')[:100]
    return JsonResponse({'attendances': [
        {
            'id': item.pk,
            'name': item.nome_cliente,
            'state': item.inbox_state,
            'step': item.current_step,
            'last_message_at': (
                item.last_message_at.isoformat() if item.last_message_at else ''
            ),
            'assigned_to': item.assigned_to.get_username() if item.assigned_to else '',
        }
        for item in items
    ]})


@login_required
def agenda_configuracao(request):
    empresa = _empresa_do_usuario(request)
    forms = {'servico': ServicoForm(prefix='servico', empresa=empresa), 'horario': DisponibilidadeSemanalForm(prefix='horario'), 'bloqueio': BloqueioAgendaForm(prefix='bloqueio')}
    if request.method == 'POST':
        kind = request.POST.get('tipo')
        form_classes = {'servico': ServicoForm, 'horario': DisponibilidadeSemanalForm, 'bloqueio': BloqueioAgendaForm}
        if kind in form_classes:
            form_kwargs = {'empresa': empresa} if kind == 'servico' else {}
            forms[kind] = form_classes[kind](request.POST, prefix=kind, **form_kwargs)
            if forms[kind].is_valid():
                item = forms[kind].save(commit=False)
                item.empresa = empresa
                try:
                    with transaction.atomic():
                        EmpresaCliente.objects.select_for_update().get(pk=empresa.pk)
                        if kind == 'servico' and Servico.objects.filter(
                            empresa=empresa,
                            nome__iexact=item.nome,
                        ).exists():
                            forms[kind].add_error('nome', 'Já existe um serviço com esse nome.')
                        else:
                            item.save()
                except IntegrityError:
                    if kind == 'servico':
                        forms[kind].add_error('nome', 'Já existe um serviço com esse nome.')
                    else:
                        raise
                if not forms[kind].errors:
                    record_audit(
                        request,
                        'schedule.configuration_added',
                        empresa=empresa,
                        target=item,
                        metadata={'kind': kind},
                    )
                    messages.success(request, 'Configuração adicionada.')
                    return redirect('agenda_configuracao')
    return render(request, 'core/agenda_configuracao.html', {
        'empresa': empresa, **forms, 'servicos': empresa.servicos.all(),
        'horarios': empresa.disponibilidades.all(), 'bloqueios': empresa.bloqueios_agenda.all(),
    })


@login_required
@require_POST
def agenda_configuracao_excluir(request, tipo, objeto_id):
    empresa = _empresa_do_usuario(request)
    model = {'servico': Servico, 'horario': DisponibilidadeSemanal, 'bloqueio': BloqueioAgenda}.get(tipo)
    if model:
        item = get_object_or_404(model, pk=objeto_id, empresa=empresa)
        record_audit(
            request,
            'schedule.configuration_deleted',
            empresa=empresa,
            target=item,
            metadata={'kind': tipo},
        )
        item.delete()
        messages.success(request, 'Configuração removida.')
    return redirect('agenda_configuracao')
