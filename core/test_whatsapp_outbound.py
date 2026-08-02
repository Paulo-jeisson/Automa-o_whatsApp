import io
import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from .models import (
    AIConfiguration,
    AIPromptProfile,
    Atendimento,
    Contato,
    EmpresaCliente,
    FluxoAtendimento,
    Mensagem,
    WhatsAppSession,
    WhatsAppIntegration,
    dados_padrao_fluxo,
)
from .services.whatsapp.client import SendTextResult, WhatsAppCloudClient
from .services.whatsapp.exceptions import WhatsAppAPIError, WhatsAppProviderError
from .services.whatsapp.outbound import send_automatic_reply, send_text_for_attendance


class FakeResponse:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.data


@override_settings(
    META_ACCESS_TOKEN='token-de-teste',
    META_GRAPH_API_VERSION='v23.0',
)
class WhatsAppCloudClientTests(TestCase):
    @patch('core.services.whatsapp.client.urlopen')
    def test_send_text_returns_external_message_id(self, urlopen_mock):
        urlopen_mock.return_value = FakeResponse({
            'messages': [{'id': 'wamid.outbound-1'}],
        })
        client = WhatsAppCloudClient(phone_number_id='123456789')

        result = client.send_text('5511988887777', 'Olá!')

        self.assertEqual(result.message_id, 'wamid.outbound-1')
        request = urlopen_mock.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload['to'], '5511988887777')
        self.assertEqual(payload['type'], 'text')
        self.assertNotIn('token-de-teste', request.full_url)

    @patch('core.services.whatsapp.client.urlopen', side_effect=TimeoutError)
    def test_send_text_handles_timeout(self, _urlopen_mock):
        client = WhatsAppCloudClient(phone_number_id='123456789')

        with self.assertRaises(WhatsAppAPIError):
            client.send_text('5511988887777', 'Olá!')

    @patch('core.services.whatsapp.client.urlopen')
    def test_send_text_handles_http_error_without_exposing_response(self, urlopen_mock):
        urlopen_mock.side_effect = HTTPError(
            url='https://graph.facebook.com/',
            code=400,
            msg='Bad request',
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"code":131000,"message":"sensitive"}}'),
        )
        client = WhatsAppCloudClient(phone_number_id='123456789')

        with self.assertRaises(WhatsAppAPIError) as context:
            client.send_text('5511988887777', 'Olá!')

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.error_code, '131000')
        self.assertNotIn('sensitive', str(context.exception))

    @patch('core.services.whatsapp.client.urlopen')
    def test_meta_error_parses_safe_structured_details(self, urlopen_mock):
        secret = 'token-super-secreto'
        urlopen_mock.side_effect = HTTPError(
            url='https://graph.facebook.com/v25.0/123456789',
            code=401,
            msg='Unauthorized',
            hdrs=None,
            fp=io.BytesIO(json.dumps({
                'error': {
                    'message': f'Expired Bearer {secret}',
                    'type': 'OAuthException',
                    'code': 190,
                    'error_subcode': 463,
                    'fbtrace_id': 'safe-trace-id',
                },
            }).encode()),
        )
        client = WhatsAppCloudClient(
            phone_number_id='123456789',
            access_token=secret,
            api_version='v25.0',
        )

        with self.assertRaises(WhatsAppAPIError) as context:
            client.test_configuration()

        error = context.exception
        self.assertEqual(error.status_code, 401)
        self.assertEqual(error.error_code, '190')
        self.assertEqual(error.error_subcode, '463')
        self.assertEqual(error.error_type, 'OAuthException')
        self.assertEqual(error.fbtrace_id, 'safe-trace-id')
        self.assertNotIn(secret, error.meta_message)
        self.assertNotIn(secret, str(error))

    @patch('core.services.whatsapp.client.urlopen')
    def test_meta_http_errors_without_json_remain_safe(self, urlopen_mock):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                urlopen_mock.side_effect = HTTPError(
                    url='https://graph.facebook.com/v25.0/123456789',
                    code=status,
                    msg='Error',
                    hdrs=None,
                    fp=io.BytesIO(b'not-json'),
                )
                client = WhatsAppCloudClient(
                    phone_number_id='123456789',
                    access_token='token-secreto',
                    api_version='v25.0',
                )
                with self.assertRaises(WhatsAppAPIError) as context:
                    client.test_configuration()
                self.assertEqual(context.exception.status_code, status)
                self.assertEqual(context.exception.error_code, '')
                self.assertNotIn('token-secreto', str(context.exception))

    @override_settings(META_ACCESS_TOKEN='token-global')
    def test_explicit_empty_token_does_not_fall_back_to_global(self):
        client = WhatsAppCloudClient(
            phone_number_id='123456789',
            access_token='',
        )

        with self.assertRaises(WhatsAppProviderError):
            client.test_configuration()

    @patch('core.services.whatsapp.client.urlopen')
    def test_send_text_handles_invalid_json(self, urlopen_mock):
        response = FakeResponse({})
        response.data = b'not-json'
        urlopen_mock.return_value = response
        client = WhatsAppCloudClient(phone_number_id='123456789')

        with self.assertRaises(WhatsAppAPIError):
            client.send_text('5511988887777', 'Olá!')

    @override_settings(META_ACCESS_TOKEN='')
    def test_send_text_rejects_missing_token(self):
        client = WhatsAppCloudClient(phone_number_id='123456789')

        with self.assertRaises(WhatsAppProviderError):
            client.send_text('5511988887777', 'Olá!')

    @patch('core.services.whatsapp.client.urlopen')
    def test_mark_as_read_uses_separate_request(self, urlopen_mock):
        urlopen_mock.return_value = FakeResponse({'success': True})
        client = WhatsAppCloudClient(phone_number_id='123456789')

        self.assertTrue(client.mark_as_read('wamid.inbound-1'))
        payload = json.loads(urlopen_mock.call_args.args[0].data)
        self.assertEqual(payload['status'], 'read')
        self.assertEqual(payload['message_id'], 'wamid.inbound-1')


