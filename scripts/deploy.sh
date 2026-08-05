#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/iaatende}"
PYTHON="${PYTHON:-$APP_DIR/venv/bin/python}"
PIP="${PIP:-$APP_DIR/venv/bin/pip}"

cd "$APP_DIR"
"$PIP" install --requirement requirements.txt
DJANGO_SETTINGS_MODULE=app.settings_production "$PYTHON" manage.py migrate --noinput
DJANGO_SETTINGS_MODULE=app.settings_production "$PYTHON" manage.py collectstatic --noinput
DJANGO_SETTINGS_MODULE=app.settings_production "$PYTHON" manage.py check --deploy
DJANGO_SETTINGS_MODULE=app.settings_production "$PYTHON" manage.py release_check

echo "Deploy preparado. Reinicie iaatende.service após revisar migrations e health check."
