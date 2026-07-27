from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import (
    AtendimentoSimuladoForm,
    ConfiguracoesContaForm,
    EmpresaClienteForm,
    FluxoAtendimentoForm,
)
from .models import (
    Atendimento,
    EmpresaCliente,
    FluxoAtendimento,
    TEMPLATES_FLUXO,
    WhatsAppIntegration,
    dados_padrao_fluxo,
    template_fluxo_por_segmento,
)
from .services.whatsapp import (
    WhatsAppProviderError,
    build_contact_url,
    notify_attendance,
)
from .services.whatsapp.client import WhatsAppCloudClient


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
    })


@login_required
@require_POST
def testar_integracao_whatsapp(request):
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
    integration = get_object_or_404(
        WhatsAppIntegration,
        company=empresa,
        is_active=True,
    )
    try:
        WhatsAppCloudClient().test_configuration(integration.phone_number_id)
    except WhatsAppProviderError as error:
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
