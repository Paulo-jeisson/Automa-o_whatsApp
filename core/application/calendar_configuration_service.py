from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify

from core.models import CalendarConfiguration, DisponibilidadeSemanal, Servico


class CalendarConfigurationService:
    @staticmethod
    def initial_for(empresa):
        return {
            'public_slug': empresa.public_slug or slugify(empresa.nome),
            'display_name': empresa.nome,
            'weekdays': ['0', '1', '2', '3', '4', '5'],
            'start_time': '08:00', 'end_time': '18:00',
            'saturday_start': '09:00', 'saturday_end': '13:00',
            'slot_duration_minutes': 30,
        }

    @classmethod
    @transaction.atomic
    def save(cls, *, empresa, data):
        start, end = data['start_time'], data['end_time']
        break_start, break_end = data.get('break_start'), data.get('break_end')
        if end <= start:
            raise ValidationError('O fim do atendimento deve ser posterior ao início.')
        if bool(break_start) != bool(break_end):
            raise ValidationError('Preencha o início e o fim do intervalo.')
        if break_start and not (start < break_start < break_end < end):
            raise ValidationError('O intervalo deve ficar dentro do horário de atendimento.')
        slug = slugify(data['public_slug'])
        if not slug:
            raise ValidationError('Informe uma URL válida para o calendário.')
        conflict = CalendarConfiguration.objects.filter(public_slug=slug).exclude(empresa=empresa).exists()
        if conflict:
            raise ValidationError('Esta URL de calendário já está em uso.')
        config, _ = CalendarConfiguration.objects.select_for_update().update_or_create(
            empresa=empresa,
            defaults={
                'enabled': data.get('enabled', False), 'public_slug': slug,
                'display_name': data['display_name'].strip(), 'weekdays': data['weekdays'],
                'start_time': start, 'end_time': end,
                'break_start': break_start, 'break_end': break_end,
                'saturday_start': data.get('saturday_start'), 'saturday_end': data.get('saturday_end'),
                'slot_duration_minutes': data['slot_duration_minutes'],
            },
        )
        # The visible calendar is the only source of truth. Servico remains an
        # internal reference because existing appointments use a foreign key.
        services = Servico.objects.select_for_update().filter(empresa=empresa)
        if config.enabled:
            service_name = config.display_name.strip()
            service = services.filter(nome__iexact=service_name).first() or services.first()
            if service is None:
                service = Servico(empresa=empresa)
            services.exclude(pk=service.pk).update(ativo=False)
            service.nome = service_name
            service.duracao_minutos = config.slot_duration_minutes
            service.ativo = True
            service.save()
        else:
            services.update(ativo=False)
        DisponibilidadeSemanal.objects.filter(empresa=empresa).delete()
        if config.enabled:
            rows = []
            for raw_day in config.weekdays:
                day = int(raw_day)
                if day == 5 and config.saturday_start and config.saturday_end:
                    windows = [(config.saturday_start, config.saturday_end)]
                elif break_start:
                    windows = [(start, break_start), (break_end, end)]
                else:
                    windows = [(start, end)]
                rows.extend(DisponibilidadeSemanal(
                    empresa=empresa, dia_semana=day, hora_inicio=window_start,
                    hora_fim=window_end, intervalo_minutos=config.slot_duration_minutes,
                ) for window_start, window_end in windows)
            DisponibilidadeSemanal.objects.bulk_create(rows)
        return config
