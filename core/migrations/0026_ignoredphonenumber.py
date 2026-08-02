from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0025_calendarconfiguration')]
    operations = [migrations.CreateModel(
        name='IgnoredPhoneNumber',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('phone_number', models.CharField(max_length=20)),
            ('name', models.CharField(blank=True, max_length=120)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ignored_phone_numbers', to='core.empresacliente')),
        ],
        options={'ordering': ['name', 'phone_number'], 'constraints': [models.UniqueConstraint(fields=('empresa', 'phone_number'), name='unique_ignored_phone_per_company')]},
    )]
