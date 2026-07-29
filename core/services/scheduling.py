from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Agendamento, BloqueioAgenda, DisponibilidadeSemanal, EmpresaCliente


class SlotUnavailable(ValidationError):
    pass


class SchedulingService:
    ACTIVE_STATUSES = (Agendamento.Status.PENDING, Agendamento.Status.CONFIRMED)

    @classmethod
    def get_available_dates(cls, empresa, servico, days=30):
        today = timezone.localdate()
        return [
            date for offset in range(days)
            if cls.get_available_slots(empresa, servico, date := today + timedelta(days=offset))
        ]

    @classmethod
    def get_available_slots(cls, empresa, servico, date):
        if servico.empresa_id != empresa.pk or not servico.ativo or date < timezone.localdate():
            return []
        windows = DisponibilidadeSemanal.objects.filter(
            empresa=empresa, dia_semana=date.weekday(), ativo=True,
        )
        blocks = list(BloqueioAgenda.objects.filter(empresa=empresa, data=date))
        if any(block.hora_inicio is None for block in blocks):
            return []
        appointments = list(Agendamento.objects.filter(
            empresa=empresa, data=date, status__in=cls.ACTIVE_STATUSES,
        ))
        now = timezone.localtime()
        result = []
        duration = timedelta(minutes=servico.duracao_minutos)
        for window in windows:
            cursor = datetime.combine(date, window.hora_inicio)
            end = datetime.combine(date, window.hora_fim)
            while cursor + duration <= end:
                slot_end = cursor + duration
                if date == now.date() and cursor.time() <= now.time():
                    cursor += timedelta(minutes=window.intervalo_minutos)
                    continue
                overlaps_block = any(
                    cursor.time() < block.hora_fim and slot_end.time() > block.hora_inicio
                    for block in blocks
                )
                overlaps_appointment = any(
                    cursor.time() < item.hora_fim and slot_end.time() > item.hora_inicio
                    for item in appointments
                )
                if not overlaps_block and not overlaps_appointment:
                    result.append(cursor.time())
                cursor += timedelta(minutes=window.intervalo_minutos)
        return sorted(set(result))

    @classmethod
    def create_appointment(
        cls, *, empresa, contato, servico, date, start_time,
        atendimento=None, origem=Agendamento.Origem.WHATSAPP, observacao='',
    ):
        if contato.empresa_id != empresa.pk or servico.empresa_id != empresa.pk:
            raise ValidationError('Contato e serviço devem pertencer à empresa.')
        if atendimento and atendimento.empresa_id != empresa.pk:
            raise ValidationError('O atendimento deve pertencer à empresa.')
        with transaction.atomic():
            # Serializa confirmações da mesma empresa inclusive quando ainda não
            # existe nenhuma linha de agendamento para o dia.
            EmpresaCliente.objects.select_for_update().get(pk=empresa.pk)
            list(empresa.agendamentos.select_for_update().filter(data=date))
            if start_time not in cls.get_available_slots(empresa, servico, date):
                raise SlotUnavailable('Este horário não está mais disponível.')
            end_time = (datetime.combine(date, start_time) + timedelta(minutes=servico.duracao_minutos)).time()
            try:
                return Agendamento.objects.create(
                    empresa=empresa, contato=contato, atendimento=atendimento,
                    servico=servico, data=date, hora_inicio=start_time,
                    hora_fim=end_time, status=Agendamento.Status.CONFIRMED,
                    origem=origem, observacao=observacao,
                )
            except IntegrityError as error:
                raise SlotUnavailable('Este horário não está mais disponível.') from error

    @classmethod
    def cancel_appointment(cls, appointment):
        appointment.status = Agendamento.Status.CANCELLED
        appointment.save(update_fields=['status', 'updated_at'])
        return appointment

    @classmethod
    def reschedule_appointment(cls, appointment, date, start_time):
        with transaction.atomic():
            old_status = appointment.status
            appointment.status = Agendamento.Status.CANCELLED
            appointment.save(update_fields=['status', 'updated_at'])
            try:
                replacement = cls.create_appointment(
                    empresa=appointment.empresa, contato=appointment.contato,
                    atendimento=appointment.atendimento, servico=appointment.servico,
                    date=date, start_time=start_time, origem=appointment.origem,
                    observacao=appointment.observacao,
                )
            except Exception:
                appointment.status = old_status
                appointment.save(update_fields=['status', 'updated_at'])
                raise
            return replacement
