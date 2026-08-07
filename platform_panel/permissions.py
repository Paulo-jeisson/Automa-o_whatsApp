from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied


class PlatformAdminPermission(AccessMixin):
    """Authorize only global platform administrators, never tenant membership."""

    permission_name = 'platform_panel.access_platform_panel'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.has_perm(self.permission_name):
            raise PermissionDenied('Acesso exclusivo da administração da plataforma.')
        return super().dispatch(request, *args, **kwargs)
