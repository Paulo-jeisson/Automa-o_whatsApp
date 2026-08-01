# Sprints 1–5 — Arquitetura, WhatsApp Web e IA

## Arquitetura

Os novos módulos seguem `domain → application → infrastructure → presentation`. O contrato `WhatsAppWebProvider` evita dependência da Evolution API nos casos de uso. Views apenas coordenam autenticação, formulários e respostas HTTP.

## Evolution API

Configure `EVOLUTION_API_URL`, `EVOLUTION_API_KEY` e `EVOLUTION_WEBHOOK_SECRET`. Cadastre o webhook do provider em `/webhooks/evolution/` enviando o segredo no header `X-ZapFluxo-Secret`.

O comando abaixo executa heartbeat e reconexão automática. Em produção, deve ser agendado pelo worker/timer:

```bash
python manage.py sync_whatsapp_sessions
```

Cada `EmpresaCliente` possui exatamente uma `WhatsAppSession`, com nome de instância único, eventos e estado persistidos.

## IA e prompts

A central `/ia/` reúne configuração, personalidade, conhecimento, fluxos, agenda, restrições, transferência humana, histórico e versões. O gerador `/ia/gerador/` produz Markdown editável e cria versões imutáveis por empresa.
