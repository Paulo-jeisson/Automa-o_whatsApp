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
    forbidden_words: str
    limitations: str
    business_hours: str
    products: str
    services: str
    notes: str
