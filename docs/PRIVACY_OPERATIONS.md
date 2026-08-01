# LGPD e ciclo dos dados

O painel `/configuracoes/privacidade/` registra solicitações de acesso,
correção e exclusão com empresa, titular, estado, verificação e auditoria.
Exports são JSON filtrado pelo tenant. Exclusões aprovadas anonimizam contato,
atendimentos, mensagens e observações de agenda sem apagar trilhas operacionais.

Execute periodicamente:

```sh
python manage.py apply_data_retention
```

A base legal para retenção excepcional deve ser registrada antes de rejeitar
uma solicitação. Suboperadores atuais incluem infraestrutura, Meta/WhatsApp,
OpenAI, provedor de e-mail e gateway de pagamento, conforme os contratos do
ambiente. Incidentes devem ser registrados no sistema operacional de alertas,
preservando evidências sem incluir conteúdo pessoal desnecessário.
