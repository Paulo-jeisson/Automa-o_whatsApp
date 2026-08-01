from datetime import date, time, timedelta
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.application.analytics_service import DashboardAnalyticsService
from core.models import (
    AIPromptProfile, AIPromptVersion, Agendamento, AttendanceAttachment,
    AttendanceNote, Atendimento, Contato, DisponibilidadeSemanal,
    EmpresaCliente, Holiday, KnowledgeBaseArticle, Servico,
)
from core.services.scheduling import SchedulingService


class PRDSprintsSixToTenTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.user = users.objects.create_user('s6-owner', password='safe-password')
        self.other = users.objects.create_user('s6-other', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Neural Company')
        self.other_company = EmpresaCliente.objects.create(usuario=self.other, nome='Other Company')
        self.contact = Contato.objects.create(empresa=self.company, whatsapp_id='5511999999999', nome='Cliente')
        self.attendance = Atendimento.objects.create(empresa=self.company, contato=self.contact, nome_cliente='Cliente', telefone_cliente='11999999999', opcao_escolhida='Ajuda', necessidade='Teste')
        self.client.force_login(self.user)

    def test_sprint6_autosave_duplicate_and_diff(self):
        response = self.client.post(reverse('prompt_autosave'), {'content': '# Draft'})
        self.assertEqual(response.status_code, 200)
        profile = AIPromptProfile.objects.get(empresa=self.company)
        self.assertEqual(profile.draft_prompt, '# Draft')
        v1 = AIPromptVersion.objects.create(profile=profile, version=1, content='linha antiga', created_by=self.user)
        self.client.post(reverse('prompt_duplicate', args=[v1.id]))
        v2 = profile.versions.get(version=2)
        response = self.client.get(reverse('prompt_diff'), {'left': v1.id, 'right': v2.id})
        self.assertContains(response, 'Versão 1')

    def test_sprint7_knowledge_types_are_tenant_isolated(self):
        KnowledgeBaseArticle.objects.create(empresa=self.company, content_type='PRODUCT', title='Produto A', content='Detalhes', price='29.90')
        KnowledgeBaseArticle.objects.create(empresa=self.other_company, title='Segredo', content='Não pode aparecer')
        response = self.client.get(reverse('base_conhecimento'))
        self.assertContains(response, 'Produto A')
        self.assertNotContains(response, 'Segredo')

    def test_sprint8_holiday_never_returns_available_slots(self):
        target = timezone.localdate() + timedelta(days=7)
        service = Servico.objects.create(empresa=self.company, nome='Consulta', duracao_minutos=30)
        DisponibilidadeSemanal.objects.create(empresa=self.company, dia_semana=target.weekday(), hora_inicio=time(9), hora_fim=time(12))
        Holiday.objects.create(empresa=self.company, date=target, name='Feriado')
        self.assertEqual(SchedulingService.get_available_slots(self.company, service, target), [])

    @override_settings(STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    })
    def test_sprint9_crm_notes_files_and_reopen(self):
        self.client.post(reverse('conversation_note', args=[self.attendance.id]), {'text': 'Nota privada'})
        self.assertTrue(AttendanceNote.objects.filter(atendimento=self.attendance, text='Nota privada').exists())
        self.client.post(reverse('conversation_tag', args=[self.attendance.id]), {'name': 'Prioridade', 'color': '#ff0080'})
        self.assertTrue(self.attendance.tags.filter(name='Prioridade').exists())
        upload = SimpleUploadedFile('audio.txt', b'content', content_type='text/plain')
        self.client.post(reverse('conversation_attachment', args=[self.attendance.id]), {'file': upload})
        self.assertTrue(AttendanceAttachment.objects.filter(atendimento=self.attendance).exists())
        self.attendance.status = Atendimento.STATUS_FINALIZADO; self.attendance.current_step = Atendimento.Step.FINISHED; self.attendance.save()
        self.client.post(reverse('conversation_reopen', args=[self.attendance.id]))
        self.attendance.refresh_from_db(); self.assertEqual(self.attendance.current_step, Atendimento.Step.WAITING_HUMAN)

    def test_sprint10_dashboard_and_csv_export(self):
        metrics = DashboardAnalyticsService.build(self.company)
        self.assertEqual(metrics['conversations'], 1)
        self.assertEqual(self.client.get(reverse('analytics_dashboard')).status_code, 200)
        response = self.client.get(reverse('analytics_export'))
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn(b'Data,Conversas', response.content)
