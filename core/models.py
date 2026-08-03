from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils import timezone
from django.core.validators import MaxValueValidator, MinValueValidator


class EmpresaCliente(models.Model):
    SEGMENTO_ESTACIONAMENTO = 'estacionamento'
    SEGMENTO_CLINICA = 'clinica'
    SEGMENTO_ADVOCACIA = 'advocacia'
    SEGMENTO_CONTABILIDADE = 'contabilidade'
    SEGMENTO_ASSISTENCIA = 'assistencia'
    SEGMENTO_COMERCIO = 'comercio'

    SEGMENTO_CHOICES = [
        (SEGMENTO_ESTACIONAMENTO, 'Estacionamento'),
        (SEGMENTO_CLINICA, 'Clinica'),
        (SEGMENTO_ADVOCACIA, 'Advocacia'),
        (SEGMENTO_CONTABILIDADE, 'Contabilidade'),
        (SEGMENTO_ASSISTENCIA, 'Assistencia tecnica'),
        (SEGMENTO_COMERCIO, 'Pequeno comercio'),
    ]

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='empresa_cliente',
        verbose_name='usuario responsavel',
    )
    nome = models.CharField('nome da empresa', max_length=120)
    segmento = models.CharField(
        'segmento',
        max_length=30,
        choices=SEGMENTO_CHOICES,
        default=SEGMENTO_ESTACIONAMENTO,
    )
    nome_dono = models.CharField('nome do dono', max_length=120, blank=True)
    whatsapp_dono = models.CharField('WhatsApp do dono', max_length=13, blank=True)
    endereco = models.CharField('endereco', max_length=180, blank=True)
    horario_funcionamento = models.CharField('horario de funcionamento', max_length=120, blank=True)
    mensagem_inicial = models.TextField('mensagem inicial', blank=True)
    public_slug = models.SlugField('slug publico', max_length=140, unique=True, blank=True, null=True)
    ativa = models.BooleanField('ativa', default=True)
    criada_em = models.DateTimeField('criada em', auto_now_add=True)
    atualizada_em = models.DateTimeField('atualizada em', auto_now=True)

    class Meta:
        verbose_name = 'empresa cliente'
        verbose_name_plural = 'empresas clientes'
        ordering = ['nome']

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        if not self.public_slug:
            self.public_slug = self._gerar_public_slug()
        super().save(*args, **kwargs)

    def _gerar_public_slug(self):
        base = slugify(self.nome) or 'empresa'
        slug = base
        contador = 2
        while EmpresaCliente.objects.filter(public_slug=slug).exclude(pk=self.pk).exists():
            slug = f'{base}-{contador}'
            contador += 1
        return slug

    def get_atendimento_url(self):
        return reverse('atendimento_publico', kwargs={'public_slug': self.public_slug})


class WhatsAppIntegration(models.Model):
    class OnboardingStatus(models.TextChoices):
        LEGACY = 'LEGACY', 'Configuração manual'
        CONNECTED = 'CONNECTED', 'Conectado'
        ERROR = 'ERROR', 'Erro de conexão'
        EXPIRED = 'EXPIRED', 'Token expirado'
        DISCONNECTED = 'DISCONNECTED', 'Desconectado'

    company = models.OneToOneField(
        EmpresaCliente,
        on_delete=models.CASCADE,
        related_name='whatsapp_integration',
        verbose_name='empresa',
    )
    phone_number_id = models.CharField(
        'Phone Number ID',
        max_length=64,
        unique=True,
        db_index=True,
    )
    whatsapp_business_account_id = models.CharField(
        'WhatsApp Business Account ID',
        max_length=64,
    )
    access_token_encrypted = models.TextField('token criptografado', blank=True, editable=False)
    onboarding_status = models.CharField(
        'status do onboarding',
        max_length=20,
        choices=OnboardingStatus.choices,
        default=OnboardingStatus.LEGACY,
    )
    display_phone_number = models.CharField('número exibido', max_length=32, blank=True)
    verified_name = models.CharField('nome verificado', max_length=120, blank=True)
    token_expires_at = models.DateTimeField('token expira em', null=True, blank=True)
    connected_at = models.DateTimeField('conectada em', null=True, blank=True)
    disconnected_at = models.DateTimeField('desconectada em', null=True, blank=True)
    last_error_code = models.CharField('último código de erro', max_length=32, blank=True)
    is_active = models.BooleanField('ativa', default=True)
    last_communication_at = models.DateTimeField(
        'última comunicação',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField('criada em', auto_now_add=True)
    updated_at = models.DateTimeField('atualizada em', auto_now=True)

    class Meta:
        verbose_name = 'integração do WhatsApp'
        verbose_name_plural = 'integrações do WhatsApp'
        ordering = ['company__nome']
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(phone_number_id=''),
                name='whatsapp_phone_number_id_not_empty',
            ),
            models.CheckConstraint(
                condition=~models.Q(whatsapp_business_account_id=''),
                name='whatsapp_waba_id_not_empty',
            ),
        ]

    def __str__(self):
        return f'WhatsApp - {self.company.nome}'

    @property
    def is_connected(self):
        return (
            self.is_active
            and self.onboarding_status in {
                self.OnboardingStatus.CONNECTED,
                self.OnboardingStatus.LEGACY,
            }
        )

    def set_access_token(self, token):
        from .services.whatsapp.tokens import encrypt_token
        self.access_token_encrypted = encrypt_token(token)

    def get_access_token(self):
        from .services.whatsapp.tokens import decrypt_token
        return decrypt_token(self.access_token_encrypted) if self.access_token_encrypted else ''


