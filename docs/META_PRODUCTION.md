# Meta em produção

Use `/configuracoes/whatsapp/producao/` ou:

```sh
python manage.py meta_production_check COMPANY_ID
```

O checklist automático cobre configuração do app, HTTPS, assinatura do
webhook, Embedded Signup, integração e token por tenant. Advanced Access,
templates aprovados, mensagens reais e o teste com duas empresas são
comprovações externas: registre-as somente após executar o teste no Meta
Business Manager com dois números reais.

O sistema impede reutilizar o mesmo `phone_number_id`, valida que o número
pertence à WABA autorizada e deriva a empresa exclusivamente da integração
encontrada pelo webhook.
