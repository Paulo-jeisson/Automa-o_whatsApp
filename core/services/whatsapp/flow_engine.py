import re
from datetime import date, datetime
from unicodedata import normalize

from django.utils import timezone

from core.models import Agendamento, Atendimento, FluxoAtendimento, Servico
from core.services.scheduling import SchedulingService, SlotUnavailable


class FlowEngine:
    ACTION_SCHEDULE = 'AGENDAR'
    ACTION_LOOKUP = 'CONSULTAR_AGENDAMENTO'
    ACTION_HUMAN = 'FALAR_COM_ATENDENTE'

    @classmethod
    def process(cls, atendimento, mensagem):
        if not atendimento.automation_enabled or mensagem.tipo != 'text' or not mensagem.texto:
            return None
        fluxo = FluxoAtendimento.objects.filter(empresa=atendimento.empresa).first()
        if fluxo is None:
            return None
        text = mensagem.texto.strip()
        handlers = {
            Atendimento.Step.MENU: cls._menu,
            Atendimento.Step.SERVICE: cls._service,
            Atendimento.Step.DATE: cls._date,
            Atendimento.Step.TIME: cls._time,
            Atendimento.Step.CONFIRMATION: cls._confirmation,
        }
        handler = handlers.get(atendimento.current_step)
        if handler is None:
            return None
        return handler(atendimento, fluxo, text)

    @classmethod
    def _options(cls, fluxo):
        result = []
        for item in fluxo.opcoes:
            if isinstance(item, dict):
                result.append({'label': item.get('label', ''), 'action': item.get('action', '')})
            else:
                label = str(item)
                normalized = cls._normalize(label)
                action = ''
                if 'agend' in normalized and not any(word in normalized for word in ('consult', 'confirm')):
                    action = cls.ACTION_SCHEDULE
                elif 'consult' in normalized or 'confirm' in normalized:
                    action = cls.ACTION_LOOKUP
                elif any(word in normalized for word in ('atendente', 'recepc', 'vendedor', 'tecnico', 'contador', 'escritorio')):
                    action = cls.ACTION_HUMAN
                result.append({'label': label, 'action': action})
        return result

    @staticmethod
    def _normalize(value):
        return normalize('NFKD', value).encode('ascii', 'ignore').decode().lower()

    @classmethod
    def _menu_text(cls, fluxo, invalid=False):
        options = cls._options(fluxo)
        menu = '\n'.join(f'{index} - {item["label"]}' for index, item in enumerate(options, 1))
        prefix = 'Não consegui identificar essa opção.\n\n' if invalid else ''
        return f'{prefix}{fluxo.saudacao}\n\n{fluxo.pergunta_menu}\n\n{menu}'

    @classmethod
    def _menu(cls, atendimento, fluxo, text):
        if not text.isdigit():
            return cls._menu_text(fluxo)
        options = cls._options(fluxo)
        index = int(text) - 1
        if index < 0 or index >= len(options):
            return cls._menu_text(fluxo, True)
        action = options[index]['action']
        if action == cls.ACTION_SCHEDULE:
            atendimento.current_step = Atendimento.Step.SERVICE
            atendimento.flow_context = {}
            atendimento.save(update_fields=['current_step', 'flow_context'])
            return cls._service_text(atendimento)
        if action == cls.ACTION_LOOKUP:
            return cls._lookup(atendimento, fluxo)
        if action == cls.ACTION_HUMAN:
            atendimento.current_step = Atendimento.Step.WAITING_HUMAN
            atendimento.automation_enabled = False
            atendimento.save(update_fields=['current_step', 'automation_enabled'])
            return 'Seu atendimento foi encaminhado para nossa equipe.'
        return cls._menu_text(fluxo, True)

    @staticmethod
    def _service_text(atendimento, invalid=False):
        services = Servico.objects.filter(empresa=atendimento.empresa, ativo=True)
        prefix = 'Não consegui identificar essa opção.\n\n' if invalid else ''
        if not services:
            return 'No momento não há serviços disponíveis.\n\n0 - Voltar'
        choices = '\n'.join(f'{index} - {service.nome}' for index, service in enumerate(services, 1))
        return f'{prefix}Qual serviço você deseja?\n\n{choices}\n0 - Voltar'

    @classmethod
    def _service(cls, atendimento, fluxo, text):
        if text == '0':
            atendimento.current_step = Atendimento.Step.MENU
            atendimento.flow_context = {}
            atendimento.save(update_fields=['current_step', 'flow_context'])
            return cls._menu_text(fluxo)
        services = list(Servico.objects.filter(empresa=atendimento.empresa, ativo=True))
        if not text.isdigit() or not 1 <= int(text) <= len(services):
            return cls._service_text(atendimento, True)
        service = services[int(text) - 1]
        atendimento.current_step = Atendimento.Step.DATE
        atendimento.flow_context = {'service_id': service.pk}
        atendimento.save(update_fields=['current_step', 'flow_context'])
        return 'Qual data deseja?\n\nUse DD/MM ou DD/MM/AAAA.\n0 - Voltar'

    @classmethod
    def _date(cls, atendimento, fluxo, text):
        if text == '0':
            atendimento.current_step = Atendimento.Step.SERVICE
            atendimento.save(update_fields=['current_step'])
            return cls._service_text(atendimento)
        selected_date = cls._parse_date(text)
        if selected_date is None or selected_date < timezone.localdate():
            return 'Data inválida ou passada. Use DD/MM ou DD/MM/AAAA.\n0 - Voltar'
        service = cls._context_service(atendimento)
        if service is None:
            atendimento.current_step = Atendimento.Step.SERVICE
            atendimento.save(update_fields=['current_step'])
            return cls._service_text(atendimento)
        slots = SchedulingService.get_available_slots(atendimento.empresa, service, selected_date)
        if not slots:
            return 'Não há horários disponíveis nessa data. Informe outra data.\n0 - Voltar'
        context = atendimento.flow_context
        context.update({'date': selected_date.isoformat(), 'slots': [slot.strftime('%H:%M') for slot in slots]})
        atendimento.current_step = Atendimento.Step.TIME
        atendimento.flow_context = context
        atendimento.save(update_fields=['current_step', 'flow_context'])
        return cls._time_text(atendimento)

    @staticmethod
    def _parse_date(value):
        for fmt in ('%d/%m/%Y', '%d/%m'):
            try:
                parsed = datetime.strptime(value, fmt)
                return parsed.date() if fmt.endswith('%Y') else date(timezone.localdate().year, parsed.month, parsed.day)
            except ValueError:
                continue
        return None

    @staticmethod
    def _time_text(atendimento, invalid=False):
        slots = atendimento.flow_context.get('slots', [])
        prefix = 'Não consegui identificar essa opção.\n\n' if invalid else ''
        choices = '\n'.join(f'{index} - {slot}' for index, slot in enumerate(slots, 1))
        selected_date = date.fromisoformat(atendimento.flow_context['date'])
        return f'{prefix}Horários disponíveis para {selected_date:%d/%m}:\n\n{choices}\n0 - Voltar'

    @classmethod
    def _time(cls, atendimento, fluxo, text):
        if text == '0':
            atendimento.current_step = Atendimento.Step.DATE
            atendimento.save(update_fields=['current_step'])
            return 'Qual data deseja?\n\nUse DD/MM ou DD/MM/AAAA.\n0 - Voltar'
        slots = atendimento.flow_context.get('slots', [])
        if not text.isdigit() or not 1 <= int(text) <= len(slots):
            return cls._time_text(atendimento, True)
        context = atendimento.flow_context
        context['time'] = slots[int(text) - 1]
        atendimento.current_step = Atendimento.Step.CONFIRMATION
        atendimento.flow_context = context
        atendimento.save(update_fields=['current_step', 'flow_context'])
        return cls._confirmation_text(atendimento)

    @classmethod
    def _confirmation_text(cls, atendimento):
        service = cls._context_service(atendimento)
        selected_date = date.fromisoformat(atendimento.flow_context['date'])
        return (
            f'Confirme seu agendamento:\n\nServiço: {service.nome}\n'
            f'Data: {selected_date:%d/%m/%Y}\nHorário: {atendimento.flow_context["time"]}\n\n'
            '1 - Confirmar\n2 - Alterar horário\n0 - Cancelar'
        )

    @classmethod
    def _confirmation(cls, atendimento, fluxo, text):
        if text == '0':
            atendimento.current_step = Atendimento.Step.MENU
            atendimento.flow_context = {}
            atendimento.save(update_fields=['current_step', 'flow_context'])
            return f'Agendamento cancelado.\n\n{cls._menu_text(fluxo)}'
        if text == '2':
            atendimento.current_step = Atendimento.Step.TIME
            atendimento.save(update_fields=['current_step'])
            return cls._time_text(atendimento)
        if text != '1':
            return f'Não consegui identificar essa opção.\n\n{cls._confirmation_text(atendimento)}'
        service = cls._context_service(atendimento)
        selected_date = date.fromisoformat(atendimento.flow_context['date'])
        start = datetime.strptime(atendimento.flow_context['time'], '%H:%M').time()
        try:
            appointment = SchedulingService.create_appointment(
                empresa=atendimento.empresa, contato=atendimento.contato,
                atendimento=atendimento, servico=service, date=selected_date,
                start_time=start, origem=Agendamento.Origem.WHATSAPP,
            )
        except SlotUnavailable:
            slots = SchedulingService.get_available_slots(atendimento.empresa, service, selected_date)
            atendimento.current_step = Atendimento.Step.TIME if slots else Atendimento.Step.DATE
            atendimento.flow_context = {**atendimento.flow_context, 'slots': [slot.strftime('%H:%M') for slot in slots]}
            atendimento.save(update_fields=['current_step', 'flow_context'])
            if slots:
                return f'Esse horário acabou de ficar indisponível.\n\n{cls._time_text(atendimento)}'
            return 'Esse horário acabou de ficar indisponível. Informe outra data.\n0 - Voltar'
        atendimento.current_step = Atendimento.Step.FINISHED
        atendimento.status = Atendimento.STATUS_FINALIZADO
        atendimento.flow_context = {'appointment_id': appointment.pk}
        atendimento.save(update_fields=['current_step', 'status', 'flow_context'])
        return 'Agendamento confirmado ✅'

    @staticmethod
    def _context_service(atendimento):
        return Servico.objects.filter(
            pk=atendimento.flow_context.get('service_id'),
            empresa=atendimento.empresa, ativo=True,
        ).first()

    @classmethod
    def _lookup(cls, atendimento, fluxo):
        appointment = Agendamento.objects.filter(
            empresa=atendimento.empresa, contato=atendimento.contato,
            data__gte=timezone.localdate(),
            status__in=(Agendamento.Status.PENDING, Agendamento.Status.CONFIRMED),
        ).select_related('servico').first()
        if appointment is None:
            return f'Você não possui agendamentos futuros.\n\n{cls._menu_text(fluxo)}'
        return (
            f'Seu próximo agendamento:\n\n{appointment.servico.nome}\n'
            f'{appointment.data:%d/%m/%Y}\n{appointment.hora_inicio:%H:%M}\n'
            f'Status: {appointment.get_status_display()}'
        )
