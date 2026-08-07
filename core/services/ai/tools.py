"""Tools de negócio vinculadas a um atendimento autenticado."""

from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (
    Agendamento, Atendimento, Servico, KnowledgeBaseArticle,
    BusinessDataRecord,
)
from core.services.business_data import search_business_data
from core.services.scheduling import SchedulingService, SlotUnavailable

from .exceptions import AIToolError, AIToolValidationError


TOOL_DEFINITIONS = [
    {
        'type': 'function', 'name': 'listar_servicos',
        'description': 'Lista os serviços ativos da empresa deste atendimento.',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'strict': True,
    },
    {
        'type': 'function', 'name': 'obter_informacoes_empresa',
        'description': 'Obtém informações públicas da empresa deste atendimento.',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'strict': True,
    },
    {
        'type': 'function', 'name': 'consultar_disponibilidade',
        'description': 'Consulta horários reais para serviço e data.',
        'parameters': {
            'type': 'object',
            'properties': {
                'servico_id': {'type': 'integer'},
                'data': {'type': 'string', 'description': 'YYYY-MM-DD'},
                'profissional': {'type': ['string', 'null']},
            },
            'required': ['servico_id', 'data', 'profissional'],
            'additionalProperties': False,
        },
        'strict': True,
    },
    {
        'type': 'function', 'name': 'criar_agendamento',
        'description': 'Cria agendamento após confirmação explícita do cliente.',
        'parameters': {
            'type': 'object',
            'properties': {
                'servico_id': {'type': 'integer'},
                'data': {'type': 'string'},
                'hora': {'type': 'string', 'description': 'HH:MM'},
                'confirmado_pelo_cliente': {'type': 'boolean'},
                'observacao': {'type': 'string'},
            },
            'required': ['servico_id', 'data', 'hora', 'confirmado_pelo_cliente', 'observacao'],
            'additionalProperties': False,
        },
        'strict': True,
    },
    {
        'type': 'function', 'name': 'consultar_agendamento',
        'description': 'Lista agendamentos do contato deste atendimento.',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'strict': True,
    },
    {
        'type': 'function', 'name': 'cancelar_agendamento',
        'description': 'Cancela agendamento ativo do contato deste atendimento.',
        'parameters': {
            'type': 'object',
            'properties': {
                'agendamento_id': {'type': 'integer'},
                'confirmado_pelo_cliente': {'type': 'boolean'},
            },
            'required': ['agendamento_id', 'confirmado_pelo_cliente'],
            'additionalProperties': False,
        },
        'strict': True,
    },
    {
        'type': 'function', 'name': 'confirmar_agendamento',
        'description': 'Confirma um agendamento pendente do cliente.',
        'parameters': {
            'type': 'object',
            'properties': {'agendamento_id': {'type': 'integer'}, 'confirmado_pelo_cliente': {'type': 'boolean'}},
            'required': ['agendamento_id', 'confirmado_pelo_cliente'], 'additionalProperties': False,
        },
        'strict': True,
    },
    {
        'type': 'function', 'name': 'reagendar_agendamento',
        'description': 'Reagenda após confirmação, validando novamente a disponibilidade.',
        'parameters': {
            'type': 'object',
            'properties': {
                'agendamento_id': {'type': 'integer'}, 'data': {'type': 'string'},
                'hora': {'type': 'string'}, 'confirmado_pelo_cliente': {'type': 'boolean'},
            },
            'required': ['agendamento_id', 'data', 'hora', 'confirmado_pelo_cliente'],
            'additionalProperties': False,
        },
        'strict': True,
    },
    {
        'type': 'function', 'name': 'pesquisar_base_conhecimento',
        'description': 'Pesquisa conteúdo administrado pela empresa antes de responder dúvidas específicas.',
        'parameters': {
            'type': 'object', 'properties': {'consulta': {'type': 'string'}},
            'required': ['consulta'], 'additionalProperties': False,
        },
        'strict': True,
    },
    {
        'type': 'function', 'name': 'pesquisar_dados_negocio',
        'description': 'Use sempre para consultar fatos importados pela empresa antes de responder sobre cardápio, produtos, preços, estoque, serviços, clientes, veículos, relatórios ou outros dados do negócio. Envie a pergunta original completa do cliente. Cada resultado retorna todas e somente as colunas autorizadas pela empresa; preserve a relação entre os campos da mesma linha.',
        'parameters': {
            'type': 'object', 'properties': {'consulta': {'type': 'string'}},
            'required': ['consulta'], 'additionalProperties': False,
        },
        'strict': True,
    },
    {
        'type': 'function', 'name': 'solicitar_atendente',
        'description': 'Desativa a automação e coloca o atendimento na fila humana.',
        'parameters': {
            'type': 'object',
            'properties': {'motivo': {'type': 'string'}},
            'required': ['motivo'],
            'additionalProperties': False,
        },
        'strict': True,
    },
    {
        'type': 'function', 'name': 'salvar_contexto_conversa',
        'description': 'Salva fatos confirmados para as próximas mensagens.',
        'parameters': {
            'type': 'object',
            'properties': {
                'intent': {'type': ['string', 'null']},
                'service_id': {'type': ['integer', 'null']},
                'service_name': {'type': ['string', 'null']},
                'date': {'type': ['string', 'null']},
                'period': {'type': ['string', 'null']},
                'time': {'type': ['string', 'null']},
                'appointment_id': {'type': ['integer', 'null']},
                'awaiting_confirmation': {'type': ['boolean', 'null']},
            },
            'required': [
                'intent', 'service_id', 'service_name', 'date', 'period',
                'time', 'appointment_id', 'awaiting_confirmation',
            ],
            'additionalProperties': False,
        },
        'strict': True,
    },
]


