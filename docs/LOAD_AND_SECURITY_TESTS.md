# Testes de carga e segurança

O script `scripts/load_webhook.py` envia eventos únicos, assinados e
concorrentes para um ambiente explicitamente autorizado:

```sh
python scripts/load_webhook.py \
  --url https://staging.example.com/webhooks/whatsapp/ \
  --app-secret "$META_APP_SECRET" \
  --phone-number-id 123 \
  --requests 1000 --concurrency 50
```

Execute os patamares de 10, 50 e 100 empresas em staging, registrando taxa de
sucesso, média, p95, fila máxima e dead-letter. Nunca execute contra produção
sem janela aprovada.

A suíte automatizada cobre isolamento multiempresa, IDOR, CSRF, assinatura de
webhook, repetição/idempotência, rate limit, prompt injection, tools da IA,
concorrência da agenda e indisponibilidade de Meta/OpenAI. Após a carga, rode
`check_operations` e confirme que não houve inconsistência ou quebra de tenant.
