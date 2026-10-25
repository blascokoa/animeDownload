#!/bin/sh
# Atajo para usar animedl con Docker:  ./animedl.sh add <url>   ./animedl.sh list
# Si el daemon está en marcha (docker compose up -d) el comando se ejecuta dentro de ese
# contenedor; si no, en uno temporal.
cd "$(dirname "$0")" || exit 1
if [ -n "$(docker compose ps -q --status running animedl 2>/dev/null)" ]; then
    exec docker compose exec animedl entrypoint.sh "$@"
fi
exec docker compose run --rm animedl "$@"
