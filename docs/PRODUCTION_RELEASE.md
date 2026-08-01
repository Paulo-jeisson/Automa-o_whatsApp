# Release de produção — Sprint 12

## Artefatos

- `Dockerfile` multi-stage, usuário sem privilégios e health check;
- `compose.yaml` com PostgreSQL, web, worker e monitor;
- CI executa checks, migrações, testes, staticfiles e build Docker;
- tags `v*` publicam imagens imutáveis no GitHub Container Registry;
- systemd/Nginx continuam disponíveis para deploy sem containers.

## Checklist de release

1. crie uma tag semântica e registre mudanças;
2. confirme CI verde e imagem publicada;
3. faça backup e valide com `pg_restore --list`;
4. aplique a release em staging;
5. execute `python manage.py migrate`, `collectstatic` e `release_check`;
6. valide `/health/live/`, `/health/ready/` e um atendimento controlado;
7. promova a mesma imagem para produção;
8. observe erros, fila, latência e sessões WhatsApp;
9. mantenha a imagem anterior disponível para rollback.

## Escalabilidade

Web e worker são stateless; sessões, rate limit, jobs e eventos ficam no banco. Escale réplicas web horizontalmente atrás do proxy. Execute workers concorrentes por fila, mantendo PostgreSQL gerenciado e arquivos em storage compartilhado/objeto. Em múltiplos hosts, substitua o cache local por Redis sem alterar as interfaces da aplicação.

## SLO inicial

- disponibilidade mensal: 99,5%;
- readiness: 100% durante operação normal;
- jobs mortos: zero sem investigação;
- backup diário e teste de restauração mensal;
- resposta a alerta crítico: até 15 minutos.

## Rollback

Pare promoções, preserve evidências, faça backup, reverta para a imagem anterior e valide probes. Migrações destrutivas exigem plano próprio; nunca faça downgrade cego do banco.