class Contato(models.Model):
    empresa = models.ForeignKey(
        EmpresaCliente,
        on_delete=models.CASCADE,
        related_name='contatos',
    )
    whatsapp_id = models.CharField('número WhatsApp', max_length=32)
    nome = models.CharField('nome', max_length=120, blank=True)
    criado_em = models.DateTimeField('criado em', auto_now_add=True)
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'contato'
        verbose_name_plural = 'contatos'
        ordering = ['nome', 'whatsapp_id']
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'whatsapp_id'],
                name='unique_contact_per_company_whatsapp',
            ),
            models.CheckConstraint(
                condition=~models.Q(whatsapp_id=''),
                name='contact_whatsapp_id_not_empty',
            ),
        ]

    def __str__(self):
        return self.nome or self.whatsapp_id


class FluxoAtendimento(models.Model):
    empresa = models.OneToOneField(
        EmpresaCliente,
        on_delete=models.CASCADE,
        related_name='fluxo_atendimento',
        verbose_name='empresa',
    )
    saudacao = models.CharField('saudacao automatica', max_length=180)
    pergunta_menu = models.CharField('pergunta do menu', max_length=180)
    pergunta_dados = models.CharField('pergunta de coleta de dados', max_length=180)
    pergunta_finalizacao = models.CharField('pergunta de finalizacao', max_length=180)
    opcoes = models.JSONField('opcoes do menu', default=list)
    atualizado_em = models.DateTimeField('atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'fluxo de atendimento'
        verbose_name_plural = 'fluxos de atendimento'

    def __str__(self):
        return f'Fluxo - {self.empresa.nome}'


TEMPLATES_FLUXO = {
    EmpresaCliente.SEGMENTO_ESTACIONAMENTO: {
        'nome': 'Estacionamento',
        'descricao': 'Preços, vagas e entrada de veículos.',
        'saudacao': 'Olá! Bem-vindo ao {empresa}.',
        'pergunta_menu': 'Como podemos ajudar com seu veículo?',
        'pergunta_dados': 'Informe seu nome, telefone, veículo e placa, quando necessário.',
        'pergunta_finalizacao': 'Atendimento registrado. Em breve nossa equipe dará retorno.',
        'opcoes': [
            'Saber preco',
            'Ver disponibilidade de vaga',
            'Falar com atendente',
            'Informar entrada de veiculo',
        ],
    },
    EmpresaCliente.SEGMENTO_CLINICA: {
        'nome': 'Clínica',
        'descricao': 'Agendamentos, horários e contato com a recepção.',
        'saudacao': 'Olá! Você está no atendimento da {empresa}.',
        'pergunta_menu': 'Como podemos cuidar de você hoje?',
        'pergunta_dados': 'Informe seu nome, telefone e conte brevemente o que precisa.',
        'pergunta_finalizacao': 'Recebemos seus dados. Nossa recepção entrará em contato em breve.',
        'opcoes': [
            'Agendar consulta',
            'Confirmar horario',
            'Falar com recepcao',
            'Enviar duvida',
        ],
    },
    EmpresaCliente.SEGMENTO_ADVOCACIA: {
        'nome': 'Advocacia',
        'descricao': 'Triagem inicial, casos e documentos.',
        'saudacao': 'Olá! Bem-vindo ao atendimento de {empresa}.',
        'pergunta_menu': 'Qual tipo de atendimento você procura?',
        'pergunta_dados': 'Informe seu nome, telefone e um resumo da sua necessidade.',
        'pergunta_finalizacao': 'Sua solicitação foi registrada com sigilo. A equipe retornará em breve.',
        'opcoes': [
            'Agendar atendimento',
            'Enviar caso',
            'Falar com escritorio',
            'Consultar documentos',
        ],
    },
    EmpresaCliente.SEGMENTO_CONTABILIDADE: {
        'nome': 'Contabilidade',
        'descricao': 'Documentos, dúvidas fiscais e propostas.',
        'saudacao': 'Olá! Você está falando com {empresa}.',
        'pergunta_menu': 'Como nossa equipe contábil pode ajudar?',
        'pergunta_dados': 'Informe seu nome, telefone, empresa e necessidade.',
        'pergunta_finalizacao': 'Solicitação recebida. Um responsável dará retorno em breve.',
        'opcoes': [
            'Enviar documento',
            'Tirar duvida fiscal',
            'Falar com contador',
            'Solicitar proposta',
        ],
    },
    EmpresaCliente.SEGMENTO_ASSISTENCIA: {
        'nome': 'Assistência técnica',
        'descricao': 'Orçamentos, acompanhamento e suporte.',
        'saudacao': 'Olá! Bem-vindo à assistência {empresa}.',
        'pergunta_menu': 'O que você precisa resolver?',
        'pergunta_dados': 'Informe seu nome, telefone, equipamento e problema apresentado.',
        'pergunta_finalizacao': 'Pedido registrado. Nossa equipe técnica entrará em contato.',
        'opcoes': [
            'Solicitar orcamento',
            'Acompanhar servico',
            'Falar com tecnico',
            'Informar problema',
        ],
    },
    EmpresaCliente.SEGMENTO_COMERCIO: {
        'nome': 'Pequeno comércio',
        'descricao': 'Produtos, preços e contato com vendas.',
        'saudacao': 'Olá! Bem-vindo à {empresa}.',
        'pergunta_menu': 'Como podemos ajudar na sua compra?',
        'pergunta_dados': 'Informe seu nome, telefone e o produto que procura.',
        'pergunta_finalizacao': 'Recebemos seu pedido. Um vendedor falará com você em breve.',
        'opcoes': [
            'Consultar produto',
            'Saber preco',
            'Falar com vendedor',
            'Ver horario de atendimento',
        ],
    },
}


def template_fluxo_por_segmento(segmento, empresa_nome='sua empresa'):
    template = TEMPLATES_FLUXO.get(
        segmento,
        TEMPLATES_FLUXO[EmpresaCliente.SEGMENTO_ESTACIONAMENTO],
    )
    return {
        'saudacao': template['saudacao'].format(empresa=empresa_nome),
        'pergunta_menu': template['pergunta_menu'],
        'pergunta_dados': template['pergunta_dados'],
        'pergunta_finalizacao': template['pergunta_finalizacao'],
        'opcoes': list(template['opcoes']),
    }


def opcoes_padrao_por_segmento(segmento):
    return template_fluxo_por_segmento(segmento)['opcoes']


def dados_padrao_fluxo(empresa):
    segmento = empresa.segmento if empresa else EmpresaCliente.SEGMENTO_ESTACIONAMENTO
    nome = empresa.nome if empresa else 'sua empresa'
    return template_fluxo_por_segmento(segmento, nome)


class Atendimento(models.Model):
    class Step(models.TextChoices):
        MENU = 'MENU', 'Menu'
        SERVICE = 'SERVICE', 'Escolha do serviço'
        DATE = 'DATE', 'Escolha da data'
        TIME = 'TIME', 'Escolha do horário'
        CONFIRMATION = 'CONFIRMATION', 'Confirmação'
        WAITING_HUMAN = 'WAITING_HUMAN', 'Aguardando atendente'
        HUMAN = 'HUMAN', 'Em atendimento humano'
        FINISHED = 'FINISHED', 'Finalizado'

    STATUS_NOVO = 'novo'
    STATUS_EM_ANDAMENTO = 'em_andamento'
    STATUS_FINALIZADO = 'finalizado'

    STATUS_CHOICES = [
        (STATUS_NOVO, 'Novo'),
        (STATUS_EM_ANDAMENTO, 'Em andamento'),
        (STATUS_FINALIZADO, 'Finalizado'),
    ]

    empresa = models.ForeignKey(
        EmpresaCliente,
        on_delete=models.CASCADE,
        related_name='atendimentos',
        verbose_name='empresa',
    )
    contato = models.ForeignKey(
        Contato,
        on_delete=models.PROTECT,
        related_name='atendimentos',
        null=True,
        blank=True,
    )
    nome_cliente = models.CharField('nome do cliente', max_length=120)
    telefone_cliente = models.CharField('telefone do cliente', max_length=13)
    opcao_escolhida = models.CharField('opcao escolhida', max_length=120)
    necessidade = models.CharField('necessidade', max_length=180)
    observacao = models.TextField('observacao', blank=True)
    status = models.CharField('status', max_length=30, choices=STATUS_CHOICES, default=STATUS_NOVO)
    automation_enabled = models.BooleanField('automação ativa', default=True)
    current_step = models.CharField('etapa atual', max_length=24, choices=Step.choices, default=Step.MENU)
    flow_context = models.JSONField('contexto da conversa', default=dict, blank=True)
    conversation_state = models.JSONField('estado estruturado da conversa', default=dict, blank=True)
    conversation_summary = models.TextField('resumo da conversa', blank=True)
    summarized_message_count = models.PositiveIntegerField('mensagens incluídas no resumo', default=0)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_attendances',
        verbose_name='responsável atual',
    )
    assigned_at = models.DateTimeField('assumido em', null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='closed_attendances',
        verbose_name='finalizado por',
    )
    closed_at = models.DateTimeField('finalizado em', null=True, blank=True)
    handoff_reason = models.CharField('motivo da transferência', max_length=500, blank=True)
    last_message_at = models.DateTimeField('última mensagem em', null=True, blank=True, db_index=True)
    criado_em = models.DateTimeField('criado em', auto_now_add=True)
    avisado_em = models.DateTimeField('avisado em', null=True, blank=True)

    class Meta:
        verbose_name = 'atendimento'
        verbose_name_plural = 'atendimentos'
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['empresa', 'status', '-last_message_at']),
            models.Index(fields=['empresa', 'current_step', '-last_message_at']),
        ]

    def __str__(self):
        return f'{self.nome_cliente} - {self.empresa.nome}'

    @property
    def inbox_state(self):
        if self.status == self.STATUS_FINALIZADO or self.current_step == self.Step.FINISHED:
            return 'finished'
        if self.current_step == self.Step.WAITING_HUMAN:
            return 'waiting_human'
        if self.current_step == self.Step.HUMAN:
            return 'human'
        if self.status == self.STATUS_NOVO and not self.mensagens.exists():
            return 'new'
        return 'ai' if self.automation_enabled else 'new'

    @property
    def inbox_state_label(self):
        return {
            'new': 'Novo',
            'ai': 'IA atendendo',
            'waiting_human': 'Aguardando humano',
            'human': 'Em atendimento humano',
            'finished': 'Finalizado',
        }[self.inbox_state]


