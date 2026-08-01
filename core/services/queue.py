import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import AsyncJob, Mensagem


logger = logging.getLogger('queue')


def enqueue(task_name, payload, *, idempotency_key, queue='default', max_attempts=5):
    job, created = AsyncJob.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            'task_name': task_name, 'payload': payload, 'queue': queue,
            'max_attempts': max_attempts,
        },
    )
    if created and settings.TASK_QUEUE_EAGER:
        process_job(job.pk)
    return job


def process_job(job_id):
    with transaction.atomic():
        job = AsyncJob.objects.select_for_update().get(pk=job_id)
        if job.status in {AsyncJob.Status.COMPLETED, AsyncJob.Status.DEAD}:
            return job
        if job.available_at > timezone.now():
            return job
        job.status = AsyncJob.Status.PROCESSING
        job.attempts += 1
        job.locked_at = timezone.now()
        job.save(update_fields=['status', 'attempts', 'locked_at'])
    try:
        _dispatch(job.task_name, job.payload)
    except Exception as error:
        logger.exception('queue.job.failed job_id=%s task=%s attempt=%s', job.pk, job.task_name, job.attempts)
        job.last_error = f'{type(error).__name__}: {error}'[:2000]
        if job.attempts >= job.max_attempts:
            job.status = AsyncJob.Status.DEAD
        else:
            job.status = AsyncJob.Status.RETRY
            job.available_at = timezone.now() + timedelta(
                seconds=min(settings.TASK_QUEUE_MAX_BACKOFF, settings.TASK_QUEUE_BACKOFF * (2 ** (job.attempts - 1))),
            )
        job.save(update_fields=['status', 'available_at', 'last_error'])
        return job
    job.status = AsyncJob.Status.COMPLETED
    job.completed_at = timezone.now()
    job.last_error = ''
    job.save(update_fields=['status', 'completed_at', 'last_error'])
    return job


def process_next(*, queue='default'):
    job = AsyncJob.objects.filter(
        queue=queue,
        status__in=[AsyncJob.Status.PENDING, AsyncJob.Status.RETRY],
        available_at__lte=timezone.now(),
    ).order_by('available_at', 'pk').first()
    return process_job(job.pk) if job else None


def _dispatch(task_name, payload):
    if task_name == 'whatsapp.automatic_reply':
        from core.services.whatsapp.outbound import send_automatic_reply
        message = Mensagem.objects.select_related('atendimento', 'contato', 'empresa').get(pk=payload['message_id'])
        return send_automatic_reply(message)
    raise ValueError(f'Tarefa não registrada: {task_name}')
