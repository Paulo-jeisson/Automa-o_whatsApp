from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0028_active_prompt_publication'),
    ]

    # Mantida como marco de compatibilidade. Migrações não publicam nem
    # reescrevem identidade configurada por usuários.
    operations = []
