from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0024_aipromptprofile_response_delay_seconds')]

    operations = [
        migrations.CreateModel(
            name='CalendarConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=False)),
                ('public_slug', models.SlugField(max_length=140, unique=True)),
                ('display_name', models.CharField(max_length=140)),
                ('weekdays', models.JSONField(default=list)),
                ('start_time', models.TimeField(default='08:00')),
                ('end_time', models.TimeField(default='18:00')),
                ('break_start', models.TimeField(blank=True, null=True)),
                ('break_end', models.TimeField(blank=True, null=True)),
                ('saturday_start', models.TimeField(blank=True, default='09:00', null=True)),
                ('saturday_end', models.TimeField(blank=True, default='13:00', null=True)),
                ('slot_duration_minutes', models.PositiveSmallIntegerField(default=30)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('empresa', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='calendar_configuration', to='core.empresacliente')),
            ],
            options={'ordering': ['empresa__nome']},
        ),
    ]
