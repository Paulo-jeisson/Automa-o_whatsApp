from dataclasses import asdict
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

from core.domain.prompt_template import PROMPT_SECTIONS
from core.infrastructure.markdown_builder import MarkdownBuilder
from core.models import AIConfiguration, AIPromptProfile, AIPromptVersion

from .dto import PromptGeneratorInput


class PromptCompilerService:
    """Valida, compila e versiona o prompt isolado de uma empresa."""

    REQUIRED_FIELDS = {
        'agent_name': 'Nome do agente',
        'company_name': 'Nome da empresa',
        'segment': 'Segmento',
    }

    @staticmethod
    def default_prompt():
        path = Path(__file__).resolve().parent.parent / 'domain' / 'default_system_prompt.md'
        return path.read_text(encoding='utf-8').strip()

    @classmethod
    @transaction.atomic
    def ensure_default_profile(cls, *, empresa, user=None):
        profile, created = AIPromptProfile.objects.select_for_update().get_or_create(empresa=empresa)
        if created or not profile.generated_prompt.strip():
            content = cls.default_prompt()
            profile.generated_prompt = content
            profile.draft_prompt = content
            profile.save(update_fields=['generated_prompt', 'draft_prompt', 'updated_at'])
            if not profile.versions.exists():
                AIPromptVersion.objects.create(
                    profile=profile, version=1, content=content, created_by=user,
                )
        return profile

    @classmethod
    def validate(cls, data: PromptGeneratorInput):
        errors = {
            field: f'{label} é obrigatório.'
            for field, label in cls.REQUIRED_FIELDS.items()
            if not str(getattr(data, field, '') or '').strip()
        }
        if errors:
            raise ValidationError(errors)

    @classmethod
    def compile(cls, data: PromptGeneratorInput) -> str:
        cls.validate(data)
        prompt = cls.default_prompt()
        prompt = prompt.replace('Paulo', data.agent_name.strip())
        prompt = prompt.replace('Pj.Advocacia', data.company_name.strip())
        prompt = prompt.replace('consultório jurídico', data.segment.strip())
        prompt = prompt.replace('atendente virtual', data.profession.strip() or 'atendente virtual')
        complement = (
            '\n\n---\n\n# [CONFIGURAÇÃO PERSONALIZADA]\n\n'
            f'- **Ramo do negócio:** {data.segment.strip()}\n'
            f'- **Uso do calendário / agendamentos:** {data.calendar_usage.strip()}\n'
            f'- **Profissão do agente:** {data.profession.strip()}\n'
            f'- **Personalidade e tom:** {data.personality.strip()}\n'
            f'- **Informações adicionais:** {data.additional_information.strip() or "Nenhuma."}'
        )
        return prompt.rstrip() + complement + '\n'

    @classmethod
    @transaction.atomic
    def compile_and_save(cls, *, empresa, user, data):
        content = cls.compile(data)
        profile, _ = AIPromptProfile.objects.select_for_update().get_or_create(empresa=empresa)
        last = profile.versions.order_by('-version').first()
        version = AIPromptVersion.objects.create(
            profile=profile,
            version=(last.version if last else 0) + 1,
            content=content,
            created_by=user,
        )
        profile.generator_data = asdict(data)
        profile.generated_prompt = content
        profile.draft_prompt = content
        profile.save()
        AIConfiguration.objects.update_or_create(
            empresa=empresa,
            defaults={
                'enabled': True,
                'assistant_name': data.agent_name.strip(),
                'tone': data.personality.strip() or 'cordial e objetivo',
                'business_description': data.segment.strip(),
                'additional_information': data.additional_information.strip(),
            },
        )
        return version

    @classmethod
    @transaction.atomic
    def save_editor_version(cls, *, empresa, user, content, response_delay_seconds=3):
        value = str(content or '').strip()
        if not value:
            raise ValidationError({'content': 'O prompt não pode ficar vazio.'})
        try:
            delay = int(response_delay_seconds)
        except (TypeError, ValueError):
            raise ValidationError({'response_delay_seconds': 'Informe um tempo de resposta válido.'})
        if not 0 <= delay <= 60:
            raise ValidationError({'response_delay_seconds': 'O tempo deve ficar entre 0 e 60 segundos.'})
        profile, _ = AIPromptProfile.objects.select_for_update().get_or_create(empresa=empresa)
        last = profile.versions.order_by('-version').first()
        version = AIPromptVersion.objects.create(
            profile=profile, version=(last.version if last else 0) + 1,
            content=value, created_by=user,
        )
        profile.generated_prompt = value
        profile.draft_prompt = value
        profile.response_delay_seconds = delay
        profile.save()
        AIConfiguration.objects.update_or_create(
            empresa=empresa, defaults={'enabled': True},
        )
        return version
