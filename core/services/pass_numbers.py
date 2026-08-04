from django.utils import timezone

from core.models import AsyncJob, IgnoredPhoneNumber, Mensagem
from core.services.phone_numbers import brazilian_phone_variants, normalize_phone_number


def is_pass_number(company_id, phone_number):
    target = brazilian_phone_variants(phone_number)
    if not target:
        return False
    saved_numbers = IgnoredPhoneNumber.objects.filter(
        empresa_id=company_id,
    ).values_list('phone_number', flat=True)
    return any(target & brazilian_phone_variants(saved) for saved in saved_numbers)


def cancel_pending_auto_reply_jobs(company_id, phone_number):
    """Conclude queued replies for this tenant/phone before a worker can claim them."""
    target = brazilian_phone_variants(phone_number)
    if not target:
        return 0
    jobs = AsyncJob.objects.filter(
        task_name='whatsapp.automatic_reply',
        status__in=[AsyncJob.Status.PENDING, AsyncJob.Status.RETRY],
        payload__company_id=company_id,
    )
    cancelled = 0
    for job in jobs:
        message = Mensagem.objects.select_related('contato').filter(
            pk=job.payload.get('message_id'), empresa_id=company_id,
        ).first()
        if message and target & brazilian_phone_variants(message.contato.whatsapp_id):
            updated = AsyncJob.objects.filter(
                pk=job.pk,
                status__in=[AsyncJob.Status.PENDING, AsyncJob.Status.RETRY],
            ).update(
                status=AsyncJob.Status.COMPLETED,
                completed_at=timezone.now(),
                last_error='cancelled:pass_number',
            )
            cancelled += updated
    return cancelled


def store_pass_number(*, company, phone_number, name=''):
    normalized = normalize_phone_number(phone_number)
    number, created = IgnoredPhoneNumber.objects.get_or_create(
        empresa=company,
        phone_number=normalized,
        defaults={'name': str(name or '').strip()[:120]},
    )
    return number, created
