from unittest.mock import Mock, call, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.application.whatsapp_service import (
    WhatsAppOperationInProgress, WhatsAppSessionService, _company_operation_lock,
)
from django.db import OperationalError
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
        self.service._wait_until_deleted = Mock()
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

    def test_create_403_is_never_treated_as_existing(self):
        self.provider.create.side_effect = EvolutionRequestError('forbidden', status_code=403)
        result = self.service.clear(self.company)
        self.assertEqual(result.state, SessionState.ERROR)
        self.assertEqual(result.qr_code, '')
        self.provider.status.assert_called_once_with(self.session.instance_name)
        self.provider.configure_webhook.assert_not_called()

    def test_create_conflict_continues_only_after_remote_existence_is_confirmed(self):
        self.provider.create.side_effect = EvolutionRequestError(
            'conflict', status_code=409, provider_message='instance already exists',
        )
        self.provider.status.side_effect = [
            SessionSnapshot(state=SessionState.OFFLINE),
            SessionSnapshot(state=SessionState.WAITING_QR, qr_code='data:image/png;base64,CONFIRMADO'),
        ]
        result = self.service.clear(self.company)
        self.assertEqual(result.state, SessionState.WAITING_QR)
        self.assertIn('CONFIRMADO', result.qr_code)
        self.provider.configure_webhook.assert_called_once_with(self.session.instance_name)

    def test_reconnect_recreates_only_when_remote_absence_is_proven(self):
        self.provider.status.side_effect = EvolutionRequestError('missing', status_code=404)
        result = self.service.reconnect(self.company)
        self.assertEqual(result.state, SessionState.WAITING_QR)
        self.provider.create.assert_called_once_with(self.session.instance_name)
        self.provider.configure_webhook.assert_called_once_with(self.session.instance_name)

    def test_reconnect_403_does_not_recreate(self):
        self.provider.status.side_effect = EvolutionRequestError('forbidden', status_code=403)
        result = self.service.reconnect(self.company)
        self.assertEqual(result.state, SessionState.ERROR)
        self.provider.create.assert_not_called()

    def test_delete_is_polled_until_remote_404_before_create(self):
        service = WhatsAppSessionService(provider=self.provider)
        self.provider.status.side_effect = [
            SessionSnapshot(state=SessionState.OFFLINE),
            SessionSnapshot(state=SessionState.OFFLINE),
            EvolutionRequestError('missing', status_code=404),
        ]
        with patch('core.application.whatsapp_service.time.sleep'):
            service.clear(self.company)
        calls = self.provider.method_calls
        create_index = calls.index(call.create(self.session.instance_name))
        self.assertEqual(calls[:create_index].count(call.status(self.session.instance_name)), 3)

    def test_delete_poll_is_bounded(self):
        service = WhatsAppSessionService(provider=self.provider)
        service.deletion_poll_delays = (0, 0)
        self.provider.status.return_value = SessionSnapshot(state=SessionState.OFFLINE)
        result = service.clear(self.company)
        self.assertEqual(result.state, SessionState.ERROR)
        self.provider.create.assert_not_called()

    def test_create_403_name_in_use_reconciles_connected_instance(self):
        self.provider.create.side_effect = EvolutionRequestError(
            'forbidden', status_code=403, provider_message='This name is already in use',
        )
        self.provider.status.return_value = SessionSnapshot(state=SessionState.CONNECTED)
        result = self.service.clear(self.company)
        self.assertEqual(result.state, SessionState.CONNECTED)
        self.assertEqual(result.qr_code, '')

    def test_sqlite_database_lock_is_retried_but_other_errors_are_not_hidden(self):
        session = self.service.ensure(self.company)
        session.save = Mock(side_effect=[OperationalError('database is locked'), None])
        with patch('core.application.whatsapp_service.time.sleep') as sleep_mock:
            self.service._persist(session)
        self.assertEqual(session.save.call_count, 2)
        sleep_mock.assert_called_once()

        session.save = Mock(side_effect=OperationalError('disk I/O error'))
        with self.assertRaises(OperationalError):
            self.service._persist(session)
        self.assertEqual(session.save.call_count, 1)

    def test_operation_lock_is_scoped_per_company(self):
        with _company_operation_lock(100):
            with self.assertRaises(WhatsAppOperationInProgress):
                with _company_operation_lock(100):
                    pass
            with _company_operation_lock(200):
                pass
