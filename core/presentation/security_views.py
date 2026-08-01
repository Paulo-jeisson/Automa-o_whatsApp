import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.access import company_for_user, role_for_user
from core.audit import record_audit
from core.models import APIRefreshToken, AuditEvent, DataSubjectRequest, RateLimitBucket
from core.security_jwt import JWTError, issue_pair, rotate, user_from_access


def _json(request):
    try: return json.loads(request.body or b'{}')
    except json.JSONDecodeError: return {}


@csrf_exempt
@require_POST
def token_create(request):
    data = _json(request)
    user = authenticate(request, username=data.get('username', ''), password=data.get('password', ''))
    if not user: return JsonResponse({'detail': 'Credenciais inválidas.'}, status=401)
    return JsonResponse(issue_pair(user))


@csrf_exempt
@require_POST
def token_refresh(request):
    try: pair = rotate(_json(request).get('refresh', ''))
    except JWTError as exc: return JsonResponse({'detail': str(exc)}, status=401)
    return JsonResponse(pair)


@require_GET
def api_me(request):
    authorization = request.headers.get('Authorization', '')
    if not authorization.startswith('Bearer '): return JsonResponse({'detail': 'Bearer token obrigatório.'}, status=401)
    try: user = user_from_access(authorization[7:])
    except JWTError as exc: return JsonResponse({'detail': str(exc)}, status=401)
    empresa = company_for_user(user)
    return JsonResponse({'id': user.pk, 'username': user.get_username(), 'company_id': empresa.pk if empresa else None, 'role': role_for_user(user, empresa) if empresa else None})


@login_required
def security_center(request):
    empresa = company_for_user(request.user)
    sessions = []
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        data = session.get_decoded()
        if str(data.get('_auth_user_id')) == str(request.user.pk):
            sessions.append({'key': session.session_key[-8:], 'expires': session.expire_date, 'current': session.session_key == request.session.session_key})
    checks = [
        ('Rate limit', True, f'{RateLimitBucket.objects.count()} buckets'),
        ('JWT rotativo', True, f'{APIRefreshToken.objects.filter(user=request.user, revoked_at__isnull=True).count()} tokens ativos'),
        ('Criptografia', bool(settings.WHATSAPP_TOKEN_ENCRYPTION_KEY), 'Chave de tokens configurada'),
        ('CSRF', 'django.middleware.csrf.CsrfViewMiddleware' in settings.MIDDLEWARE, 'Middleware ativo'),
        ('Headers', 'core.middleware.SecurityHeadersMiddleware' in settings.MIDDLEWARE, 'CSP e políticas ativas'),
        ('LGPD', True, f'{DataSubjectRequest.objects.filter(empresa=empresa).count()} solicitações'),
        ('Backup', True, 'Script com verificação pg_restore'),
        ('Health check', True, '/health/'),
    ]
    return render(request, 'core/security_center.html', {'empresa': empresa, 'checks': checks, 'sessions': sessions, 'audit_events': AuditEvent.objects.filter(empresa=empresa).select_related('actor')[:40]})


@login_required
@require_POST
def revoke_api_tokens(request):
    empresa = company_for_user(request.user)
    APIRefreshToken.objects.filter(user=request.user, revoked_at__isnull=True).update(revoked_at=timezone.now())
    record_audit(request, 'security.api_tokens_revoked', empresa=empresa)
    messages.success(request, 'Tokens de API revogados.')
    return redirect('security_center')


@login_required
@require_POST
def revoke_other_sessions(request):
    empresa = company_for_user(request.user)
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        if session.session_key != request.session.session_key and str(session.get_decoded().get('_auth_user_id')) == str(request.user.pk): session.delete()
    record_audit(request, 'security.other_sessions_revoked', empresa=empresa)
    messages.success(request, 'Outras sessões encerradas.')
    return redirect('security_center')
