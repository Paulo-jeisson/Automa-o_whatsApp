from django.urls import path

from . import views

app_name = 'platform'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('empresas/', views.CompanyListView.as_view(), name='companies'),
    path('empresas/<int:pk>/', views.CompanyDetailView.as_view(), name='company_detail'),
    path('openai/', views.OpenAIUsageView.as_view(), name='openai'),
    path('financeiro/', views.FinanceView.as_view(), name='finance'),
    path('infraestrutura/', views.InfrastructureView.as_view(), name='infrastructure'),
    path('logs/', views.LogView.as_view(), name='logs'),
    path('assinaturas/', views.SubscriptionListView.as_view(), name='subscriptions'),
    path('configuracoes/', views.PlatformSettingsView.as_view(), name='settings'),
]
