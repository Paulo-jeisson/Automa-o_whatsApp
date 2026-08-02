import hashlib
import hmac
import json
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
