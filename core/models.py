from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify


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
    criado_em = models.DateTimeField('criado em', auto_now_add=True)
    avisado_em = models.DateTimeField('avisado em', null=True, blank=True)

    class Meta:
        verbose_name = 'atendimento'
        verbose_name_plural = 'atendimentos'
        ordering = ['-criado_em']

    def __str__(self):
        return f'{self.nome_cliente} - {self.empresa.nome}'


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
