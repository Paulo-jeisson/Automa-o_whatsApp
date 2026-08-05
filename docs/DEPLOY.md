# Deploy de produção — iaatende.app

Este procedimento usa uma VPS Hostinger com Linux, Nginx, Gunicorn e PostgreSQL.
SQLite é suportado somente no desenvolvimento. A Cloudflare é usada apenas para
DNS, proxy, SSL e segurança: não execute Django em hospedagem compartilhada ou
Cloudflare Workers.

## 1. Preparar o servidor

Instale Python 3.12+, PostgreSQL, Nginx e os clientes `pg_dump`/`pg_restore`.
Crie o usuário de sistema e os diretórios:

```bash
sudo useradd --system --home /opt/iaatende --shell /usr/sbin/nologin iaatende
sudo install -d -o iaatende -g www-data /opt/iaatende
sudo install -d -m 0750 -o root -g iaatende /etc/iaatende
sudo install -d -m 0700 -o iaatende -g iaatende /var/backups/iaatende
```

Clone ou copie uma versão tagueada para `/opt/iaatende`, crie o ambiente virtual
e instale as dependências:

```bash
sudo -u iaatende python3 -m venv /opt/iaatende/venv
sudo -u iaatende /opt/iaatende/venv/bin/pip install -r /opt/iaatende/requirements.txt
```

## 2. PostgreSQL

Crie banco e usuário exclusivos. Não coloque a senha no repositório:

```sql
CREATE ROLE iaatende LOGIN PASSWORD 'senha-gerada-em-cofre';
CREATE DATABASE iaatende OWNER iaatende;
```

Crie `/etc/iaatende/iaatende.env` com permissão `0600`. Use
[`.env.example`](../.env.example) como referência e use, no mínimo:

```env
APP_ENV=production
DEBUG=False
PUBLIC_BASE_URL=https://iaatende.app
SITE_URL=https://iaatende.app
ALLOWED_HOSTS=iaatende.app,www.iaatende.app
CSRF_TRUSTED_ORIGINS=https://iaatende.app,https://www.iaatende.app
ASAAS_CHECKOUT_SUCCESS_URL=https://iaatende.app/assinatura/retorno/
ASAAS_CHECKOUT_CANCEL_URL=https://iaatende.app/planos/
EVOLUTION_WEBHOOK_URL=https://iaatende.app/webhooks/evolution/
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=True
SECURE_HSTS_PRELOAD=False
```

Preencha separadamente segredos, SMTP, Asaas, Evolution e `POSTGRES_*`. Durante
homologação mantenha `SECURE_HSTS_PRELOAD=False`. Troque para `True` somente após
validar HTTPS em todos os subdomínios e entender o caráter duradouro do preload.

## 3. Aplicação

Execute como o usuário da aplicação:

```bash
sudo -u iaatende APP_DIR=/opt/iaatende /opt/iaatende/scripts/deploy.sh
```

O script instala dependências, executa migrations, coleta arquivos estáticos e
roda o check do Django usando `app.settings_production`.

## 4. Gunicorn e Nginx

Instale na origem um Cloudflare Origin Certificate válido para `iaatende.app` e
`www.iaatende.app` nos caminhos documentados no arquivo Nginx. Depois:

```bash
sudo cp deploy/systemd/iaatende.service /etc/systemd/system/
sudo cp deploy/nginx/iaatende.conf /etc/nginx/sites-available/iaatende
sudo ln -s /etc/nginx/sites-available/iaatende /etc/nginx/sites-enabled/iaatende
sudo systemctl daemon-reload
sudo systemctl enable --now iaatende
sudo nginx -t
sudo systemctl reload nginx
```

Valide:

```bash
curl --fail https://iaatende.app/health/
```

A resposta saudável informa `application=ok`, `database=ok` e
`database_engine=postgresql`.

## 5. Cloudflare

Crie manualmente os registros, mantendo o proxy habilitado após validar a origem:

```text
A      @      IP_PÚBLICO_DA_VPS
CNAME  www    iaatende.app
AAAA   @      IPv6_DA_VPS       # apenas se a VPS possuir IPv6 configurado
```

Em **SSL/TLS**, mantenha obrigatoriamente **Full (strict)**. Nunca use Flexible.
A origem Nginx também precisa de certificado válido. O `www` é redirecionado com
301 para `https://iaatende.app` na origem.

## 6. Backup automático

Instale o arquivo de cron após revisar horários e caminhos:

```bash
sudo cp deploy/cron/iaatende-backup /etc/cron.d/iaatende-backup
sudo chmod 0644 /etc/cron.d/iaatende-backup
```

O backup usa o formato custom do PostgreSQL, valida o arquivo com
`pg_restore --list` e mantém 14 dias por padrão. Armazene uma cópia criptografada
fora do servidor.

Execução manual:

```bash
set -a
. /etc/iaatende/iaatende.env
set +a
BACKUP_DIR=/var/backups/iaatende ./scripts/backup_postgres.sh
```

## 7. Restauração

Restaure primeiro em um banco temporário, nunca diretamente sobre produção sem
janela de manutenção e backup recente:

```bash
createdb iaatende_restore_test
export POSTGRES_DB=iaatende_restore_test
export CONFIRM_RESTORE=iaatende_restore_test
./scripts/restore_postgres.sh /var/backups/iaatende/iaatende-DATA.dump
DJANGO_SETTINGS_MODULE=app.settings_production python manage.py check
dropdb iaatende_restore_test
```

O script exige que `CONFIRM_RESTORE` seja exatamente igual ao banco de destino,
valida o arquivo antes da operação e interrompe no primeiro erro.

## 8. Rollback

Mantenha releases anteriores em diretórios separados. Para rollback:

1. interrompa novas alterações;
2. faça backup do banco;
3. avalie a reversibilidade das migrations;
4. aponte `/opt/iaatende` para a release anterior;
5. reinicie `iaatende.service`;
6. valide `/health/` e um fluxo controlado.

Nunca reverta migrations destrutivas sem procedimento específico e backup
restaurável.
