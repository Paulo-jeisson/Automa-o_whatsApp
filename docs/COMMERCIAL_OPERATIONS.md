# Operação comercial — Sprints 11 a 15

## Equipe e permissões

O proprietário existente continua sendo identificado por `EmpresaCliente.usuario`.
Membros adicionais usam `CompanyMembership` com os papéis proprietário,
administrador, recepcionista e atendente. O middleware de papéis protege
configuração da empresa, equipe, agenda e central de atendimento.

Convites expiram em sete dias. Apenas o hash do token é persistido; o token bruto
é enviado por e-mail. Aceitação exige uma sessão autenticada com o mesmo e-mail.

## Planos e limites

`Plan` define operadores, atendimentos, mensagens, chamadas de IA, números de
WhatsApp e disponibilidade da IA. `Subscription` suporta `TRIAL`, `ACTIVE`,
`PAST_DUE`, `SUSPENDED` e `CANCELED`. Contadores são mensais e atualizados de
forma transacional.

Empresas antigas sem assinatura continuam operando para preservar
compatibilidade. Novos cadastros recebem assinatura trial.

## Cadastro e onboarding

`/cadastro/` cria usuário, empresa, associação de proprietário, assinatura trial
e checklist. O onboarding acompanha empresa, serviço, horário, WhatsApp, IA e
teste final, ativando o checklist somente quando todos estiverem concluídos.

## Stripe

Variáveis:

- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_API_VERSION`
- `TRIAL_DAYS`

O Checkout é criado exclusivamente no backend em modo `subscription`. O retorno
do navegador não ativa a conta. Somente o webhook `/webhooks/stripe/`, após
validar assinatura sobre o corpo bruto, altera assinatura e histórico.
`PaymentEvent.external_id` torna o processamento idempotente.

Eventos tratados: `checkout.session.completed`, `invoice.paid`,
`invoice.payment_failed`, `customer.subscription.updated` e
`customer.subscription.deleted`.

## Lembretes

Cada empresa configura antecedências, nome e idioma de um template aprovado na
Meta. O template deve conter, no WhatsApp Manager, as opções Confirmar,
Cancelar e Falar com atendente. O backend envia os parâmetros cliente, serviço,
data e horário.

Execute periodicamente:

```bash
python manage.py process_appointment_reminders
```

Em produção, configure cron ou systemd timer a cada minuto. A constraint por
agendamento e antecedência impede duplicação. Resultado, ID da Meta e falha
sanitizada ficam registrados em `AppointmentReminder`.
