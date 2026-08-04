import hashlib
import logging
from dataclasses import asdict
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.domain.prompt_template import PROMPT_SECTIONS
from core.infrastructure.markdown_builder import MarkdownBuilder
from core.models import AIConfiguration, AIPromptProfile, AIPromptVersion

from .dto import PromptGeneratorInput


logger = logging.getLogger('whatsapp.ai.prompt')


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
    def render_identity(cls, content, *, agent_name, company_name, segment, profession):
        values = {
            '{{AGENT_NAME}}': str(agent_name or '').strip(),
            '{{COMPANY_NAME}}': str(company_name or '').strip(),
            '{{SEGMENT}}': str(segment or '').strip(),
            '{{PROFESSION}}': str(profession or '').strip(),
        }
        rendered = content
        for placeholder, value in values.items():
            rendered = rendered.replace(placeholder, value)
        return rendered

    @classmethod
    @transaction.atomic
    def ensure_default_profile(cls, *, empresa, user=None):
        profile, created = AIPromptProfile.objects.select_for_update().get_or_create(empresa=empresa)
        if created or not profile.generated_prompt.strip():
            configuration = AIConfiguration.objects.filter(empresa=empresa).first()
            content = cls.render_identity(
                cls.default_prompt(),
                agent_name=(configuration.assistant_name if configuration else empresa.nome),
                company_name=empresa.nome,
                segment=empresa.get_segmento_display(),
                profession='atendente virtual',
            )
            profile.generated_prompt = content
            profile.draft_prompt = content
            profile.save(update_fields=['generated_prompt', 'draft_prompt', 'updated_at'])
            if not profile.versions.exists():
                cls._create_active_version(profile=profile, user=user, content=content)
        return profile

    @staticmethod
    def _content_hash(content):
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    @classmethod
    def _create_active_version(cls, *, profile, user, content):
        value = str(content or '')
        if not value.strip():
            raise ValidationError({'content': 'O prompt não pode ficar vazio.'})
        profile.versions.filter(is_active=True).update(is_active=False)
        last = profile.versions.order_by('-version').first()
        version = AIPromptVersion.objects.create(
            profile=profile,
            version=(last.version if last else 0) + 1,
            content=value,
            content_hash=cls._content_hash(value),
            is_active=True,
            published_at=timezone.now(),
            created_by=user,
        )
        profile.generated_prompt = value
        return version

    @classmethod
    def _log_published(cls, *, profile, version, user):
        logger.info(
            'whatsapp.ai.prompt.published company_id=%s prompt_profile_id=%s '
            'prompt_version_id=%s prompt_hash=%s published_by=%s',
            profile.empresa_id, profile.pk, version.pk, version.content_hash,
            getattr(user, 'pk', None),
        )

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
        prompt = cls.render_identity(
            cls.default_prompt(),
            agent_name=data.agent_name,
            company_name=data.company_name,
            segment=data.segment,
            profession=data.profession or 'atendente virtual',
        )
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
        version = cls._create_active_version(profile=profile, user=user, content=content)
        profile.generator_data = asdict(data)
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
        cls._log_published(profile=profile, version=version, user=user)
        return version

    @classmethod
    @transaction.atomic
    def publish_editor_prompt(cls, *, empresa, user, content, response_delay_seconds=3):
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
        version = cls._create_active_version(profile=profile, user=user, content=value)
        profile.draft_prompt = value
        profile.response_delay_seconds = delay
        profile.save()
        AIConfiguration.objects.update_or_create(
            empresa=empresa, defaults={'enabled': True},
        )
        cls._log_published(profile=profile, version=version, user=user)
        return version

    save_editor_version = publish_editor_prompt

    @classmethod
    @transaction.atomic
    def save_draft(cls, *, empresa, content, response_delay_seconds=3):
        value = str(content or '')
        try:
            delay = int(response_delay_seconds)
        except (TypeError, ValueError):
            raise ValidationError({'response_delay_seconds': 'Informe um tempo de resposta válido.'})
        if not 0 <= delay <= 60:
            raise ValidationError({'response_delay_seconds': 'O tempo deve ficar entre 0 e 60 segundos.'})
        profile, _ = AIPromptProfile.objects.select_for_update().get_or_create(empresa=empresa)
        profile.draft_prompt = value
        profile.response_delay_seconds = delay
        profile.autosaved_at = timezone.now()
        profile.save(update_fields=['draft_prompt', 'response_delay_seconds', 'autosaved_at', 'updated_at'])
        return profile
