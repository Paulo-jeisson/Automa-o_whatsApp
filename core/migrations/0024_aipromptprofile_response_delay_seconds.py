from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [('core', '0023_atendimento_core_atendi_empresa_e070e8_idx_and_more')]

    operations = [
        migrations.AddField(
            model_name='aipromptprofile',
            name='response_delay_seconds',
            field=models.PositiveSmallIntegerField(
                default=3,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(60),
                ],
            ),
        ),
    ]