class Mensagem(models.Model):
    DIRECAO_ENTRADA = 'entrada'
    DIRECAO_SAIDA = 'saida'
    DIRECAO_CHOICES = [
        (DIRECAO_ENTRADA, 'Entrada'),
        (DIRECAO_SAIDA, 'Saída'),
    ]
    STATUS_RECEBIDA = 'received'
    STATUS_ACEITA = 'accepted'
    STATUS_ENVIADA = 'sent'
    STATUS_ENTREGUE = 'delivered'
    STATUS_LIDA = 'read'
    STATUS_FALHA = 'failed'
    STATUS_CHOICES = [
        (STATUS_RECEBIDA, 'Recebida'),
        (STATUS_ACEITA, 'Aceita pela Meta'),
        (STATUS_ENVIADA, 'Enviada'),
        (STATUS_ENTREGUE, 'Entregue'),
        (STATUS_LIDA, 'Lida'),
        (STATUS_FALHA, 'Falhou'),
    ]

    empresa = models.ForeignKey(
        EmpresaCliente,
        on_delete=models.CASCADE,
        related_name='mensagens',
    )
    atendimento = models.ForeignKey(
        Atendimento,
        on_delete=models.CASCADE,
        related_name='mensagens',
    )
    contato = models.ForeignKey(
        Contato,
        on_delete=models.PROTECT,
        related_name='mensagens',
    )
    external_message_id = models.CharField(
        'ID externo da mensagem',
        max_length=255,
        unique=True,
    )
    direcao = models.CharField(
        'direção',
        max_length=10,
        choices=DIRECAO_CHOICES,
    )
    tipo = models.CharField('tipo', max_length=32, default='unknown')
    texto = models.TextField('texto', blank=True)
    status = models.CharField(
        'status',
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_RECEBIDA,
    )
    erro_codigo = models.CharField('código de erro', max_length=32, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_whatsapp_messages',
        verbose_name='enviada por',
    )
    timestamp_meta = models.DateTimeField('horário na Meta', null=True, blank=True)
    criado_em = models.DateTimeField('criada em', auto_now_add=True)

    class Meta:
        verbose_name = 'mensagem'
        verbose_name_plural = 'mensagens'
        ordering = ['-timestamp_meta', '-criado_em']
        indexes = [
            models.Index(fields=['empresa', 'atendimento', '-criado_em']),
        ]

    def __str__(self):
        return f'{self.get_direcao_display()} - {self.external_message_id}'


