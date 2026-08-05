# Operação comercial — Asaas

## Domínio e acesso

`Subscription` usa `TRIAL`, `ACTIVE`, `GRACE`, `BLOCKED` e `CANCELED`. O acesso
é calculado por status **e datas**. Ausência de assinatura ou datas inválidas
falha de forma fechada quando `SUBSCRIPTION_ENFORCEMENT_ENABLED=true`.

- Trial: exatamente 3 dias, usando timezone.
- Mensal: R$ 147,00 e ciclo `MONTHLY`.
- Anual: R$ 997,00 e ciclo `YEARLY`. “12x de R$ 83,08” é apenas apresentação.
- Inadimplência: `GRACE` até 3 dias; depois `BLOCKED` mesmo sem worker.
- Cancelamento: acesso somente até `current_period_end` já pago.

O `SubscriptionAccessMiddleware` protege HTML, API, AJAX e HTMX. IA, Evolution,
Meta, envio outbound e lembretes validam novamente no serviço, pois webhooks e
workers não possuem usuário autenticado.

## Checkout e métodos

O backend aceita somente os códigos `monthly` e `annual`; preço e ciclo vêm do
catálogo interno. Pix e cartão usam o Checkout hospedado Asaas (`RECURRENT`).
Na versão atual da API, o Checkout hospedado documenta `PIX` e `CREDIT_CARD`, não
boleto. Boleto usa a assinatura oficial `BOLETO` e redireciona para a `invoiceUrl`
da primeira cobrança. Como a criação direta de cliente exige CPF/CNPJ, esse dado
é solicitado somente no POST do boleto, enviado ao Asaas e não persistido pelo
IAATENDE. No Checkout hospedado, a identificação é coletada pelo próprio Asaas e
o `customer` retornado no webhook é associado à empresa. Nenhum dado bruto de
cartão passa pelo IAATENDE.

Cartão pode renovar automaticamente. Pix e boleto geram cobranças futuras, mas
dependem de nova quitação pelo pagador. Nenhum método libera acesso enquanto a
cobrança estiver pendente. O retorno do navegador apenas exibe o estado salvo;
somente um webhook autenticado confirma acesso.

## Webhook exclusivo

Endpoint: `POST /webhooks/asaas/`

Configure token exclusivo no cabeçalho `asaas-access-token`. O endpoint limita o
corpo a 256 KiB, compara o token em tempo constante, sanitiza o payload persistido
e garante unicidade por `(provider, provider_event_id)`. Referências que não
começam com `iaatende:company:` são marcadas `IGNORED`.

Eventos financeiros tratados:

- confirmação: `PAYMENT_CONFIRMED`, `PAYMENT_RECEIVED`;
- pendência: `PAYMENT_CREATED`, `PAYMENT_AWAITING_RISK_ANALYSIS`;
- atraso: `PAYMENT_OVERDUE`;
- bloqueio/reversão: `PAYMENT_REFUNDED`, `PAYMENT_REFUND_IN_PROGRESS`,
  `PAYMENT_CHARGEBACK_REQUESTED`, `PAYMENT_CHARGEBACK_DISPUTE`,
  `PAYMENT_DELETED`, `PAYMENT_CREDIT_CARD_CAPTURE_REFUSED`;
- cancelamento: `SUBSCRIPTION_DELETED`, `SUBSCRIPTION_INACTIVATED`.

Antes de ativar, são cruzados referência, empresa, cliente, assinatura, plano e
valor. Um evento duplicado não prolonga a vigência.

## Configuração Sandbox

1. Crie uma chave no Sandbox Asaas.
2. Gere um token de webhook aleatório e exclusivo do IAATENDE.
3. Preencha as variáveis `ASAAS_*` de `.env.example`.
4. Cadastre `https://SEU-DOMINIO/webhooks/asaas/` no painel Asaas.
5. Selecione os eventos listados acima.
6. Mantenha `ASAAS_ENVIRONMENT=sandbox` e `SUBSCRIPTION_ENFORCEMENT_ENABLED=true`.
7. Rode `python manage.py migrate` e `python manage.py test`.
8. Simule cartão aprovado/recusado, Pix, boleto, atraso, duplicidade, estorno,
   chargeback, cancelamento, timeout e retorno do checkout.

## Checklist para produção

- suíte completa e testes Sandbox aprovados;
- HTTPS, URLs de callback e host de produção revisados;
- chave e token em cofre, sem presença em logs ou Git;
- webhook IAATENDE separado de outros produtos na mesma conta;
- alertas para eventos `FAILED`, indisponibilidade e divergência financeira;
- reconciliação de cobranças e backup testados;
- assinaturas Stripe legadas inventariadas e tratadas manualmente;
- `SUBSCRIPTION_ENFORCEMENT_ENABLED=true` confirmado.

## Migração e rollback

Os campos Stripe foram renomeados para `legacy_stripe_*` e não são usados pelo
fluxo operacional. Assinaturas que possuíam IDs Stripe recebem provider
`LEGACY_STRIPE`; não há conversão automática para IDs Asaas. Antes da produção,
exporte esses registros e defina migração ou encerramento manual por cliente.

Rollback: desabilite temporariamente a barreira apenas durante uma janela de
incidente controlada, restaure a versão anterior da aplicação e reverta a
migration somente após restaurar um backup compatível. A migration reversa não
converte estados Asaas em assinaturas Stripe funcionais.
