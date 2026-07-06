#!/bin/sh
set -e

# Apply DB migrations before serving (safe to run on every start; no-op when up to date).
python manage.py migrate --noinput

# Seed demo categories + listings (idempotent by name/title; safe on every start).
python manage.py seed || true

# gthread workers: the live-bid SSE stream holds a connection open for the life of the
# browser tab. Threads (not whole sync workers) service those long-lived requests, so
# many viewers can watch an auction without starving request handling. --timeout 0
# disables the worker timeout that would otherwise kill idle-but-open SSE streams.
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --worker-class gthread \
    --workers "${GUNICORN_WORKERS:-3}" \
    --threads "${GUNICORN_THREADS:-16}" \
    --timeout "${GUNICORN_TIMEOUT:-0}" \
    --access-logfile -
