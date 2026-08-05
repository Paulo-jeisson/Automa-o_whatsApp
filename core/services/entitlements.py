import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from core.domain.exceptions import SubscriptionAccessDenied
from core.models import Subscription, UsageCounter


logger = logging.getLogger('billing.access')


class EntitlementService:
    @staticmethod
    def subscription(empresa):
        try:
            subscription = empresa.subscription
        except Subscription.DoesNotExist:
            return None
        now = timezone.now()
        updates = []
        if subscription.status == Subscription.Status.TRIAL and not subscription.has_access:
            subscription.status = Subscription.Status.BLOCKED
            subscription.blocked_at = now
            updates = ['status', 'blocked_at', 'updated_at']
        elif subscription.status == Subscription.Status.GRACE and not subscription.has_access:
            subscription.status = Subscription.Status.BLOCKED
            subscription.blocked_at = now
            updates = ['status', 'blocked_at', 'updated_at']
        elif subscription.status == Subscription.Status.CANCELED and not subscription.has_access and not subscription.blocked_at:
            subscription.blocked_at = now
            updates = ['blocked_at', 'updated_at']
        if updates:
            subscription.save(update_fields=updates)
        return subscription

    @classmethod
    def access_state(cls, empresa):
        subscription = cls.subscription(empresa)
        if subscription is None:
            return False, None, 'missing_subscription'
        if not subscription.has_access:
            return False, subscription, 'invalid_or_expired_subscription'
        return True, subscription, 'grace' if subscription.status == Subscription.Status.GRACE else 'allowed'

    @classmethod
    def require_company_access(cls, empresa):
        if not getattr(settings, 'SUBSCRIPTION_ENFORCEMENT_ENABLED', True):
            return cls.subscription(empresa)
        allowed, subscription, reason = cls.access_state(empresa)
        if not allowed:
            logger.warning('subscription.access_denied company_id=%s reason=%s', empresa.pk, reason)
            raise SubscriptionAccessDenied('Uma assinatura ativa é necessária.')
        return subscription

    @classmethod
    def require_access(cls, empresa):
        try:
            return cls.require_company_access(empresa)
        except SubscriptionAccessDenied as error:
            raise PermissionDenied(str(error)) from error

    @classmethod
    def require_limit(cls, empresa, resource):
        subscription = cls.require_access(empresa)
        if not subscription:
            return
        plan = subscription.plan
        if resource == 'operators':
            current = empresa.memberships.filter(is_active=True).exclude(user_id=empresa.usuario_id).count() + 1
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
        subscription = cls.require_company_access(empresa)
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
