from dataclasses import dataclass


@dataclass(frozen=True)
class CompanyAIContext:
    company_id: int
    company_name: str
    assistant_name: str
    greeting: str
    tone: str
    business_description: str
    additional_information: str
    human_handoff_rules: str
    faq: str
    policies: str
    guidance: str
    cancellation_rules: str
    service_rules: str
    allowed_information: str
    saved_system_prompt: str


@dataclass(frozen=True)
class AIConversationContext:
    company: CompanyAIContext
    customer_name: str
    current_step: str
    state: dict
    summary: str
    recent_messages: tuple


def build_company_context(configuration):
    """Converte um modelo já autorizado em dados imutáveis para o agente."""
    profile = getattr(configuration.empresa, 'prompt_profile', None)
    return CompanyAIContext(
        company_id=configuration.empresa_id,
        company_name=configuration.empresa.nome,
        assistant_name=configuration.assistant_name,
        greeting=configuration.greeting,
        tone=configuration.tone,
        business_description=configuration.business_description,
        additional_information=configuration.additional_information,
        human_handoff_rules=configuration.human_handoff_rules,
        faq=configuration.faq,
        policies=configuration.policies,
        guidance=configuration.guidance,
        cancellation_rules=configuration.cancellation_rules,
        service_rules=configuration.service_rules,
        allowed_information=configuration.allowed_information,
        saved_system_prompt=profile.generated_prompt if profile else '',
    )


def build_conversation_context(configuration, atendimento, memory_service=None):
    if atendimento.empresa_id != configuration.empresa_id:
        raise ValueError('A configuração e o atendimento devem pertencer à mesma empresa.')
    if memory_service is None:
        from .memory import ConversationMemoryService
        memory_service = ConversationMemoryService()
    memory = memory_service.build(atendimento=atendimento)
    return AIConversationContext(
        company=build_company_context(configuration),
        customer_name=memory.customer_name,
        current_step=memory.current_step,
        state=memory.state,
        summary=memory.summary,
        recent_messages=memory.recent_messages,
    )
