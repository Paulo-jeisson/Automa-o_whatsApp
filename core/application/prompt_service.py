"""Compatibilidade com integrações que ainda importam o nome antigo."""
from .prompt_compiler_service import PromptCompilerService


class PromptGeneratorService(PromptCompilerService):
    render = PromptCompilerService.compile

    @classmethod
    def save_version(cls, *, empresa, user, data, content=None):
        if content is not None:
            return cls.save_editor_version(empresa=empresa, user=user, content=content)
        return cls.compile_and_save(empresa=empresa, user=user, data=data)
