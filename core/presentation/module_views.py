import hmac
import json
import difflib
import csv

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.access import company_for_user
from core.application.dto import PromptGeneratorInput
from core.application.prompt_service import PromptGeneratorService
from core.application.whatsapp_service import WhatsAppSessionService
from django.utils import timezone
from core.models import (
    AIPromptProfile, AIPromptTemplate, AIPromptVersion, AttendanceAttachment,
    AttendanceNote, AttendanceTag, Atendimento, Holiday, WhatsAppSession,
)
from core.application.analytics_service import DashboardAnalyticsService
from .forms import PromptGeneratorForm


def _company(request):
    return company_for_user(request.user)


@login_required
def whatsapp_dashboard(request):
    empresa = _company(request)
    service = WhatsAppSessionService()
    session = service.ensure(empresa)
    return render(request, 'core/whatsapp_dashboard.html', {
        'empresa': empresa, 'session': session,
        'events': session.events.all()[:50],
    })


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
    session = handler(empresa)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'state': session.state, 'state_label': session.get_state_display(),
            'qr_code': session.qr_code, 'phone_number': session.phone_number,
            'device_name': session.device_name, 'ping_ms': session.ping_ms,
            'last_error': session.last_error,
        })
    if session.last_error:
        messages.error(request, session.last_error)
    else:
        messages.success(request, 'Sessão WhatsApp atualizada.')
    return redirect('whatsapp_dashboard')


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
    expected = settings.EVOLUTION_WEBHOOK_SECRET
    supplied = request.headers.get('x-zapfluxo-secret', '')
    if expected and not hmac.compare_digest(expected, supplied):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        payload = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'invalid json'}, status=400)
    instance_name = payload.get('instance') or payload.get('instanceName')
    session = WhatsAppSession.objects.filter(instance_name=instance_name).first()
    if not session:
        return JsonResponse({'error': 'unknown instance'}, status=404)
    event = payload.get('event', 'WEBHOOK').upper()
    data = payload.get('data') or {}
    state = str(data.get('state') or data.get('status') or '').lower()
    state_map = {'open': 'CONNECTED', 'connected': 'CONNECTED', 'connecting': 'CONNECTING', 'close': 'OFFLINE', 'disconnected': 'OFFLINE'}
    if state in state_map:
        session.state = state_map[state]
        session.save(update_fields=['state', 'updated_at'])
    session.events.create(kind=event[:40], message='Evento recebido do provider.', payload=payload)
    return JsonResponse({'ok': True})


def whatsapp_health(request):
    configured = bool(settings.EVOLUTION_API_URL and settings.EVOLUTION_API_KEY)
    return JsonResponse({'ok': configured, 'provider': 'evolution-api'}, status=200 if configured else 503)


@login_required
def ai_dashboard(request):
    empresa = _company(request)
    profile = getattr(empresa, 'prompt_profile', None)
    return render(request, 'core/ai_dashboard.html', {'empresa': empresa, 'profile': profile})


@login_required
def prompt_generator(request):
    empresa = _company(request)
    profile = getattr(empresa, 'prompt_profile', None)
    initial = dict(profile.generator_data) if profile else {
        'company_name': empresa.nome, 'segment': empresa.get_segmento_display(),
        'business_hours': empresa.horario_funcionamento,
    }
    form = PromptGeneratorForm(request.POST or None, initial=initial)
    preview = (profile.draft_prompt or profile.generated_prompt) if profile else ''
    if request.method == 'POST' and form.is_valid():
        data = PromptGeneratorInput(**form.cleaned_data)
        preview = request.POST.get('prompt_content', '').strip() or PromptGeneratorService.render(data)
        if request.POST.get('action') == 'save':
            version = PromptGeneratorService.save_version(
                empresa=empresa, user=request.user, data=data, content=preview,
            )
            messages.success(request, f'Prompt salvo como versão {version.version}.')
            return redirect('prompt_generator')
    profile = getattr(empresa, 'prompt_profile', None)
    return render(request, 'core/prompt_generator.html', {
        'empresa': empresa, 'form': form, 'preview': preview,
        'versions': profile.versions.all()[:20] if profile else [],
        'prompt_templates': [('sales', 'Comercial'), ('support', 'Suporte'), ('scheduling', 'Agendamento')],
    })


@login_required
@require_POST
def prompt_restore(request, version_id):
    empresa = _company(request)
    version = get_object_or_404(AIPromptVersion, pk=version_id, profile__empresa=empresa)
    profile = version.profile
    profile.generated_prompt = version.content
    profile.save(update_fields=['generated_prompt', 'updated_at'])
    messages.success(request, f'Versão {version.version} restaurada para edição.')
    return redirect('prompt_generator')


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
    number = (source.profile.versions.order_by('-version').first().version) + 1
    AIPromptVersion.objects.create(profile=source.profile, version=number, content=source.content, created_by=request.user)
    messages.success(request, f'Versão {source.version} duplicada como {number}.')
    return redirect('prompt_generator')


@login_required
def prompt_diff(request):
    empresa = _company(request)
    left = get_object_or_404(AIPromptVersion, pk=request.GET.get('left'), profile__empresa=empresa)
    right = get_object_or_404(AIPromptVersion, pk=request.GET.get('right'), profile__empresa=empresa)
    diff = difflib.HtmlDiff(wrapcolumn=90).make_table(left.content.splitlines(), right.content.splitlines(), f'v{left.version}', f'v{right.version}', context=True)
    return render(request, 'core/prompt_diff.html', {'empresa': empresa, 'left': left, 'right': right, 'diff': diff})


@login_required
@require_POST
def prompt_apply_template(request):
    empresa = _company(request)
    templates = {
        'sales': '# Identidade\nVocê é um consultor comercial.\n\n# Missão\nQualificar oportunidades e orientar o próximo passo.\n\n# Restrições\nNunca invente preços ou condições.',
        'support': '# Identidade\nVocê é um agente de suporte.\n\n# Missão\nDiagnosticar com perguntas objetivas e resolver com segurança.\n\n# Transferência Humana\nTransfira incidentes críticos.',
        'scheduling': '# Identidade\nVocê é um assistente de agendamentos.\n\n# Agendamento\nConsulte disponibilidade antes de confirmar. Nunca crie horários indisponíveis.',
    }
    content = templates.get(request.POST.get('template'))
    if not content:
        return JsonResponse({'error': 'template invalid'}, status=400)
    profile, _ = AIPromptProfile.objects.get_or_create(empresa=empresa)
    profile.draft_prompt = content
    profile.autosaved_at = timezone.now()
    profile.save(update_fields=['draft_prompt', 'autosaved_at', 'updated_at'])
    return redirect('prompt_generator')


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
    response = HttpResponse(content_type='text/csv', headers={'Content-Disposition': 'attachment; filename="zapfluxo-dashboard.csv"'})
    writer = csv.writer(response); writer.writerow(['Data', 'Conversas'])
    for row in DashboardAnalyticsService.build(empresa)['daily']: writer.writerow([row['date'], row['total']])
    return response
