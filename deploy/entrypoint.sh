#!/bin/sh
# Convención PUID/PGID de las apps de NAS (D36). Si el contenedor arranca sin root (compose `user:`), no hace nada.
set -eu
if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-1000}"; PGID="${PGID:-1000}"; UMASK="${UMASK:-022}"
  if ! getent group "$PGID" >/dev/null 2>&1; then groupadd -g "$PGID" sentinel; fi
  if ! getent passwd "$PUID" >/dev/null 2>&1; then useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin sentinel; fi
  chown "$PUID:$PGID" /config          # solo /config; /archive se respeta tal cual (sus permisos vienen del montaje)
  umask "$UMASK"
  exec gosu "$PUID:$PGID" "$@"
fi
exec "$@"
