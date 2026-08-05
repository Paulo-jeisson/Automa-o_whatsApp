from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0030_asaas_billing'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditevent',
            name='actor',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='iaatende_audit_events',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
