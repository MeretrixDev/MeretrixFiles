#!/bin/sh
set -e

# Миграции запускает только один сервис (приложение), иначе два контейнера
# могли бы накатывать их одновременно
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "Applying database migrations..."
    alembic upgrade head
fi

exec "$@"