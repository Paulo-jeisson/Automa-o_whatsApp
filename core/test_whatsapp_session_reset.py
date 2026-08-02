from unittest.mock import Mock, call

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.application.whatsapp_service import WhatsAppSessionService
from core.domain.exceptions import ProviderUnavailable
from core.domain.whatsapp import SessionSnapshot, SessionState
from core.infrastructure.evolution import EvolutionRequestError
from core.models import EmpresaCliente


class WhatsAppSessionResetTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('reset-owner', password='safe-password')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Empresa Reset')
        self.provider = Mock()
        self.provider.status.return_value = SessionSnapshot(state=SessionState.OFFLINE)
        self.provider.create.return_value = SessionSnapshot(state=SessionState.INITIALIZING)
        self.provider.qr_code.return_value = SessionSnapshot(
            state=SessionState.WAITING_QR,
            qr_code='data:image/png;base64,NOVO',
            ping_ms=7,
        )
        self.service = WhatsAppSessionService(provider=self.provider)
        self.session = self.service.ensure(self.company)
        self.session.state = SessionState.OFFLINE
        self.session.qr_code = 'data:image/png;base64,EXPIRADO'
        self.session.phone_number = '5511999999999'
        self.session.device_name = 'Aparelho antigo'
        self.session.reconnect_attempts = 4
        self.session.save()

    def test_close_session_is_deleted_recreated_and_receives_fresh_qr(self):
        original_name = self.session.instance_name

        result = self.service.clear(self.company)

        self.assertEqual(result.instance_name, original_name)
        self.assertEqual(result.state, SessionState.WAITING_QR)
        self.assertEqual(result.qr_code, 'data:image/png;base64,NOVO')
        self.assertNotIn('EXPIRADO', result.qr_code)
        self.assertEqual(result.phone_number, '')
        self.assertEqual(result.device_name, '')
        self.assertEqual(result.reconnect_attempts, 0)
        self.provider.status.assert_called_once_with(original_name)
        self.provider.logout.assert_called_once_with(original_name)
        self.provider.delete.assert_called_once_with(original_name)
        self.provider.create.assert_called_once_with(original_name)
        self.provider.configure_webhook.assert_called_once_with(original_name)
        self.provider.qr_code.assert_called_once_with(original_name)
        self.assertTrue(result.events.filter(kind='SESSION_CLEARED').exists())
        self.assertTrue(result.events.filter(kind='QR_GENERATED').exists())

    def test_successful_logout_keeps_complete_reset_flow(self):
        self.service.clear(self.company)
        self.assertEqual(
            self.provider.method_calls,
            [
                call.status(self.session.instance_name),
                call.logout(self.session.instance_name),
                call.delete(self.session.instance_name),
                call.create(self.session.instance_name),
                call.configure_webhook(self.session.instance_name),
                call.qr_code(self.session.instance_name),
            ],
        )

    def test_logout_401_404_and_409_are_idempotent(self):
        for status_code in (401, 404, 409):
            with self.subTest(status_code=status_code):
                self.provider.reset_mock()
                self.provider.status.return_value = SessionSnapshot(state=SessionState.OFFLINE)
                self.provider.create.return_value = SessionSnapshot(state=SessionState.INITIALIZING)
                self.provider.qr_code.return_value = SessionSnapshot(
                    state=SessionState.WAITING_QR, qr_code=f'data:image/png;base64,QR{status_code}',
                )
                self.provider.logout.side_effect = EvolutionRequestError(
                    'logout indisponível', status_code=status_code,
                )

                result = self.service.clear(self.company)

                self.assertEqual(result.state, SessionState.WAITING_QR)
                self.assertEqual(result.qr_code, f'data:image/png;base64,QR{status_code}')
                self.provider.delete.assert_called_once()
                self.provider.create.assert_called_once()
                self.provider.qr_code.assert_called_once()

    def test_delete_not_found_is_success_and_recreates_instance(self):
        self.provider.delete.side_effect = EvolutionRequestError('não encontrada', status_code=404)

        result = self.service.clear(self.company)

        self.assertEqual(result.state, SessionState.WAITING_QR)
        self.provider.create.assert_called_once_with(self.session.instance_name)

    def test_logout_timeout_is_not_fatal(self):
        self.provider.logout.side_effect = ProviderUnavailable('timeout')

        result = self.service.clear(self.company)

        self.assertEqual(result.qr_code, 'data:image/png;base64,NOVO')
        self.provider.delete.assert_called_once()

    def test_invalid_or_empty_new_qr_never_restores_expired_qr(self):
        self.provider.qr_code.return_value = SessionSnapshot(state=SessionState.WAITING_QR)

        result = self.service.clear(self.company)

        self.assertEqual(result.state, SessionState.ERROR)
        self.assertEqual(result.qr_code, '')
        self.assertNotIn('EXPIRADO', result.qr_code)
