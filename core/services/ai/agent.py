from dataclasses import dataclass

from .client import OpenAIClient
from .context import build_company_context, build_conversation_context
from .exceptions import AIConfigurationError
from .prompts import build_conversation_instructions, build_instructions
from .tools import AIToolExecutor, tool_definitions


@dataclass(frozen=True)
class AIReply:
    text: str
    provider_response_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0


class AIAgent:
    """Orquestra contexto e provedor sem executar regras de negócio."""

    def __init__(self, client=None):
        self.client = client or OpenAIClient()

    def respond(self, *, configuration, user_input, atendimento=None):
        if not configuration.enabled:
            raise AIConfigurationError('A IA está desativada para esta empresa.')
        message = str(user_input or '').strip()
        if not message:
            raise AIConfigurationError('A mensagem do cliente está vazia.')

        if atendimento is None:
            context = build_company_context(configuration)
            instructions = build_instructions(context)
        else:
            context = build_conversation_context(configuration, atendimento)
            instructions = build_conversation_instructions(context)
        request = {'instructions': instructions, 'user_input': message}
        if atendimento is not None:
            executor = AIToolExecutor(atendimento=atendimento)
            request.update({
                'tools': tool_definitions(),
                'tool_executor': executor.execute,
            })
        response = self.client.generate(**request)
        return AIReply(
            text=response.text,
            provider_response_id=response.response_id,
            input_tokens=int(getattr(response, 'input_tokens', 0) or 0),
            output_tokens=int(getattr(response, 'output_tokens', 0) or 0),
            tool_calls=int(getattr(response, 'tool_calls', 0) or 0),
        )

    @staticmethod
    def execute_tool(*, atendimento, name, arguments=None):
        return AIToolExecutor(atendimento=atendimento).execute(name, arguments)