def tool_definitions():
    return list(TOOL_DEFINITIONS)


class AIToolExecutor:
    """Fachada permitida à IA; a empresa nunca é um argumento."""

    OPERATIONS = {item['name'] for item in TOOL_DEFINITIONS}

    def __init__(self, *, atendimento):
        if not atendimento or not atendimento.pk or not atendimento.empresa_id:
            raise AIToolValidationError('Atendimento autenticado é obrigatório.')
        self.atendimento = atendimento
        self.empresa = atendimento.empresa

    def execute(self, name, arguments=None):
        if name not in self.OPERATIONS:
            raise AIToolValidationError('Tool não permitida.')
        arguments = arguments or {}
        if 'empresa_id' in arguments:
            raise AIToolValidationError('A empresa não pode ser escolhida pela IA.')
        try:
            return getattr(self, name)(**arguments)
        except TypeError as error:
            raise AIToolValidationError('Argumentos inválidos para a tool.') from error
        except (ValidationError, ValueError) as error:
            message = getattr(error, 'message', None) or str(error)
            raise AIToolValidationError(message) from error

    def listar_servicos(self):
        return {'servicos': [
            {
                'id': item.pk, 'nome': item.nome, 'descricao': item.descricao,
                'duracao_minutos': item.duracao_minutos,
            }
            for item in Servico.objects.filter(empresa=self.empresa, ativo=True)
        ]}

    def obter_informacoes_empresa(self):
        configuration = getattr(self.empresa, 'ai_configuration', None)
        return {
            'nome': self.empresa.nome,
            'endereco': self.empresa.endereco,
            'horario_funcionamento': self.empresa.horario_funcionamento,
            'descricao_publica': configuration.business_description if configuration else '',
            'informacoes_publicas': configuration.additional_information if configuration else '',
        }

    def consultar_disponibilidade(self, *, servico_id, data, profissional=None):
        if profissional:
            raise AIToolValidationError('A agenda atual não possui profissionais cadastrados.')
        service = self._service(servico_id)
        selected_date = self._date(data)
        slots = SchedulingService.get_available_slots(self.empresa, service, selected_date)
        return {
            'servico_id': service.pk,
            'data': selected_date.isoformat(),
            'horarios': [slot.strftime('%H:%M') for slot in slots],
        }

    def criar_agendamento(
        self, *, servico_id, data, hora, confirmado_pelo_cliente, observacao='',
    ):
        if confirmado_pelo_cliente is not True:
            raise AIToolValidationError('É necessária confirmação explícita do cliente.')
        if not self.atendimento.contato_id:
            raise AIToolValidationError('O atendimento não possui contato vinculado.')
        try:
            appointment = SchedulingService.create_appointment(
                empresa=self.empresa,
                contato=self.atendimento.contato,
                atendimento=self.atendimento,
                servico=self._service(servico_id),
                date=self._date(data),
                start_time=self._time(hora),
                origem=Agendamento.Origem.WHATSAPP,
                observacao=str(observacao or '')[:1000],
            )
        except SlotUnavailable as error:
            raise AIToolError('O horário não está mais disponível.') from error
        return self._appointment_payload(appointment)

    def consultar_agendamento(self):
        if not self.atendimento.contato_id:
            return {'agendamentos': []}
        appointments = Agendamento.objects.filter(
            empresa=self.empresa, contato=self.atendimento.contato,
        ).select_related('servico')[:20]
        return {'agendamentos': [self._appointment_payload(item) for item in appointments]}

    def cancelar_agendamento(self, *, agendamento_id, confirmado_pelo_cliente):
        if confirmado_pelo_cliente is not True:
            raise AIToolValidationError('É necessária confirmação explícita do cliente.')
        if not self.atendimento.contato_id:
            raise AIToolValidationError('O atendimento não possui contato vinculado.')
        with transaction.atomic():
            appointment = Agendamento.objects.select_for_update().filter(
                pk=agendamento_id,
                empresa=self.empresa,
                contato=self.atendimento.contato,
                status__in=SchedulingService.ACTIVE_STATUSES,
            ).select_related('servico').first()
            if not appointment:
                raise AIToolValidationError('Agendamento ativo não encontrado para este cliente.')
            if appointment.data < timezone.localdate():
                raise AIToolValidationError('Agendamentos passados não podem ser cancelados.')
            SchedulingService.cancel_appointment(appointment)
        return self._appointment_payload(appointment)

    def confirmar_agendamento(self, *, agendamento_id, confirmado_pelo_cliente):
        if confirmado_pelo_cliente is not True:
            raise AIToolValidationError('É necessária confirmação explícita do cliente.')
        with transaction.atomic():
            appointment = self._customer_appointment(agendamento_id, lock=True)
            if appointment.status != Agendamento.Status.PENDING:
                raise AIToolValidationError('O agendamento não está pendente.')
            appointment.status = Agendamento.Status.CONFIRMED
            appointment.save(update_fields=['status', 'updated_at'])
        return self._appointment_payload(appointment)

    def reagendar_agendamento(self, *, agendamento_id, data, hora, confirmado_pelo_cliente):
        if confirmado_pelo_cliente is not True:
            raise AIToolValidationError('É necessária confirmação explícita do cliente.')
        with transaction.atomic():
            appointment = self._customer_appointment(agendamento_id, lock=True)
            if appointment.status not in SchedulingService.ACTIVE_STATUSES:
                raise AIToolValidationError('Agendamento ativo não encontrado.')
            replacement = SchedulingService.reschedule_appointment(
                appointment, self._date(data), self._time(hora),
            )
        return {'anterior': self._appointment_payload(appointment), 'novo': self._appointment_payload(replacement)}

    def pesquisar_base_conhecimento(self, *, consulta):
        from django.db.models import Q
        terms = [term for term in str(consulta).strip().split() if len(term) >= 3][:8]
        if not terms:
            raise AIToolValidationError('Informe uma consulta mais específica.')
        combined = Q()
        for term in terms:
            combined |= Q(title__icontains=term) | Q(content__icontains=term) | Q(keywords__icontains=term)
        articles = KnowledgeBaseArticle.objects.filter(
            combined, empresa=self.empresa, is_active=True,
        )[:5]
        return {'resultados': [
            {'id': item.pk, 'titulo': item.title, 'categoria': item.category, 'conteudo': item.content[:2000]}
            for item in articles
        ]}

    def pesquisar_dados_negocio(self, *, consulta):
        if not str(consulta or '').strip():
            raise AIToolValidationError('Informe uma consulta mais específica.')
        records, _ = search_business_data(
            empresa=self.empresa, query=consulta, limit=50,
        )
        return {'resultados': [
            {
                'base': item.source.name,
                'arquivo': item.source.source_filename,
                'tipo': item.source.data_type,
                # A consulta seleciona os registros, mas a whitelist configurada
                # pelo usuário determina integralmente o que pode chegar à IA.
                'dados': item.visible_data,
            }
            for item in records
        ], 'orientacao': (
            'Liste ou resuma fielmente os valores retornados. '
            'Se vazio, informe que não encontrou nos dados ativos desta empresa.'
        )}

    def _customer_appointment(self, appointment_id, *, lock=False):
        if not self.atendimento.contato_id:
            raise AIToolValidationError('O atendimento não possui contato vinculado.')
        query = Agendamento.objects.filter(
            pk=appointment_id, empresa=self.empresa, contato=self.atendimento.contato,
        ).select_related('servico')
        if lock:
            query = query.select_for_update()
        appointment = query.first()
        if not appointment:
            raise AIToolValidationError('Agendamento não encontrado para este cliente.')
        if appointment.data < timezone.localdate():
            raise AIToolValidationError('Agendamentos passados não podem ser alterados.')
        return appointment

    def solicitar_atendente(self, *, motivo):
        reason = str(motivo or '').strip()[:500]
        state = dict(self.atendimento.conversation_state or {})
        state['handoff_reason'] = reason
        normalized_reason = reason.casefold()
        state['handoff_type'] = (
            'HANDOFF_REQUESTED_BY_CUSTOMER'
            if 'cliente' in normalized_reason and 'solicit' in normalized_reason
            else 'HANDOFF_BUSINESS_RULE'
        )
        self.atendimento.current_step = Atendimento.Step.WAITING_HUMAN
        self.atendimento.automation_enabled = False
        self.atendimento.status = Atendimento.STATUS_EM_ANDAMENTO
        self.atendimento.conversation_state = state
        self.atendimento.handoff_reason = reason
        self.atendimento.save(update_fields=[
            'current_step', 'automation_enabled', 'status', 'conversation_state',
            'handoff_reason',
        ])
        return {'status': Atendimento.Step.WAITING_HUMAN, 'motivo': reason}

    def salvar_contexto_conversa(self, **values):
        from .memory import ConversationMemoryService
        state = ConversationMemoryService().update_state(
            atendimento=self.atendimento,
            values=values,
        )
        return {'salvo': True, 'estado': state}

    def _service(self, service_id):
        service = Servico.objects.filter(
            pk=service_id, empresa=self.empresa, ativo=True,
        ).first()
        if not service:
            raise AIToolValidationError('Serviço ativo não encontrado.')
        return service

    @staticmethod
    def _date(value):
        selected = date.fromisoformat(str(value))
        if selected < timezone.localdate():
            raise AIToolValidationError('A data não pode estar no passado.')
        return selected

    @staticmethod
    def _time(value):
        return datetime.strptime(str(value), '%H:%M').time()

    @staticmethod
    def _appointment_payload(appointment):
        return {
            'id': appointment.pk,
            'servico': appointment.servico.nome,
            'data': appointment.data.isoformat(),
            'hora': appointment.hora_inicio.strftime('%H:%M'),
            'status': appointment.status,
        }
