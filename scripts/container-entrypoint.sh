#!/usr/bin/env sh
set -eu
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py check --deploy
exec "$@"
