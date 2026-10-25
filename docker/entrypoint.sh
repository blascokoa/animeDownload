#!/bin/sh
# Runs animedl as PUID:PGID so the videos on your disk belong to your user. Folders are only
# chowned when that user cannot write to them (so your media folders keep their owner).
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data /series /movies /downloads
    chown -R "$PUID:$PGID" /data 2>/dev/null || true
    for dir in /series /movies /downloads; do
        if ! setpriv --reuid="$PUID" --regid="$PGID" --clear-groups test -w "$dir"; then
            chown "$PUID:$PGID" "$dir" 2>/dev/null || true
        fi
    done
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups animedl "$@"
fi
exec animedl "$@"