class Servico(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='servicos')
    nome = models.CharField(max_length=120)
    descricao = models.TextField(blank=True)
    duracao_minutos = models.PositiveIntegerField(default=60)
    ativo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome']
        constraints = [
            models.UniqueConstraint(fields=['empresa', 'nome'], name='unique_service_name_per_company'),
            models.CheckConstraint(condition=models.Q(duracao_minutos__gt=0), name='service_duration_positive'),
        ]

    def __str__(self):
        return self.nome


class DisponibilidadeSemanal(models.Model):
    class DiaSemana(models.IntegerChoices):
        SEGUNDA = 0, 'Segunda-feira'
        TERCA = 1, 'Terça-feira'
        QUARTA = 2, 'Quarta-feira'
        QUINTA = 3, 'Quinta-feira'
        SEXTA = 4, 'Sexta-feira'
        SABADO = 5, 'Sábado'
        DOMINGO = 6, 'Domingo'

    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='disponibilidades')
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()
    intervalo_minutos = models.PositiveIntegerField(default=30)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ['dia_semana', 'hora_inicio']
        constraints = [
            models.CheckConstraint(condition=models.Q(hora_fim__gt=models.F('hora_inicio')), name='availability_end_after_start'),
            models.CheckConstraint(condition=models.Q(intervalo_minutos__gt=0), name='availability_interval_positive'),
            models.UniqueConstraint(fields=['empresa', 'dia_semana', 'hora_inicio', 'hora_fim'], name='unique_availability_window'),
        ]

    def __str__(self):
        return f'{self.get_dia_semana_display()} {self.hora_inicio:%H:%M}-{self.hora_fim:%H:%M}'


