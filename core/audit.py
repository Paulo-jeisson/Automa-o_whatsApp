from .models import AuditEvent
from .security import client_ip, hash_identifier


def record_audit(request, action, *, empresa=None, target=None, metadata=None):
    actor = request.user if request.user.is_authenticated else None
    AuditEvent.objects.create(
        actor=actor,
        empresa=empresa,
        action=action,
        target_type=target._meta.label_lower if target is not None else '',
        target_id=str(target.pk) if target is not None and target.pk is not None else '',
        metadata=metadata or {},
        ip_hash=hash_identifier(client_ip(request)),
    )
