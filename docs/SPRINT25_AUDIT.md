# Auditoria final — Sprint 25

Data: 30/07/2026

## Resultado

**NO-GO para comercialização recorrente.**

O produto possui cobertura automatizada consistente, migrations aplicadas e
hardening de produção sem erro crítico. Entretanto, os critérios externos e
operacionais obrigatórios ainda não possuem evidência real suficiente. Esta
classificação não impede piloto controlado sem cobrança, mas impede declarar o
ZapFluxo um SaaS comercial completo.

## Evidências

| Critério | Resultado | Evidência |
|---|---|---|
| Testes completos | Aprovado | 185 testes aprovados |
| Migrations | Aprovado | `makemigrations --check --dry-run` sem mudanças; 0018 aplicada |
| `check --deploy` | Aprovado com aviso | Nenhum erro crítico; `SECURE_HSTS_PRELOAD=False` |
| Agenda e concorrência | Aprovado automatizado | Disponibilidade, lock, confirmação, cancelamento e reagendamento |
| Atendimento humano | Aprovado automatizado | Handoff, exclusão mútua IA/humano e auditoria |
| Multiempresa | Aprovado automatizado | IDOR, webhook, tools, knowledge base, LGPD e métricas isolados |
| Segurança | Aprovado automatizado | CSRF, rate limit, assinatura, prompt injection e permissões |
| Onboarding | Aprovado automatizado | Cadastro, empresa, serviços, agenda, IA e Embedded Signup mockado |
| Cobrança | Aprovado automatizado | Webhook assinado, idempotência e estados; gateway real não validado |
| IA | Aprovado automatizado | Tools, guardrails, fallback, tokens e custos; chamada real não validada |
| WhatsApp | Aprovado automatizado | Parser, assinatura, idempotência e envio mockado; fluxo real não validado |
| Meta multiempresa real | Pendente bloqueante | Faltam evidências com duas empresas e dois números reais |
| Backup PostgreSQL | Pendente bloqueante | Scripts existem; backup real desta release não foi produzido |
| Restore PostgreSQL | Pendente bloqueante | Restore em banco temporário não foi executado |
| Carga em staging | Pendente bloqueante | Executor existe; endpoint autorizado e resultados 10/50/100 ausentes |
| Cobrança recorrente real | Pendente bloqueante | Credenciais/webhook reais não configurados neste ambiente |
| Métricas reais de IA | Pendente | Estrutura pronta; ainda não há utilização real registrada |

## Bloqueadores para GO

1. Executar backup PostgreSQL e restaurá-lo em banco temporário, validando
   migrations, integridade e `/health/`.
2. Concluir o checklist Meta com duas empresas e dois números reais, incluindo
   entrada, saída, templates, Advanced Access e isolamento.
3. Validar uma conversa real com IA, tool de disponibilidade, criação de
   agendamento e atualização do painel.
4. Validar cobrança real em sandbox do gateway: contratação, renovação, falha,
   suspensão e reativação.
5. Executar carga autorizada em staging nos patamares 10, 50 e 100 empresas e
   registrar p95, erros, backlog e dead-letter.
6. Repetir a simulação ponta a ponta completa após os itens anteriores.

## Critério de reclassificação

- **GO COM RESSALVAS:** todos os bloqueadores técnicos aprovados, restando
  somente limitações comerciais documentadas e não críticas.
- **GO:** simulação ponta a ponta real aprovada, backup/restore validado,
  monitoramento ativo e nenhuma pendência bloqueante.
