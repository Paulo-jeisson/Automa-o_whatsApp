import hashlib
import hmac
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    AIConfiguration,
    AIPromptProfile,
    AsyncJob,
    Atendimento,
    Contato,
    EmpresaCliente,
    FluxoAtendimento,
    Mensagem,
    WhatsAppSession,
    WhatsAppIntegration,
)
from .services.whatsapp.parser import parse_webhook_payload
from .services.whatsapp.client import SendTextResult
from .services.queue import process_job


APP_SECRET = 'app-secret-de-teste'
VERIFY_TOKEN = 'verify-token-de-teste'


def signature_for(body):
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f'sha256={digest}'


def webhook_payload(phone_number_id='phone-123', event='message'):
    value = {
        'messaging_product': 'whatsapp',
        'metadata': {
            'display_phone_number': '5511999999999',
            'phone_number_id': phone_number_id,
        },
    }
    if event == 'message':
        value.update({
            'contacts': [{'profile': {'name': 'Cliente Teste'}, 'wa_id': '5511988887777'}],
            'messages': [{
                'from': '5511988887777',
                'id': 'wamid.message-1',
                'timestamp': '1710000000',
                'text': {'body': 'Olá'},
                'type': 'text',
            }],
        })
    elif event == 'status':
        value['statuses'] = [{
            'id': 'wamid.message-1',
            'recipient_id': '5511988887777',
            'status': 'delivered',
            'timestamp': '1710000001',
        }]

    return {
        'object': 'whatsapp_business_account',
        'entry': [{
            'id': 'waba-123',
            'changes': [{'field': 'messages', 'value': value}],
        }],
    }


