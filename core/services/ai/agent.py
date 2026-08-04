import hashlib
import logging
from dataclasses import dataclass

from ...models import AIPromptProfile
from .client import OpenAIClient
from .context import build_company_context, build_conversation_context
from .exceptions import AIConfigurationError
from .prompts import build_conversation_instructions, build_instructions
from .tools import AIToolExecutor, tool_definitions


logger = logging.getLogger('whatsapp.ai.prompt')


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

    @staticmethod
    def _log_prompt_provenance(*, configuration, atendimento, instructions):
        profile = AIPromptProfile.objects.filter(
            empresa_id=configuration.empresa_id,
        ).first()
        configured_prompt = profile.generated_prompt if profile else ''
        visible_prompt = (
            (profile.draft_prompt or profile.generated_prompt)
            if profile else ''
        )
        version = None
        if profile and configured_prompt:
            version = profile.versions.filter(
                content=configured_prompt, is_active=True,
            ).first()
        prompt_hash = hashlib.sha256(instructions.encode('utf-8')).hexdigest()
        configured_hash = hashlib.sha256(configured_prompt.encode('utf-8')).hexdigest()
        visible_hash = hashlib.sha256(visible_prompt.encode('utf-8')).hexdigest()
        origin = (
            'Banco:AIPromptProfile.generated_prompt+'
            + ('build_conversation_instructions' if atendimento else 'build_instructions')
            if profile else
            'Banco:AIConfiguration+build_instructions'
        )
        logger.info(
            'whatsapp.ai.prompt_loaded company_id=%s attendance_id=%s '
            'prompt_id=%s prompt_version=%s prompt_hash=%s prompt_length=%s '
            'prompt_preview=%r prompt_origin=%s configured_prompt_hash=%s '
            'visible_prompt_hash=%s configured_matches_visible=%s',
            configuration.empresa_id,
            getattr(atendimento, 'pk', None),
            getattr(profile, 'pk', None),
            getattr(version, 'version', None),
            prompt_hash,
            len(instructions),
            instructions[:300],
            origin,
            configured_hash,
            visible_hash,
            configured_prompt == visible_prompt,
        )

    def respond(self, *, configuration, user_input, atendimento=None):
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
        self._log_prompt_provenance(
            configuration=configuration,
            atendimento=atendimento,
            instructions=instructions,
        )
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
