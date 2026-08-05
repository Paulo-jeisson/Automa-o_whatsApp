#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_DB:?POSTGRES_DB não definida}"
: "${POSTGRES_USER:?POSTGRES_USER não definida}"
: "${POSTGRES_HOST:?POSTGRES_HOST não definida}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD não definida}"

POSTGRES_PORT="${POSTGRES_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/iaatende}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="$BACKUP_DIR/iaatende-$timestamp.dump"

install -d -m 0700 "$BACKUP_DIR"
export PGPASSWORD="$POSTGRES_PASSWORD"
pg_dump \
    --format=custom \
    --no-owner \
    --no-privileges \
    --host="$POSTGRES_HOST" \
    --port="$POSTGRES_PORT" \
    --username="$POSTGRES_USER" \
    --file="$destination" \
    "$POSTGRES_DB"
chmod 0600 "$destination"
pg_restore --list "$destination" >/dev/null
find "$BACKUP_DIR" -type f -name 'iaatende-*.dump' -mtime "+$RETENTION_DAYS" -delete

echo "$destination"
