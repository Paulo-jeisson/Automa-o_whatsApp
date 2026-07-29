# Arquitetura da IA

## Escopo atual

As Sprints 3 a 10 formam o fluxo conversacional, com tools seguras, memória,
guardrails, integração ao WhatsApp, inbox, handoff e atualização incremental.

Fluxo da camada:

```text
Atendimento
→ AIAgent
→ contexto autorizado e memória limitada
→ OpenAIClient
→ Responses API
→ tool solicitada
→ AIToolExecutor vinculado ao atendimento
→ serviços internos
→ resposta persistida e enviada pela WhatsApp Cloud API
```

O cliente do provedor não importa modelos Django, não acessa o banco e não
executa regras de agenda. Ele recebe instruções e texto e devolve somente uma
resposta sanitizada.

## Módulos

- `client.py`: único adaptador para o SDK oficial e Responses API;
- `agent.py`: orquestra contexto, prompt e resposta;
- `prompts.py`: instruções centrais;
- `context.py`: combina empresa, cliente, estado, resumo e janela recente;
- `memory.py`: compacta histórico e persiste estado estruturado permitido;
- `tools.py`: contratos e executor das operações permitidas;
- `exceptions.py`: erros sanitizados da integração.

## Tools seguras

As tools disponíveis são: listar serviços, obter informações públicas,
consultar disponibilidade, criar/consultar/cancelar agendamento e solicitar
atendente. A tool de contexto salva somente fatos estruturados permitidos.

O executor recebe um `Atendimento` previamente autenticado. O `empresa_id` é
derivado desse atendimento e nunca aparece como argumento de tool. Serviços,
agenda, contato e agendamentos são filtrados novamente pelo tenant. Escritas
exigem confirmação explícita, e a criação reutiliza o serviço transacional de
agenda, que revalida o horário antes de persistir.

Não existe tool para SQL, ORM genérico, configurações ou escolha de empresa.

## Memória e limite de contexto

O atendimento armazena:

- `conversation_state`: fatos estruturados permitidos, como intenção, serviço,
  data, período, horário e confirmação pendente;
- `conversation_summary`: resumo incremental das mensagens antigas;
- `summarized_message_count`: cursor de compactação idempotente.

Somente as mensagens mais recentes são enviadas integralmente. Quando o
histórico ultrapassa o gatilho, mensagens anteriores viram um resumo limitado
em tamanho. Os limites são configuráveis por:

- `AI_CONTEXT_MESSAGE_LIMIT`;
- `AI_CONTEXT_SUMMARY_TRIGGER`;
- `AI_CONTEXT_SUMMARY_MAX_CHARS`.

## Configuração

Variáveis globais:

- `OPENAI_API_KEY`: chave exclusiva do backend;
- `AI_ENABLED`: kill switch global;
- `AI_MODEL`: modelo configurável;
- `AI_TIMEOUT`: timeout da chamada em segundos.

Configuração por empresa:

- IA ativa/inativa;
- nome do assistente;
- mensagem inicial;
- tom;
- descrição;
- informações adicionais;
- regras de transferência humana.

Uma empresa somente fica efetivamente disponível quando a chave existe,
`AI_ENABLED=True` e sua própria configuração está ativa.

## Orquestração, guardrails e fallback

Quando a configuração global e a configuração da empresa estão ativas, uma
mensagem textual persistida pelo webhook é enviada ao `AIConversationService`.
O agente recebe somente o tenant do atendimento, memória limitada e as tools
permitidas. Empresas sem IA continuam usando o fluxo legado.

Antes e depois do provedor existem validações determinísticas. Pedidos para
revelar prompt, acessar outro tenant, executar SQL ou ignorar disponibilidade
são recusados. Timeout, erro de rede, resposta vazia, tool inválida e falhas
inesperadas geram mensagem segura e transferência para humano. Se a própria Meta
falhar, o atendimento entra na fila humana para ação operacional.

## Inbox, handoff e tempo real

A inbox separa novos, IA atendendo, aguardando humano, atendimento humano e
finalizados. Registra responsável, momento da tomada, autor de mensagem manual,
encerramento e motivo de transferência. Envio automático e tomada humana
serializam o atendimento com `select_for_update`, impedindo que ambos respondam
ao mesmo tempo.

O painel consulta endpoints JSON autenticados e isolados por tenant. A conversa
busca mensagens por cursor incremental a cada três segundos; a inbox verifica
alterações a cada cinco segundos. Essa solução não exige infraestrutura de
filas, prevista somente na Sprint 19.

## Limites intencionais

Processamento assíncrono, múltiplos funcionários e WebSocket distribuído não
foram antecipados: pertencem respectivamente às Sprints 19, 11 e à evolução de
escala da atualização em tempo real.
