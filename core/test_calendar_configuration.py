from datetime import time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import CalendarConfiguration, DisponibilidadeSemanal, EmpresaCliente, Servico


class CalendarConfigurationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('calendar-owner', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Clínica Agenda')
        self.client.force_login(self.user)

    def payload(self):
        return {
            'enabled': 'on', 'public_slug': 'clinica-agenda', 'display_name': 'Clínica Agenda',
            'weekdays': ['0', '1', '2', '3', '4', '5'],
            'start_time': '08:00', 'end_time': '18:00',
            'break_start': '12:00', 'break_end': '13:00',
            'saturday_start': '09:00', 'saturday_end': '13:00',
            'slot_duration_minutes': '30',
        }

    def test_calendar_screen_matches_configuration_and_syncs_ai_availability(self):
        response = self.client.post(reverse('agenda'), self.payload())
        self.assertRedirects(response, reverse('agenda'))
        config = CalendarConfiguration.objects.get(empresa=self.company)
        self.assertTrue(config.enabled)
        self.assertEqual(config.public_slug, 'clinica-agenda')
        self.assertEqual(DisponibilidadeSemanal.objects.filter(empresa=self.company).count(), 11)
        service = Servico.objects.get(empresa=self.company, ativo=True)
        self.assertEqual(service.nome, 'Clínica Agenda')
        self.assertEqual(service.duracao_minutos, 30)
        saturday = DisponibilidadeSemanal.objects.get(empresa=self.company, dia_semana=5)
        self.assertEqual((saturday.hora_inicio, saturday.hora_fim), (time(9), time(13)))
        page = self.client.get(reverse('agenda'))
        self.assertContains(page, 'CALENDÁRIO DE AGENDAMENTOS')
        self.assertContains(page, 'PRÓXIMOS AGENDAMENTOS')

    def test_calendar_configuration_is_tenant_isolated(self):
        self.client.post(reverse('agenda'), self.payload())
        other_user = get_user_model().objects.create_user('calendar-other', password='safe-password')
        other = EmpresaCliente.objects.create(usuario=other_user, nome='Outra Empresa')
        self.client.force_login(other_user)
        page = self.client.get(reverse('agenda'))
        self.assertNotContains(page, 'clinica-agenda')
        self.assertFalse(DisponibilidadeSemanal.objects.filter(empresa=other).exists())
        self.assertFalse(Servico.objects.filter(empresa=other, ativo=True).exists())

    def test_legacy_configuration_route_no_longer_exists(self):
        self.assertEqual(self.client.get('/agenda/configuracao/').status_code, 404)
