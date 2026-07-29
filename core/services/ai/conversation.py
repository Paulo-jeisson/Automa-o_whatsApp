"""Orquestra uma resposta de IA para uma mensagem já autenticada."""

import logging

from django.db import transaction

from core.models import AIConfiguration, Atendimento
from core.services.entitlements import EntitlementService
from django.core.exceptions import PermissionDenied

from .agent import AIAgent
from .exceptions import AIServiceError
from .guardrails import (
    FALLBACK_MESSAGE,
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
        configuration = AIConfiguration.objects.filter(
            empresa_id=atendimento.empresa_id,
            enabled=True,
        ).first()
        if not configuration or not configuration.is_available:
            return None
        subscription = EntitlementService.subscription(atendimento.empresa)
        if subscription and (not subscription.has_access or not subscription.plan.ai_enabled):
            return None
        return configuration

    def reply(self, *, inbound_message):
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
            return validate_ai_output(response.text)
        except (AIServiceError, PermissionDenied) as error:
            logger.warning(
                'ai.conversation.failed company_id=%s attendance_id=%s type=%s',
                atendimento.empresa_id, atendimento.pk, type(error).__name__,
            )
            self._handoff(atendimento, 'Falha no atendimento automático.')
            return FALLBACK_MESSAGE
        except Exception as error:
            logger.exception(
                'ai.conversation.unexpected company_id=%s attendance_id=%s type=%s',
                atendimento.empresa_id, atendimento.pk, type(error).__name__,
            )
            self._handoff(atendimento, 'Falha inesperada no atendimento automático.')
            return FALLBACK_MESSAGE

    @staticmethod
    def _handoff(atendimento, reason):
        with transaction.atomic():
            locked = Atendimento.objects.select_for_update().get(pk=atendimento.pk)
            if locked.current_step != Atendimento.Step.HUMAN:
                AIToolExecutor(atendimento=locked).solicitar_atendente(motivo=reason)