class CalendarConfiguration(models.Model):
    empresa = models.OneToOneField(EmpresaCliente, on_delete=models.CASCADE, related_name='calendar_configuration')
    enabled = models.BooleanField(default=False)
    public_slug = models.SlugField(max_length=140, unique=True)
    display_name = models.CharField(max_length=140)
    weekdays = models.JSONField(default=list)
    start_time = models.TimeField(default='08:00')
    end_time = models.TimeField(default='18:00')
    break_start = models.TimeField(null=True, blank=True)
    break_end = models.TimeField(null=True, blank=True)
    saturday_start = models.TimeField(null=True, blank=True, default='09:00')
    saturday_end = models.TimeField(null=True, blank=True, default='13:00')
    slot_duration_minutes = models.PositiveSmallIntegerField(default=30)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['empresa__nome']


class IgnoredPhoneNumber(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='ignored_phone_numbers')
    phone_number = models.CharField(max_length=20)
    name = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name', 'phone_number']
        constraints = [models.UniqueConstraint(fields=['empresa', 'phone_number'], name='unique_ignored_phone_per_company')]

    def __str__(self):
        return self.name or self.phone_number



class BloqueioAgenda(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='bloqueios_agenda')
    data = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fim = models.TimeField(null=True, blank=True)
    motivo = models.CharField(max_length=180, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['data', 'hora_inicio']
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(hora_inicio__isnull=True, hora_fim__isnull=True) | models.Q(hora_inicio__isnull=False, hora_fim__isnull=False, hora_fim__gt=models.F('hora_inicio'))),
                name='block_times_both_null_or_valid',
            ),
        ]

    def __str__(self):
        return f'{self.data:%d/%m/%Y} - {self.motivo or "Bloqueado"}'


class Agendamento(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendente'
        CONFIRMED = 'CONFIRMED', 'Confirmado'
        CANCELLED = 'CANCELLED', 'Cancelado'
        COMPLETED = 'COMPLETED', 'Concluído'

    class Origem(models.TextChoices):
        WHATSAPP = 'WHATSAPP', 'WhatsApp'
        MANUAL = 'MANUAL', 'Manual'

    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='agendamentos')
    contato = models.ForeignKey(Contato, on_delete=models.PROTECT, related_name='agendamentos')
    atendimento = models.ForeignKey(Atendimento, on_delete=models.SET_NULL, related_name='agendamentos', null=True, blank=True)
    servico = models.ForeignKey(Servico, on_delete=models.PROTECT, related_name='agendamentos')
    data = models.DateField()
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    origem = models.CharField(max_length=16, choices=Origem.choices, default=Origem.MANUAL)
    observacao = models.TextField(blank=True)
    rescheduled_from = models.OneToOneField(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rescheduled_to',
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['data', 'hora_inicio']
        indexes = [models.Index(fields=['empresa', 'data', 'status'])]
        constraints = [
            models.CheckConstraint(condition=models.Q(hora_fim__gt=models.F('hora_inicio')), name='appointment_end_after_start'),
            models.UniqueConstraint(fields=['empresa', 'data', 'hora_inicio'], condition=models.Q(status__in=['PENDING', 'CONFIRMED']), name='unique_active_appointment_start'),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.contato_id and self.empresa_id and self.contato.empresa_id != self.empresa_id:
            raise ValidationError({'contato': 'O contato deve pertencer à mesma empresa.'})
        if self.servico_id and self.empresa_id and self.servico.empresa_id != self.empresa_id:
            raise ValidationError({'servico': 'O serviço deve pertencer à mesma empresa.'})
        if self.atendimento_id and self.empresa_id and self.atendimento.empresa_id != self.empresa_id:
            raise ValidationError({'atendimento': 'O atendimento deve pertencer à mesma empresa.'})

    def __str__(self):
        return f'{self.servico} - {self.data:%d/%m/%Y} {self.hora_inicio:%H:%M}'


class RateLimitBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    window_started_at = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'bucket de limitação'
        verbose_name_plural = 'buckets de limitação'


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='zapfluxo_audit_events',
    )
    empresa = models.ForeignKey(
        EmpresaCliente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
    )
    action = models.CharField(max_length=80)
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['empresa', '-created_at']),
            models.Index(fields=['action', '-created_at']),
        ]

    def __str__(self):
        return f'{self.action} em {self.created_at:%d/%m/%Y %H:%M}'


