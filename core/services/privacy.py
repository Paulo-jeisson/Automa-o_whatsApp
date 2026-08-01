import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import (
    Agendamento, Atendimento, Contato, DataRetentionPolicy,
    DataSubjectRequest, Mensagem,
)


class PrivacyService:
    @staticmethod
    def export_subject_data(data_request):
        contact = PrivacyService._contact(data_request)
        attendances = Atendimento.objects.filter(empresa=data_request.empresa, contato=contact)
        return {
            'request_id': data_request.pk,
            'generated_at': timezone.now().isoformat(),
            'company': {'id': data_request.empresa_id, 'name': data_request.empresa.nome},
            'contact': {'name': contact.nome, 'whatsapp_id': contact.whatsapp_id},
            'attendances': list(attendances.values(
                'id', 'nome_cliente', 'necessidade', 'status', 'criado_em',
            )),
            'messages': list(Mensagem.objects.filter(
                empresa=data_request.empresa, contato=contact,
            ).values('id', 'direcao', 'tipo', 'texto', 'status', 'timestamp_meta', 'criado_em')),
            'appointments': list(Agendamento.objects.filter(
                empresa=data_request.empresa, contato=contact,
            ).values('id', 'servico__nome', 'data', 'hora_inicio', 'status', 'origem')),
        }

    @staticmethod
    def execute_deletion(data_request):
        if data_request.request_type != DataSubjectRequest.RequestType.DELETION:
            raise ValueError('A solicitação não é de exclusão.')
        if data_request.status != DataSubjectRequest.Status.APPROVED:
            raise ValueError('A solicitação precisa estar aprovada.')
        contact = PrivacyService._contact(data_request)
        with transaction.atomic():
            messages = Mensagem.objects.filter(empresa=data_request.empresa, contato=contact)
            messages.update(texto='', erro_codigo='')
            attendances = Atendimento.objects.filter(empresa=data_request.empresa, contato=contact)
            attendances.update(
                nome_cliente='Titular anonimizado', telefone_cliente='',
                necessidade='', observacao='', conversation_state={},
                conversation_summary='', flow_context={},
            )
            Agendamento.objects.filter(
                empresa=data_request.empresa, contato=contact,
            ).update(observacao='')
            digest = hashlib.sha256(
                f'{data_request.empresa_id}:{contact.pk}:{contact.whatsapp_id}'.encode()
            ).hexdigest()[:20]
            contact.nome = 'Titular anonimizado'
            contact.whatsapp_id = f'anon-{digest}'
            contact.save(update_fields=['nome', 'whatsapp_id', 'atualizado_em'])
            data_request.status = DataSubjectRequest.Status.COMPLETED
            data_request.completed_at = timezone.now()
            data_request.contact = contact
            data_request.save(update_fields=['status', 'completed_at', 'contact'])
        return data_request

    @staticmethod
    def apply_retention(empresa, now=None):
        policy, _ = DataRetentionPolicy.objects.get_or_create(empresa=empresa)
        now = now or timezone.now()
        message_cutoff = now - timedelta(days=policy.message_retention_days)
        attendance_cutoff = now - timedelta(days=policy.attendance_retention_days)
        old_messages = Mensagem.objects.filter(empresa=empresa, criado_em__lt=message_cutoff)
        if policy.anonymize_instead_of_delete:
            affected = old_messages.update(texto='', erro_codigo='')
            affected += Atendimento.objects.filter(
                empresa=empresa, criado_em__lt=attendance_cutoff,
            ).update(
                nome_cliente='Titular anonimizado', telefone_cliente='',
                necessidade='', observacao='', conversation_state={},
                conversation_summary='', flow_context={},
            )
            return affected
        message_count, _ = old_messages.delete()
        attendance_count, _ = Atendimento.objects.filter(
            empresa=empresa, criado_em__lt=attendance_cutoff,
        ).delete()
        return message_count + attendance_count

    @staticmethod
    def serialize_export(data):
        return json.dumps(data, ensure_ascii=False, default=str, indent=2)

    @staticmethod
    def _contact(data_request):
        contact = data_request.contact or Contato.objects.filter(
            empresa=data_request.empresa, whatsapp_id=data_request.whatsapp_id,
        ).first()
        if not contact:
            raise ValueError('Contato não encontrado nesta empresa.')
        return contact
