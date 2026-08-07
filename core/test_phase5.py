from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Agendamento, Atendimento, BloqueioAgenda, Contato,
    DisponibilidadeSemanal, EmpresaCliente, FluxoAtendimento, Mensagem, Servico,
)
from core.services.scheduling import SchedulingService, SlotUnavailable
from core.services.whatsapp.flow_engine import FlowEngine


class Phase5Base(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('empresa-a', password='senha-forte')
        self.empresa = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa A')
        self.contato = Contato.objects.create(empresa=self.empresa, whatsapp_id='5588999999999', nome='Ana')
        self.atendimento = Atendimento.objects.create(
            empresa=self.empresa, contato=self.contato, nome_cliente='Ana',
            telefone_cliente='5588999999999', opcao_escolhida='WhatsApp', necessidade='Oi',
        )
        self.fluxo = FluxoAtendimento.objects.create(
            empresa=self.empresa, saudacao='Olá!', pergunta_menu='Como posso ajudar?',
            pergunta_dados='Dados?', pergunta_finalizacao='Fim',
            opcoes=[
                {'label': 'Agendar', 'action': 'AGENDAR'},
                {'label': 'Consultar agendamento', 'action': 'CONSULTAR_AGENDAMENTO'},
                {'label': 'Falar com atendente', 'action': 'FALAR_COM_ATENDENTE'},
            ],
        )
        self.servico = Servico.objects.create(empresa=self.empresa, nome='Consulta', duracao_minutos=60)
        self.future = timezone.localdate() + timedelta(days=7)
        DisponibilidadeSemanal.objects.create(
            empresa=self.empresa, dia_semana=self.future.weekday(),
            hora_inicio=time(8), hora_fim=time(12), intervalo_minutos=60,
        )
        self.counter = 0

    def process(self, text):
        self.counter += 1
        message = Mensagem.objects.create(
            empresa=self.empresa, atendimento=self.atendimento, contato=self.contato,
            external_message_id=f'in-{self.counter}', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto=text,
        )
        response = FlowEngine.process(self.atendimento, message)
        self.atendimento.refresh_from_db()
        return response


class FlowEngineTests(Phase5Base):
    def test_complete_schedule_flow_creates_confirmed_appointment(self):
        self.assertIn('Agendar', self.process('Oi'))
        self.assertIn('Consulta', self.process('1'))
        self.assertEqual(self.atendimento.current_step, Atendimento.Step.SERVICE)
        self.process('1')
        self.assertEqual(self.atendimento.current_step, Atendimento.Step.DATE)
        self.assertIn('Horários disponíveis', self.process(self.future.strftime('%d/%m/%Y')))
        self.process('2')
        self.assertIn('Confirme', self.process('invalid'))
        self.assertEqual(self.process('1'), 'Agendamento confirmado ✅')
        appointment = Agendamento.objects.get()
        self.assertEqual(appointment.status, Agendamento.Status.CONFIRMED)
        self.assertEqual(appointment.empresa, self.empresa)

    def test_invalid_service_keeps_step(self):
        self.process('1')
        response = self.process('99')
        self.assertIn('Não consegui', response)
        self.assertEqual(self.atendimento.current_step, Atendimento.Step.SERVICE)

    def test_invalid_and_past_dates_keep_date_step(self):
        self.process('1')
        self.process('1')
        self.assertIn('Data inválida', self.process('ontem'))
        past = timezone.localdate() - timedelta(days=1)
        self.assertIn('passada', self.process(past.strftime('%d/%m/%Y')))
        self.assertEqual(self.atendimento.current_step, Atendimento.Step.DATE)

    def test_human_handoff_disables_automation(self):
        response = self.process('3')
        self.assertIn('encaminhado', response)
        self.assertFalse(self.atendimento.automation_enabled)
        self.assertEqual(self.atendimento.current_step, Atendimento.Step.WAITING_HUMAN)
        self.assertIsNone(self.process('1'))

    def test_lookup_is_limited_to_contact_and_company(self):
        SchedulingService.create_appointment(
            empresa=self.empresa, contato=self.contato, servico=self.servico,
            date=self.future, start_time=time(8),
        )
        response = self.process('2')
        self.assertIn('Consulta', response)
        self.assertIn(self.future.strftime('%d/%m/%Y'), response)

    def test_lookup_without_appointment(self):
        self.assertIn('não possui', self.process('2'))


class SchedulingTests(Phase5Base):
    def test_occupied_and_blocked_slots_are_not_offered(self):
        SchedulingService.create_appointment(
            empresa=self.empresa, contato=self.contato, servico=self.servico,
            date=self.future, start_time=time(8),
        )
        BloqueioAgenda.objects.create(
            empresa=self.empresa, data=self.future, hora_inicio=time(10), hora_fim=time(11),
        )
        slots = SchedulingService.get_available_slots(self.empresa, self.servico, self.future)
        self.assertNotIn(time(8), slots)
        self.assertNotIn(time(10), slots)
        self.assertIn(time(9), slots)

    def test_duplicate_or_overlapping_booking_is_rejected(self):
        SchedulingService.create_appointment(
            empresa=self.empresa, contato=self.contato, servico=self.servico,
            date=self.future, start_time=time(8),
        )
        with self.assertRaises(SlotUnavailable):
            SchedulingService.create_appointment(
                empresa=self.empresa, contato=self.contato, servico=self.servico,
                date=self.future, start_time=time(8),
            )
        self.assertEqual(Agendamento.objects.count(), 1)

    def test_cancel_releases_slot(self):
        appointment = SchedulingService.create_appointment(
            empresa=self.empresa, contato=self.contato, servico=self.servico,
            date=self.future, start_time=time(8),
        )
        SchedulingService.cancel_appointment(appointment)
        self.assertIn(time(8), SchedulingService.get_available_slots(self.empresa, self.servico, self.future))


class AgendaPanelTests(Phase5Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_agenda_and_dashboard_use_company_data(self):
        appointment = SchedulingService.create_appointment(
            empresa=self.empresa, contato=self.contato, servico=self.servico,
            date=self.future, start_time=time(8),
        )
        response = self.client.get(reverse('agenda'), {'inicio': self.future, 'fim': self.future})
        self.assertContains(response, appointment.servico.nome)
        self.assertRedirects(self.client.get(reverse('dashboard')), reverse('prompt_generator'))

    def test_idor_cannot_open_other_company_appointment(self):
        other_user = get_user_model().objects.create_user('empresa-b')
        other_company = EmpresaCliente.objects.create(usuario=other_user, nome='Empresa B')
        other_contact = Contato.objects.create(empresa=other_company, whatsapp_id='5511999999999')
        other_service = Servico.objects.create(empresa=other_company, nome='Reunião')
        appointment = Agendamento.objects.create(
            empresa=other_company, contato=other_contact, servico=other_service,
            data=self.future, hora_inicio=time(8), hora_fim=time(9),
        )
        self.assertEqual(self.client.get(reverse('agendamento_detalhe', args=[appointment.pk])).status_code, 404)

    def test_manual_booking(self):
        response = self.client.post(reverse('agendamento_novo'), {
            'nome_contato': 'Bruno', 'telefone': '5588888888888',
            'servico': self.servico.pk, 'data': self.future,
            'hora_inicio': '09:00', 'status': Agendamento.Status.CONFIRMED,
            'observacao': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Agendamento.objects.filter(origem=Agendamento.Origem.MANUAL).exists())

    def test_cancel_and_human_takeover_are_tenant_scoped(self):
        appointment = SchedulingService.create_appointment(
            empresa=self.empresa, contato=self.contato, servico=self.servico,
            date=self.future, start_time=time(8),
        )
        self.client.post(reverse('agendamento_status', args=[appointment.pk]), {'status': Agendamento.Status.CANCELLED})
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Agendamento.Status.CANCELLED)
        self.client.post(reverse('assumir_atendimento', args=[self.atendimento.pk]))
        self.atendimento.refresh_from_db()
        self.assertEqual(self.atendimento.current_step, Atendimento.Step.HUMAN)
        self.assertFalse(self.atendimento.automation_enabled)
