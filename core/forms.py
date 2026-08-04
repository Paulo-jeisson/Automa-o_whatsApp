import re

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import get_user_model

from .models import (
    Agendamento, AIConfiguration, Atendimento, BloqueioAgenda, Contato, DisponibilidadeSemanal,
    EmpresaCliente, FluxoAtendimento, Servico,
    CompanyInvitation, CompanyMembership, ReminderConfiguration, KnowledgeBaseArticle,
    DataRetentionPolicy, DataSubjectRequest, BusinessDataSource,
    MetaOnboardingVerification, CalendarConfiguration,
)


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    company_name = forms.CharField(label='Nome da empresa', max_length=120)
    segment = forms.ChoiceField(label='Segmento', choices=EmpresaCliente.SEGMENTO_CHOICES)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ('username', 'email', 'company_name', 'segment', 'password1', 'password2')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Este e-mail já está em uso.')
        return email


class CompanyInvitationForm(forms.ModelForm):
    class Meta:
        model = CompanyInvitation
        fields = ('email', 'role')

    def clean_role(self):
        role = self.cleaned_data['role']
        if role == CompanyMembership.Role.OWNER:
            raise forms.ValidationError('O papel de proprietário não pode ser convidado.')
        return role


class ReminderConfigurationForm(forms.ModelForm):
    offsets = forms.CharField(
        label='Antecedência em horas',
        help_text='Exemplo: 24, 2',
    )

    class Meta:
        model = ReminderConfiguration
        fields = ('enabled', 'template_name', 'language_code')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['offsets'].initial = ', '.join(map(str, self.instance.offsets_hours))

    def clean_offsets(self):
        try:
            values = sorted({int(item.strip()) for item in self.cleaned_data['offsets'].split(',')}, reverse=True)
        except ValueError as error:
            raise forms.ValidationError('Use somente horas inteiras separadas por vírgula.') from error
        if not values or any(value <= 0 or value > 720 for value in values):
            raise forms.ValidationError('Informe períodos entre 1 e 720 horas.')
        return values

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.offsets_hours = self.cleaned_data['offsets']
        if commit:
            instance.save()
        return instance


class AIConfigurationForm(forms.ModelForm):
    class Meta:
        model = AIConfiguration
        fields = [
            'assistant_name',
            'greeting',
            'tone',
            'business_description',
            'additional_information',
            'human_handoff_rules',
            'faq', 'policies', 'guidance', 'cancellation_rules',
            'service_rules', 'allowed_information',
        ]
        widgets = {
            'greeting': forms.Textarea(attrs={'rows': 3}),
            'business_description': forms.Textarea(attrs={'rows': 4}),
            'additional_information': forms.Textarea(attrs={'rows': 5}),
            'human_handoff_rules': forms.Textarea(attrs={'rows': 4}),
            'faq': forms.Textarea(attrs={'rows': 4}),
            'policies': forms.Textarea(attrs={'rows': 4}),
            'guidance': forms.Textarea(attrs={'rows': 4}),
            'cancellation_rules': forms.Textarea(attrs={'rows': 4}),
            'service_rules': forms.Textarea(attrs={'rows': 4}),
            'allowed_information': forms.Textarea(attrs={'rows': 4}),
        }

    def clean_assistant_name(self):
        return self.cleaned_data['assistant_name'].strip()


class KnowledgeBaseArticleForm(forms.ModelForm):
    content_type = forms.ChoiceField(
        label='Tipo', choices=KnowledgeBaseArticle.ContentType.choices, required=False,
        initial=KnowledgeBaseArticle.ContentType.FAQ,
    )
    class Meta:
        model = KnowledgeBaseArticle
        fields = ('content_type', 'title', 'category', 'content', 'price', 'attachment', 'keywords', 'is_active')
        widgets = {'content': forms.Textarea(attrs={'rows': 8})}

    def clean_content_type(self):
        return self.cleaned_data.get('content_type') or KnowledgeBaseArticle.ContentType.FAQ


