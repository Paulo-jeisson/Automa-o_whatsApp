from datetime import time, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import (
    AIConfiguration,
    Agendamento,
    Atendimento,
    Contato,
    DisponibilidadeSemanal,
    EmpresaCliente,
    Mensagem,
    Servico,
)
from core.services.ai import AIAgent, AIConfigurationError, AIToolValidationError
from core.services.ai.memory import ConversationMemoryService
from core.services.ai.tools import AIToolExecutor, tool_definitions


class AIToolExecutorTests(TestCase):
    def setUp(self):
        user_a = get_user_model().objects.create_user('tools-a')
        self.company_a = EmpresaCliente.objects.create(
            usuario=user_a,
            nome='Clínica A',
            endereco='Rua A',
            horario_funcionamento='08h às 18h',
        )
        user_b = get_user_model().objects.create_user('tools-b')
        self.company_b = EmpresaCliente.objects.create(usuario=user_b, nome='Clínica B')
        self.contact_a = Contato.objects.create(
            empresa=self.company_a, whatsapp_id='55110001', nome='Ana',
        )
        self.contact_b = Contato.objects.create(
            empresa=self.company_b, whatsapp_id='55110002', nome='Bia',
        )
        self.attendance_a = Atendimento.objects.create(
            empresa=self.company_a, contato=self.contact_a,
            nome_cliente='Ana', telefone_cliente='55110001',
            opcao_escolhida='', necessidade='',
        )
        self.service_a = Servico.objects.create(
            empresa=self.company_a, nome='Consulta A', duracao_minutos=30,
        )
        self.service_b = Servico.objects.create(
            empresa=self.company_b, nome='Consulta secreta B', duracao_minutos=30,
        )
        AIConfiguration.objects.create(
            empresa=self.company_a,
            business_description='Atendimento clínico.',
            additional_information='Aceita cartão.',
        )
        self.future = timezone.localdate() + timedelta(days=7)
        DisponibilidadeSemanal.objects.create(
            empresa=self.company_a,
            dia_semana=self.future.weekday(),
            hora_inicio=time(8),
            hora_fim=time(10),
            intervalo_minutos=30,
        )
        self.executor = AIToolExecutor(atendimento=self.attendance_a)

    def test_definitions_expose_only_allowlisted_operations(self):
        names = {item['name'] for item in tool_definitions()}
        self.assertEqual(names, AIToolExecutor.OPERATIONS)
        self.assertNotIn('empresa_id', str(tool_definitions()))

    def test_services_and_company_information_are_tenant_scoped(self):
        services = self.executor.execute('listar_servicos')
        company = self.executor.execute('obter_informacoes_empresa')

        self.assertEqual([item['nome'] for item in services['servicos']], ['Consulta A'])
        self.assertNotIn('Consulta secreta B', str(services))
        self.assertEqual(company['nome'], 'Clínica A')
        self.assertEqual(company['endereco'], 'Rua A')

    def test_model_cannot_choose_company_or_service_from_another_tenant(self):
        with self.assertRaises(AIToolValidationError):
            self.executor.execute('listar_servicos', {'empresa_id': self.company_b.pk})
        with self.assertRaises(AIToolValidationError):
            self.executor.execute('consultar_disponibilidade', {
                'servico_id': self.service_b.pk,
                'data': self.future.isoformat(),
                'profissional': None,
            })

    def test_availability_create_query_and_cancel_validate_backend_again(self):
        available = self.executor.execute('consultar_disponibilidade', {
            'servico_id': self.service_a.pk,
            'data': self.future.isoformat(),
            'profissional': None,
        })
        self.assertIn('08:00', available['horarios'])
        with self.assertRaises(AIToolValidationError):
            self.executor.execute('criar_agendamento', {
                'servico_id': self.service_a.pk,
                'data': self.future.isoformat(),
                'hora': '08:00',
                'confirmado_pelo_cliente': False,
                'observacao': '',
            })

        created = self.executor.execute('criar_agendamento', {
            'servico_id': self.service_a.pk,
            'data': self.future.isoformat(),
            'hora': '08:00',
            'confirmado_pelo_cliente': True,
            'observacao': 'Confirmado no atendimento.',
        })
        appointment = Agendamento.objects.get(pk=created['id'])
        self.assertEqual(appointment.empresa, self.company_a)
        self.assertEqual(appointment.contato, self.contact_a)
        self.assertEqual(appointment.atendimento, self.attendance_a)
        self.assertEqual(
            self.executor.execute('consultar_agendamento')['agendamentos'][0]['id'],
            appointment.pk,
        )

        cancelled = self.executor.execute('cancelar_agendamento', {
            'agendamento_id': appointment.pk,
            'confirmado_pelo_cliente': True,
        })
        self.assertEqual(cancelled['status'], Agendamento.Status.CANCELLED)

    def test_contact_cannot_cancel_other_company_appointment(self):
        attendance_b = Atendimento.objects.create(
            empresa=self.company_b, contato=self.contact_b,
            nome_cliente='Bia', telefone_cliente='55110002',
            opcao_escolhida='', necessidade='',
        )
        foreign = Agendamento.objects.create(
            empresa=self.company_b, contato=self.contact_b,
            atendimento=attendance_b, servico=self.service_b,
            data=self.future, hora_inicio=time(12), hora_fim=time(12, 30),
            status=Agendamento.Status.CONFIRMED,
        )
        with self.assertRaises(AIToolValidationError):
            self.executor.execute('cancelar_agendamento', {
                'agendamento_id': foreign.pk,
                'confirmado_pelo_cliente': True,
            })
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, Agendamento.Status.CONFIRMED)

    def test_handoff_disables_automation_and_persists_reason(self):
        result = self.executor.execute('solicitar_atendente', {'motivo': 'Cliente pediu'})
        self.attendance_a.refresh_from_db()
        self.assertEqual(result['status'], Atendimento.Step.WAITING_HUMAN)
        self.assertFalse(self.attendance_a.automation_enabled)
        self.assertEqual(
            self.attendance_a.conversation_state['handoff_reason'],
            'Cliente pediu',
        )


class ConversationMemoryTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('memory-a')
        self.company = EmpresaCliente.objects.create(usuario=user, nome='Empresa Memória')
        self.contact = Contato.objects.create(
            empresa=self.company, whatsapp_id='55119999', nome='Carla',
        )
        self.attendance = Atendimento.objects.create(
            empresa=self.company, contato=self.contact,
            nome_cliente='Carla', telefone_cliente='55119999',
            opcao_escolhida='', necessidade='',
        )
        self.configuration = AIConfiguration.objects.create(
            empresa=self.company, enabled=True, assistant_name='Lia',
        )

    def _message(self, index):
        return Mensagem.objects.create(
            empresa=self.company,
            atendimento=self.attendance,
            contato=self.contact,
            external_message_id=f'memory-{index}',
            direcao=(
                Mensagem.DIRECAO_ENTRADA if index % 2
                else Mensagem.DIRECAO_SAIDA
            ),
            tipo='text',
            texto=f'mensagem {index}',
        )

    def test_long_history_is_summarized_and_recent_window_is_bounded(self):
        for index in range(1, 7):
            self._message(index)
        service = ConversationMemoryService(
            message_limit=2, summary_trigger=4, summary_max_chars=500,
        )

        memory = service.build(atendimento=self.attendance)

        self.assertEqual(len(memory.recent_messages), 2)
        self.assertEqual(
            [item.text for item in memory.recent_messages],
            ['mensagem 5', 'mensagem 6'],
        )
        self.assertIn('mensagem 1', memory.summary)
        self.assertIn('mensagem 4', memory.summary)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.summarized_message_count, 4)

    def test_structured_state_accepts_only_known_scalar_fields(self):
        service = ConversationMemoryService()
        state = service.update_state(atendimento=self.attendance, values={
            'intent': 'agendamento',
            'date': 'amanhã',
            'period': 'manhã',
        })
        self.assertEqual(state['intent'], 'agendamento')
        with self.assertRaises(AIConfigurationError):
            service.update_state(
                atendimento=self.attendance,
                values={'sql': 'SELECT *'},
            )

    def test_agent_receives_summary_state_and_only_recent_messages(self):
        self.attendance.conversation_state = {
            'intent': 'agendamento', 'period': 'manhã',
        }
        self.attendance.conversation_summary = 'Cliente procura consulta.'
        self.attendance.save(update_fields=['conversation_state', 'conversation_summary'])
        self._message(1)
        provider = Mock()
        provider.generate.return_value = SimpleNamespace(text='Certo', response_id='r1')

        AIAgent(client=provider).respond(
            configuration=self.configuration,
            atendimento=self.attendance,
            user_input='Pode continuar',
        )

        instructions = provider.generate.call_args.kwargs['instructions']
        self.assertIn('Empresa Memória', instructions)
        self.assertIn('Carla', instructions)
        self.assertIn('"intent": "agendamento"', instructions)
        self.assertIn('Cliente procura consulta.', instructions)
        self.assertIn('mensagem 1', instructions)

    def test_context_rejects_mismatched_company(self):
        other_user = get_user_model().objects.create_user('memory-b')
        other_company = EmpresaCliente.objects.create(usuario=other_user, nome='Outra')
        other_configuration = AIConfiguration.objects.create(
            empresa=other_company, enabled=True,
        )
        provider = Mock()
        with self.assertRaises(ValueError):
            AIAgent(client=provider).respond(
                configuration=other_configuration,
                atendimento=self.attendance,
                user_input='Olá',
            )
        provider.generate.assert_not_called()


class OpenAIToolLoopTests(TestCase):
    def test_client_executes_structured_call_and_returns_final_answer(self):
        from core.services.ai.client import OpenAIClient

        sdk = Mock()
        sdk.responses.create.side_effect = [
            SimpleNamespace(
                id='response-tools',
                output_text='',
                output=[SimpleNamespace(
                    type='function_call',
                    call_id='call-1',
                    name='listar_servicos',
                    arguments='{}',
                )],
            ),
            SimpleNamespace(
                id='response-final',
                output_text='Temos consulta.',
                output=[],
            ),
        ]
        executor = Mock(return_value={'servicos': [{'nome': 'Consulta'}]})

        from django.test import override_settings
        with override_settings(
            AI_ENABLED=True, OPENAI_API_KEY='test-only', AI_MODEL='gpt-test',
        ):
            result = OpenAIClient(sdk_client=sdk).generate(
                instructions='Instruções',
                user_input='Quais serviços?',
                tools=[{'type': 'function', 'name': 'listar_servicos'}],
                tool_executor=executor,
            )

        self.assertEqual(result.text, 'Temos consulta.')
        executor.assert_called_once_with('listar_servicos', {})
        followup = sdk.responses.create.call_args_list[1].kwargs
        self.assertEqual(followup['previous_response_id'], 'response-tools')
        self.assertEqual(followup['input'][0]['call_id'], 'call-1')
        self.assertIn('Consulta', followup['input'][0]['output'])
