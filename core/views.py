from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.utils.crypto import constant_time_compare
import secrets

from .forms import (
    AgendamentoForm,
    AtendimentoSimuladoForm,
    BloqueioAgendaForm,
    ConfiguracoesContaForm,
    DisponibilidadeSemanalForm,
    EmpresaClienteForm,
    FluxoAtendimentoForm,
    ServicoForm,
)
from .models import (
    Agendamento,
    Atendimento,
    BloqueioAgenda,
    DisponibilidadeSemanal,
    EmpresaCliente,
    FluxoAtendimento,
    Servico,
    TEMPLATES_FLUXO,
    WhatsAppIntegration,
    dados_padrao_fluxo,
    template_fluxo_por_segmento,
)
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


def politica_privacidade(request):
    return render(request, 'core/politica_privacidade.html')


def termos_servico(request):
    return render(request, 'core/termos_servico.html')


def exclusao_dados(request):
    return render(request, 'core/exclusao_dados.html')


@login_required
def dashboard(request):
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
    public_url = _public_attendance_url(request, empresa) if empresa else ''
    hoje = timezone.localdate()
    atendimentos = empresa.atendimentos.all() if empresa else Atendimento.objects.none()
    context = {
        'empresa': empresa,
        'public_url': public_url,
        'total_atendimentos': atendimentos.count(),
        'atendimentos_hoje': atendimentos.filter(criado_em__date=hoje).count(),
        'novos_atendimentos': atendimentos.filter(status=Atendimento.STATUS_NOVO).count(),
        'ultimos_atendimentos': atendimentos[:5],
        'status_conta': 'Ativa' if empresa and empresa.ativa else 'Pendente',
        'agendamentos_hoje': empresa.agendamentos.filter(data=hoje).count() if empresa else 0,
        'agendamentos_confirmados': empresa.agendamentos.filter(status=Agendamento.Status.CONFIRMED).count() if empresa else 0,
        'agendamentos_pendentes': empresa.agendamentos.filter(status=Agendamento.Status.PENDING).count() if empresa else 0,
        'aguardando_humano': atendimentos.filter(current_step=Atendimento.Step.WAITING_HUMAN).count(),
        'atendimentos_ativos': atendimentos.exclude(status=Atendimento.STATUS_FINALIZADO).count(),
    }
    return render(request, 'core/dashboard.html', context)


@login_required
def minha_empresa(request):
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()

    if request.method == 'POST':
        form = EmpresaClienteForm(request.POST, instance=empresa)
        if form.is_valid():
            empresa = form.save(commit=False)
            empresa.usuario = request.user
            empresa.save()
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
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
    whatsapp_integration = (
        getattr(empresa, 'whatsapp_integration', None)
        if empresa else None
    )

    if request.method == 'POST':
        form = ConfiguracoesContaForm(request.POST, user=request.user)
        if form.is_valid():
            user, password_changed = form.save()
            if password_changed:
                update_session_auth_hash(request, user)
            messages.success(request, 'Configurações da conta salvas com sucesso.')
            return redirect('configuracoes')
    else:
        form = ConfiguracoesContaForm(user=request.user)

    return render(request, 'core/configuracoes.html', {
        'empresa': empresa,
        'form': form,
        'status_conta': 'Ativa' if empresa and empresa.ativa else 'Pendente',
        'provider_whatsapp': settings.WHATSAPP_PROVIDER,
        'whatsapp_integration': whatsapp_integration,
        'embedded_signup_ready': all([
            settings.META_APP_ID,
            settings.META_APP_SECRET,
            settings.META_EMBEDDED_SIGNUP_CONFIG_ID,
        ]),
    })


@login_required
def whatsapp_onboarding(request):
    empresa = _empresa_do_usuario(request)
    integration = getattr(empresa, 'whatsapp_integration', None)
    if request.method == 'POST':
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
        messages.success(request, 'WhatsApp desconectado desta empresa.')
    return redirect('configuracoes')


@login_required
@require_POST
def testar_integracao_whatsapp(request):
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
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
        messages.success(request, 'Integração validada com sucesso na Meta.')
    return redirect('configuracoes')


@login_required
def fluxo(request):
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
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
            form.save()
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
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
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
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
    if empresa is None:
        messages.warning(request, 'Cadastre sua empresa antes de acompanhar atendimentos.')
        return redirect('minha_empresa')

    hoje = timezone.localdate()
    atendimentos_base = empresa.atendimentos.select_related('empresa')
    atendimentos_filtrados = atendimentos_base

    status = request.GET.get('status', '')
    data = request.GET.get('data', '')
    segmento = request.GET.get('segmento', '')

    if status:
        atendimentos_filtrados = atendimentos_filtrados.filter(status=status)

    if data:
        atendimentos_filtrados = atendimentos_filtrados.filter(criado_em__date=data)

    if segmento:
        atendimentos_filtrados = atendimentos_filtrados.filter(empresa__segmento=segmento)

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
        },
        'status_conta': 'Ativa' if empresa.ativa else 'Pendente',
    }
    return render(request, 'core/atendimentos.html', context)


@login_required
@require_POST
def atualizar_status_atendimento(request, atendimento_id):
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
    atendimento = get_object_or_404(Atendimento, pk=atendimento_id, empresa=empresa)
    novo_status = request.POST.get('status')
    status_validos = {status for status, _label in Atendimento.STATUS_CHOICES}

    if novo_status in status_validos:
        atendimento.status = novo_status
        atendimento.save(update_fields=['status'])
        messages.success(request, 'Status do atendimento atualizado.')
    else:
        messages.error(request, 'O status informado não é válido.')

    return redirect(_safe_next_url(request))


@login_required
@require_POST
def avisar_whatsapp_atendimento(request, atendimento_id):
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
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
    return get_object_or_404(EmpresaCliente, usuario=request.user)


@login_required
def agenda(request):
    empresa = _empresa_do_usuario(request)
    start = request.GET.get('inicio') or timezone.localdate().isoformat()
    end = request.GET.get('fim') or start
    appointments = empresa.agendamentos.select_related('contato', 'servico').filter(data__range=(start, end))
    status = request.GET.get('status', '')
    if status in Agendamento.Status.values:
        appointments = appointments.filter(status=status)
    return render(request, 'core/agenda.html', {
        'empresa': empresa, 'agendamentos': appointments, 'status_choices': Agendamento.Status.choices,
        'filtros': {'inicio': start, 'fim': end, 'status': status},
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
    if status in (Agendamento.Status.CANCELLED, Agendamento.Status.COMPLETED):
        appointment.status = status
        appointment.save(update_fields=['status', 'updated_at'])
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
    atendimento = get_object_or_404(Atendimento, pk=atendimento_id, empresa=empresa)
    atendimento.current_step = Atendimento.Step.HUMAN
    atendimento.automation_enabled = False
    atendimento.status = Atendimento.STATUS_EM_ANDAMENTO
    atendimento.save(update_fields=['current_step', 'automation_enabled', 'status'])
    messages.success(request, 'Atendimento assumido pela equipe.')
    return redirect('atendimento_detalhe', atendimento_id=atendimento.pk)


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
        get_object_or_404(model, pk=objeto_id, empresa=empresa).delete()
        messages.success(request, 'Configuração removida.')
    return redirect('agenda_configuracao')