class AIConfiguration(models.Model):
    empresa = models.OneToOneField(
        EmpresaCliente,
        on_delete=models.CASCADE,
        related_name='ai_configuration',
    )
    enabled = models.BooleanField('IA ativa', default=False)
    assistant_name = models.CharField('nome do assistente', max_length=80, default='Assistente')
    greeting = models.CharField('mensagem inicial', max_length=300, blank=True)
    tone = models.CharField('tom de atendimento', max_length=120, default='cordial e objetivo')
    business_description = models.TextField('descrição do estabelecimento', blank=True)
    additional_information = models.TextField('informações adicionais', blank=True)
    human_handoff_rules = models.TextField(
        'quando transferir para humano',
        blank=True,
        default='Quando o cliente solicitar uma pessoa ou o assunto estiver fora do escopo.',
    )
    faq = models.TextField('perguntas frequentes', blank=True)
    policies = models.TextField('políticas', blank=True)
    guidance = models.TextField('orientações de atendimento', blank=True)
    cancellation_rules = models.TextField('regras de cancelamento', blank=True)
    service_rules = models.TextField('regras de atendimento', blank=True)
    allowed_information = models.TextField('informações permitidas', blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'configuração de IA'
        verbose_name_plural = 'configurações de IA'

    @property
    def is_available(self):
        return self.enabled and settings.AI_ENABLED and bool(settings.OPENAI_API_KEY)

    def __str__(self):
        return f'IA - {self.empresa.nome}'


class CompanyMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'OWNER', 'Proprietário'
        ADMIN = 'ADMIN', 'Administrador'
        RECEPTIONIST = 'RECEPTIONIST', 'Recepcionista'
        AGENT = 'AGENT', 'Atendente'

    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='company_memberships')
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_company_memberships')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['empresa', 'user'], name='unique_company_membership')]


class CompanyInvitation(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='invitations')
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=CompanyMembership.Role.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='company_invitations')
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Plan(models.Model):
    name = models.CharField(max_length=80)
    code = models.SlugField(unique=True)
    price_cents = models.PositiveIntegerField(default=0)
    stripe_price_id = models.CharField(max_length=120, blank=True)
    operator_limit = models.PositiveIntegerField(default=1)
    attendance_limit = models.PositiveIntegerField(default=100)
    message_limit = models.PositiveIntegerField(default=1000)
    ai_call_limit = models.PositiveIntegerField(default=500)
    whatsapp_limit = models.PositiveIntegerField(default=1)
    ai_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Subscription(models.Model):
    class Status(models.TextChoices):
        TRIAL = 'TRIAL', 'Período de teste'
        ACTIVE = 'ACTIVE', 'Ativa'
        PAST_DUE = 'PAST_DUE', 'Pagamento pendente'
        SUSPENDED = 'SUSPENDED', 'Suspensa'
        CANCELED = 'CANCELED', 'Cancelada'

    empresa = models.OneToOneField(EmpresaCliente, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='subscriptions')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TRIAL)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    stripe_customer_id = models.CharField(max_length=120, blank=True)
    stripe_subscription_id = models.CharField(max_length=120, blank=True, unique=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def has_access(self):
        return self.status in {self.Status.TRIAL, self.Status.ACTIVE}


class UsageCounter(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='usage_counters')
    period = models.CharField(max_length=7)
    attendances = models.PositiveIntegerField(default=0)
    messages = models.PositiveIntegerField(default=0)
    ai_calls = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['empresa', 'period'], name='unique_usage_period')]


class PaymentEvent(models.Model):
    external_id = models.CharField(max_length=120, unique=True)
    event_type = models.CharField(max_length=80)
    processed_at = models.DateTimeField(auto_now_add=True)


class PaymentHistory(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='payment_history')
    external_id = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=30)
    amount_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default='brl')
    created_at = models.DateTimeField(auto_now_add=True)


class CompanyOnboarding(models.Model):
    empresa = models.OneToOneField(EmpresaCliente, on_delete=models.CASCADE, related_name='onboarding')
    test_completed = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)


def default_reminder_offsets():
    return [24, 2]


class ReminderConfiguration(models.Model):
    empresa = models.OneToOneField(EmpresaCliente, on_delete=models.CASCADE, related_name='reminder_configuration')
    enabled = models.BooleanField(default=False)
    offsets_hours = models.JSONField(default=default_reminder_offsets)
    template_name = models.CharField(max_length=120, default='lembrete_agendamento')
    language_code = models.CharField(max_length=12, default='pt_BR')
    updated_at = models.DateTimeField(auto_now=True)


class AppointmentReminder(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendente'
        SENT = 'SENT', 'Enviado'
        FAILED = 'FAILED', 'Falhou'

    appointment = models.ForeignKey(Agendamento, on_delete=models.CASCADE, related_name='reminders')
    offset_hours = models.PositiveIntegerField()
    scheduled_for = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    external_message_id = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=32, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['appointment', 'offset_hours'], name='unique_appointment_reminder')]


