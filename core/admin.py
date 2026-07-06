from django.contrib import admin
from .models import Atendimento, EmpresaCliente, FluxoAtendimento


@admin.register(EmpresaCliente)
class EmpresaClienteAdmin(admin.ModelAdmin):
    list_display = ('nome', 'segmento', 'public_slug', 'nome_dono', 'whatsapp_dono', 'usuario', 'ativa', 'criada_em')
    list_filter = ('segmento', 'ativa', 'criada_em')
    search_fields = ('nome', 'public_slug', 'nome_dono', 'whatsapp_dono', 'usuario__username', 'usuario__email')
    autocomplete_fields = ('usuario',)


@admin.register(FluxoAtendimento)
class FluxoAtendimentoAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'pergunta_menu', 'atualizado_em')
    search_fields = ('empresa__nome', 'pergunta_menu')
    autocomplete_fields = ('empresa',)


@admin.register(Atendimento)
class AtendimentoAdmin(admin.ModelAdmin):
    list_display = ('nome_cliente', 'telefone_cliente', 'empresa', 'opcao_escolhida', 'status', 'avisado_em', 'criado_em')
    list_filter = ('status', 'avisado_em', 'criado_em')
    search_fields = ('nome_cliente', 'telefone_cliente', 'empresa__nome', 'opcao_escolhida')
    autocomplete_fields = ('empresa',)
