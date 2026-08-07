from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Agendamento, AIConfiguration, AsyncJob, Atendimento, Contato,
    DisponibilidadeSemanal, EmpresaCliente, KnowledgeBaseArticle,
    OperationalAlert, OperationalMetric, Servico,
)
from core.services.ai.tools import AIToolExecutor, AIToolValidationError
from core.services.observability import raise_alert, record_metric, run_operational_checks
from core.services.queue import enqueue, process_job


class SprintFixtures(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('sprints-16-20', password='test-pass')
        self.other_user = get_user_model().objects.create_user('other-tenant')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Clínica A')
        self.other = EmpresaCliente.objects.create(usuario=self.other_user, nome='Clínica B')
        self.contact = Contato.objects.create(empresa=self.company, whatsapp_id='5511999999999')
        self.attendance = Atendimento.objects.create(
            empresa=self.company, contato=self.contact, nome_cliente='Ana',
            telefone_cliente='5511999999999', opcao_escolhida='', necessidade='',
        )
        self.service = Servico.objects.create(empresa=self.company, nome='Consulta', duracao_minutos=30)
        self.date = timezone.localdate() + timedelta(days=7)
        DisponibilidadeSemanal.objects.create(
            empresa=self.company, dia_semana=self.date.weekday(),
            hora_inicio=time(8), hora_fim=time(12), intervalo_minutos=30,
        )
        self.appointment = Agendamento.objects.create(
            empresa=self.company, contato=self.contact, atendimento=self.attendance,
            servico=self.service, data=self.date, hora_inicio=time(8),
            hora_fim=time(8, 30), status=Agendamento.Status.CONFIRMED,
            origem=Agendamento.Origem.WHATSAPP,
        )
        self.executor = AIToolExecutor(atendimento=self.attendance)


class AppointmentCycleTests(SprintFixtures):
    def test_reschedule_is_atomic_linked_and_tenant_scoped(self):
        result = self.executor.execute('reagendar_agendamento', {
            'agendamento_id': self.appointment.pk, 'data': self.date.isoformat(),
            'hora': '09:00', 'confirmado_pelo_cliente': True,
        })
        self.appointment.refresh_from_db()
        replacement = Agendamento.objects.get(pk=result['novo']['id'])
        self.assertEqual(self.appointment.status, Agendamento.Status.CANCELLED)
        self.assertIsNotNone(self.appointment.cancelled_at)
        self.assertEqual(replacement.rescheduled_from_id, self.appointment.pk)
        self.assertEqual(replacement.status, Agendamento.Status.CONFIRMED)

    def test_changes_require_explicit_confirmation(self):
        with self.assertRaises(AIToolValidationError):
            self.executor.execute('reagendar_agendamento', {
                'agendamento_id': self.appointment.pk, 'data': self.date.isoformat(),
                'hora': '09:00', 'confirmado_pelo_cliente': False,
            })


class PersonalizationAndKnowledgeTests(SprintFixtures):
    def test_personalization_fields_are_saved_per_company(self):
        config = AIConfiguration.objects.create(
            empresa=self.company, assistant_name='Lia', faq='Aceitamos o convênio X.',
            policies='Chegar 10 minutos antes.', cancellation_rules='Avisar com 24 horas.',
        )
        other = AIConfiguration.objects.create(empresa=self.other, assistant_name='Beto', faq='FAQ B')
        self.assertNotEqual(config.assistant_name, other.assistant_name)
        self.assertNotIn(other.faq, config.faq)

    def test_knowledge_search_never_crosses_tenants(self):
        KnowledgeBaseArticle.objects.create(
            empresa=self.company, title='Preparo', content='Jejum de oito horas.', keywords='exame jejum',
        )
        KnowledgeBaseArticle.objects.create(
            empresa=self.other, title='Segredo', content='Jejum secreto da empresa B.', keywords='jejum',
        )
        result = self.executor.execute('pesquisar_base_conhecimento', {'consulta': 'jejum'})
        self.assertIn('Jejum de oito horas', str(result))
        self.assertNotIn('empresa B', str(result))

    def test_customer_cannot_edit_other_tenant_article(self):
        article = KnowledgeBaseArticle.objects.create(empresa=self.other, title='Privado', content='B')
        self.client.force_login(self.user)
        response = self.client.post(reverse('base_conhecimento'), {
            'article_id': article.pk, 'title': 'Invadido', 'content': 'A', 'category': '',
            'keywords': '', 'is_active': 'on',
        })
        self.assertEqual(response.status_code, 404)


class QueueAndObservabilityTests(SprintFixtures):
    @override_settings(TASK_QUEUE_EAGER=False)
    def test_queue_is_idempotent_and_moves_permanent_failure_to_dead_letter(self):
        first = enqueue('unknown.task', {}, idempotency_key='same-event', max_attempts=1)
        second = enqueue('unknown.task', {}, idempotency_key='same-event', max_attempts=1)
        self.assertEqual(first.pk, second.pk)
        process_job(first.pk)
        first.refresh_from_db()
        self.assertEqual(first.status, AsyncJob.Status.DEAD)

    def test_metrics_and_alerts_are_persisted_and_deduplicated(self):
        metric = record_metric('webhook.latency', value=12.5, empresa=self.company)
        first = raise_alert('queue_backlog', 'Fila acumulando', fingerprint='queue')
        second = raise_alert('queue_backlog', 'Fila ainda acumulando', fingerprint='queue')
        self.assertEqual(metric.value, 12.5)
        self.assertEqual(first.pk, second.pk)
        second.refresh_from_db()
        self.assertEqual(second.occurrences, 2)
        self.assertEqual(OperationalMetric.objects.count(), 1)
        self.assertEqual(OperationalAlert.objects.count(), 1)

    @override_settings(
        TASK_QUEUE_BACKLOG_WARNING_COUNT=2,
        TASK_QUEUE_BACKLOG_MAX_AGE_SECONDS=60,
    )
    def test_queue_monitor_records_depth_and_alerts_on_threshold(self):
        for index in range(2):
            AsyncJob.objects.create(
                task_name='unknown', queue='whatsapp',
                idempotency_key=f'monitored-{index}',
            )

        alerts = run_operational_checks()

        self.assertTrue(any(alert.kind == 'queue_backlog' for alert in alerts))
        self.assertEqual(
            OperationalMetric.objects.filter(name='queue.depth').latest('recorded_at').value,
            2,
        )
        self.assertTrue(OperationalMetric.objects.filter(name='queue.processing').exists())
        self.assertTrue(OperationalMetric.objects.filter(name='queue.oldest_age_seconds').exists())
