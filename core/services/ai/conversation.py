"""Orquestra uma resposta de IA para uma mensagem já autenticada."""

import logging
import time
from decimal import Decimal

from django.db import transaction

from core.models import AIConfiguration, AIResponseDraft, Atendimento
from core.services.entitlements import EntitlementService
from django.core.exceptions import PermissionDenied

from .agent import AIAgent
from .exceptions import (
    AIAmbiguousResultError, AIServiceError, AIPermanentError,
    AIProviderError, AITemporaryError,
)
from .guardrails import (
    OUT_OF_SCOPE_MESSAGE,
    reject_adversarial_input,
    validate_ai_output,
)
from .tools import AIToolExecutor


logger = logging.getLogger('ai.conversation')


class AIConversationService:
    def __init__(self, *, agent=None):
        self.agent = agent or AIAgent()

    @staticmethod
    def is_enabled(atendimento):
        from core.domain.exceptions import SubscriptionAccessDenied
        try:
            subscription = EntitlementService.require_company_access(atendimento.empresa)
        except SubscriptionAccessDenied:
            return None
        try:
            configuration = AIConfiguration.objects.get(
                empresa_id=atendimento.empresa_id,
            )
        except AIConfiguration.DoesNotExist:
            return None
        if not configuration.is_available:
            return None
        if subscription and not subscription.plan.ai_enabled:
            return None
        return configuration

    def reply(self, *, inbound_message, user_input=None):
        started = time.monotonic()
        atendimento = inbound_message.atendimento
        configuration = self.is_enabled(atendimento)
        if configuration is None:
            return None
        draft, created = AIResponseDraft.objects.get_or_create(
            inbound_message=inbound_message,
            defaults={
                'empresa': atendimento.empresa,
                'atendimento': atendimento,
                'idempotency_key': f'ai-response:{inbound_message.external_message_id}',
            },
        )
        if draft.status in {AIResponseDraft.Status.GENERATED, AIResponseDraft.Status.SENT}:
            return draft.response_text
        if draft.status == AIResponseDraft.Status.AMBIGUOUS:
            raise AIAmbiguousResultError('Resultado anterior da IA é ambíguo; retry automático bloqueado.')
        if not created and draft.status == AIResponseDraft.Status.GENERATING:
            draft.status = AIResponseDraft.Status.AMBIGUOUS
            draft.last_error = 'interrupted_while_generating'
            draft.save(update_fields=['status', 'last_error', 'updated_at'])
            raise AIAmbiguousResultError('Execução anterior interrompida durante a chamada de IA.')
        user_input = inbound_message.ai_text if user_input is None else str(user_input).strip()
        if reject_adversarial_input(user_input):
            logger.warning(
                'ai.input.rejected company_id=%s attendance_id=%s',
                atendimento.empresa_id, atendimento.pk,
            )
            return OUT_OF_SCOPE_MESSAGE
        try:
            if not draft.quota_consumed:
                with transaction.atomic():
                    locked_draft = AIResponseDraft.objects.select_for_update().get(pk=draft.pk)
                    if not locked_draft.quota_consumed:
                        EntitlementService.consume(atendimento.empresa, 'ai_calls')
                        locked_draft.quota_consumed = True
                        locked_draft.status = AIResponseDraft.Status.GENERATING
                        locked_draft.save(update_fields=['quota_consumed', 'status', 'updated_at'])
                    draft.quota_consumed = locked_draft.quota_consumed
            response = self.agent.respond(
                configuration=configuration,
                atendimento=atendimento,
                user_input=user_input,
            )
            self._record_usage(
                atendimento, response=response,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            response_text = validate_ai_output(response.text)
            draft.status = AIResponseDraft.Status.GENERATED
            draft.response_text = response_text
            draft.provider_response_id = response.provider_response_id
            draft.last_error = ''
            draft.save(update_fields=[
                'status', 'response_text', 'provider_response_id', 'last_error', 'updated_at',
            ])
            return response_text
        except AIAmbiguousResultError as error:
            draft.status = AIResponseDraft.Status.AMBIGUOUS
            draft.last_error = type(error).__name__
            draft.save(update_fields=['status', 'last_error', 'updated_at'])
            self._record_usage(
                atendimento, error=error,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            logger.error(
                'ai.conversation.ambiguous company_id=%s attendance_id=%s message_id=%s',
                atendimento.empresa_id, atendimento.pk, inbound_message.external_message_id,
            )
            raise
        except (AITemporaryError, AIPermanentError) as error:
            draft.status = AIResponseDraft.Status.FAILED
            draft.last_error = type(error).__name__
            draft.save(update_fields=['status', 'last_error', 'updated_at'])
            from core.services.observability import record_metric
            record_metric('ai.failure', empresa=atendimento.empresa, labels={'type': type(error).__name__})
            self._record_usage(
                atendimento, error=error,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            logger.warning(
                'ai.conversation.failed company_id=%s attendance_id=%s type=%s',
                atendimento.empresa_id, atendimento.pk, type(error).__name__,
            )
            state = dict(atendimento.conversation_state or {})
            state['last_ai_failure_type'] = (
                'AI_TEMPORARY_FAILURE' if isinstance(error, AITemporaryError)
                else 'AI_PERMANENT_FAILURE'
            )
            Atendimento.objects.filter(
                pk=atendimento.pk, empresa_id=atendimento.empresa_id,
                automation_enabled=True,
            ).update(conversation_state=state)
            raise
        except AIProviderError as error:
            from core.services.observability import record_metric
            record_metric('ai.failure', empresa=atendimento.empresa, labels={'type': type(error).__name__})
            self._record_usage(
                atendimento, error=error,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            logger.warning(
                'ai.conversation.temporary_failure company_id=%s attendance_id=%s type=%s',
                atendimento.empresa_id, atendimento.pk, type(error).__name__,
            )
            raise AITemporaryError('Falha temporária no atendimento automático.') from error
        except (AIServiceError, PermissionDenied) as error:
            from core.services.observability import record_metric
            record_metric('ai.failure', empresa=atendimento.empresa, labels={'type': type(error).__name__})
            self._record_usage(
                atendimento, error=error,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            logger.warning(
                'ai.conversation.permanent_failure company_id=%s attendance_id=%s type=%s',
                atendimento.empresa_id, atendimento.pk, type(error).__name__,
            )
            raise AIPermanentError('Falha permanente no atendimento automático.') from error
        except Exception as error:
            from core.services.observability import record_metric
            record_metric('ai.failure', empresa=atendimento.empresa, labels={'type': type(error).__name__})
            self._record_usage(
                atendimento, error=error,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            logger.exception(
                'ai.conversation.unexpected company_id=%s attendance_id=%s type=%s',
                atendimento.empresa_id, atendimento.pk, type(error).__name__,
            )
            raise AITemporaryError('Falha temporária inesperada no atendimento automático.') from error

    @staticmethod
    def _record_usage(atendimento, *, response=None, error=None, latency_ms=0):
        from django.conf import settings
        from core.models import AIUsageRecord
        input_tokens = int(getattr(response, 'input_tokens', 0) or 0)
        output_tokens = int(getattr(response, 'output_tokens', 0) or 0)
        cost = (
            Decimal(input_tokens) * settings.AI_INPUT_COST_PER_MILLION
            + Decimal(output_tokens) * settings.AI_OUTPUT_COST_PER_MILLION
        ) / Decimal(1_000_000)
        AIUsageRecord.objects.create(
            empresa=atendimento.empresa, atendimento=atendimento,
            provider_response_id=getattr(response, 'provider_response_id', ''),
            model=settings.AI_MODEL, input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=int(getattr(response, 'tool_calls', 0) or 0),
            latency_ms=max(0, latency_ms), succeeded=error is None,
            error_type=type(error).__name__ if error else '',
            estimated_cost_usd=cost,
        )

    @staticmethod
    def _handoff(atendimento, reason):
        with transaction.atomic():
            locked = Atendimento.objects.select_for_update().get(pk=atendimento.pk)
            if locked.current_step != Atendimento.Step.HUMAN:
                AIToolExecutor(atendimento=locked).solicitar_atendente(motivo=reason)

    @staticmethod
    def handoff_after_failure(atendimento, *, failure_type='AI_PERMANENT_FAILURE'):
        reason = 'Falhas da IA esgotaram a política de novas tentativas.'
        with transaction.atomic():
            locked = Atendimento.objects.select_for_update().get(
                pk=atendimento.pk, empresa_id=atendimento.empresa_id,
            )
            if locked.assigned_to_id or locked.current_step == Atendimento.Step.HUMAN:
                return locked
            state = dict(locked.conversation_state or {})
            state['handoff_reason'] = reason
            state['handoff_type'] = failure_type
            locked.current_step = Atendimento.Step.WAITING_HUMAN
            locked.automation_enabled = False
            locked.status = Atendimento.STATUS_EM_ANDAMENTO
            locked.handoff_reason = reason
            locked.conversation_state = state
            locked.save(update_fields=[
                'current_step', 'automation_enabled', 'status', 'handoff_reason',
                'conversation_state',
            ])
            return locked