class KnowledgeBaseArticle(models.Model):
    class ContentType(models.TextChoices):
        FAQ = 'FAQ', 'FAQ'
        PRODUCT = 'PRODUCT', 'Produto'
        SERVICE = 'SERVICE', 'Serviço'
        PRICE = 'PRICE', 'Valor'
        DOCUMENT = 'DOCUMENT', 'Documento'
        POLICY = 'POLICY', 'Política'

    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='knowledge_articles')
    content_type = models.CharField(max_length=16, choices=ContentType.choices, default=ContentType.FAQ)
    title = models.CharField('título', max_length=180)
    category = models.CharField('categoria', max_length=80, blank=True)
    content = models.TextField('conteúdo')
    keywords = models.CharField('palavras-chave', max_length=300, blank=True)
    price = models.DecimalField('valor', max_digits=12, decimal_places=2, null=True, blank=True)
    attachment = models.FileField('arquivo', upload_to='knowledge/%Y/%m/', blank=True)
    is_active = models.BooleanField('ativo', default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'title']
        constraints = [
            models.UniqueConstraint(fields=['empresa', 'title'], name='unique_knowledge_title_per_company'),
        ]

    def __str__(self):
        return self.title


class BusinessDataSource(models.Model):
    class DataType(models.TextChoices):
        PRODUCT = 'PRODUCT', 'Produtos e estoque'
        SERVICE = 'SERVICE', 'Serviços e valores'
        ORDER = 'ORDER', 'Pedidos e ordens de serviço'
        PROPERTY = 'PROPERTY', 'Imóveis'
        CASE = 'CASE', 'Casos e processos'
        OTHER = 'OTHER', 'Outros dados'

    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='business_data_sources')
    name = models.CharField('nome da base', max_length=120)
    data_type = models.CharField('tipo de dados', max_length=16, choices=DataType.choices)
    source_filename = models.CharField('arquivo de origem', max_length=255)
    columns = models.JSONField('colunas importadas', default=list)
    ai_visible_columns = models.JSONField('colunas permitidas para a IA', default=list)
    row_count = models.PositiveIntegerField('registros importados', default=0)
    is_active = models.BooleanField('disponível para a IA', default=True)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='business_data_imports')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [models.UniqueConstraint(fields=['empresa', 'name'], name='unique_business_source_name_per_company')]

    def __str__(self):
        return self.name


class BusinessDataRecord(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='business_data_records')
    source = models.ForeignKey(BusinessDataSource, on_delete=models.CASCADE, related_name='records')
    row_number = models.PositiveIntegerField()
    data = models.JSONField(default=dict)
    searchable_text = models.TextField(blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['row_number']
        constraints = [models.UniqueConstraint(fields=['source', 'row_number'], name='unique_business_record_row')]
        indexes = [models.Index(fields=['empresa', 'source'])]

    @property
    def visible_data(self):
        allowed = set(self.source.ai_visible_columns or [])
        return {key: value for key, value in self.data.items() if key in allowed}


class AsyncJob(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendente'
        PROCESSING = 'PROCESSING', 'Processando'
        RETRY = 'RETRY', 'Nova tentativa'
        COMPLETED = 'COMPLETED', 'Concluído'
        DEAD = 'DEAD', 'Falha permanente'

    queue = models.CharField(max_length=40, default='default', db_index=True)
    task_name = models.CharField(max_length=120)
    payload = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['available_at', 'id']
        indexes = [models.Index(fields=['queue', 'status', 'available_at'])]


class OperationalMetric(models.Model):
    name = models.CharField(max_length=100, db_index=True)
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, null=True, blank=True, related_name='operational_metrics')
    value = models.FloatField(default=1)
    labels = models.JSONField(default=dict, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=['name', '-recorded_at'])]


class OperationalAlert(models.Model):
    class Severity(models.TextChoices):
        WARNING = 'WARNING', 'Atenção'
        CRITICAL = 'CRITICAL', 'Crítico'

    fingerprint = models.CharField(max_length=180, unique=True)
    kind = models.CharField(max_length=80, db_index=True)
    severity = models.CharField(max_length=12, choices=Severity.choices)
    message = models.CharField(max_length=500)
    is_open = models.BooleanField(default=True, db_index=True)
    occurrences = models.PositiveIntegerField(default=1)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)


class DataRetentionPolicy(models.Model):
    empresa = models.OneToOneField(EmpresaCliente, on_delete=models.CASCADE, related_name='retention_policy')
    message_retention_days = models.PositiveIntegerField(default=730)
    attendance_retention_days = models.PositiveIntegerField(default=730)
    anonymize_instead_of_delete = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class DataSubjectRequest(models.Model):
    class RequestType(models.TextChoices):
        ACCESS = 'ACCESS', 'Acesso/exportação'
        DELETION = 'DELETION', 'Exclusão'
        CORRECTION = 'CORRECTION', 'Correção'

    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'Recebida'
        VERIFYING = 'VERIFYING', 'Verificando identidade'
        APPROVED = 'APPROVED', 'Aprovada'
        COMPLETED = 'COMPLETED', 'Concluída'
        REJECTED = 'REJECTED', 'Rejeitada'

    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='data_subject_requests')
    request_type = models.CharField(max_length=16, choices=RequestType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECEIVED)
    subject_name = models.CharField(max_length=120, blank=True)
    whatsapp_id = models.CharField(max_length=32)
    contact = models.ForeignKey(Contato, on_delete=models.SET_NULL, null=True, blank=True, related_name='privacy_requests')
    verification_notes = models.TextField(blank=True)
    legal_basis_for_retention = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']
        indexes = [models.Index(fields=['empresa', 'status', '-requested_at'])]


