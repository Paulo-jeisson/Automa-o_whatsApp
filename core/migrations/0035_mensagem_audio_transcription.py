from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0034_ai_delay_two_seconds')]

    operations = [
        migrations.AddField(
            model_name='mensagem', name='transcription_status',
            field=models.CharField(
                choices=[
                    ('NOT_REQUIRED', 'Não aplicável'), ('PENDING', 'Pendente'),
                    ('PROCESSING', 'Processando'), ('COMPLETED', 'Concluída'),
                    ('FAILED', 'Falhou'), ('DISABLED', 'Desativada'),
                ],
                default='NOT_REQUIRED', max_length=16, verbose_name='status da transcrição',
            ),
        ),
        migrations.AddField(
            model_name='mensagem', name='transcription_text',
            field=models.TextField(blank=True, verbose_name='texto transcrito'),
        ),
        migrations.AddField(
            model_name='mensagem', name='transcription_model',
            field=models.CharField(blank=True, max_length=80, verbose_name='modelo de transcrição'),
        ),
        migrations.AddField(
            model_name='mensagem', name='transcription_error',
            field=models.CharField(blank=True, max_length=80, verbose_name='erro de transcrição'),
        ),
    ]
