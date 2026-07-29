from django.urls import path

from . import views
from .webhook_views import whatsapp_webhook


urlpatterns = [
    path('', views.landing_page, name='landing_page'),
    path('politica-de-privacidade/', views.politica_privacidade, name='politica_privacidade'),
    path('termos-de-servico/', views.termos_servico, name='termos_servico'),
    path('exclusao-de-dados/', views.exclusao_dados, name='exclusao_dados'),
    path('painel/', views.dashboard, name='dashboard'),
    path('atendimentos/', views.atendimentos, name='atendimentos'),
    path('atendimentos/<int:atendimento_id>/', views.atendimento_detalhe, name='atendimento_detalhe'),
    path('atendimentos/<int:atendimento_id>/assumir/', views.assumir_atendimento, name='assumir_atendimento'),
    path('atendimentos/<int:atendimento_id>/status/', views.atualizar_status_atendimento, name='atualizar_status_atendimento'),
    path('atendimentos/<int:atendimento_id>/avisar-whatsapp/', views.avisar_whatsapp_atendimento, name='avisar_whatsapp_atendimento'),
    path('minha-empresa/', views.minha_empresa, name='minha_empresa'),
    path('configuracoes/', views.configuracoes, name='configuracoes'),
    path('configuracoes/whatsapp/conectar/', views.whatsapp_onboarding, name='whatsapp_onboarding'),
    path('configuracoes/whatsapp/desconectar/', views.whatsapp_desconectar, name='whatsapp_desconectar'),
    path('configuracoes/testar-whatsapp/', views.testar_integracao_whatsapp, name='testar_integracao_whatsapp'),
    path('agenda/', views.agenda, name='agenda'),
    path('agenda/novo/', views.agendamento_novo, name='agendamento_novo'),
    path('agenda/<int:agendamento_id>/', views.agendamento_detalhe, name='agendamento_detalhe'),
    path('agenda/<int:agendamento_id>/editar/', views.agendamento_editar, name='agendamento_editar'),
    path('agenda/<int:agendamento_id>/status/', views.agendamento_status, name='agendamento_status'),
    path('agenda/configuracao/', views.agenda_configuracao, name='agenda_configuracao'),
    path('agenda/configuracao/<str:tipo>/<int:objeto_id>/excluir/', views.agenda_configuracao_excluir, name='agenda_configuracao_excluir'),
    path('fluxo/', views.fluxo, name='fluxo'),
    path('fluxo/aplicar-template/', views.aplicar_template_fluxo, name='aplicar_template_fluxo'),
    path('atendimento/<slug:public_slug>/', views.atendimento_publico, name='atendimento_publico'),
    path('webhooks/whatsapp/', whatsapp_webhook, name='whatsapp_webhook'),
]
