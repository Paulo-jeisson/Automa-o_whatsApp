# Persistência operacional da Evolution API

Use `deploy/evolution/compose.yaml` como stack separada do Django. Antes do
deploy, fixe em `EVOLUTION_IMAGE` exatamente a versão já validada em staging e
confirme na documentação dessa versão os nomes das variáveis de banco/cache.
O PostgreSQL `evolution-db` é exclusivo da Evolution; não reutilize o banco nem
o usuário do Django. Redis usa AOF e as credenciais Baileys possuem também o
volume `evolution_instances`; nenhum desses dados existe somente no container.

## Teste obrigatório de sobrevivência

1. Faça backup dos volumes/banco, conecte uma instância e registre apenas seu nome.
2. Execute `docker compose -f deploy/evolution/compose.yaml restart evolution`.
3. Consulte `/instance/connectionState/{instance}`; deve voltar `open` sem QR.
4. Execute `docker compose -f deploy/evolution/compose.yaml down` e depois `up -d`.
5. Repita a consulta e envie/receba uma mensagem controlada.
6. Reinicie a VPS, confirme os três containers como `healthy/running` e repita.
7. Se qualquer etapa pedir QR, interrompa o rollout e restaure o backup; não use
   “Limpar sessão” como tentativa automática de recuperação.

Não execute `down -v`: essa opção remove as credenciais persistidas. Proteja os
backups e `.env` como segredos. O Manager só deve ser exposto atrás de HTTPS e
autenticação; configure `EVOLUTION_BIND_ADDRESS` com um endereço privado ou de
loopback, nunca com exposição pública irrestrita.
