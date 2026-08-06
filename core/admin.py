from django.contrib import admin
from .models import (
    Agendamento,
    AIConfiguration,
    AuditEvent,
    Atendimento,
    BloqueioAgenda,
    Contato,
    DisponibilidadeSemanal,
    EmpresaCliente,
    FluxoAtendimento,
    Mensagem,
    Servico,
    WhatsAppIntegration,
    AppointmentReminder,
    CompanyInvitation,
    CompanyMembership,
    CompanyOnboarding,
    PaymentEvent,
    PaymentHistory,
    Plan,
    ReminderConfiguration,
    Subscription,
    UsageCounter,
    AsyncJob,
    KnowledgeBaseArticle,
    OperationalAlert,
    OperationalMetric,
    AIUsageRecord,
    AIResponseDraft,
    BlockedInboundMessage,
    DataRetentionPolicy,
    DataSubjectRequest,
    MetaOnboardingVerification,
    BusinessDataSource,
    BusinessDataRecord,
)

admin.site.register(Servico)
admin.site.register(DisponibilidadeSemanal)
admin.site.register(BloqueioAgenda)
admin.site.register(Agendamento)
admin.site.register(Plan)
admin.site.register(Subscription)
admin.site.register(CompanyMembership)
admin.site.register(CompanyInvitation)
admin.site.register(CompanyOnboarding)
admin.site.register(UsageCounter)
admin.site.register(PaymentEvent)
admin.site.register(PaymentHistory)
admin.site.register(ReminderConfiguration)
admin.site.register(AppointmentReminder)
admin.site.register(KnowledgeBaseArticle)
admin.site.register(AsyncJob)
admin.site.register(OperationalMetric)
admin.site.register(OperationalAlert)
admin.site.register(AIUsageRecord)
admin.site.register(AIResponseDraft)
admin.site.register(BlockedInboundMessage)
admin.site.register(DataRetentionPolicy)
admin.site.register(DataSubjectRequest)
admin.site.register(MetaOnboardingVerification)
admin.site.register(BusinessDataSource)
admin.site.register(BusinessDataRecord)


@admin.register(AIConfiguration)
class AIConfigurationAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'assistant_name', 'updated_at')
    list_filter = ('updated_at',)
    search_fields = ('empresa__nome', 'assistant_name')
    autocomplete_fields = ('empresa',)
    exclude = ('enabled',)


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ('action', 'actor', 'empresa', 'target_type', 'target_id', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('action', 'actor__username', 'empresa__nome', 'target_id')
    readonly_fields = (
        'actor', 'empresa', 'action', 'target_type', 'target_id',
        'metadata', 'ip_hash', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
    list_display = ('nome_cliente', 'telefone_cliente', 'contato', 'empresa', 'status', 'current_step', 'assigned_to', 'automation_enabled', 'last_message_at', 'criado_em')
    list_filter = ('status', 'current_step', 'automation_enabled', 'avisado_em', 'criado_em')
    search_fields = ('nome_cliente', 'telefone_cliente', 'empresa__nome', 'opcao_escolhida')
    autocomplete_fields = ('empresa', 'contato', 'assigned_to', 'closed_by')


@admin.register(WhatsAppIntegration)
class WhatsAppIntegrationAdmin(admin.ModelAdmin):
    list_display = (
        'company',
        'phone_number_id',
        'whatsapp_business_account_id',
        'onboarding_status',
        'is_active',
        'last_communication_at',
        'created_at',
        'updated_at',
    )
    list_filter = ('onboarding_status', 'is_active', 'last_communication_at', 'created_at')
    search_fields = (
        'company__nome', 'phone_number_id', 'whatsapp_business_account_id',
        'display_phone_number', 'verified_name',
    )
    autocomplete_fields = ('company',)
    readonly_fields = (
        'connected_at', 'disconnected_at', 'last_communication_at',
        'created_at', 'updated_at',
    )


@admin.register(Contato)
class ContatoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'whatsapp_id', 'empresa', 'criado_em', 'atualizado_em')
    search_fields = ('nome', 'whatsapp_id', 'empresa__nome')
    list_filter = ('empresa', 'criado_em')
    autocomplete_fields = ('empresa',)


@admin.register(Mensagem)
class MensagemAdmin(admin.ModelAdmin):
    list_display = (
        'external_message_id',
        'contato',
        'empresa',
        'atendimento',
        'direcao',
        'tipo',
        'status',
        'timestamp_meta',
        'criado_em',
    )
    search_fields = (
        'external_message_id',
        'contato__nome',
        'contato__whatsapp_id',
        'empresa__nome',
    )
    list_filter = ('direcao', 'tipo', 'status', 'empresa', 'criado_em')
    autocomplete_fields = ('empresa', 'atendimento', 'contato')
    readonly_fields = (
        'empresa',
        'atendimento',
        'contato',
        'external_message_id',
        'direcao',
        'tipo',
        'texto',
        'status',
        'erro_codigo',
        'timestamp_meta',
        'criado_em',
    )
