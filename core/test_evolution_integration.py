import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.infrastructure.evolution import EvolutionProvider, EvolutionSendResult
from core.models import (
    AIConfiguration, AIPromptProfile, AsyncJob, Atendimento, Contato,
    EmpresaCliente, Mensagem, WhatsAppSession,
)
from core.services.ai.context import build_company_context
from core.services.ai.prompts import build_instructions
from core.services.evolution_webhook import EvolutionWebhookService
from core.services.queue import process_job
from core.services.whatsapp.outbound import send_automatic_reply, send_text_for_attendance


def message_payload(instance, message_id='evo-in-1', phone='5511999999999', message=None):
    return {
        'event': 'messages.upsert', 'instance': instance,
        'data': {
            'key': {'id': message_id, 'remoteJid': f'{phone}@s.whatsapp.net', 'fromMe': False},
            'pushName': 'Cliente Evolution', 'messageTimestamp': '1770000000',
            'message': message or {'conversation': 'Quero agendar um horário'},
        },
    }


@override_settings(EVOLUTION_WEBHOOK_SECRET='webhook-secret', TASK_QUEUE_EAGER=False)
class EvolutionWebhookTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('evo-owner')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Empresa Evolution')
        self.session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='evo-company-a', state='CONNECTED',
        )
        other_user = get_user_model().objects.create_user('evo-other')
        self.other = EmpresaCliente.objects.create(usuario=other_user, nome='Outra Evolution')
        self.other_session = WhatsAppSession.objects.create(
            empresa=self.other, instance_name='evo-company-b', state='CONNECTED',
        )

    def _post(self, payload, secret='webhook-secret'):
        return self.client.post(
            reverse('evolution_webhook'), data=json.dumps(payload),
            content_type='application/json', HTTP_X_ZAPFLUXO_SECRET=secret,
        )

    def test_webhook_rejects_invalid_secret(self):
        response = self._post(message_payload(self.session.instance_name), 'invalid')
        self.assertEqual(response.status_code, 401)
        self.assertFalse(AsyncJob.objects.exists())

    def test_webhook_accepts_hmac_signature(self):
        payload = message_payload(self.session.instance_name)
        body = json.dumps(payload).encode()
        signature = hmac.new(b'webhook-secret', body, hashlib.sha256).hexdigest()
        response = self.client.post(
            reverse('evolution_webhook'), data=body, content_type='application/json',
            HTTP_X_EVOLUTION_SIGNATURE=f'sha256={signature}',
        )
        self.assertEqual(response.status_code, 202)

    def test_instance_selects_company_and_duplicate_is_idempotent(self):
        payload = message_payload(self.session.instance_name)
        self.assertEqual(self._post(payload).status_code, 202)
        self.assertEqual(self._post(payload).status_code, 202)
        self.assertEqual(AsyncJob.objects.count(), 1)
        job = AsyncJob.objects.get()
        EvolutionWebhookService(provider=Mock()).process(job.payload['session_id'], job.payload['payload'])
        EvolutionWebhookService(provider=Mock()).process(job.payload['session_id'], job.payload['payload'])
        inbound = Mensagem.objects.get(external_message_id='evo-in-1')
        self.assertEqual(inbound.empresa, self.company)
        self.assertEqual(inbound.atendimento.empresa, self.company)
        self.assertFalse(Mensagem.objects.filter(empresa=self.other).exists())
        self.assertEqual(Mensagem.objects.filter(external_message_id='evo-in-1').count(), 1)
        self.assertEqual(AsyncJob.objects.filter(task_name='whatsapp.automatic_reply').count(), 1)

    def test_media_is_downloaded_and_persisted_without_exposing_failure(self):
        provider = Mock()
        provider.download_media.return_value = b'audio-bytes'
        payload = message_payload(
            self.session.instance_name, message_id='evo-audio-1',
            message={'audioMessage': {'mimetype': 'audio/ogg'}},
        )
        EvolutionWebhookService(provider=provider).process(self.session.pk, payload)
        provider.download_media.assert_called_once()
        message = Mensagem.objects.get(external_message_id='evo-audio-1')
        self.assertEqual(message.tipo, 'audio')
        self.assertEqual(message.empresa, self.company)

    def test_qr_event_updates_only_the_instance_session(self):
        payload = {
            'event': 'qrcode.updated', 'instance': self.session.instance_name,
            'data': {'qrcode': {'base64': 'cXItZXZvbHV0aW9u'}},
        }
        EvolutionWebhookService(provider=Mock()).process(self.session.pk, payload)
        self.session.refresh_from_db()
        self.other_session.refresh_from_db()
        self.assertEqual(self.session.state, 'WAITING_QR')
        self.assertEqual(self.session.qr_code, 'data:image/png;base64,cXItZXZvbHV0aW9u')
        self.assertEqual(self.other_session.qr_code, '')

    def test_status_presence_and_internal_messages_never_create_reply_jobs(self):
        internal_payloads = [
            {
                'event': 'messages.update', 'instance': self.session.instance_name,
                'data': {'key': {'id': 'status-delivery'}, 'status': 'DELIVERY_ACK'},
            },
            {
                'event': 'messages.update', 'instance': self.session.instance_name,
                'data': {'key': {'id': 'status-read'}, 'status': 'READ'},
            },
            {
                'event': 'messages.update', 'instance': self.session.instance_name,
                'data': {'key': {'id': 'status-received'}, 'status': 'RECEIVED'},
            },
            {
                'event': 'presence.update', 'instance': self.session.instance_name,
                'data': {'id': 'presence-1'},
            },
            {
                'event': 'messages.upsert', 'instance': self.session.instance_name,
                'data': {
                    'key': {'id': 'protocol-1', 'remoteJid': '5511999999999@s.whatsapp.net'},
                    'message': {'protocolMessage': {'type': 'REVOKE'}},
                },
            },
            {
                'event': 'messages.upsert', 'instance': self.session.instance_name,
                'data': {
                    'key': {'id': 'reaction-1', 'remoteJid': '5511999999999@s.whatsapp.net'},
                    'message': {'reactionMessage': {'text': '👍'}},
                },
            },
        ]
        for payload in internal_payloads:
            with self.subTest(event=payload['event'], data=payload['data']):
                with self.assertLogs('evolution.webhook', level='INFO') as logs:
                    EvolutionWebhookService(provider=Mock()).process(self.session.pk, payload)
                self.assertFalse(AsyncJob.objects.filter(task_name='whatsapp.automatic_reply').exists())
                self.assertTrue(any('whatsapp.reply.reason' in line for line in logs.output))

    def test_real_user_message_types_create_reply_jobs(self):
        cases = {
            'conversation': {'conversation': 'Olá'},
            'list': {'listMessage': {'title': 'Opção escolhida'}},
            'audio': {'audioMessage': {'mimetype': 'audio/ogg'}},
            'image': {'imageMessage': {'caption': 'Imagem do cliente'}},
            'document': {'documentMessage': {'displayName': 'arquivo.pdf'}},
        }
        for index, (kind, message) in enumerate(cases.items(), start=1):
            with self.subTest(kind=kind):
                provider = Mock()
                provider.download_media.return_value = b'media'
                payload = message_payload(
                    self.session.instance_name,
                    message_id=f'real-{index}',
                    phone=f'55119999999{index:02d}',
                    message=message,
                )
                EvolutionWebhookService(provider=provider).process(self.session.pk, payload)
                inbound = Mensagem.objects.get(external_message_id=f'real-{index}')
                self.assertTrue(AsyncJob.objects.filter(
                    task_name='whatsapp.automatic_reply',
                    payload__message_id=inbound.pk,
                    payload__company_id=self.company.pk,
                ).exists())

    @override_settings(AI_ENABLED=True, OPENAI_API_KEY='test-only')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.ai.conversation.AIAgent.respond')
    def test_upsert_conversation_with_delivery_ack_is_answered(
        self, respond, send, _mark,
    ):
        AIConfiguration.objects.create(empresa=self.company, enabled=True)
        AIPromptProfile.objects.create(
            empresa=self.company, generated_prompt='# Prompt ativo', response_delay_seconds=0,
        )
        respond.return_value = SimpleNamespace(
            text='Olá! Como posso ajudar?', provider_response_id='ack-ai-1',
            input_tokens=1, output_tokens=2, tool_calls=0,
        )
        send.return_value = EvolutionSendResult('ack-out-1')
        payload = {
            'event': 'messages.upsert',
            'data': {
                'key': {
                    'remoteJid': '558898176087@s.whatsapp.net',
                    'fromMe': False,
                    'id': 'AC961B50F12FE5F7165727BD69719528',
                },
                'status': 'DELIVERY_ACK',
                'message': {'conversation': 'Oi'},
                'messageType': 'conversation',
            },
        }
        with self.assertLogs('evolution.webhook', level='INFO') as webhook_logs:
            EvolutionWebhookService(provider=Mock()).process(self.session.pk, payload)
        inbound = Mensagem.objects.get(external_message_id='AC961B50F12FE5F7165727BD69719528')
        job = AsyncJob.objects.get(
            task_name='whatsapp.automatic_reply', payload__message_id=inbound.pk,
        )
        with self.assertLogs('queue', level='INFO') as queue_logs:
            with self.assertLogs('whatsapp.outbound', level='INFO') as reply_logs:
                completed = process_job(job.pk)
        self.assertEqual(completed.status, AsyncJob.Status.COMPLETED)
        self.assertTrue(Mensagem.objects.filter(external_message_id='ack-out-1').exists())
        self.assertTrue(any('reason=eligible_user_message' in line for line in webhook_logs.output))
        self.assertTrue(any('outcome=sent' in line for line in reply_logs.output))
        self.assertTrue(any('result=handled' in line for line in queue_logs.output))

    def test_upsert_extended_text_with_delivery_ack_is_enqueued(self):
        payload = {
            'event': 'messages.upsert',
            'data': {
                'key': {
                    'remoteJid': '558898176088@s.whatsapp.net',
                    'fromMe': False,
                    'id': 'extended-with-ack',
                },
                'status': 'DELIVERY_ACK',
                'message': {'extendedTextMessage': {'text': 'Mensagem expandida'}},
                'messageType': 'extendedTextMessage',
            },
        }
        EvolutionWebhookService(provider=Mock()).process(self.session.pk, payload)
        inbound = Mensagem.objects.get(external_message_id='extended-with-ack')
        self.assertEqual(inbound.texto, 'Mensagem expandida')
        self.assertTrue(AsyncJob.objects.filter(
            task_name='whatsapp.automatic_reply', payload__message_id=inbound.pk,
        ).exists())


class EvolutionOutboundTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('outbound-evo')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Outbound Evolution')
        self.session = WhatsAppSession.objects.create(
            empresa=self.company, instance_name='outbound-instance', state='CONNECTED',
        )
        self.contact = Contato.objects.create(empresa=self.company, whatsapp_id='5511988887777')
        self.attendance = Atendimento.objects.create(
            empresa=self.company, contato=self.contact, nome_cliente='Cliente',
            telefone_cliente='5511988887777', opcao_escolhida='WhatsApp', necessidade='Teste',
        )

    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    def test_send_text_uses_company_instance_and_saves_history(self, send_mock):
        send_mock.return_value = EvolutionSendResult('evo-out-1')
        result = send_text_for_attendance(self.attendance, 'Resposta da IA')
        send_mock.assert_called_once_with('outbound-instance', '5511988887777', 'Resposta da IA')
        self.assertEqual(result.external_message_id, 'evo-out-1')
        self.assertEqual(result.empresa, self.company)
        self.assertEqual(result.status, Mensagem.STATUS_ACEITA)

    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    def test_human_transfer_stops_automatic_reply(self, send_mock):
        self.attendance.current_step = Atendimento.Step.WAITING_HUMAN
        self.attendance.save(update_fields=['current_step'])
        inbound = Mensagem.objects.create(
            empresa=self.company, atendimento=self.attendance, contato=self.contact,
            external_message_id='evo-human-1', direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text', texto='Quero falar com alguém',
        )
        self.assertIsNone(send_automatic_reply(inbound))
        send_mock.assert_not_called()

    def test_active_prompt_is_loaded_only_from_company_profile(self):
        configuration = AIConfiguration.objects.create(empresa=self.company, enabled=True)
        AIPromptProfile.objects.create(empresa=self.company, generated_prompt='# PROMPT EXCLUSIVO A')
        other_user = get_user_model().objects.create_user('prompt-other')
        other = EmpresaCliente.objects.create(usuario=other_user, nome='Outra')
        AIPromptProfile.objects.create(empresa=other, generated_prompt='# SEGREDO EMPRESA B')
        instructions = build_instructions(build_company_context(configuration))
        self.assertIn('# PROMPT EXCLUSIVO A', instructions)
        self.assertNotIn('# SEGREDO EMPRESA B', instructions)


