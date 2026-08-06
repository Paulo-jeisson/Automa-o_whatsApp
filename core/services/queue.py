import logging
import time
import hashlib
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.db import OperationalError, connection, transaction
from django.db.models import F
from django.utils import timezone

from core.models import AsyncJob, Mensagem


logger = logging.getLogger('queue')
DATABASE_LOCK_RETRIES = 3
DATABASE_LOCK_RETRY_SECONDS = 0.05


@contextmanager
def _conversation_lock(conversation_key):
    """Serialize one conversation while allowing unrelated conversations in parallel."""
    if not conversation_key or connection.vendor != 'postgresql':
        yield
        return
    lock_id = int.from_bytes(
        hashlib.sha256(conversation_key.encode('utf-8')).digest()[:8], 'big', signed=True,
    )
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_lock(%s)', [lock_id])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [lock_id])


def _is_database_locked(error):
    return 'database is locked' in str(error).lower()


def _retry_database_locked(operation, *, job_id=None):
    for retry in range(1, DATABASE_LOCK_RETRIES + 1):
        try:
            return operation()
        except OperationalError as error:
            if not _is_database_locked(error) or retry == DATABASE_LOCK_RETRIES:
                raise
            logger.warning(
                'queue.job.retry_database_locked job_id=%s retry=%s', job_id, retry,
            )
            time.sleep(DATABASE_LOCK_RETRY_SECONDS * retry)


def enqueue(
    task_name, payload, *, idempotency_key, queue='default', max_attempts=5,
    conversation_key='',
):
    """Persist an idempotent pending job; workers are the only executors."""
    def create_job():
        return AsyncJob.objects.get_or_create(
            idempotency_key=idempotency_key,
            defaults={
                'task_name': task_name, 'payload': payload, 'queue': queue,
                'max_attempts': max_attempts, 'conversation_key': conversation_key,
            },
        )

    job, _created = _retry_database_locked(create_job)
    return job


def _claim_job(job_id):
    def claim():
        with transaction.atomic():
            jobs = AsyncJob.objects
            if connection.features.has_select_for_update:
                kwargs = (
                    {'skip_locked': True}
                    if connection.features.has_select_for_update_skip_locked
                    else {}
                )
                jobs = jobs.select_for_update(**kwargs)
            job = jobs.filter(pk=job_id).first()
            if job is None or (
                job.status not in {AsyncJob.Status.PENDING, AsyncJob.Status.RETRY}
                or job.available_at > timezone.now()
            ):
                return None
            if job.conversation_key and AsyncJob.objects.filter(
                conversation_key=job.conversation_key,
                pk__lt=job.pk,
                status__in=[
                    AsyncJob.Status.PENDING, AsyncJob.Status.RETRY,
                    AsyncJob.Status.PROCESSING,
                ],
            ).exists():
                return None
            now = timezone.now()
            claimed = AsyncJob.objects.filter(
                pk=job.pk,
                status__in=[AsyncJob.Status.PENDING, AsyncJob.Status.RETRY],
                available_at__lte=timezone.now(),
            ).update(
                status=AsyncJob.Status.PROCESSING,
                attempts=F('attempts') + 1,
                locked_at=now,
                lease_expires_at=now + timedelta(seconds=settings.TASK_QUEUE_LEASE_SECONDS),
            )
            if not claimed:
                return None
        return AsyncJob.objects.get(pk=job_id)

    return _retry_database_locked(claim, job_id=job_id)


def recover_expired_jobs(*, queue=None):
    """Return only expired leases to RETRY; active workers keep their jobs."""
    now = timezone.now()
    recovered = dead = 0
    with transaction.atomic():
        jobs = AsyncJob.objects.select_for_update().filter(
            status=AsyncJob.Status.PROCESSING,
            lease_expires_at__isnull=False,
            lease_expires_at__lte=now,
        )
        if queue:
            jobs = jobs.filter(queue=queue)
        for job in jobs:
            job.last_error = 'Worker lease expired before completion.'
            job.locked_at = None
            job.lease_expires_at = None
            if job.attempts >= job.max_attempts:
                job.status = AsyncJob.Status.DEAD
                dead += 1
            else:
                job.status = AsyncJob.Status.RETRY
                job.available_at = now + timedelta(seconds=settings.TASK_QUEUE_BACKOFF)
                recovered += 1
            job.save(update_fields=[
                'status', 'available_at', 'locked_at', 'lease_expires_at', 'last_error',
            ])
            logger.warning(
                'queue.lease.expired job_id=%s task=%s status=%s attempt=%s queue=%s',
                job.pk, job.task_name, job.status, job.attempts, job.queue,
            )
    logger.warning('queue.lease.recovered recovered=%s dead=%s queue=%s', recovered, dead, queue or 'all')
    return recovered, dead


