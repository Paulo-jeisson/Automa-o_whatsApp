from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.db import transaction

from core.models import Subscription, UsageCounter


class EntitlementService:
    @staticmethod
    def subscription(empresa):
        try:
            subscription = empresa.subscription
        except Subscription.DoesNotExist:
            return None
        if subscription.status == Subscription.Status.TRIAL and subscription.trial_ends_at and subscription.trial_ends_at < timezone.now():
            subscription.status = Subscription.Status.SUSPENDED
            subscription.save(update_fields=['status', 'updated_at'])
        return subscription

    @classmethod
    def require_access(cls, empresa):
        subscription = cls.subscription(empresa)
        if subscription and not subscription.has_access:
            raise PermissionDenied('Assinatura sem acesso.')
        return subscription

    @classmethod
    def require_limit(cls, empresa, resource):
        subscription = cls.require_access(empresa)
        if not subscription:
            return
        plan = subscription.plan
        if resource == 'operators':
            current = empresa.memberships.filter(
                is_active=True,
            ).exclude(user_id=empresa.usuario_id).count() + 1
            limit = plan.operator_limit
        elif resource == 'whatsapps':
            current = int(hasattr(empresa, 'whatsapp_integration'))
            limit = plan.whatsapp_limit
        else:
            counter, _ = UsageCounter.objects.get_or_create(
                empresa=empresa, period=timezone.localdate().strftime('%Y-%m'),
            )
            current = getattr(counter, resource)
            limit = getattr(plan, f'{resource[:-1] if resource.endswith("s") else resource}_limit')
        if current >= limit:
            raise PermissionDenied('Limite do plano atingido.')

    @classmethod
    def consume(cls, empresa, resource):
        subscription = cls.subscription(empresa)
        if not subscription:
            return
        field = resource
        limit_field = f'{resource[:-1] if resource.endswith("s") else resource}_limit'
        with transaction.atomic():
            counter, _ = UsageCounter.objects.select_for_update().get_or_create(
                empresa=empresa, period=timezone.localdate().strftime('%Y-%m'),
            )
            if getattr(counter, field) >= getattr(subscription.plan, limit_field):
                raise PermissionDenied('Limite do plano atingido.')
            setattr(counter, field, getattr(counter, field) + 1)
            counter.save(update_fields=[field])
