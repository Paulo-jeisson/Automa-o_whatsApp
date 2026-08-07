from django.db import models


class PlatformAccess(models.Model):
    """Permission anchor; the panel itself has no tenant or company owner."""

    class Meta:
        default_permissions = ()
        permissions = [('access_platform_panel', 'Can access the Platform master panel')]
        verbose_name = 'acesso ao Platform'
        verbose_name_plural = 'acessos ao Platform'
