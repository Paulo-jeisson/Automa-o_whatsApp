from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    AIConfiguration, AIPromptProfile, AsyncJob, Atendimento, Contato,
    EmpresaCliente, IgnoredPhoneNumber, Mensagem, WhatsAppSession,
)
from core.services.evolution_webhook import EvolutionWebhookService
from core.services.phone_numbers import brazilian_phone_variants, normalize_phone_number
from core.services.queue import process_job
from core.services.whatsapp.outbound import send_automatic_reply


@override_settings(AI_ENABLED=True, OPENAI_API_KEY='test-only')
class IgnoredPhoneNumberTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('pass-owner', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa Pass')
        self.session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='pass-instance', state='CONNECTED',
        )
        AIConfiguration.objects.create(empresa=self.company, enabled=True)
        AIPromptProfile.objects.create(
            empresa=self.company, generated_prompt='# Prompt ativo', response_delay_seconds=0,
        )
        self.client.force_login(self.user)

    @staticmethod
    def evolution_message(message_id='pass-evo-1', phone='5527999999999', from_me=False):
        return {
            'event': 'messages.upsert',
            'data': {
                'key': {
                    'id': message_id,
                    'remoteJid': f'{phone}@s.whatsapp.net',
                    'fromMe': from_me,
                },
                'message': {'conversation': 'Olá'},
            },
        }

    def test_add_normalizes_and_lists_phone(self):
        response = self.client.post(reverse('ignored_numbers'), {
            'phone_number': '+55 (27) 99999-9999', 'name': 'Paulo',
        })
        self.assertRedirects(response, reverse('ignored_numbers'))
        number = IgnoredPhoneNumber.objects.get(empresa=self.company)
        self.assertEqual(number.phone_number, '5527999999999')
        page = self.client.get(reverse('ignored_numbers'))
        self.assertContains(page, 'NÚMEROS PASS')
        self.assertContains(page, '5527999999999')

    def test_normalizes_suffix_and_matches_br_ninth_digit_variants(self):
        self.assertEqual(
            normalize_phone_number('+55 (27) 99999-9999@s.whatsapp.net'),
            '5527999999999',
        )
        self.assertIn('5527999999999', brazilian_phone_variants('55 27 9999-9999@c.us'))

    @patch('core.services.whatsapp.outbound.AIConversationService.reply')
    def test_pass_number_never_calls_ai_or_sends_automatic_reply(self, ai_reply):
        IgnoredPhoneNumber.objects.create(empresa=self.company, phone_number='5527999999999')
        contact = Contato.objects.create(
            empresa=self.company, whatsapp_id='5527999999999', nome='Cliente',
        )
        attendance = Atendimento.objects.create(
            empresa=self.company, contato=contact, nome_cliente='Cliente',
            telefone_cliente='27999999999', opcao_escolhida='Ajuda', necessidade='Teste',
        )
        inbound = Mensagem.objects.create(
            empresa=self.company, atendimento=attendance, contato=contact,
            external_message_id='pass-inbound-1', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto='Olá',
        )
        self.assertIsNone(send_automatic_reply(inbound))
        ai_reply.assert_not_called()

    def test_pass_number_does_not_create_automatic_reply_job(self):
        IgnoredPhoneNumber.objects.create(
            empresa=self.company, phone_number='+55 (27) 99999-9999',
        )
        with self.assertLogs('evolution.webhook', level='INFO') as logs:
            EvolutionWebhookService(provider=Mock()).process(
                self.session.pk, self.evolution_message(),
            )
        self.assertFalse(AsyncJob.objects.filter(task_name='whatsapp.automatic_reply').exists())
        self.assertTrue(any('reason=pass_number' in line for line in logs.output))

    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    def test_number_added_after_job_creation_cancels_job_without_sending(self, send_text):
        EvolutionWebhookService(provider=Mock()).process(
            self.session.pk, self.evolution_message(message_id='queued-before-pass'),
        )
        job = AsyncJob.objects.get(task_name='whatsapp.automatic_reply')
        self.assertEqual(job.status, AsyncJob.Status.PENDING)
        IgnoredPhoneNumber.objects.create(
            empresa=self.company, phone_number='55 27 99999-9999',
        )
        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.Status.COMPLETED)
        self.assertEqual(job.last_error, 'cancelled:pass_number')
        process_job(job.pk)
        send_text.assert_not_called()

    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    def test_worker_rechecks_pass_number_before_sending(self, send_text):
        EvolutionWebhookService(provider=Mock()).process(
            self.session.pk, self.evolution_message(message_id='defense-depth'),
        )
        job = AsyncJob.objects.get(task_name='whatsapp.automatic_reply')
        IgnoredPhoneNumber.objects.bulk_create([
            IgnoredPhoneNumber(empresa=self.company, phone_number='5527999999999'),
        ])
        completed = process_job(job.pk)
        self.assertEqual(completed.status, AsyncJob.Status.COMPLETED)
        send_text.assert_not_called()

    def test_from_me_event_is_ignored(self):
        EvolutionWebhookService(provider=Mock()).process(
            self.session.pk, self.evolution_message(message_id='own-message', from_me=True),
        )
        self.assertFalse(Mensagem.objects.filter(external_message_id='own-message').exists())
        self.assertFalse(AsyncJob.objects.exists())

    def test_connected_own_number_is_ignored_even_if_from_me_is_false(self):
        self.session.phone_number = '+55 (27) 99999-9999'
        self.session.save(update_fields=['phone_number'])
        EvolutionWebhookService(provider=Mock()).process(
            self.session.pk, self.evolution_message(message_id='own-number'),
        )
        self.assertFalse(Mensagem.objects.filter(external_message_id='own-number').exists())
        self.assertFalse(AsyncJob.objects.exists())

    def test_loop_protection_pauses_conversation_before_sixth_reply_job(self):
        EvolutionWebhookService(provider=Mock()).process(
            self.session.pk, self.evolution_message(message_id='loop-seed'),
        )
        inbound = Mensagem.objects.get(external_message_id='loop-seed')
        AsyncJob.objects.all().delete()
        for index in range(5):
            Mensagem.objects.create(
                empresa=self.company, atendimento=inbound.atendimento, contato=inbound.contato,
                external_message_id=f'loop-out-{index}', direcao=Mensagem.DIRECAO_SAIDA,
                tipo='text', texto='Resposta automática',
            )
        with self.assertLogs('evolution.webhook', level='INFO') as logs:
            EvolutionWebhookService(provider=Mock()).process(
                self.session.pk, self.evolution_message(message_id='loop-sixth'),
            )
        inbound.atendimento.refresh_from_db()
        self.assertFalse(inbound.atendimento.automation_enabled)
        self.assertFalse(AsyncJob.objects.exists())
        self.assertTrue(any('reason=loop_protection' in line for line in logs.output))

    def test_other_company_cannot_see_or_delete_number(self):
        number = IgnoredPhoneNumber.objects.create(
            empresa=self.company, phone_number='5527999999999', name='Segredo A',
        )
        other_user = get_user_model().objects.create_user('pass-other', password='safe-password')
        EmpresaCliente.objects.create(usuario=other_user, nome='Outra Empresa')
        self.client.force_login(other_user)
        self.assertNotContains(self.client.get(reverse('ignored_numbers')), 'Segredo A')
        self.assertEqual(
            self.client.post(reverse('ignored_number_delete', args=[number.id])).status_code,
            404,
        )
        self.assertTrue(IgnoredPhoneNumber.objects.filter(pk=number.id).exists())

    def test_pass_number_is_isolated_per_company(self):
        IgnoredPhoneNumber.objects.create(empresa=self.company, phone_number='5527999999999')
        other_user = get_user_model().objects.create_user('pass-tenant-b')
        other = EmpresaCliente.objects.create(usuario=other_user, nome='Empresa B')
        other_session = WhatsAppSession.objects.create(
            empresa=other, instance_name='pass-instance-b', state='CONNECTED',
        )
        EvolutionWebhookService(provider=Mock()).process(
            other_session.pk, self.evolution_message(message_id='tenant-b-message'),
        )
        self.assertTrue(AsyncJob.objects.filter(
            task_name='whatsapp.automatic_reply', payload__company_id=other.pk,
        ).exists())
