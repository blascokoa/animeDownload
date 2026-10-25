#!/bin/sh
# Atajo para usar animedl dentro de Docker:  ./animedl.sh add <url>   ./animedl.sh list
cd "$(dirname "$0")" && exec docker compose run --rm animedl "$@"
