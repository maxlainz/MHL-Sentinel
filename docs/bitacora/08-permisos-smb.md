# 08 — Permisos de lo que la app crea en el archivo (2026-10-03)

**TL;DR**: con el UMASK 022 de la imagen, `ascmhl/` nacía 755 y el equipo no podía borrar ni mover nada dentro por SMB desde Mac. La imagen pasa a `UMASK 000` y `PGID 100` por defecto (D69); el código ya respetaba el umask (ningún `mode=`, `chmod`, `tempfile` ni `copystat` sobre `/archive`), así que no cambia lógica. Test nuevo `tests/test_permissions.py`.

## Qué se hizo
- Auditoría de escrituras en `/archive`: `mhlwriter` (temporales + `os.replace`, `mkdir` sin modo, `shutil.move` dentro del mismo volumen), `sealer.quarantine_orphan_manifests` (`rename`) y `ascmhl` 1.2 (`os.mkdir` y `open(..., "wb")` sin modo). Todo hereda el umask. `history_mirror` usa `copystat`, pero escribe en `/config`. Sin hard links.
- `deploy/Dockerfile` y `deploy/entrypoint.sh`: defaults `PGID=100`, `UMASK=000`; el resto de la convención (tini + gosu, chown solo de `/config`, `exec` sin root) ya estaba.
- Compose, `.env.example`, `README.md` y `deploy/README.md`: `PUID`, `PGID "100"`, `UMASK "000"` con el porqué.
- `tests/test_permissions.py`: con umask 0, dos generaciones de proyecto, raíz, huérfano apartado y `ascmhl_superseded/` quedan 777/666; y los defaults de imagen, entrypoint y compose.

## Siguiente paso
Publicado como `v0.6.3`: Watchtower lo sube al NAS; en el NAS, abrir permisos una vez de las `ascmhl/` creadas antes con 022 (ver mensaje de la sesión). Lo de bitácora 07 sigue en pie.

## Vault
Ampliada la nota `Permisos de carpeta compartida en QNAP (share, ACL y POSIX)`: borrar exige escritura en la carpeta, umask y modos explícitos que se la saltan, convención `PUID`/`PGID`/`UMASK` de los contenedores de NAS.
