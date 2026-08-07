import json
import difflib
import csv
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.access import company_for_user, ensure_company_for_user
from core.application.dto import PromptGeneratorInput
from core.application.prompt_compiler_service import PromptCompilerService
from core.application.whatsapp_service import WhatsAppOperationInProgress, WhatsAppSessionService
from core.domain.exceptions import ProviderUnavailable
from django.utils import timezone
from core.models import (
    AIPromptProfile, AIPromptVersion, AttendanceAttachment,
    AttendanceNote, AttendanceTag, Atendimento, Holiday, WhatsAppSession, IgnoredPhoneNumber,
)
from core.application.analytics_service import DashboardAnalyticsService
from .forms import PromptGeneratorForm


def _company(request):
    return company_for_user(request.user)


@login_required
def whatsapp_dashboard(request):
    WhatsAppSessionService().refresh(_company(request))
    return redirect(f"{reverse('prompt_generator')}#whatsapp-qr")


@login_required
@require_POST
def whatsapp_action(request, action):
    empresa = _company(request)
    service = WhatsAppSessionService()
    handlers = {
        'connect': service.connect,
        'refresh': service.refresh,
        'reconnect': service.reconnect,
        'clear': service.clear,
    }
    handler = handlers.get(action)
    if not handler:
        return JsonResponse({'error': 'Ação inválida.'}, status=400)
    operation_message = ''
    try:
        session = handler(empresa)
    except WhatsAppOperationInProgress as exc:
        operation_message = str(exc)
        session = WhatsAppSession.objects.get(empresa=empresa)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'state': session.state, 'state_label': session.get_state_display(),
            'qr_code': session.qr_code, 'phone_number': session.phone_number,
            'device_name': session.device_name, 'ping_ms': session.ping_ms,
            'last_error': session.last_error,
        })
    if operation_message:
        messages.warning(request, operation_message)
    elif session.state == 'CONNECTED' and action == 'connect':
        messages.info(request, 'WhatsApp já está conectado.')
    elif session.last_error:
        messages.error(request, session.last_error)
    else:
        messages.success(request, 'Sessão WhatsApp atualizada.')
    next_url = request.POST.get('next', '')
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(f"{reverse('prompt_generator')}#whatsapp-qr")


@login_required
@require_GET
def whatsapp_status(request):
    session = WhatsAppSessionService().refresh(_company(request))
    return JsonResponse({
        'state': session.state, 'state_label': session.get_state_display(),
        'qr_code': session.qr_code, 'phone_number': session.phone_number,
        'device_name': session.device_name, 'ping_ms': session.ping_ms,
        'online_seconds': session.online_seconds,
        'last_sync_at': session.last_sync_at.isoformat() if session.last_sync_at else None,
        'last_error': session.last_error,
    })


@csrf_exempt
@require_POST
def evolution_webhook(request):
    try:
        from core.services.evolution_webhook import EvolutionWebhookError, EvolutionWebhookService
        EvolutionWebhookService().accept(request.body, request.headers)
    except ProviderUnavailable:
        return JsonResponse({'error': 'unauthorized'}, status=401)
    except EvolutionWebhookError:
        return JsonResponse({'error': 'invalid payload'}, status=400)
    return JsonResponse({'accepted': True}, status=202)


def whatsapp_health(request):
    configured = bool(
        settings.EVOLUTION_API_URL
        and settings.EVOLUTION_API_KEY
        and settings.EVOLUTION_WEBHOOK_SECRET
    )
    return JsonResponse({'ok': configured, 'provider': 'evolution-api'}, status=200 if configured else 503)


@login_required
def ai_dashboard(request):
    return prompt_generator(request)


