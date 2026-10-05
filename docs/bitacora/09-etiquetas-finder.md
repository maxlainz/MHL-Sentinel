# 09 — Etiquetas de Finder por estado (2026-10-05)

**TL;DR**: nueva opción en Ajustes → Appearance (apagada por defecto) que pone una etiqueta de Finder a cada carpeta de proyecto según su estado: verde «MHL OK», amarillo «MHL pendiente», rojo «MHL revisar» (D70). El formato en disco supone Samba con `streams_xattr`; falta comprobarlo en el NAS (#24).

## Qué se hizo
- Entrevista: tres colores, opt-in, implementar sin medir antes; y, tras leer la nota `Caché de directorios del cliente SMB en macOS`, actualizar el mtime de la carpeta madre para que los Mac vean el cambio.
- `finder_tags.py`: lectura y escritura del xattr `user.DosStream.com.apple.metadata:_kMDItemUserTags:$DATA` (plist binario + NUL), fusión con las etiquetas del equipo, `TagSync` que solo escribe cuando cambia un estado y avisa una vez de cada fallo.
- `supervisor.py`: la sincronización corre en el tick, solo fuera del horario laboral (D33).
- `config.py`, Ajustes: `finder_tags: false`.
- Tests: `tests/test_finder_tags.py` (xattrs simulados para que pasen en macOS; uno real sobre ext4 en Linux), tick y formulario.

## Predicción (sin medir)
En el NAS, una etiqueta puesta a mano desde un Mac aparece como `user.DosStream.com.apple.metadata:_kMDItemUserTags:$DATA` empezando por `bplist` y acabando en `00`, sin ficheros `._*`. Comando y criterio en #24.

## Siguiente paso
Medir en el NAS (#24) antes de activar la opción allí; si el formato difiere, ajustar `XATTR`. Lo demás de la bitácora 08 sigue en pie.

## Vault
Nota nueva `Etiquetas de Finder (atributo _kMDItemUserTags)`; añadida al mapa `Archivo`.
