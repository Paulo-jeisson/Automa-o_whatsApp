from django.db import transaction

from .dto import PromptGeneratorInput
from .validators import required_text
from core.models import AIPromptProfile, AIPromptVersion


class PromptGeneratorService:
    @staticmethod
    def render(data: PromptGeneratorInput) -> str:
        agent = required_text(data.agent_name, 'Nome do agente')
        company = required_text(data.company_name, 'Empresa')
        calendar = 'Utilizar a agenda e validar disponibilidade antes de confirmar.' if data.uses_calendar else 'Não realizar agendamentos.'
        return f'''# Identidade
Você é **{agent}**, {data.profession or 'assistente virtual'} da **{company}**, empresa do segmento **{data.segment}**.

# Missão
{data.objective or 'Atender clientes com clareza, agilidade e segurança.'}

# Personalidade
- Personalidade: {data.personality or 'Prestativa e objetiva'}
- Tom de voz: {data.tone or 'Natural e profissional'}
- Forma de atendimento: {data.service_style or 'Conduza uma pergunta por vez'}

# Fluxo de Atendimento
1. Cumprimente e identifique a necessidade.
2. Colete somente os dados necessários.
3. Confirme o entendimento antes de orientar.
4. Encerre resumindo o próximo passo.

# Agendamento
{calendar}
Horário de funcionamento: {data.business_hours or 'conforme informado pela empresa'}.

# Produtos e Serviços
## Produtos
{data.products or 'Consulte a base de conhecimento.'}

## Serviços
{data.services or 'Consulte a base de conhecimento.'}

# Transferência para Humano
Transfira quando houver solicitação explícita, risco, reclamação grave ou falta de informação confiável.

# Restrições
- Nunca invente preços, prazos ou políticas.
- Limitações: {data.limitations or 'Não executar ações fora do atendimento autorizado.'}
- Palavras proibidas: {data.forbidden_words or 'Nenhuma cadastrada.'}
- Preserve dados pessoais e informações sigilosas.

# Observações
{data.notes or 'Sem observações adicionais.'}

# Exemplos de Resposta
> Olá! Eu sou {agent}, da {company}. Como posso ajudar?
'''

    @classmethod
    @transaction.atomic
    def save_version(cls, *, empresa, user, data, content=None):
        profile, _ = AIPromptProfile.objects.get_or_create(empresa=empresa)
        rendered = content or cls.render(data)
        last = profile.versions.order_by('-version').first()
        version = (last.version if last else 0) + 1
        profile.generated_prompt = rendered
        profile.generator_data = data.__dict__
        profile.save()
        return AIPromptVersion.objects.create(
            profile=profile, version=version, content=rendered, created_by=user,
        )