@login_required
def prompt_generator(request):
    empresa = ensure_company_for_user(request.user)
    profile = getattr(empresa, 'prompt_profile', None)
    initial = dict(profile.generator_data) if profile and profile.generator_data else {
        'company_name': empresa.nome, 'segment': empresa.get_segmento_display(),
    }
    if 'calendar_usage' not in initial:
        initial['calendar_usage'] = 'Use o agendamento e consulte a disponibilidade antes de confirmar horários.'
    form = PromptGeneratorForm(request.POST or None, initial=initial)
    if request.method == 'POST' and request.POST.get('action') == 'draft' and form.is_valid():
        profile, _ = AIPromptProfile.objects.get_or_create(empresa=empresa)
        profile.generator_data = form.cleaned_data
        profile.save(update_fields=['generator_data', 'updated_at'])
        messages.success(request, 'Rascunho salvo para esta empresa.')
        return redirect('prompt_generator')
    if request.method == 'POST' and form.is_valid():
        cleaned = form.cleaned_data
        data = PromptGeneratorInput(
            agent_name=cleaned['agent_name'], company_name=cleaned['company_name'],
            segment=cleaned['segment'], uses_calendar=True,
            profession=cleaned['profession'], personality=cleaned['personality'],
            objective='Receber, orientar e conduzir o cliente ao próximo passo.',
            service_style='Conversa natural de WhatsApp', tone=cleaned['personality'],
            products='', services='', additional_information=cleaned['additional_information'],
            calendar_usage=cleaned['calendar_usage'],
        )
        version = PromptCompilerService.compile_and_save(
            empresa=empresa, user=request.user, data=data,
        )
        messages.success(request, f'Prompt compilado e salvo como versão {version.version}.')
        return redirect('prompt_editor')
    return render(request, 'core/prompt_generator.html', {
        'empresa': empresa, 'form': form, 'profile': profile,
    })


@login_required
def prompt_editor(request):
    empresa = _company(request)
    profile = PromptCompilerService.ensure_default_profile(empresa=empresa, user=request.user)
    if request.method == 'POST':
        try:
            if request.POST.get('action') == 'draft':
                PromptCompilerService.save_draft(
                    empresa=empresa, content=request.POST.get('prompt_content'),
                    response_delay_seconds=request.POST.get('response_delay_seconds', 2),
                )
                messages.success(request, 'Rascunho salvo. O prompt ativo não foi alterado.')
            else:
                version = PromptCompilerService.publish_editor_prompt(
                    empresa=empresa, user=request.user, content=request.POST.get('prompt_content'),
                    response_delay_seconds=request.POST.get('response_delay_seconds', 2),
                )
                messages.success(request, f'Prompt publicado como versão {version.version}.')
            return redirect('prompt_editor')
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
            profile.refresh_from_db()
    active_version = profile.versions.filter(is_active=True).first()
    return render(request, 'core/prompt_editor.html', {
        'empresa': empresa,
        'profile': profile,
        'prompt': profile.generated_prompt,
        'draft_prompt': profile.draft_prompt,
        'active_version': active_version,
        'has_unpublished_changes': profile.draft_prompt != profile.generated_prompt,
        'audio_transcription_enabled': (
            settings.AI_ENABLED and settings.AI_AUDIO_TRANSCRIPTION_ENABLED
        ),
        'versions': profile.versions.all()[:30],
    })


@login_required
@require_POST
def prompt_restore(request, version_id):
    empresa = _company(request)
    version = get_object_or_404(AIPromptVersion, pk=version_id, profile__empresa=empresa)
    restored = PromptCompilerService.publish_editor_prompt(
        empresa=empresa, user=request.user, content=version.content,
        response_delay_seconds=version.profile.response_delay_seconds,
    )
    messages.success(request, f'Versão {version.version} restaurada e publicada como versão {restored.version}.')
    return redirect('prompt_editor')


@login_required
@require_POST
def prompt_autosave(request):
    profile, _ = AIPromptProfile.objects.get_or_create(empresa=_company(request))
    profile.draft_prompt = request.POST.get('content', '')
    profile.autosaved_at = timezone.now()
    profile.save(update_fields=['draft_prompt', 'autosaved_at', 'updated_at'])
    return JsonResponse({'saved': True, 'at': profile.autosaved_at.isoformat()})


