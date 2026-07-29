from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from core.models import (
    Agendamento, AppointmentReminder, Mensagem, ReminderConfiguration,
    WhatsAppIntegration,
)
from core.services.whatsapp.client import WhatsAppCloudClient
from core.services.whatsapp.tokens import access_token_for


class ReminderService:
    @classmethod
    def schedule(cls, appointment):
        try:
            config = appointment.empresa.reminder_configuration
        except ReminderConfiguration.DoesNotExist:
            return []
        if not config.enabled or appointment.status != Agendamento.Status.CONFIRMED:
            return []
        appointment_at = timezone.make_aware(
            datetime.combine(appointment.data, appointment.hora_inicio),
            timezone.get_current_timezone(),
        )
        return [
            AppointmentReminder.objects.get_or_create(
                appointment=appointment,
                offset_hours=offset,
                defaults={'scheduled_for': appointment_at - timedelta(hours=offset)},
            )[0]
            for offset in config.offsets_hours
        ]

    @classmethod
    def process_due(cls, now=None):
        now = now or timezone.now()
        for appointment in Agendamento.objects.filter(
            status=Agendamento.Status.CONFIRMED,
            data__gte=timezone.localdate(),
            empresa__reminder_configuration__enabled=True,
        ).select_related('empresa', 'contato', 'atendimento'):
            cls.schedule(appointment)
        due = AppointmentReminder.objects.filter(
            status=AppointmentReminder.Status.PENDING,
            scheduled_for__lte=now,
            appointment__status=Agendamento.Status.CONFIRMED,
        ).select_related('appointment__empresa', 'appointment__contato', 'appointment__servico')
        sent = 0
        for reminder in due:
            if cls.send(reminder):
                sent += 1
        return sent

    @classmethod
    def send(cls, reminder):
        with transaction.atomic():
            locked = AppointmentReminder.objects.select_for_update().select_related(
                'appointment__empresa', 'appointment__contato',
                'appointment__servico', 'appointment__atendimento',
            ).get(pk=reminder.pk)
            if locked.status != AppointmentReminder.Status.PENDING:
                return False
            appointment = locked.appointment
            config = appointment.empresa.reminder_configuration
            integration = WhatsAppIntegration.objects.filter(
                company=appointment.empresa, is_active=True,
            ).first()
            if not integration:
                locked.status = AppointmentReminder.Status.FAILED
                locked.error_code = 'NO_INTEGRATION'
                locked.save(update_fields=['status', 'error_code'])
                return False
            try:
                result = WhatsAppCloudClient(
                    phone_number_id=integration.phone_number_id,
                    access_token=access_token_for(integration),
                ).send_template(
                    appointment.contato.whatsapp_id,
                    template_name=config.template_name,
                    language_code=config.language_code,
                    parameters=[
                        appointment.contato.nome or 'Cliente',
                        appointment.servico.nome,
                        appointment.data.strftime('%d/%m/%Y'),
                        appointment.hora_inicio.strftime('%H:%M'),
                    ],
                )
            except Exception:
                locked.status = AppointmentReminder.Status.FAILED
                locked.error_code = 'PROVIDER_ERROR'
                locked.save(update_fields=['status', 'error_code'])
                return False
            locked.status = AppointmentReminder.Status.SENT
            locked.external_message_id = result.message_id
            locked.sent_at = timezone.now()
            locked.save(update_fields=['status', 'external_message_id', 'sent_at'])
            if appointment.atendimento_id:
                Mensagem.objects.get_or_create(
                    external_message_id=result.message_id,
                    defaults={
                        'empresa': appointment.empresa,
                        'atendimento': appointment.atendimento,
                        'contato': appointment.contato,
                        'direcao': Mensagem.DIRECAO_SAIDA,
                        'tipo': 'template',
                        'texto': f'Lembrete: {appointment}',
                        'status': Mensagem.STATUS_ACEITA,
                    },
                )
            return True