@override_settings(
    META_ACCESS_TOKEN='token-de-teste', AI_ENABLED=True, OPENAI_API_KEY='test-only',
)
class OutboundServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='outbound', password='senha-segura')
        self.company = EmpresaCliente.objects.create(usuario=self.user, nome='Empresa Outbound')
        AIConfiguration.objects.create(empresa=self.company, enabled=True)
        AIPromptProfile.objects.create(
            empresa=self.company, generated_prompt='# Prompt ativo para testes',
            response_delay_seconds=0,
        )
        self.integration = WhatsAppIntegration.objects.create(
            company=self.company,
            phone_number_id='123456789',
            whatsapp_business_account_id='987654321',
        )
        WhatsAppSession.objects.create(
            empresa=self.company, instance_name='outbound-evolution', state='CONNECTED',
        )
        self.contact = Contato.objects.create(
            empresa=self.company,
            whatsapp_id='5511988887777',
            nome='Cliente',
        )
        self.attendance = Atendimento.objects.create(
            empresa=self.company,
            contato=self.contact,
            nome_cliente='Cliente',
            telefone_cliente='5511988887777',
            opcao_escolhida='WhatsApp',
            necessidade='Teste',
        )
        self.inbound = Mensagem.objects.create(
            empresa=self.company,
            atendimento=self.attendance,
            contato=self.contact,
            external_message_id='wamid.inbound-1',
            direcao=Mensagem.DIRECAO_ENTRADA,
            tipo='text',
            texto='Oi',
        )

    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    def test_success_persists_outbound_after_meta_accepts(self, send_mock):
        send_mock.return_value = SendTextResult('wamid.outbound-1')

        message = send_text_for_attendance(self.attendance, 'Resposta')

        self.assertEqual(message.direcao, Mensagem.DIRECAO_SAIDA)
        self.assertEqual(message.status, Mensagem.STATUS_ACEITA)
        self.assertEqual(message.external_message_id, 'wamid.outbound-1')

    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    def test_api_failure_does_not_create_outbound(self, send_mock):
        send_mock.side_effect = WhatsAppAPIError('Falha segura.')

        with self.assertRaises(WhatsAppAPIError):
            send_text_for_attendance(self.attendance, 'Resposta')

        self.assertFalse(Mensagem.objects.filter(direcao=Mensagem.DIRECAO_SAIDA).exists())

    def test_cross_tenant_contact_is_rejected(self):
        User = get_user_model()
        other_user = User.objects.create_user(username='other', password='senha-segura')
        other_company = EmpresaCliente.objects.create(usuario=other_user, nome='Outra empresa')
        other_contact = Contato.objects.create(
            empresa=other_company,
            whatsapp_id='5511977776666',
        )
        self.attendance.contato = other_contact

        with self.assertRaises(WhatsAppProviderError):
            send_text_for_attendance(self.attendance, 'Resposta')

    @patch('core.services.whatsapp.outbound.EvolutionProvider.mark_as_read')
    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    @patch('core.services.whatsapp.outbound.AIConversationService.reply', return_value=None)
    def test_new_inbound_sends_configured_flow_response(self, _reply_mock, send_mock, _read_mock):
        FluxoAtendimento.objects.create(
            empresa=self.company,
            **dados_padrao_fluxo(self.company),
        )
        send_mock.return_value = SendTextResult('wamid.outbound-1')

        outbound = send_automatic_reply(self.inbound)

        self.assertIsNotNone(outbound)
        sent_text = send_mock.call_args.args[2]
        self.assertIn(self.company.nome, sent_text)
        self.assertIn('1 -', sent_text)

    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    def test_disabled_automation_does_not_send(self, send_mock):
        self.attendance.automation_enabled = False
        self.attendance.save(update_fields=['automation_enabled'])
        self.inbound.refresh_from_db()

        self.assertIsNone(send_automatic_reply(self.inbound))
        send_mock.assert_not_called()

    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    def test_outbound_message_never_triggers_auto_reply(self, send_mock):
        outbound = Mensagem.objects.create(
            empresa=self.company,
            atendimento=self.attendance,
            contato=self.contact,
            external_message_id='wamid.outbound-existing',
            direcao=Mensagem.DIRECAO_SAIDA,
            tipo='text',
            texto='Resposta',
            status=Mensagem.STATUS_ACEITA,
        )

        self.assertIsNone(send_automatic_reply(outbound))
        send_mock.assert_not_called()

    @patch('core.services.whatsapp.outbound.EvolutionProvider.send_text')
    def test_non_text_inbound_receives_safe_fallback(self, send_mock):
        self.inbound.tipo = 'image'
        self.inbound.texto = ''
        send_mock.return_value = SendTextResult('wamid.media-fallback')

        self.assertIsNotNone(send_automatic_reply(self.inbound))
        send_mock.assert_called_once()

    def test_manual_command_requires_explicit_confirmation(self):
        with self.assertRaises(CommandError):
            call_command('whatsapp_send_test', atendimento=self.attendance.pk)

    @patch('core.management.commands.whatsapp_send_test.send_text_for_attendance')
    def test_manual_command_uses_existing_attendance_contact(self, send_mock):
        send_mock.return_value = SimpleNamespace(external_message_id='wamid.manual-1')
        output = StringIO()

        call_command(
            'whatsapp_send_test',
            atendimento=self.attendance.pk,
            confirm=True,
            stdout=output,
        )

        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0].pk, self.attendance.pk)
        self.assertIn('wamid.manual-1', output.getvalue())