class MetaOnboardingVerification(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='meta_verifications')
    integration = models.ForeignKey(WhatsAppIntegration, on_delete=models.CASCADE, related_name='production_verifications')
    inbound_verified = models.BooleanField(default=False)
    outbound_verified = models.BooleanField(default=False)
    tenant_isolation_verified = models.BooleanField(default=False)
    templates_verified = models.BooleanField(default=False)
    permissions_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    verified_at = models.DateTimeField(auto_now_add=True)


class AIUsageRecord(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='ai_usage_records')
    atendimento = models.ForeignKey(Atendimento, on_delete=models.SET_NULL, null=True, blank=True, related_name='ai_usage_records')
    provider_response_id = models.CharField(max_length=120, blank=True)
    model = models.CharField(max_length=80)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    tool_calls = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    succeeded = models.BooleanField(default=True)
    error_type = models.CharField(max_length=80, blank=True)
    estimated_cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['empresa', '-created_at']),
            models.Index(fields=['empresa', 'succeeded', '-created_at']),
        ]


class WhatsAppSession(models.Model):
    STATE_CHOICES = [
        ('OFFLINE', 'Offline'), ('INITIALIZING', 'Inicializando'),
        ('WAITING_QR', 'Aguardando QR'), ('CONNECTING', 'Conectando'),
        ('CONNECTED', 'Conectado'), ('ERROR', 'Erro'),
        ('RECONNECTING', 'Reconectando'),
    ]
    empresa = models.OneToOneField(EmpresaCliente, on_delete=models.CASCADE, related_name='whatsapp_session')
    instance_name = models.SlugField(max_length=120, unique=True)
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default='OFFLINE', db_index=True)
    qr_code = models.TextField(blank=True)
    phone_number = models.CharField(max_length=32, blank=True)
    device_name = models.CharField(max_length=120, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    ping_ms = models.PositiveIntegerField(null=True, blank=True)
    reconnect_attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def online_seconds(self):
        if not self.connected_at or self.state != 'CONNECTED':
            return 0
        return max(0, int((timezone.now() - self.connected_at).total_seconds()))


class WhatsAppSessionEvent(models.Model):
    session = models.ForeignKey(WhatsAppSession, on_delete=models.CASCADE, related_name='events')
    kind = models.CharField(max_length=40, db_index=True)
    message = models.CharField(max_length=300, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']


class AIPromptProfile(models.Model):
    empresa = models.OneToOneField(EmpresaCliente, on_delete=models.CASCADE, related_name='prompt_profile')
    generator_data = models.JSONField(default=dict, blank=True)
    generated_prompt = models.TextField(blank=True)
    draft_prompt = models.TextField(blank=True)
    autosaved_at = models.DateTimeField(null=True, blank=True)
    response_delay_seconds = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(0), MaxValueValidator(60)],
    )
    updated_at = models.DateTimeField(auto_now=True)


class AIPromptVersion(models.Model):
    profile = models.ForeignKey(AIPromptProfile, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveIntegerField()
    content = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['profile', 'version'], name='unique_ai_prompt_version')]


class AIPromptTemplate(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='prompt_templates', null=True, blank=True)
    name = models.CharField(max_length=120)
    content = models.TextField()
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_system', 'name']
        constraints = [models.UniqueConstraint(fields=['empresa', 'name'], name='unique_prompt_template_per_company')]


class Holiday(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='holidays')
    date = models.DateField()
    name = models.CharField(max_length=120)
    blocks_schedule = models.BooleanField(default=True)

    class Meta:
        ordering = ['date']
        constraints = [models.UniqueConstraint(fields=['empresa', 'date'], name='unique_holiday_per_company')]


class AttendanceTag(models.Model):
    empresa = models.ForeignKey(EmpresaCliente, on_delete=models.CASCADE, related_name='attendance_tags')
    name = models.CharField(max_length=40)
    color = models.CharField(max_length=7, default='#00e5ff')
    attendances = models.ManyToManyField(Atendimento, related_name='tags', blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['empresa', 'name'], name='unique_attendance_tag_per_company')]


class AttendanceNote(models.Model):
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, related_name='internal_notes')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    text = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)


class AttendanceAttachment(models.Model):
    atendimento = models.ForeignKey(Atendimento, on_delete=models.CASCADE, related_name='attachments')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    file = models.FileField(upload_to='attendances/%Y/%m/')
    media_type = models.CharField(max_length=20, default='document')
    created_at = models.DateTimeField(auto_now_add=True)


class APIRefreshToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='api_refresh_tokens')
    jti_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > timezone.now()
