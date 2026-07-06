from urllib.parse import quote

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


def opcoes_padrao_por_segmento(segmento):
    opcoes = {
        EmpresaCliente.SEGMENTO_ESTACIONAMENTO: [
            'Saber preco',
            'Ver disponibilidade de vaga',
            'Falar com atendente',
            'Informar entrada de veiculo',
        ],
        EmpresaCliente.SEGMENTO_CLINICA: [
            'Agendar consulta',
            'Confirmar horario',
            'Falar com recepcao',
            'Enviar duvida',
        ],
        EmpresaCliente.SEGMENTO_ADVOCACIA: [
            'Agendar atendimento',
            'Enviar caso',
            'Falar com escritorio',
            'Consultar documentos',
        ],
        EmpresaCliente.SEGMENTO_CONTABILIDADE: [
            'Enviar documento',
            'Tirar duvida fiscal',
            'Falar com contador',
            'Solicitar proposta',
        ],
        EmpresaCliente.SEGMENTO_ASSISTENCIA: [
            'Solicitar orcamento',
            'Acompanhar servico',
            'Falar com tecnico',
            'Informar problema',
        ],
        EmpresaCliente.SEGMENTO_COMERCIO: [
            'Consultar produto',
            'Saber preco',
            'Falar com vendedor',
            'Ver horario de atendimento',
        ],
    }
    return opcoes.get(segmento, opcoes[EmpresaCliente.SEGMENTO_ESTACIONAMENTO])


def dados_padrao_fluxo(empresa):
    nome = empresa.nome if empresa else 'sua empresa'
    return {
        'saudacao': f'Ola, voce esta falando com {nome}.',
        'pergunta_menu': 'Como podemos ajudar?',
        'pergunta_dados': 'Para continuar, informe nome, telefone e sua necessidade.',
        'pergunta_finalizacao': 'Obrigado. Em breve o responsavel dara retorno.',
        'opcoes': opcoes_padrao_por_segmento(empresa.segmento if empresa else EmpresaCliente.SEGMENTO_ESTACIONAMENTO),
    }


class Atendimento(models.Model):
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
    nome_cliente = models.CharField('nome do cliente', max_length=120)
    telefone_cliente = models.CharField('telefone do cliente', max_length=13)
    opcao_escolhida = models.CharField('opcao escolhida', max_length=120)
    necessidade = models.CharField('necessidade', max_length=180)
    observacao = models.TextField('observacao', blank=True)
    status = models.CharField('status', max_length=30, choices=STATUS_CHOICES, default=STATUS_NOVO)
    criado_em = models.DateTimeField('criado em', auto_now_add=True)
    avisado_em = models.DateTimeField('avisado em', null=True, blank=True)

    class Meta:
        verbose_name = 'atendimento'
        verbose_name_plural = 'atendimentos'
        ordering = ['-criado_em']

    def __str__(self):
        return f'{self.nome_cliente} - {self.empresa.nome}'

    def mensagem_aviso_whatsapp(self):
        linhas = [
            'Novo atendimento recebido:',
            f'Cliente: {self.nome_cliente}',
            f'Telefone: {self.telefone_cliente}',
            f'Opcao: {self.opcao_escolhida}',
            f'Segmento: {self.empresa.get_segmento_display()}',
            f'Necessidade: {self.necessidade}',
        ]

        if self.observacao:
            linhas.append(f'Observacao: {self.observacao}')

        return '\n'.join(linhas)

    def get_whatsapp_aviso_url(self):
        telefone = self.empresa.whatsapp_dono
        mensagem = quote(self.mensagem_aviso_whatsapp())
        return f'https://wa.me/{telefone}?text={mensagem}'
