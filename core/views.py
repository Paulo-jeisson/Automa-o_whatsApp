from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import AtendimentoSimuladoForm, EmpresaClienteForm, FluxoAtendimentoForm
from .models import Atendimento, EmpresaCliente, FluxoAtendimento, dados_padrao_fluxo


@login_required
def dashboard(request):
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
    public_url = request.build_absolute_uri(
        empresa.get_atendimento_url()
    ) if empresa else ''
    hoje = timezone.localdate()
    atendimentos = empresa.atendimentos.all() if empresa else Atendimento.objects.none()
    context = {
        'empresa': empresa,
        'public_url': public_url,
        'total_atendimentos': atendimentos.count(),
        'atendimentos_hoje': atendimentos.filter(criado_em__date=hoje).count(),
        'novos_atendimentos': atendimentos.filter(status=Atendimento.STATUS_NOVO).count(),
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
        'public_url': request.build_absolute_uri(empresa.get_atendimento_url()),
        'status_conta': 'Ativa' if empresa.ativa else 'Pendente',
    }
    return render(request, 'core/fluxo.html', context)


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
        messages.error(request, 'Status informado nao e valido.')

    next_url = request.POST.get('next') or 'atendimentos'
    return redirect(next_url)


@login_required
@require_POST
def avisar_whatsapp_atendimento(request, atendimento_id):
    empresa = EmpresaCliente.objects.filter(usuario=request.user).first()
    atendimento = get_object_or_404(Atendimento, pk=atendimento_id, empresa=empresa)

    if not empresa.whatsapp_dono:
        messages.error(request, 'Cadastre o WhatsApp do dono antes de enviar avisos.')
        return redirect(request.POST.get('next') or 'atendimentos')

    atendimento.avisado_em = timezone.now()
    atendimento.save(update_fields=['avisado_em'])

    return redirect(atendimento.get_whatsapp_aviso_url())