class EvolutionProviderTests(TestCase):
    @override_settings(
        EVOLUTION_API_URL='https://evolution.test', EVOLUTION_API_KEY='key',
        EVOLUTION_WEBHOOK_SECRET='secret', PUBLIC_BASE_URL='https://zapfluxo.test',
    )
    @patch('core.infrastructure.evolution.urlopen')
    def test_create_registers_secure_webhook_and_qr_events(self, urlopen_mock):
        response = Mock()
        response.read.return_value = b'{"qrcode":{"base64":"abc"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen_mock.return_value = response
        snapshot = EvolutionProvider().create('instance-a')
        request = urlopen_mock.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload['webhook']['url'], 'https://zapfluxo.test/webhooks/evolution/')
        self.assertEqual(payload['webhook']['headers']['x-zapfluxo-secret'], 'secret')
        self.assertIn('MESSAGES_UPSERT', payload['webhook']['events'])
        self.assertTrue(snapshot.qr_code.startswith('data:image/png;base64,'))

    @override_settings(EVOLUTION_API_URL='https://evolution.test', EVOLUTION_API_KEY='key')
    @patch('core.infrastructure.evolution.urlopen')
    def test_provider_uses_expected_qr_and_send_endpoints(self, urlopen_mock):
        response = Mock()
        response.read.return_value = json.dumps({'key': {'id': 'sent-1'}}).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen_mock.return_value = response
        provider = EvolutionProvider()
        provider.send_text('instance-a', '5511999999999', 'Olá')
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, 'https://evolution.test/message/sendText/instance-a')
        self.assertEqual(request.headers['Apikey'], 'key')

    @override_settings(EVOLUTION_API_URL='https://evolution.test', EVOLUTION_API_KEY='key')
    @patch('core.infrastructure.evolution.time.sleep')
    @patch('core.infrastructure.evolution.urlopen')
    def test_provider_retries_transient_failure_with_backoff(self, urlopen_mock, sleep_mock):
        response = Mock()
        response.read.return_value = b'{"instances": []}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen_mock.side_effect = [URLError('timeout'), response]
        provider = EvolutionProvider()
        result = provider._request('GET', '/instance/fetchInstances')
        self.assertEqual(result['instances'], [])
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0.2)