@override_settings(META_APP_SECRET=APP_SECRET, META_VERIFY_TOKEN=VERIFY_TOKEN)
class WhatsAppWebhookTests(TestCase):
    def create_integration(self, username='empresa-a', phone_number_id='phone-123'):
        User = get_user_model()
        user = User.objects.create_user(username=username, password='senha-segura')
        company = EmpresaCliente.objects.create(usuario=user, nome=username)
        integration = WhatsAppIntegration.objects.create(
            company=company,
            phone_number_id=phone_number_id,
            whatsapp_business_account_id=f'waba-{phone_number_id}',
        )
        WhatsAppSession.objects.create(
            empresa=company, instance_name=f'evo-{username}', state='CONNECTED',
        )
        return company, integration

    def post_payload(self, payload, signature=None):
        body = json.dumps(payload).encode()
        return self.client.post(
            reverse('whatsapp_webhook'),
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=signature or signature_for(body),
        )

    def test_get_webhook_with_valid_token_returns_challenge(self):
        response = self.client.get(reverse('whatsapp_webhook'), {
            'hub.mode': 'subscribe',
            'hub.verify_token': VERIFY_TOKEN,
            'hub.challenge': 'challenge-123',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'challenge-123')

    def test_get_webhook_rejects_invalid_verify_token(self):
        response = self.client.get(reverse('whatsapp_webhook'), {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'token-incorreto',
            'hub.challenge': 'challenge-123',
        })

        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, VERIFY_TOKEN, status_code=403)

    def test_valid_post_is_accepted_and_updates_known_integration(self):
        company, integration = self.create_integration()

        response = self.post_payload(webhook_payload())

        self.assertEqual(response.status_code, 200)
        integration.refresh_from_db()
        self.assertIsNotNone(integration.last_communication_at)
        self.assertEqual(Contato.objects.filter(empresa=company).count(), 1)
        self.assertEqual(Atendimento.objects.filter(empresa=company).count(), 1)
        self.assertEqual(Mensagem.objects.filter(empresa=company).count(), 1)

    def test_invalid_signature_is_rejected(self):
        response = self.post_payload(webhook_payload(), signature='sha256=invalid')

        self.assertEqual(response.status_code, 403)

    def test_non_json_content_type_is_rejected(self):
        response = self.client.post(
            reverse('whatsapp_webhook'),
            data='not-json',
            content_type='text/plain',
        )

        self.assertEqual(response.status_code, 415)

    @override_settings(
        WHATSAPP_WEBHOOK_MAX_BYTES=10,
        DATA_UPLOAD_MAX_MEMORY_SIZE=10,
    )
    def test_oversized_payload_is_rejected(self):
        response = self.client.post(
            reverse('whatsapp_webhook'),
            data=b'{"payload":"larger-than-limit"}',
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256='sha256=unused',
        )

        self.assertEqual(response.status_code, 413)

    def test_invalid_json_is_rejected_after_signature_validation(self):
        body = b'{invalid-json'

        response = self.client.post(
            reverse('whatsapp_webhook'),
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=signature_for(body),
        )

        self.assertEqual(response.status_code, 400)

    def test_payload_without_phone_number_id_is_ignored_safely(self):
        payload = webhook_payload(phone_number_id='')

        with self.assertLogs('whatsapp.webhook', level='WARNING') as logs:
            response = self.post_payload(payload)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('phone_number_id_missing' in line for line in logs.output))

    def test_unknown_phone_number_id_does_not_touch_known_company(self):
        _company, integration = self.create_integration()

        with self.assertLogs('whatsapp.webhook', level='WARNING') as logs:
            response = self.post_payload(webhook_payload(phone_number_id='unknown-phone'))

        integration.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(integration.last_communication_at)
        self.assertTrue(any('integration.not_found' in line for line in logs.output))
        self.assertFalse(Contato.objects.exists())
        self.assertFalse(Atendimento.objects.exists())
        self.assertFalse(Mensagem.objects.exists())

    def test_inactive_integration_does_not_persist_message(self):
        _company, integration = self.create_integration()
        integration.is_active = False
        integration.save(update_fields=['is_active'])

        response = self.post_payload(webhook_payload())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contato.objects.exists())
        self.assertFalse(Atendimento.objects.exists())
        self.assertFalse(Mensagem.objects.exists())

    def test_message_event_is_parsed_and_logged_without_content(self):
        self.create_integration()

        with self.assertLogs('whatsapp.webhook', level='INFO') as logs:
            response = self.post_payload(webhook_payload(event='message'))

        self.assertEqual(response.status_code, 200)
        message_log = next(line for line in logs.output if 'message.received' in line)
        self.assertIn('wamid.message-1', message_log)
        self.assertNotIn('Olá', message_log)

    def test_status_event_is_distinguished(self):
        _company, integration = self.create_integration()

        with self.assertLogs('whatsapp.webhook', level='INFO') as logs:
            response = self.post_payload(webhook_payload(event='status'))

        integration.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(integration.last_communication_at)
        self.assertTrue(any('status.received' in line for line in logs.output))
        self.assertFalse(Mensagem.objects.exists())

    def test_unknown_event_is_accepted_without_crashing(self):
        self.create_integration()

        response = self.post_payload(webhook_payload(event='unknown'))

        self.assertEqual(response.status_code, 200)

    def test_parser_normalizes_message_fields(self):
        event = parse_webhook_payload(webhook_payload())[0]

        self.assertEqual(event.event_type, 'message')
        self.assertEqual(event.phone_number_id, 'phone-123')
        self.assertEqual(event.message_id, 'wamid.message-1')
        self.assertEqual(event.wa_id, '5511988887777')
        self.assertEqual(event.contact_name, 'Cliente Teste')
        self.assertEqual(event.message_type, 'text')
        self.assertEqual(event.text, 'Olá')

    def test_phone_number_id_isolates_company_a_from_company_b(self):
        _company_a, integration_a = self.create_integration('empresa-a', 'phone-a')
        _company_b, integration_b = self.create_integration('empresa-b', 'phone-b')

        response = self.post_payload(webhook_payload(phone_number_id='phone-b'))

        integration_a.refresh_from_db()
        integration_b.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(integration_a.last_communication_at)
        self.assertIsNotNone(integration_b.last_communication_at)
        self.assertFalse(Mensagem.objects.filter(empresa=integration_a.company).exists())
        self.assertTrue(Mensagem.objects.filter(empresa=integration_b.company).exists())

    def test_existing_contact_is_reused_inside_same_company(self):
        company, _integration = self.create_integration()
        contact = Contato.objects.create(
            empresa=company,
            whatsapp_id='5511988887777',
            nome='Contato Existente',
        )

        response = self.post_payload(webhook_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contato.objects.filter(empresa=company).count(), 1)
        self.assertEqual(Mensagem.objects.get().contato, contact)

    def test_open_attendance_is_reused(self):
        company, _integration = self.create_integration()
        contact = Contato.objects.create(
            empresa=company,
            whatsapp_id='5511988887777',
            nome='Contato Existente',
        )
        attendance = Atendimento.objects.create(
            empresa=company,
            contato=contact,
            nome_cliente=contact.nome,
            telefone_cliente=contact.whatsapp_id,
            opcao_escolhida='WhatsApp',
            necessidade='Primeiro contato',
        )

        response = self.post_payload(webhook_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Atendimento.objects.filter(empresa=company).count(), 1)
        self.assertEqual(Mensagem.objects.get().atendimento, attendance)

    def test_finished_attendance_is_not_reused(self):
        company, _integration = self.create_integration()
        contact = Contato.objects.create(
            empresa=company,
            whatsapp_id='5511988887777',
            nome='Contato Existente',
        )
        Atendimento.objects.create(
            empresa=company,
            contato=contact,
            nome_cliente=contact.nome,
            telefone_cliente=contact.whatsapp_id,
            opcao_escolhida='WhatsApp',
            necessidade='Contato encerrado',
            status=Atendimento.STATUS_FINALIZADO,
        )

        response = self.post_payload(webhook_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Atendimento.objects.filter(empresa=company).count(), 2)
        self.assertEqual(Mensagem.objects.get().atendimento.status, Atendimento.STATUS_NOVO)

    def test_duplicate_message_id_does_not_duplicate_any_record(self):
        company, _integration = self.create_integration()

        first_response = self.post_payload(webhook_payload())
        second_response = self.post_payload(webhook_payload())

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(Contato.objects.filter(empresa=company).count(), 1)
        self.assertEqual(Atendimento.objects.filter(empresa=company).count(), 1)
        self.assertEqual(Mensagem.objects.filter(empresa=company).count(), 1)

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.whatsapp.outbound.AIConversationService.reply', return_value=None)
    @override_settings(AI_ENABLED=True, OPENAI_API_KEY='test-only')
    def test_duplicate_inbound_triggers_only_one_auto_reply(self, _reply_mock, send_mock, _read_mock):
        company, _integration = self.create_integration()
        AIConfiguration.objects.create(empresa=company, enabled=True)
        AIPromptProfile.objects.create(
            empresa=company, generated_prompt='# Prompt ativo para testes',
            response_delay_seconds=0,
        )
        FluxoAtendimento.objects.create(
            empresa=company,
            saudacao='Olá!',
            pergunta_menu='Como ajudar?',
            pergunta_dados='Dados',
            pergunta_finalizacao='Fim',
            opcoes=['Opção A', 'Opção B'],
        )
        send_mock.return_value = SendTextResult('wamid.outbound-1')

        self.post_payload(webhook_payload())
        self.post_payload(webhook_payload())
        process_job(AsyncJob.objects.get(task_name='whatsapp.automatic_reply').pk)

        send_mock.assert_called_once()
        self.assertEqual(Mensagem.objects.filter(direcao=Mensagem.DIRECAO_SAIDA).count(), 1)

    def _create_outbound_message(self, external_id='wamid.message-1'):
        company, integration = self.create_integration()
        contact = Contato.objects.create(
            empresa=company,
            whatsapp_id='5511988887777',
            nome='Cliente',
        )
        attendance = Atendimento.objects.create(
            empresa=company,
            contato=contact,
            nome_cliente='Cliente',
            telefone_cliente=contact.whatsapp_id,
            opcao_escolhida='WhatsApp',
            necessidade='Teste',
        )
        message = Mensagem.objects.create(
            empresa=company,
            atendimento=attendance,
            contato=contact,
            external_message_id=external_id,
            direcao=Mensagem.DIRECAO_SAIDA,
            tipo='text',
            texto='Resposta',
            status=Mensagem.STATUS_ACEITA,
        )
        return integration, message

    def test_status_progresses_from_sent_to_delivered_to_read(self):
        _integration, message = self._create_outbound_message()

        for status, expected in [
            ('sent', Mensagem.STATUS_ENVIADA),
            ('delivered', Mensagem.STATUS_ENTREGUE),
            ('read', Mensagem.STATUS_LIDA),
        ]:
            payload = webhook_payload(event='status')
            payload['entry'][0]['changes'][0]['value']['statuses'][0]['status'] = status
            response = self.post_payload(payload)
            self.assertEqual(response.status_code, 200)
            message.refresh_from_db()
            self.assertEqual(message.status, expected)

    def test_status_does_not_regress(self):
        _integration, message = self._create_outbound_message()
        message.status = Mensagem.STATUS_LIDA
        message.save(update_fields=['status'])
        payload = webhook_payload(event='status')
        payload['entry'][0]['changes'][0]['value']['statuses'][0]['status'] = 'delivered'

        response = self.post_payload(payload)

        message.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(message.status, Mensagem.STATUS_LIDA)

    def test_failed_status_stores_only_error_code(self):
        _integration, message = self._create_outbound_message()
        payload = webhook_payload(event='status')
        status_data = payload['entry'][0]['changes'][0]['value']['statuses'][0]
        status_data['status'] = 'failed'
        status_data['errors'] = [{
            'code': 131000,
            'title': 'Sensitive diagnostic that must not be stored',
        }]

        response = self.post_payload(payload)

        message.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(message.status, Mensagem.STATUS_FALHA)
        self.assertEqual(message.erro_codigo, '131000')

    def test_unknown_status_message_id_returns_200(self):
        self.create_integration()

        response = self.post_payload(webhook_payload(event='status'))

        self.assertEqual(response.status_code, 200)

    def test_non_text_message_is_persisted_without_downloading_media(self):
        company, _integration = self.create_integration()
        payload = webhook_payload()
        message = payload['entry'][0]['changes'][0]['value']['messages'][0]
        message.pop('text')
        message['type'] = 'image'
        message['image'] = {'id': 'media-id-not-downloaded'}

        response = self.post_payload(payload)

        persisted = Mensagem.objects.get(empresa=company)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(persisted.tipo, 'image')
        self.assertEqual(persisted.texto, '')
        self.assertIn('(image)', persisted.atendimento.necessidade)

    def test_message_without_sender_is_ignored_without_partial_records(self):
        self.create_integration()
        payload = webhook_payload()
        value = payload['entry'][0]['changes'][0]['value']
        value['contacts'] = []
        value['messages'][0]['from'] = ''

        response = self.post_payload(payload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contato.objects.exists())
        self.assertFalse(Atendimento.objects.exists())
        self.assertFalse(Mensagem.objects.exists())


class WhatsAppIntegrationPanelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='painel-a', password='senha-segura')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa Painel A')
        self.integration = WhatsAppIntegration.objects.create(
            company=self.company,
            phone_number_id='123456789',
            whatsapp_business_account_id='987654321',
        )
        self.client.login(username='painel-a', password='senha-segura')

    def test_settings_hides_legacy_meta_identifiers_and_tokens(self):
        with override_settings(META_ACCESS_TOKEN='token-que-nao-pode-aparecer'):
            response = self.client.get(reverse('configuracoes'), follow=True)

        self.assertRedirects(response, reverse('trocar_senha'))
        self.assertNotContains(response, '123456789')
        self.assertNotContains(response, '987654321')
        self.assertNotContains(response, 'Phone Number ID')
        self.assertNotContains(response, 'WABA ID')
        self.assertNotContains(response, 'token-que-nao-pode-aparecer')

    @patch('core.views.WhatsAppCloudClient')
    def test_integration_test_uses_company_phone_id_without_sending_message(self, client_class):
        self.integration.set_access_token('token-da-empresa-a')
        self.integration.save(update_fields=['access_token_encrypted'])
        User = get_user_model()
        other_user = User.objects.create_user(username='painel-b', password='senha-segura')
        other_company = EmpresaCliente.objects.create(usuario=other_user, nome='Empresa Painel B')
        WhatsAppIntegration.objects.create(
            company=other_company,
            phone_number_id='222222222',
            whatsapp_business_account_id='333333333',
            access_token_encrypted='token-invalido-que-nao-deve-ser-acessado',
        )

        response = self.client.post(reverse('testar_integracao_whatsapp'))

        self.assertRedirects(
            response, reverse('configuracoes'), fetch_redirect_response=False,
        )
        client_class.assert_called_once_with(
            phone_number_id='123456789',
            access_token='token-da-empresa-a',
        )
        cloud_client = client_class.return_value
        cloud_client.test_configuration.assert_called_once_with('123456789')
        cloud_client.send_text.assert_not_called()
        cloud_client.send_template.assert_not_called()

    @patch('core.views.WhatsAppCloudClient')
    def test_user_cannot_test_another_company_integration(self, client_class):
        User = get_user_model()
        other_user = User.objects.create_user(username='painel-b', password='senha-segura')
        other_company = EmpresaCliente.objects.create(usuario=other_user, nome='Empresa Painel B')
        WhatsAppIntegration.objects.create(
            company=other_company,
            phone_number_id='222222222',
            whatsapp_business_account_id='333333333',
        )
        self.integration.delete()

        response = self.client.post(reverse('testar_integracao_whatsapp'))

        self.assertRedirects(
            response, reverse('configuracoes'), fetch_redirect_response=False,
        )
        client_class.assert_not_called()