@login_required
@require_POST
def prompt_duplicate(request, version_id):
    empresa = _company(request)
    source = get_object_or_404(AIPromptVersion, pk=version_id, profile__empresa=empresa)
    version = PromptCompilerService.publish_editor_prompt(
        empresa=empresa, user=request.user, content=source.content,
        response_delay_seconds=source.profile.response_delay_seconds,
    )
    messages.success(request, f'Versão {source.version} duplicada e publicada como {version.version}.')
    return redirect('prompt_editor')


@login_required
def prompt_diff(request):
    empresa = _company(request)
    left = get_object_or_404(AIPromptVersion, pk=request.GET.get('left'), profile__empresa=empresa)
    right = get_object_or_404(AIPromptVersion, pk=request.GET.get('right'), profile__empresa=empresa)
    diff = difflib.HtmlDiff(wrapcolumn=90).make_table(left.content.splitlines(), right.content.splitlines(), f'v{left.version}', f'v{right.version}', context=True)
    return render(request, 'core/prompt_diff.html', {'empresa': empresa, 'left': left, 'right': right, 'diff': diff})


@login_required
def prompt_export(request):
    profile = get_object_or_404(AIPromptProfile, empresa=_company(request))
    response = HttpResponse(profile.generated_prompt, content_type='text/markdown; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="prompt-ia.md"'
    return response


@login_required
def ignored_numbers(request):
    empresa = _company(request)
    if request.method == 'POST':
        from core.services.pass_numbers import store_pass_number
        from core.services.phone_numbers import normalize_phone_number
        phone = normalize_phone_number(request.POST.get('phone_number', ''))
        name = request.POST.get('name', '').strip()[:120]
        if not 10 <= len(phone) <= 15:
            messages.error(request, 'Informe um telefone com DDD e código do país.')
        else:
            _, created = store_pass_number(company=empresa, phone_number=phone, name=name)
            messages.success(request, 'Número adicionado à lista Pass.') if created else messages.warning(request, 'Este número já está na lista.')
        return redirect('ignored_numbers')
    return render(request, 'core/ignored_numbers.html', {'empresa': empresa, 'ignored_numbers': empresa.ignored_phone_numbers.all()})


@login_required
@require_POST
def ignored_number_delete(request, number_id):
    number = get_object_or_404(IgnoredPhoneNumber, pk=number_id, empresa=_company(request))
    number.delete()
    messages.success(request, 'Número removido da lista Pass.')
    return redirect('ignored_numbers')


@login_required
@require_POST
def holiday_create(request):
    empresa = _company(request)
    Holiday.objects.update_or_create(empresa=empresa, date=request.POST.get('date'), defaults={'name': request.POST.get('name', 'Feriado'), 'blocks_schedule': True})
    messages.success(request, 'Feriado adicionado à agenda.')
    return redirect('agenda')


@login_required
def conversations_crm(request):
    empresa = _company(request)
    query = request.GET.get('q', '').strip()
    state = request.GET.get('state', '')
    conversations = empresa.atendimentos.select_related('contato', 'assigned_to').prefetch_related('mensagens', 'internal_notes', 'attachments').order_by('-last_message_at', '-criado_em')
    if query:
        conversations = conversations.filter(Q(nome_cliente__icontains=query) | Q(telefone_cliente__icontains=query) | Q(mensagens__texto__icontains=query)).distinct()
    if state == 'finished': conversations = conversations.filter(status=Atendimento.STATUS_FINALIZADO)
    elif state == 'human': conversations = conversations.filter(current_step__in=[Atendimento.Step.WAITING_HUMAN, Atendimento.Step.HUMAN])
    selected_id = request.GET.get('conversation')
    selected = conversations.filter(pk=selected_id).first() if selected_id else conversations.first()
    return render(request, 'core/conversations_crm.html', {'empresa': empresa, 'conversations': conversations, 'selected': selected, 'query': query, 'state': state, 'tags': empresa.attendance_tags.all()})


@login_required
def conversation_export(request, atendimento_id):
    atendimento = get_object_or_404(
        Atendimento.objects.prefetch_related('mensagens'), pk=atendimento_id, empresa=_company(request),
    )
    lines = [f'Conversa com {atendimento.nome_cliente or atendimento.telefone_cliente}', '']
    for message in atendimento.mensagens.order_by('criado_em'):
        author = 'Cliente' if message.direcao == 'entrada' else ('Atendente' if message.sent_by_id else 'IA')
        lines.append(f'[{message.criado_em:%d/%m/%Y %H:%M:%S}] {author}: {message.texto}')
    response = HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="conversa-{atendimento.pk}.txt"'
    return response


@login_required
@require_POST
def conversation_delete(request, atendimento_id):
    atendimento = get_object_or_404(Atendimento, pk=atendimento_id, empresa=_company(request))
    atendimento.delete()
    messages.success(request, 'Conversa excluída.')
    return redirect('conversations_crm')


@login_required
@require_POST
def conversation_note(request, atendimento_id):
    atendimento = get_object_or_404(Atendimento, pk=atendimento_id, empresa=_company(request))
    text = request.POST.get('text', '').strip()
    if text: AttendanceNote.objects.create(atendimento=atendimento, author=request.user, text=text)
    return redirect(f"{reverse('conversations_crm')}?conversation={atendimento.pk}")


@login_required
@require_POST
def conversation_attachment(request, atendimento_id):
    atendimento = get_object_or_404(Atendimento, pk=atendimento_id, empresa=_company(request))
    uploaded = request.FILES.get('file')
    if uploaded:
        kind = 'audio' if uploaded.content_type.startswith('audio/') else 'image' if uploaded.content_type.startswith('image/') else 'document'
        AttendanceAttachment.objects.create(atendimento=atendimento, uploaded_by=request.user, file=uploaded, media_type=kind)
    return redirect(f"{reverse('conversations_crm')}?conversation={atendimento.pk}")


@login_required
@require_POST
def conversation_reopen(request, atendimento_id):
    atendimento = get_object_or_404(Atendimento, pk=atendimento_id, empresa=_company(request))
    atendimento.status = Atendimento.STATUS_EM_ANDAMENTO
    atendimento.current_step = Atendimento.Step.WAITING_HUMAN
    atendimento.closed_at = None
    atendimento.closed_by = None
    atendimento.save(update_fields=['status', 'current_step', 'closed_at', 'closed_by'])
    return redirect(f"{reverse('conversations_crm')}?conversation={atendimento.pk}")


@login_required
@require_POST
def conversation_tag(request, atendimento_id):
    empresa = _company(request)
    atendimento = get_object_or_404(Atendimento, pk=atendimento_id, empresa=empresa)
    name = request.POST.get('name', '').strip()[:40]
    if name:
        tag, _ = AttendanceTag.objects.get_or_create(empresa=empresa, name=name, defaults={'color': request.POST.get('color', '#00e5ff')})
        tag.attendances.add(atendimento)
    return redirect(f"{reverse('conversations_crm')}?conversation={atendimento.pk}")


@login_required
def analytics_dashboard(request):
    empresa = _company(request)
    analytics = DashboardAnalyticsService.build(empresa)
    return render(request, 'core/analytics_dashboard.html', {'empresa': empresa, 'analytics': analytics, 'chart_json': json.dumps(analytics)})


@login_required
def analytics_export(request):
    empresa = _company(request)
    response = HttpResponse(content_type='text/csv', headers={'Content-Disposition': 'attachment; filename="iaatende-dashboard.csv"'})
    writer = csv.writer(response); writer.writerow(['Data', 'Conversas'])
    for row in DashboardAnalyticsService.build(empresa)['daily']: writer.writerow([row['date'], row['total']])
    return response
