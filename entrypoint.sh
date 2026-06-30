#!/bin/sh
set -e

# Apply DB migrations before serving (safe to run on every start; no-op when up to date).
python manage.py migrate --noinput

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-3}" \
    --access-logfile -
