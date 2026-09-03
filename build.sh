#!/usr/bin/env bash

set -o errexit

python manage.py collectstatic --no-input

python manage.py migrate --no-input

python manage.py sync_persistent_data

python manage.py ensure_admin