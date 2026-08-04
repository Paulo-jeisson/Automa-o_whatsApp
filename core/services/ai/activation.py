import logging

from core.models import AIConfiguration


logger = logging.getLogger('whatsapp.outbound')


def auto_enable_company_ai(company_id):
    """Keep the legacy storage field aligned with a connected company session."""
    configuration, created = AIConfiguration.objects.get_or_create(
        empresa_id=company_id,
        defaults={'enabled': True},
    )
    changed = created
    if not created and not configuration.enabled:
        configuration.enabled = True
        configuration.save(update_fields=['enabled', 'updated_at'])
        changed = True
    if changed:
        logger.info('whatsapp.ai.auto_enabled company_id=%s', company_id)
    return configuration
