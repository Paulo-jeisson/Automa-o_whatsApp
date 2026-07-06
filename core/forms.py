import re

from django import forms

from .models import Atendimento, EmpresaCliente, FluxoAtendimento


class EmpresaClienteForm(forms.ModelForm):
    whatsapp_dono = forms.CharField(
        label='WhatsApp do dono',
        required=False,
        max_length=20,
        help_text='Use DDD e numero. Exemplo: 5588999999999.',
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
            'nome': 'Nome do negocio',
            'segmento': 'Segmento',
            'nome_dono': 'Nome do dono',
            'whatsapp_dono': 'WhatsApp do dono',
            'endereco': 'Endereco',
            'horario_funcionamento': 'Horario de funcionamento',
            'mensagem_inicial': 'Mensagem inicial',
            'ativa': 'Empresa ativa',
        }
        help_texts = {
            'whatsapp_dono': 'Use DDD e numero. Exemplo: 5588999999999.',
            'mensagem_inicial': 'Mensagem que sera usada como saudacao do atendimento.',
        }

    def clean_whatsapp_dono(self):
        value = self.cleaned_data.get('whatsapp_dono', '')
        digits = re.sub(r'\D', '', value)

        if not digits:
            return ''

        if len(digits) < 10 or len(digits) > 13:
            raise forms.ValidationError('Informe um WhatsApp valido com DDD.')

        return digits


class FluxoAtendimentoForm(forms.ModelForm):
    opcoes_texto = forms.CharField(
        label='Opcoes do menu',
        help_text='Digite uma opcao por linha.',
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
            'saudacao': 'Saudacao automatica',
            'pergunta_menu': 'Pergunta do menu',
            'pergunta_dados': 'Pergunta para coleta de dados',
            'pergunta_finalizacao': 'Mensagem de finalizacao',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['opcoes_texto'].initial = '\n'.join(self.instance.opcoes)

    def clean_opcoes_texto(self):
        value = self.cleaned_data.get('opcoes_texto', '')
        opcoes = [line.strip() for line in value.splitlines() if line.strip()]

        if len(opcoes) < 2:
            raise forms.ValidationError('Informe pelo menos duas opcoes para o menu.')

        if len(opcoes) > 8:
            raise forms.ValidationError('Use no maximo oito opcoes para manter o fluxo simples.')

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
        help_text='Informe DDD e numero.',
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
            'observacao': 'Observacao',
        }
        widgets = {
            'observacao': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'telefone_cliente': 'Informe DDD e numero.',
            'observacao': 'Opcional.',
        }

    def __init__(self, *args, fluxo=None, **kwargs):
        super().__init__(*args, **kwargs)
        opcoes = fluxo.opcoes if fluxo else []
        self.fields['opcao_escolhida'] = forms.ChoiceField(
            label='O que voce deseja?',
            choices=[(opcao, opcao) for opcao in opcoes],
        )

    def clean_telefone_cliente(self):
        value = self.cleaned_data.get('telefone_cliente', '')
        digits = re.sub(r'\D', '', value)

        if len(digits) < 10 or len(digits) > 13:
            raise forms.ValidationError('Informe um telefone valido com DDD.')

        return digits
