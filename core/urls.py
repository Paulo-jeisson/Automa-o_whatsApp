from django.urls import path

from . import views


urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('atendimentos/', views.atendimentos, name='atendimentos'),
    path('atendimentos/<int:atendimento_id>/status/', views.atualizar_status_atendimento, name='atualizar_status_atendimento'),
    path('atendimentos/<int:atendimento_id>/avisar-whatsapp/', views.avisar_whatsapp_atendimento, name='avisar_whatsapp_atendimento'),
    path('minha-empresa/', views.minha_empresa, name='minha_empresa'),
    path('fluxo/', views.fluxo, name='fluxo'),
    path('atendimento/<slug:public_slug>/', views.atendimento_publico, name='atendimento_publico'),
]
