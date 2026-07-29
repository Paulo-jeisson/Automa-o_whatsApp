"""Validações determinísticas ao redor do modelo e fallback seguro."""

import re

from .exceptions import AIServiceError


FALLBACK_MESSAGE = (
    'Estou com uma instabilidade no atendimento automático. '
    'Vou encaminhar sua conversa para nossa equipe.'
)

OUT_OF_SCOPE_MESSAGE = (
    'Não posso atender a esse pedido. Posso ajudar apenas com informações e '
    'serviços desta empresa ou encaminhar você para nossa equipe.'
)

_ATTACK_PATTERNS = (
    r'ignore .*(?:regras|instruções)',
    r'(?:mostre|revele).*(?:prompt|instruções internas)',
    r'(?:execute|rode).*(?:sql|comando)',
    r'(?:dados|informações).*(?:outra|outras).*(?:empresa|clínica)',
    r'marque.*(?:sem|mesmo sem).*(?:horário|disponibilidade)',
)


def reject_adversarial_input(text):
    normalized = str(text or '').casefold()
    return any(re.search(pattern, normalized) for pattern in _ATTACK_PATTERNS)


def validate_ai_output(text, *, max_length=4000):
    value = str(text or '').strip()
    if not value:
        raise AIServiceError('A IA retornou uma resposta vazia.')
    if len(value) > max_length:
        raise AIServiceError('A IA retornou uma resposta acima do limite.')
    return value
