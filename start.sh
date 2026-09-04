#!/usr/bin/env bash

set -o errexit

echo "==> Running database migrations..."
python manage.py migrate --no-input

echo "==> Synchronizing persistent media and initial data..."
python manage.py sync_persistent_data

echo "==> Verifying administrator account..."
python manage.py ensure_admin

echo "==> Starting web application server..."
exec gunicorn clout.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1 --timeout 300
