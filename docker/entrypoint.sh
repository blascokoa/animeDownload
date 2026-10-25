#!/bin/sh
# Runs animedl as PUID:PGID so the videos on your disk belong to your user, and makes sure
# the data directory (database, config) is writable by that user.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data /downloads
    chown "$PUID:$PGID" /data /downloads 2>/dev/null || true
    chown -R "$PUID:$PGID" /data 2>/dev/null || true
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups animedl "$@"
fi
exec animedl "$@"
