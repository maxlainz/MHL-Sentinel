#!/bin/sh
# Convención PUID/PGID/UMASK de las apps de NAS (D36, D69). Si el contenedor arranca sin root (compose `user:`), no hace nada.
# UMASK 000 por defecto: lo que la app crea en /archive (ascmhl/, ascmhl_superseded/) queda 777/666 y el equipo puede
# borrarlo o moverlo por SMB desde Mac y Windows; el acceso lo deciden los permisos de share de QTS, no los bits POSIX.
set -eu
if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-1000}"; PGID="${PGID:-100}"; UMASK="${UMASK:-000}"
  if ! getent group "$PGID" >/dev/null 2>&1; then groupadd -g "$PGID" sentinel; fi
  if ! getent passwd "$PUID" >/dev/null 2>&1; then useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin sentinel; fi
  chown "$PUID:$PGID" /config          # solo /config; /archive se respeta tal cual (sus permisos vienen del montaje)
  umask "$UMASK"
  exec gosu "$PUID:$PGID" "$@"
fi
exec "$@"
