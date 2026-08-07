from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name='PlatformAccess',
            fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'))],
            options={
                'verbose_name': 'acesso ao Platform',
                'verbose_name_plural': 'acessos ao Platform',
                'default_permissions': (),
                'permissions': [('access_platform_panel', 'Can access the Platform master panel')],
            },
        ),
    ]
