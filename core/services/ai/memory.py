"""Memória conversacional limitada e estruturada."""

from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from core.models import Mensagem

from .exceptions import AIConfigurationError


ALLOWED_STATE_FIELDS = {
    'intent', 'service_id', 'service_name', 'date', 'period', 'time',
    'appointment_id', 'awaiting_confirmation', 'handoff_reason',
}


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    text: str


@dataclass(frozen=True)
class ConversationMemory:
    customer_name: str
    current_step: str
    state: dict
    summary: str
    recent_messages: tuple


class ConversationMemoryService:
    def __init__(self, *, message_limit=None, summary_trigger=None, summary_max_chars=None):
        self.message_limit = settings.AI_CONTEXT_MESSAGE_LIMIT if message_limit is None else message_limit
        self.summary_trigger = settings.AI_CONTEXT_SUMMARY_TRIGGER if summary_trigger is None else summary_trigger
        self.summary_max_chars = settings.AI_CONTEXT_SUMMARY_MAX_CHARS if summary_max_chars is None else summary_max_chars
        if min(self.message_limit, self.summary_trigger, self.summary_max_chars) <= 0:
            raise AIConfigurationError('Os limites de contexto devem ser positivos.')

    def build(self, *, atendimento):
        self._validate_tenant(atendimento)
        self._compact_if_needed(atendimento)
        messages = list(Mensagem.objects.filter(
            atendimento=atendimento,
            empresa_id=atendimento.empresa_id,
            contato_id=atendimento.contato_id,
        ).order_by('-criado_em')[:self.message_limit])
        messages.reverse()
        return ConversationMemory(
            customer_name=atendimento.contato.nome or atendimento.nome_cliente,
            current_step=atendimento.current_step,
            state=dict(atendimento.conversation_state or {}),
            summary=atendimento.conversation_summary,
            recent_messages=tuple(
                ConversationMessage(
                    role='user' if item.direcao == Mensagem.DIRECAO_ENTRADA else 'assistant',
                    text=item.texto[:1000],
                )
                for item in messages if item.texto
            ),
        )

    def update_state(self, *, atendimento, values):
        self._validate_tenant(atendimento)
        if set(values) - ALLOWED_STATE_FIELDS:
            raise AIConfigurationError('O estado contém campos não permitidos.')
        state = dict(atendimento.conversation_state or {})
        for key, value in values.items():
            if value is None:
                state.pop(key, None)
            elif isinstance(value, (str, int, float, bool)):
                state[key] = value
            else:
                raise AIConfigurationError('Valor de estado inválido.')
        atendimento.conversation_state = state
        atendimento.save(update_fields=['conversation_state'])
        return state

    def _compact_if_needed(self, atendimento):
        queryset = Mensagem.objects.filter(
            atendimento=atendimento,
            empresa_id=atendimento.empresa_id,
            contato_id=atendimento.contato_id,
        ).order_by('criado_em')
        total = queryset.count()
        keep_from = max(total - self.message_limit, 0)
        if total < self.summary_trigger or keep_from <= atendimento.summarized_message_count:
            return
        items = queryset[atendimento.summarized_message_count:keep_from]
        additions = [
            ('Cliente: ' if item.direcao == Mensagem.DIRECAO_ENTRADA else 'Assistente: ')
            + item.texto[:300]
            for item in items if item.texto
        ]
        summary = '\n'.join(
            part for part in [atendimento.conversation_summary, *additions] if part
        )[-self.summary_max_chars:]
        with transaction.atomic():
            locked = type(atendimento).objects.select_for_update().get(pk=atendimento.pk)
            if keep_from > locked.summarized_message_count:
                locked.conversation_summary = summary
                locked.summarized_message_count = keep_from
                locked.save(update_fields=['conversation_summary', 'summarized_message_count'])
                atendimento.conversation_summary = summary
                atendimento.summarized_message_count = keep_from

    @staticmethod
    def _validate_tenant(atendimento):
        if (
            not atendimento or not atendimento.pk or not atendimento.empresa_id
            or not atendimento.contato_id
            or atendimento.contato.empresa_id != atendimento.empresa_id
        ):
            raise AIConfigurationError(
                'Atendimento e contato válidos da mesma empresa são obrigatórios.'
            )
