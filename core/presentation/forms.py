from django import forms


class PromptGeneratorForm(forms.Form):
    agent_name = forms.CharField(label='Nome do agente', max_length=100)
    company_name = forms.CharField(label='Empresa', max_length=140)
    segment = forms.CharField(label='Segmento', max_length=100)
    uses_calendar = forms.BooleanField(label='Utiliza agenda', required=False)
    profession = forms.CharField(label='Profissão', max_length=100, required=False)
    personality = forms.CharField(label='Personalidade', widget=forms.Textarea(attrs={'rows': 3}), required=False)
    objective = forms.CharField(label='Objetivo', widget=forms.Textarea(attrs={'rows': 3}), required=False)
    service_style = forms.CharField(label='Forma de atendimento', widget=forms.Textarea(attrs={'rows': 3}), required=False)
    tone = forms.CharField(label='Tom de voz', max_length=120, required=False)
    forbidden_words = forms.CharField(label='Palavras proibidas', widget=forms.Textarea(attrs={'rows': 2}), required=False)
    limitations = forms.CharField(label='Limitações', widget=forms.Textarea(attrs={'rows': 3}), required=False)
    business_hours = forms.CharField(label='Horário', max_length=180, required=False)
    products = forms.CharField(label='Produtos', widget=forms.Textarea(attrs={'rows': 3}), required=False)
    services = forms.CharField(label='Serviços', widget=forms.Textarea(attrs={'rows': 3}), required=False)
    notes = forms.CharField(label='Observações', widget=forms.Textarea(attrs={'rows': 3}), required=False)
