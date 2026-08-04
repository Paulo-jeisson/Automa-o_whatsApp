"""Orquestra uma resposta de IA para uma mensagem já autenticada."""

import logging
import time
from decimal import Decimal

from django.db import transaction

from core.models import AIConfiguration, Atendimento
from core.services.entitlements import EntitlementService
from django.core.exceptions import PermissionDenied

from .agent import AIAgent
from .exceptions import AIServiceError, AIPermanentError, AIProviderError, AITemporaryError
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
        try:
            configuration = AIConfiguration.objects.get(
                empresa_id=atendimento.empresa_id,
            )
        except AIConfiguration.DoesNotExist:
            return None
        if not configuration.is_available:
            return None
        subscription = EntitlementService.subscription(atendimento.empresa)
        if subscription and (not subscription.has_access or not subscription.plan.ai_enabled):
            return None
        return configuration

    def reply(self, *, inbound_message):
        started = time.monotonic()
        atendimento = inbound_message.atendimento
        configuration = self.is_enabled(atendimento)
        if configuration is None:
            return None
        if reject_adversarial_input(inbound_message.texto):
            logger.warning(
                'ai.input.rejected company_id=%s attendance_id=%s',
                atendimento.empresa_id, atendimento.pk,
            )
            return OUT_OF_SCOPE_MESSAGE
        try:
            EntitlementService.consume(atendimento.empresa, 'ai_calls')
            response = self.agent.respond(
                configuration=configuration,
                atendimento=atendimento,
                user_input=inbound_message.texto,
            )
            self._record_usage(
                atendimento, response=response,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return validate_ai_output(response.text)
        except (AITemporaryError, AIPermanentError) as error:
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