class BusinessDataImportForm(forms.Form):
    name = forms.CharField(label='Nome da base', max_length=120, help_text='Ex.: Catálogo de agosto')
    data_type = forms.ChoiceField(label='Tipo de informação', choices=BusinessDataSource.DataType.choices)
    spreadsheet = forms.FileField(
        label='Arquivo de dados ou documento',
        help_text='CSV, XLSX, PDF, TXT, Markdown, JSON, XML ou HTML.',
    )
    ai_visible_columns = forms.CharField(
        label='Colunas que a IA pode informar', max_length=1000, required=False,
        help_text='Para planilhas, separe por vírgulas. Em documentos, deixe vazio para disponibilizar o texto extraído.',
    )
    replace_existing = forms.BooleanField(
        label='Substituir uma base existente com o mesmo nome', required=False, initial=True,
    )

    def clean_spreadsheet(self):
        uploaded = self.cleaned_data['spreadsheet']
        suffix = uploaded.name.rsplit('.', 1)[-1].lower() if '.' in uploaded.name else ''
        if suffix not in {'csv', 'xlsx', 'pdf', 'txt', 'md', 'json', 'xml', 'html', 'htm'}:
            raise forms.ValidationError(
                'Formato não suportado. Envie CSV, XLSX, PDF, TXT, Markdown, JSON, XML ou HTML.'
            )
        if uploaded.size > 10 * 1024 * 1024:
            raise forms.ValidationError('O arquivo deve ter no máximo 10 MB.')
        return uploaded

    def clean_ai_visible_columns(self):
        columns = [item.strip() for item in self.cleaned_data['ai_visible_columns'].split(',') if item.strip()]
        if len(columns) == 1 and columns[0].casefold() in {'todas', 'todos', '*'}:
            return []
        return list(dict.fromkeys(columns))


class DataRetentionPolicyForm(forms.ModelForm):
    class Meta:
        model = DataRetentionPolicy
        fields = ('message_retention_days', 'attendance_retention_days', 'anonymize_instead_of_delete')


class DataSubjectRequestForm(forms.ModelForm):
    class Meta:
        model = DataSubjectRequest
        fields = ('request_type', 'subject_name', 'whatsapp_id', 'verification_notes')


class MetaOnboardingVerificationForm(forms.ModelForm):
    class Meta:
        model = MetaOnboardingVerification
        fields = (
            'inbound_verified', 'outbound_verified', 'tenant_isolation_verified',
            'templates_verified', 'permissions_verified', 'notes',
        )


class EmpresaClienteForm(forms.ModelForm):
    whatsapp_dono = forms.CharField(
        label='WhatsApp do dono',
        required=False,
        max_length=20,
        help_text='Use DDD e número. Exemplo: 5588999999999.',
    )

    class Meta:
        model = EmpresaCliente
        fields = [
            'nome',
            'segmento',
            'nome_dono',
            'whatsapp_dono',
            'endereco',
            'horario_funcionamento',
            'mensagem_inicial',
            'ativa',
        ]
        widgets = {
            'mensagem_inicial': forms.Textarea(attrs={'rows': 4}),
        }
        labels = {
            'nome': 'Nome do negócio',
            'segmento': 'Segmento',
            'nome_dono': 'Nome do dono',
            'whatsapp_dono': 'WhatsApp do dono',
            'endereco': 'Endereço',
            'horario_funcionamento': 'Horário de funcionamento',
            'mensagem_inicial': 'Mensagem inicial',
            'ativa': 'Empresa ativa',
        }
        help_texts = {
            'whatsapp_dono': 'Use DDD e número. Exemplo: 5588999999999.',
            'mensagem_inicial': 'Mensagem que será usada como saudação do atendimento.',
        }

    def clean_whatsapp_dono(self):
        value = self.cleaned_data.get('whatsapp_dono', '')
        digits = re.sub(r'\D', '', value)

        if not digits:
            return ''

        if len(digits) < 10 or len(digits) > 13:
            raise forms.ValidationError('Informe um WhatsApp válido com DDD.')

        return digits


