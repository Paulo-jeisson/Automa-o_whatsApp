from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def limit_existing_delays(apps, schema_editor):
    profile = apps.get_model('core', 'AIPromptProfile')
    profile.objects.filter(response_delay_seconds=3).update(response_delay_seconds=2)


class Migration(migrations.Migration):
    dependencies = [('core', '0033_production_whatsapp_reliability')]

    operations = [
        migrations.AlterField(
            model_name='aipromptprofile',
            name='response_delay_seconds',
            field=models.PositiveSmallIntegerField(
                default=2,
                validators=[MinValueValidator(0), MaxValueValidator(60)],
            ),
        ),
        migrations.RunPython(limit_existing_delays, migrations.RunPython.noop),
    ]
