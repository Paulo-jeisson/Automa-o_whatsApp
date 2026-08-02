from dataclasses import dataclass


@dataclass(frozen=True)
class PromptGeneratorInput:
    agent_name: str
    company_name: str
    segment: str
    uses_calendar: bool
    profession: str
    personality: str
    objective: str
    service_style: str
    tone: str
    products: str
    services: str
    faq: str = ''
    rules: str = ''
    restrictions: str = ''
    human_handoff: str = ''
    additional_information: str = ''
    calendar_usage: str = ''
    # Campos legados mantidos para integrações e dados anteriores.
    forbidden_words: str = ''
    limitations: str = ''
    business_hours: str = ''
    notes: str = ''
