from django.urls import path

from . import views
from .webhook_views import whatsapp_webhook


urlpatterns = [
    path('', views.landing_page, name='landing_page'),
    path('painel/', views.dashboard, name='dashboard'),
    path('atendimentos/', views.atendimentos, name='atendimentos'),
    path('atendimentos/<int:atendimento_id>/status/', views.atualizar_status_atendimento, name='atualizar_status_atendimento'),
    path('atendimentos/<int:atendimento_id>/avisar-whatsapp/', views.avisar_whatsapp_atendimento, name='avisar_whatsapp_atendimento'),
    path('minha-empresa/', views.minha_empresa, name='minha_empresa'),
    path('configuracoes/', views.configuracoes, name='configuracoes'),
    path('configuracoes/testar-whatsapp/', views.testar_integracao_whatsapp, name='testar_integracao_whatsapp'),
    path('fluxo/', views.fluxo, name='fluxo'),
    path('fluxo/aplicar-template/', views.aplicar_template_fluxo, name='aplicar_template_fluxo'),
    path('atendimento/<slug:public_slug>/', views.atendimento_publico, name='atendimento_publico'),
    path('webhooks/whatsapp/', whatsapp_webhook, name='whatsapp_webhook'),
]
