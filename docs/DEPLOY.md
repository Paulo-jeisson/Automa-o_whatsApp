# Deploy de produção

Este procedimento usa Linux, Nginx, Gunicorn e PostgreSQL. SQLite é suportado
somente pelo ambiente de desenvolvimento.

## 1. Preparar o servidor

Instale Python 3.12+, PostgreSQL, Nginx e os clientes `pg_dump`/`pg_restore`.
Crie o usuário de sistema e os diretórios:

```bash
sudo useradd --system --home /opt/zapfluxo --shell /usr/sbin/nologin zapfluxo
sudo install -d -o zapfluxo -g www-data /opt/zapfluxo
sudo install -d -m 0750 -o root -g zapfluxo /etc/zapfluxo
sudo install -d -m 0700 -o zapfluxo -g zapfluxo /var/backups/zapfluxo
```

Clone ou copie uma versão tagueada para `/opt/zapfluxo`, crie o ambiente virtual
e instale as dependências:

```bash
sudo -u zapfluxo python3 -m venv /opt/zapfluxo/venv
sudo -u zapfluxo /opt/zapfluxo/venv/bin/pip install -r /opt/zapfluxo/requirements.txt
```

## 2. PostgreSQL

Crie banco e usuário exclusivos. Não coloque a senha no repositório:

```sql
CREATE ROLE zapfluxo LOGIN PASSWORD 'senha-gerada-em-cofre';
CREATE DATABASE zapfluxo OWNER zapfluxo;
```

Crie `/etc/zapfluxo/zapfluxo.env` com permissão `0600`. Use
[`.env.example`](../.env.example) como referência, defina `DEBUG=False`,
hosts/origens reais e todas as variáveis `POSTGRES_*`.

## 3. Aplicação

Execute como o usuário da aplicação:

```bash
sudo -u zapfluxo APP_DIR=/opt/zapfluxo /opt/zapfluxo/scripts/deploy.sh
```

O script instala dependências, executa migrations, coleta arquivos estáticos e
roda o check do Django usando `app.settings_production`.

## 4. Gunicorn e Nginx

Copie os arquivos de referência, substitua `exemplo.com.br` pelo domínio real e
configure TLS com o provedor escolhido:

```bash
sudo cp deploy/systemd/zapfluxo.service /etc/systemd/system/
sudo cp deploy/nginx/zapfluxo.conf /etc/nginx/sites-available/zapfluxo
sudo ln -s /etc/nginx/sites-available/zapfluxo /etc/nginx/sites-enabled/zapfluxo
sudo systemctl daemon-reload
sudo systemctl enable --now zapfluxo
sudo nginx -t
sudo systemctl reload nginx
```

Valide:

```bash
curl --fail https://DOMINIO/health/
```

A resposta saudável informa `application=ok`, `database=ok` e
`database_engine=postgresql`.

## 5. Backup automático

Instale o arquivo de cron após revisar horários e caminhos:

```bash
sudo cp deploy/cron/zapfluxo-backup /etc/cron.d/zapfluxo-backup
sudo chmod 0644 /etc/cron.d/zapfluxo-backup
```

O backup usa o formato custom do PostgreSQL, valida o arquivo com
`pg_restore --list` e mantém 14 dias por padrão. Armazene uma cópia criptografada
fora do servidor.

Execução manual:

```bash
set -a
. /etc/zapfluxo/zapfluxo.env
set +a
BACKUP_DIR=/var/backups/zapfluxo ./scripts/backup_postgres.sh
```

## 6. Restauração

Restaure primeiro em um banco temporário, nunca diretamente sobre produção sem
janela de manutenção e backup recente:

```bash
createdb zapfluxo_restore_test
export POSTGRES_DB=zapfluxo_restore_test
export CONFIRM_RESTORE=zapfluxo_restore_test
./scripts/restore_postgres.sh /var/backups/zapfluxo/zapfluxo-DATA.dump
DJANGO_SETTINGS_MODULE=app.settings_production python manage.py check
dropdb zapfluxo_restore_test
```

O script exige que `CONFIRM_RESTORE` seja exatamente igual ao banco de destino,
valida o arquivo antes da operação e interrompe no primeiro erro.

## 7. Rollback

Mantenha releases anteriores em diretórios separados. Para rollback:

1. interrompa novas alterações;
2. faça backup do banco;
3. avalie a reversibilidade das migrations;
4. aponte `/opt/zapfluxo` para a release anterior;
5. reinicie `zapfluxo.service`;
6. valide `/health/` e um fluxo controlado.

Nunca reverta migrations destrutivas sem procedimento específico e backup
restaurável.
