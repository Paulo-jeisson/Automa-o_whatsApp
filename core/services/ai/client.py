import logging
import json
from dataclasses import dataclass

from django.conf import settings
from openai import APIConnectionError, APITimeoutError, OpenAI, OpenAIError

from .exceptions import AIConfigurationError, AIProviderError


logger = logging.getLogger('ai.provider')


@dataclass(frozen=True)
class AIClientResponse:
    text: str
    response_id: str


class OpenAIClient:
    """Adaptador do provedor. Não conhece ORM, empresa, WhatsApp ou agenda."""

    def __init__(self, *, api_key=None, model=None, timeout=None, sdk_client=None):
        self.api_key = settings.OPENAI_API_KEY if api_key is None else api_key
        self.model = model or settings.AI_MODEL
        self.timeout = settings.AI_TIMEOUT if timeout is None else timeout
        self._sdk_client = sdk_client

    def generate(
        self, *, instructions, user_input, tools=None, tool_executor=None,
        max_tool_rounds=8,
    ):
        self._validate()
        client = self._sdk_client or OpenAI(
            api_key=self.api_key,
            timeout=self.timeout,
            max_retries=0,
        )
        try:
            request = {
                'model': self.model,
                'instructions': instructions,
                'input': user_input,
            }
            if tools:
                request['tools'] = tools
            response = client.responses.create(**request)
            rounds = 0
            while calls := self._function_calls(response):
                if not tool_executor or rounds >= max_tool_rounds:
                    raise AIProviderError('A IA solicitou uma operação inválida.')
                outputs = []
                for call in calls:
                    try:
                        arguments = json.loads(call['arguments'] or '{}')
                    except (TypeError, json.JSONDecodeError) as error:
                        raise AIProviderError('A IA enviou argumentos inválidos.') from error
                    result = tool_executor(call['name'], arguments)
                    outputs.append({
                        'type': 'function_call_output',
                        'call_id': call['call_id'],
                        'output': json.dumps(result, ensure_ascii=False, default=str),
                    })
                rounds += 1
                response = client.responses.create(
                    model=self.model,
                    instructions=instructions,
                    previous_response_id=str(getattr(response, 'id', '') or ''),
                    input=outputs,
                    tools=tools,
                )
        except (APITimeoutError, APIConnectionError) as error:
            logger.warning('ai.provider.unavailable type=%s', type(error).__name__)
            raise AIProviderError('O serviço de IA está temporariamente indisponível.') from error
        except OpenAIError as error:
            logger.warning('ai.provider.rejected type=%s', type(error).__name__)
            raise AIProviderError('O provedor de IA não aceitou a solicitação.') from error

        text = str(getattr(response, 'output_text', '') or '').strip()
        if not text:
            raise AIProviderError('O provedor de IA retornou uma resposta vazia.')
        return AIClientResponse(
            text=text,
            response_id=str(getattr(response, 'id', '') or ''),
        )

    @staticmethod
    def _function_calls(response):
        calls = []
        for item in getattr(response, 'output', []) or []:
            item_type = item.get('type') if isinstance(item, dict) else getattr(item, 'type', '')
            if item_type != 'function_call':
                continue
            getter = item.get if isinstance(item, dict) else lambda key, default='': getattr(item, key, default)
            calls.append({
                'call_id': str(getter('call_id', '') or ''),
                'name': str(getter('name', '') or ''),
                'arguments': getter('arguments', '{}'),
            })
        return calls

    def _validate(self):
        if not settings.AI_ENABLED:
            raise AIConfigurationError('A integração global de IA está desativada.')
        if not self.api_key:
            raise AIConfigurationError('A credencial da OpenAI não está configurada.')
        if not self.model:
            raise AIConfigurationError('O modelo de IA não está configurado.')
        if self.timeout <= 0:
            raise AIConfigurationError('O timeout da IA deve ser positivo.')
