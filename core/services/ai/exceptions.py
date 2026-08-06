class AIServiceError(Exception):
    """Erro base sanitizado da camada de inteligência artificial."""


class AIConfigurationError(AIServiceError):
    """Configuração global ou da empresa impede o uso da IA."""


class AIProviderError(AIServiceError):
    """O provedor de IA não respondeu de forma utilizável."""


class AITemporaryError(AIProviderError):
    """Falha recuperável que deve seguir a política de retry do worker."""


class AIAmbiguousResultError(AITemporaryError):
    """The provider may have completed a billable request before timeout."""


class AIPermanentError(AIProviderError):
    """Falha não recuperável que ainda respeita o limite uniforme do worker."""


class AIToolError(AIServiceError):
    """Uma operação segura solicitada pela IA não pôde ser executada."""


class AIToolValidationError(AIToolError):
    """A tool ou seus argumentos violam o contrato permitido."""
