from django import forms


class PromptGeneratorForm(forms.Form):
    agent_name = forms.CharField(
        label='Nome do(a) agente', max_length=100,
        widget=forms.TextInput(attrs={'placeholder': 'Ex: Maria, João, etc.'}),
    )
    company_name = forms.CharField(
        label='Nome da sua empresa', max_length=140,
        widget=forms.TextInput(attrs={'placeholder': 'Ex: Clínica São José'}),
    )
    segment = forms.CharField(
        label='Ramo do seu negócio', max_length=140,
        widget=forms.TextInput(attrs={'placeholder': 'Ex: Saúde, Educação, Comércio, etc.'}),
    )
    calendar_usage = forms.CharField(
        label='Uso do calendário / agendamentos', max_length=300,
        widget=forms.TextInput(attrs={'placeholder': 'Ex: Ofereça agendamento quando o cliente quiser marcar um horário.'}),
    )
    profession = forms.CharField(
        label='Profissão do(a) agente', max_length=100,
        widget=forms.TextInput(attrs={'placeholder': 'Ex: Recepcionista, Atendente, Consultor, etc.'}),
    )
    personality = forms.CharField(
        label='Personalidade e tom',
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Ex: Acolhedor, paciente, proativo, amigável e natural...'}),
        help_text='Descreva como o agente deve se comportar e se comunicar',
    )
    additional_information = forms.CharField(
        label='Complemento (informações adicionais)', required=False,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Adicione informações extras que você gostaria que o agente soubesse...'}),
    )
