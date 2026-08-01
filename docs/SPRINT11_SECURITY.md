# Sprint 11 — Segurança

## Controles

- rate limit persistente para login, APIs, webhooks e operações sensíveis;
- JWT HS256 com access token de 15 minutos, refresh rotativo e revogação persistida;
- criptografia de tokens WhatsApp com chave dedicada;
- sessões seguras, revogação de sessões remotas e cookies hardened em produção;
- RBAC por empresa e isolamento multi-tenant;
- auditoria administrativa com IP anonimizado;
- fluxos LGPD de acesso, correção, exportação e exclusão;
- CSRF em formulários e webhooks autenticados por assinatura/segredo;
- CSP, `nosniff`, Referrer Policy, Permissions Policy e COOP;
- health check de aplicação/banco;
- backup PostgreSQL com permissões `0600`, retenção e validação por `pg_restore`.

## JWT

`POST /api/auth/token/`, `POST /api/auth/refresh/` e `GET /api/me/`. Refresh tokens são de uso único. Configure `JWT_ACCESS_SECONDS` e `JWT_REFRESH_SECONDS` no ambiente.

## Operação

A central `/seguranca/` mostra controles, sessões e auditoria, e permite encerrar outras sessões ou revogar tokens API.