def process_job(job_id):
    job = _claim_job(job_id)
    if job is None:
        return AsyncJob.objects.get(pk=job_id)
    return _run_claimed_job(job)


def _run_claimed_job(job):
    logger.info(
        'queue.job.started job_id=%s task=%s attempt=%s',
        job.pk, job.task_name, job.attempts,
    )
    try:
        with _conversation_lock(job.conversation_key):
            dispatch_result = _dispatch(job.task_name, job.payload)
    except Exception as error:
        database_locked = isinstance(error, OperationalError) and _is_database_locked(error)
        if database_locked:
            logger.warning(
                'queue.job.retry_database_locked job_id=%s retry=%s', job.pk, job.attempts,
            )
        else:
            logger.exception(
                'queue.job.failed job_id=%s task=%s attempt=%s',
                job.pk, job.task_name, job.attempts,
            )
        job.last_error = f'{type(error).__name__}: {error}'[:2000]
        exhausted = job.attempts >= job.max_attempts
        job.status = AsyncJob.Status.DEAD if exhausted else AsyncJob.Status.RETRY
        if not exhausted:
            delay = (
                DATABASE_LOCK_RETRY_SECONDS
                if database_locked
                else min(
                    settings.TASK_QUEUE_MAX_BACKOFF,
                    settings.TASK_QUEUE_BACKOFF * (2 ** (job.attempts - 1)),
                )
            )
            job.available_at = timezone.now() + timedelta(seconds=delay)
        job.lease_expires_at = None
        job.locked_at = None
        _retry_database_locked(
            lambda: job.save(update_fields=['status', 'available_at', 'last_error', 'locked_at', 'lease_expires_at']),
            job_id=job.pk,
        )
        if exhausted:
            _handle_exhausted_job(job, error)
        return job
    job.status = AsyncJob.Status.COMPLETED
    job.completed_at = timezone.now()
    job.last_error = ''
    job.locked_at = None
    job.lease_expires_at = None
    _retry_database_locked(
        lambda: job.save(update_fields=['status', 'completed_at', 'last_error', 'locked_at', 'lease_expires_at']),
        job_id=job.pk,
    )
    logger.info(
        'queue.job.completed job_id=%s task=%s attempt=%s result=%s',
        job.pk, job.task_name, job.attempts,
        'handled' if dispatch_result is not None else 'no_action',
    )
    return job


def process_next(*, queue='default'):
    candidates = AsyncJob.objects.filter(
        queue=queue,
        status__in=[AsyncJob.Status.PENDING, AsyncJob.Status.RETRY],
        available_at__lte=timezone.now(),
    ).order_by('available_at', 'pk').values_list('pk', flat=True)[:10]
    for candidate in candidates:
        job = _claim_job(candidate)
        if job:
            return _run_claimed_job(job)
    return None


def _dispatch(task_name, payload):
    if task_name == 'evolution.webhook':
        from core.services.evolution_webhook import EvolutionWebhookService
        return EvolutionWebhookService().process(payload['session_id'], payload['payload'])
    if task_name == 'whatsapp.automatic_reply':
        from core.services.whatsapp.outbound import send_automatic_reply
        message = Mensagem.objects.select_related('atendimento', 'contato', 'empresa').get(
            pk=payload['message_id'], empresa_id=payload['company_id'],
        )
        return send_automatic_reply(message)
    raise ValueError(f'Tarefa não registrada: {task_name}')


def _handle_exhausted_job(job, error):
    if job.task_name != 'whatsapp.automatic_reply':
        return
    from core.services.ai.conversation import AIConversationService
    try:
        message = Mensagem.objects.select_related('atendimento').get(
            pk=job.payload.get('message_id'),
            empresa_id=job.payload.get('company_id'),
            direcao=Mensagem.DIRECAO_ENTRADA,
        )
    except Mensagem.DoesNotExist:
        return
    AIConversationService.handoff_after_failure(
        message.atendimento, failure_type='AI_PERMANENT_FAILURE',
    )
    logger.error(
        'whatsapp.auto_reply.exhausted company_id=%s attendance_id=%s job_id=%s attempts=%s type=%s',
        message.empresa_id, message.atendimento_id, job.pk, job.attempts, type(error).__name__,
    )
