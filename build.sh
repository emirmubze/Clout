#!/usr/bin/env bash
set -o errexit

echo "==> Installing project dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Collecting static assets..."
python manage.py collectstatic --no-input