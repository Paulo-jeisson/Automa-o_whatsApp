#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
    echo "Uso: $0 /caminho/backup.dump" >&2
    exit 2
fi

: "${POSTGRES_DB:?POSTGRES_DB não definida}"
: "${POSTGRES_USER:?POSTGRES_USER não definida}"
: "${POSTGRES_HOST:?POSTGRES_HOST não definida}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD não definida}"
: "${CONFIRM_RESTORE:?Defina CONFIRM_RESTORE com o nome exato do banco de destino}"

if [[ "$CONFIRM_RESTORE" != "$POSTGRES_DB" ]]; then
    echo "CONFIRM_RESTORE não corresponde a POSTGRES_DB; restauração cancelada." >&2
    exit 2
fi

backup_file="$1"
if [[ ! -f "$backup_file" ]]; then
    echo "Backup não encontrado: $backup_file" >&2
    exit 2
fi

POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export PGPASSWORD="$POSTGRES_PASSWORD"
pg_restore --list "$backup_file" >/dev/null
pg_restore \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --exit-on-error \
    --host="$POSTGRES_HOST" \
    --port="$POSTGRES_PORT" \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    "$backup_file"

echo "Restauração concluída em $POSTGRES_DB."
