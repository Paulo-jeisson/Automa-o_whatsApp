import hmac
import logging

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .services.whatsapp.exceptions import InvalidWebhookPayload, InvalidWebhookSignature
from .services.whatsapp.parser import decode_payload, parse_webhook_payload
from .services.whatsapp.security import validate_webhook_signature
from .services.whatsapp.webhook import process_webhook_events


logger = logging.getLogger('whatsapp.webhook')


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def whatsapp_webhook(request):
    if request.method == 'GET':
        return _verify_webhook(request)
    return _receive_webhook(request)


def _verify_webhook(request):
    mode = request.GET.get('hub.mode', '')
    received_token = request.GET.get('hub.verify_token', '')
    challenge = request.GET.get('hub.challenge', '')
    configured_token = settings.META_VERIFY_TOKEN

    valid_token = bool(configured_token) and hmac.compare_digest(
        received_token,
        configured_token,
    )
    if mode == 'subscribe' and valid_token and challenge:
        logger.info('whatsapp.webhook.verified')
        return HttpResponse(challenge, content_type='text/plain')

    logger.warning('whatsapp.webhook.verification_failed')
    return HttpResponse('Verificação inválida.', status=403, content_type='text/plain')


def _receive_webhook(request):
    if request.content_type != 'application/json':
        return JsonResponse({'detail': 'Content-Type não suportado.'}, status=415)

    try:
        raw_body = request.body
    except RequestDataTooBig:
        logger.warning('whatsapp.webhook.payload_too_large')
        return JsonResponse({'detail': 'Payload muito grande.'}, status=413)
    if len(raw_body) > settings.WHATSAPP_WEBHOOK_MAX_BYTES:
        logger.warning('whatsapp.webhook.payload_too_large bytes=%s', len(raw_body))
        return JsonResponse({'detail': 'Payload muito grande.'}, status=413)

    if not settings.META_APP_SECRET:
        logger.error('whatsapp.webhook.app_secret_missing')
        return JsonResponse({'detail': 'Webhook indisponível.'}, status=503)

    try:
        validate_webhook_signature(
            raw_body,
            request.headers.get('X-Hub-Signature-256', ''),
            settings.META_APP_SECRET,
        )
    except InvalidWebhookSignature:
        logger.warning('whatsapp.signature.invalid')
        return JsonResponse({'detail': 'Assinatura inválida.'}, status=403)

    try:
        payload = decode_payload(raw_body)
    except InvalidWebhookPayload:
        logger.warning('whatsapp.webhook.invalid_json')
        return JsonResponse({'detail': 'JSON inválido.'}, status=400)

    events = parse_webhook_payload(payload)
    logger.info('whatsapp.webhook.received events=%s', len(events))
    process_webhook_events(events)
    return JsonResponse({'status': 'received'})
