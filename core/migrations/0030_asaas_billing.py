import uuid
from datetime import timedelta

from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone
import django.db.models.deletion


def migrate_billing_data(apps, schema_editor):
    Plan = apps.get_model('core', 'Plan')
    Subscription = apps.get_model('core', 'Subscription')
    PaymentEvent = apps.get_model('core', 'PaymentEvent')
    monthly, _ = Plan.objects.update_or_create(
        code='monthly', defaults={'name': 'Mensal', 'price_cents': 14700, 'billing_cycle': 'MONTHLY', 'is_active': True},
    )
    Plan.objects.update_or_create(
        code='annual', defaults={'name': 'Anual', 'price_cents': 99700, 'billing_cycle': 'YEARLY', 'is_active': True},
    )
    for subscription in Subscription.objects.all().iterator():
        if subscription.legacy_stripe_customer_id or subscription.legacy_stripe_subscription_id:
            subscription.provider = 'LEGACY_STRIPE'
        if subscription.status == 'PAST_DUE':
            subscription.status = 'GRACE'
            subscription.overdue_since = timezone.now()
            subscription.grace_period_ends_at = timezone.now() + timedelta(days=3)
        elif subscription.status == 'SUSPENDED':
            subscription.status = 'BLOCKED'
            subscription.blocked_at = timezone.now()
        if subscription.status == 'TRIAL' and subscription.trial_ends_at:
            subscription.trial_started_at = subscription.trial_ends_at - timedelta(days=3)
        subscription.save()
    PaymentEvent.objects.all().update(provider='LEGACY_STRIPE', status='PROCESSED')


class Migration(migrations.Migration):
    dependencies = [('core', '0029_republish_company_2_prompt')]
    operations = [
        migrations.RenameField('plan', 'stripe_price_id', 'legacy_stripe_price_id'),
        migrations.RenameField('subscription', 'stripe_customer_id', 'legacy_stripe_customer_id'),
        migrations.RenameField('subscription', 'stripe_subscription_id', 'legacy_stripe_subscription_id'),
        migrations.RenameField('paymentevent', 'external_id', 'provider_event_id'),
        migrations.AddField('plan', 'billing_cycle', models.CharField(choices=[('MONTHLY', 'Mensal'), ('YEARLY', 'Anual')], default='MONTHLY', max_length=12)),
        migrations.AddField('subscription', 'provider', models.CharField(choices=[('ASAAS', 'Asaas'), ('LEGACY_STRIPE', 'Stripe (legado)')], default='ASAAS', max_length=20)),
        migrations.AddField('subscription', 'billing_reference', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
        migrations.AddField('subscription', 'trial_started_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('subscription', 'current_period_start', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('subscription', 'provider_customer_id', models.CharField(blank=True, max_length=120)),
        migrations.AddField('subscription', 'provider_subscription_id', models.CharField(blank=True, max_length=120, null=True)),
        migrations.AddField('subscription', 'provider_checkout_id', models.CharField(blank=True, max_length=120, null=True)),
        migrations.AddField('subscription', 'checkout_expires_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('subscription', 'last_payment_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('subscription', 'overdue_since', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('subscription', 'grace_period_ends_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('subscription', 'blocked_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('subscription', 'canceled_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('subscription', 'created_at', models.DateTimeField(auto_now_add=True, default=timezone.now), preserve_default=False),
        migrations.AlterField('subscription', 'status', models.CharField(choices=[('TRIAL', 'Período de teste'), ('ACTIVE', 'Ativa'), ('GRACE', 'Pagamento em atraso'), ('BLOCKED', 'Bloqueada'), ('CANCELED', 'Cancelada')], default='TRIAL', max_length=20)),
        migrations.AlterField('plan', 'legacy_stripe_price_id', models.CharField(blank=True, editable=False, max_length=120)),
        migrations.AlterField('subscription', 'legacy_stripe_customer_id', models.CharField(blank=True, editable=False, max_length=120)),
        migrations.AlterField('subscription', 'legacy_stripe_subscription_id', models.CharField(blank=True, editable=False, max_length=120, null=True)),
        migrations.AddConstraint('subscription', models.UniqueConstraint(condition=Q(provider_subscription_id__isnull=False), fields=('provider', 'provider_subscription_id'), name='unique_provider_subscription')),
        migrations.AddField('paymentevent', 'provider', models.CharField(default='ASAAS', max_length=20)),
        migrations.AddField('paymentevent', 'status', models.CharField(choices=[('RECEIVED', 'Recebido'), ('PROCESSING', 'Processando'), ('PROCESSED', 'Processado'), ('FAILED', 'Falhou'), ('IGNORED', 'Ignorado')], default='RECEIVED', max_length=20)),
        migrations.AddField('paymentevent', 'empresa', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payment_events', to='core.empresacliente')),
        migrations.AddField('paymentevent', 'subscription', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='events', to='core.subscription')),
        migrations.AddField('paymentevent', 'payment_external_id', models.CharField(blank=True, max_length=120)),
        migrations.AddField('paymentevent', 'payload', models.JSONField(default=dict)),
        migrations.AddField('paymentevent', 'received_at', models.DateTimeField(auto_now_add=True, default=timezone.now), preserve_default=False),
        migrations.AlterField('paymentevent', 'processed_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('paymentevent', 'failed_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('paymentevent', 'attempts', models.PositiveIntegerField(default=0)),
        migrations.AddField('paymentevent', 'last_error', models.TextField(blank=True)),
        migrations.AlterField('paymentevent', 'provider_event_id', models.CharField(max_length=160)),
        migrations.AddConstraint('paymentevent', models.UniqueConstraint(fields=('provider', 'provider_event_id'), name='unique_provider_event')),
        migrations.AddField('paymenthistory', 'provider', models.CharField(default='ASAAS', max_length=20)),
        migrations.AddField('paymenthistory', 'subscription', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payments', to='core.subscription')),
        migrations.AddField('paymenthistory', 'plan_code', models.SlugField(blank=True)),
        migrations.AddField('paymenthistory', 'due_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('paymenthistory', 'paid_at', models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(migrate_billing_data, migrations.RunPython.noop),
    ]