class FluxoAtendimentoForm(forms.ModelForm):
    opcoes_texto = forms.CharField(
        label='Opções do menu',
        help_text='Digite uma opção por linha.',
        widget=forms.Textarea(attrs={'rows': 5}),
    )

    class Meta:
        model = FluxoAtendimento
        fields = [
            'saudacao',
            'pergunta_menu',
            'pergunta_dados',
            'pergunta_finalizacao',
            'opcoes_texto',
        ]
        labels = {
            'saudacao': 'Saudação automática',
            'pergunta_menu': 'Pergunta do menu',
            'pergunta_dados': 'Pergunta para coleta de dados',
            'pergunta_finalizacao': 'Mensagem de finalizacao',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['opcoes_texto'].initial = '\n'.join(
                f'{item.get("label", "")} | {item.get("action", "")}' if isinstance(item, dict) else str(item)
                for item in self.instance.opcoes
            )

    def clean_opcoes_texto(self):
        value = self.cleaned_data.get('opcoes_texto', '')
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        valid_actions = {'AGENDAR', 'CONSULTAR_AGENDAMENTO', 'FALAR_COM_ATENDENTE'}
        opcoes = []
        for line in lines:
            if '|' not in line:
                opcoes.append(line)
                continue
            label, action = (part.strip() for part in line.split('|', 1))
            action = action.upper()
            if not label or action not in valid_actions:
                raise forms.ValidationError(
                    'Após | use AGENDAR, CONSULTAR_AGENDAMENTO ou FALAR_COM_ATENDENTE.'
                )
            opcoes.append({'label': label, 'action': action})

        if len(opcoes) < 2:
            raise forms.ValidationError('Informe pelo menos duas opções para o menu.')

        if len(opcoes) > 8:
            raise forms.ValidationError('Use no máximo oito opções para manter o fluxo simples.')

        return opcoes

    def save(self, commit=True):
        fluxo = super().save(commit=False)
        fluxo.opcoes = self.cleaned_data['opcoes_texto']
        if commit:
            fluxo.save()
        return fluxo


class AtendimentoSimuladoForm(forms.ModelForm):
    telefone_cliente = forms.CharField(
        label='Telefone',
        max_length=20,
        help_text='Informe DDD e número.',
    )

    class Meta:
        model = Atendimento
        fields = [
            'opcao_escolhida',
            'nome_cliente',
            'telefone_cliente',
            'necessidade',
            'observacao',
        ]
        labels = {
            'opcao_escolhida': 'O que voce deseja?',
            'nome_cliente': 'Seu nome',
            'telefone_cliente': 'Telefone',
            'necessidade': 'Necessidade',
            'observacao': 'Observação',
        }
        widgets = {
            'observacao': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'telefone_cliente': 'Informe DDD e número.',
            'observacao': 'Opcional.',
        }

    def __init__(self, *args, fluxo=None, **kwargs):
        super().__init__(*args, **kwargs)
        opcoes = fluxo.opcoes if fluxo else []
        labels = [item.get('label', '') if isinstance(item, dict) else item for item in opcoes]
        self.fields['opcao_escolhida'] = forms.ChoiceField(
            label='O que voce deseja?',
            choices=[(opcao, opcao) for opcao in labels],
        )

    def clean_telefone_cliente(self):
        value = self.cleaned_data.get('telefone_cliente', '')
        digits = re.sub(r'\D', '', value)

        if len(digits) < 10 or len(digits) > 13:
            raise forms.ValidationError('Informe um telefone válido com DDD.')

        return digits


class ConfiguracoesContaForm(forms.Form):
    first_name = forms.CharField(label='Nome', max_length=150, required=False)
    last_name = forms.CharField(label='Sobrenome', max_length=150, required=False)
    email = forms.EmailField(label='E-mail', required=False)
    new_password = forms.CharField(
        label='Nova senha',
        required=False,
        min_length=8,
        widget=forms.PasswordInput,
        help_text='Deixe em branco para manter a senha atual.',
    )
    confirm_password = forms.CharField(
        label='Confirmar nova senha',
        required=False,
        widget=forms.PasswordInput,
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user and not self.is_bound:
            self.initial.update({
                'first_name': user.first_name,
                'last_name': user.last_name,
                'email': user.email,
            })

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('new_password')
        confirmation = cleaned_data.get('confirm_password')

        if password != confirmation:
            self.add_error('confirm_password', 'As senhas informadas não são iguais.')
        return cleaned_data

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if email and get_user_model().objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError('Este e-mail já está em uso.')
        return email

    def save(self):
        if self.user is None:
            raise ValueError('Um usuário é obrigatório para salvar as configurações.')

        self.user.first_name = self.cleaned_data['first_name']
        self.user.last_name = self.cleaned_data['last_name']
        self.user.email = self.cleaned_data['email']
        password_changed = bool(self.cleaned_data.get('new_password'))
        if password_changed:
            self.user.set_password(self.cleaned_data['new_password'])
        self.user.save()
        return self.user, password_changed


class AccountPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label='Senha atual', strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password', 'autofocus': True}),
    )
    new_password1 = forms.CharField(
        label='Nova senha', strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    new_password2 = forms.CharField(
        label='Confirmar nova senha', strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )


class ServicoForm(forms.ModelForm):
    class Meta:
        model = Servico
        fields = ['nome', 'descricao', 'duracao_minutos', 'ativo']
        widgets = {'descricao': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, empresa=None, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)

    def clean_nome(self):
        nome = self.cleaned_data['nome'].strip()
        if self.empresa is not None:
            duplicates = Servico.objects.filter(
                empresa=self.empresa,
                nome__iexact=nome,
            )
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise forms.ValidationError('Já existe um serviço com esse nome.')
        return nome


class DisponibilidadeSemanalForm(forms.ModelForm):
    class Meta:
        model = DisponibilidadeSemanal
        fields = ['dia_semana', 'hora_inicio', 'hora_fim', 'intervalo_minutos', 'ativo']
        widgets = {'hora_inicio': forms.TimeInput(attrs={'type': 'time'}), 'hora_fim': forms.TimeInput(attrs={'type': 'time'})}


class CalendarConfigurationForm(forms.ModelForm):
    WEEKDAY_CHOICES = [('0', 'SEG'), ('1', 'TER'), ('2', 'QUA'), ('3', 'QUI'), ('4', 'SEX'), ('5', 'SÁB'), ('6', 'DOM')]
    weekdays = forms.MultipleChoiceField(
        label='Dias de atendimento', choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = CalendarConfiguration
        fields = [
            'enabled', 'public_slug', 'display_name', 'weekdays', 'start_time', 'end_time',
            'break_start', 'break_end', 'saturday_start', 'saturday_end', 'slot_duration_minutes',
        ]
        widgets = {
            'start_time': forms.TimeInput(attrs={'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'type': 'time'}),
            'break_start': forms.TimeInput(attrs={'type': 'time'}),
            'break_end': forms.TimeInput(attrs={'type': 'time'}),
            'saturday_start': forms.TimeInput(attrs={'type': 'time'}),
            'saturday_end': forms.TimeInput(attrs={'type': 'time'}),
            'slot_duration_minutes': forms.Select(choices=[(15, '15 min'), (20, '20 min'), (30, '30 min'), (45, '45 min'), (60, '60 min'), (90, '90 min'), (120, '2 horas')]),
        }


class BloqueioAgendaForm(forms.ModelForm):
    class Meta:
        model = BloqueioAgenda
        fields = ['data', 'hora_inicio', 'hora_fim', 'motivo']
        widgets = {
            'data': forms.DateInput(attrs={'type': 'date'}),
            'hora_inicio': forms.TimeInput(attrs={'type': 'time'}),
            'hora_fim': forms.TimeInput(attrs={'type': 'time'}),
        }


class AgendamentoForm(forms.ModelForm):
    nome_contato = forms.CharField(label='Cliente', max_length=120)
    telefone = forms.CharField(label='WhatsApp', max_length=32)

    class Meta:
        model = Agendamento
        fields = ['servico', 'data', 'hora_inicio', 'status', 'observacao']
        widgets = {
            'data': forms.DateInput(attrs={'type': 'date'}),
            'hora_inicio': forms.TimeInput(attrs={'type': 'time'}),
            'observacao': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, empresa=None, **kwargs):
        self.empresa = empresa
        super().__init__(*args, **kwargs)
        self.fields['servico'].queryset = Servico.objects.filter(empresa=empresa, ativo=True)
        if self.instance.pk:
            self.fields['nome_contato'].initial = self.instance.contato.nome
            self.fields['telefone'].initial = self.instance.contato.whatsapp_id

    def clean_telefone(self):
        digits = re.sub(r'\D', '', self.cleaned_data['telefone'])
        if len(digits) < 10 or len(digits) > 32:
            raise forms.ValidationError('Informe um telefone válido com DDD.')
        return digits

    def save_contact(self):
        contato, _ = Contato.objects.get_or_create(
            empresa=self.empresa, whatsapp_id=self.cleaned_data['telefone'],
            defaults={'nome': self.cleaned_data['nome_contato']},
        )
        if self.cleaned_data['nome_contato'] and contato.nome != self.cleaned_data['nome_contato']:
            contato.nome = self.cleaned_data['nome_contato']
            contato.save(update_fields=['nome', 'atualizado_em'])
        return contato
