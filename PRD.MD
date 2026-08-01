PROMPT ESTRATÉGICO — REESTRUTURAÇÃO DO ZAPFLUXO (QR CODE + IA)
OBJETIVO

Refatorar o ZapFluxo para torná-lo uma plataforma SaaS profissional de atendimento automatizado via WhatsApp Web utilizando QR Code.

Toda implementação deverá seguir arquitetura limpa, princípios SOLID, Clean Code, DRY, KISS, Repository Pattern, Service Layer, testes automatizados e isolamento total por empresa.

IMPORTANTE

A implementação deve ocorrer em Sprints.

Ao final de cada Sprint:

executar testes;
corrigir regressões;
atualizar documentação;
somente então iniciar a Sprint seguinte.

Nenhuma Sprint poderá quebrar funcionalidades existentes.

[x] SPRINT 1
Refatoração da arquitetura

Objetivo:

Organizar todo o projeto antes das novas funcionalidades.

Implementar:

Services
Providers
Repositories
DTOs
Validators
Exceptions
Helpers
Logs

Separar:

Application

Domain

Infrastructure

Presentation

Criar interfaces para comunicação com WhatsApp.

Nada de regras de negócio dentro das Views.

[x] SPRINT 2
Novo módulo WhatsApp

Criar menu:

WhatsApp

com:

Dashboard

Conexão

Sessões

Logs

Configurações

Tela semelhante à referência.

Status

QR Code

Reconectar

Limpar sessão

Número conectado

Nome do aparelho

Última sincronização

Tempo online

Ping

Criar estados

Offline

Inicializando

Aguardando QR

Conectando

Conectado

Erro

Reconectando

Tudo responsivo.

[x] SPRINT 3
Integração QR Code

Substituir completamente a Meta Cloud API.

Utilizar arquitetura compatível com WhatsApp Web (por exemplo, Evolution API).

Implementar:

Gerar QR

Atualização automática

Reconexão

Persistência da sessão

Reutilização da sessão

Eventos

Webhooks

Heartbeat

Reconnect automático

Health Check

Cada empresa deverá possuir sua própria sessão.

[x] SPRINT 4
IA

Criar módulo IA.

Menu

IA

Submenus

Configuração

Prompt

Personalidade

Conhecimento

Fluxos

Agendamentos

Restrições

Transferência Humana

Histórico

Versões

Seguir o layout semelhante à referência, porém com identidade visual própria.

[x] SPRINT 5
Gerador de Prompt

Criar exatamente esse conceito da imagem.

Campos:

Nome do agente

Empresa

Segmento

Utiliza agenda

Profissão

Personalidade

Objetivo

Forma de atendimento

Tom de voz

Palavras proibidas

Limitações

Horário

Produtos

Serviços

Observações

Ao clicar em

Gerar Prompt

o sistema deverá montar automaticamente um Prompt profissional.

Depois permitir editar manualmente.

Salvar versões.

Histórico.

Preview.

[x] SPRINT 6
Prompt Manual

Adicionar editor grande.

Suporte Markdown.

Auto Save.

Versionamento.

Diferença entre versões.

Botão Restaurar.

Botão Duplicar.

Templates.

[x] SPRINT 7
Base de Conhecimento

Criar módulo exclusivo.

Cadastrar

FAQ

Produtos

Serviços

Valores

Documentos

PDF

Word

TXT

Políticas

Perguntas Frequentes

A IA deverá consultar essa base antes de responder.

[x] SPRINT 8
Agenda Inteligente

Novo módulo Agenda.

Horários.

Bloqueios.

Feriados.

Reagendamento.

Confirmação.

Cancelamento.

Integração automática com IA.

A IA nunca poderá marcar horários indisponíveis.

[x] SPRINT 9
Conversas

Tela semelhante aos CRMs modernos.

Lista lateral.

Pesquisa.

Filtros.

Etiquetas.

Arquivos.

Áudios.

Imagens.

Transferência para humano.

Notas internas.

Finalizar atendimento.

Reabrir.

Tudo em tempo real.

[x] SPRINT 10
Dashboard

Indicadores.

Conversas.

IA.

Humanos.

Agendamentos.

Tempo médio.

Clientes.

Funil.

Gráficos.

Exportação.

[x] SPRINT 11
Segurança

Rate Limit.

JWT.

Criptografia.

Backup.

Logs.

Auditoria.

Health Check.

Sessões.

Permissões.

LGPD.

Proteção CSRF.

Headers.

[x] SPRINT 12
Comercialização

Refatoração final.

Documentação.

Testes.

Performance.

Correção de Bugs.

Docker.

Deploy.

CI/CD.

Backup.

Monitoramento.

Escalabilidade.

Preparação para produção.

MÓDULO "GERADOR DE PROMPT" 

A tela deverá conter um assistente guiado para criação do agente de IA.

Etapa 1 – Identidade do Agente
Nome do agente.
Cargo/Função.
Nome da empresa.
Segmento de atuação.
Descrição da empresa.
Idioma principal.
Fuso horário.

Etapa 2 – Atendimento
Objetivo principal do agente.
Tipos de atendimento realizados.
Horário de funcionamento.
Uso de agenda (Sim/Não).
Tempo médio de atendimento.
Regras de transferência para humano.

Etapa 3 – Personalidade
Tom de voz.
Nível de formalidade.
Uso de emojis.
Forma de saudação.
Como encerrar conversas.
Frases obrigatórias.
Frases proibidas.

Etapa 4 – Conhecimento
Produtos.
Serviços.
Valores.
Perguntas frequentes.
Políticas.
Documentos anexos.

Etapa 5 – Restrições
O que a IA pode fazer.
O que nunca deve fazer.
Informações sigilosas.
Limites de autonomia.
Situações para encaminhar ao atendente humano.

Etapa 6 – Geração

Ao concluir o assistente, o sistema deverá gerar automaticamente um prompt estruturado em Markdown, organizado em seções como:

Identidade
Missão
Personalidade
Regras Gerais
Fluxo de Atendimento
Agendamento
Transferência para Humano
Restrições
Exemplos de Resposta

Esse prompt poderá ser editado manualmente e terá versionamento automático.
