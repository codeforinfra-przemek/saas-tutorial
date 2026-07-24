#!/bin/sh
set -eu

RUN_PORT="${PORT:-8000}"

python manage.py migrate --no-input
exec gunicorn saashome.wsgi:application --bind "[::]:${RUN_PORT}"
