# 07 — Proyecto borrado que deja su `ascmhl/` (2026-10-03)

**TL;DR.** El owner reporta un proyecto borrado del NAS que se queda en un limbo: en revisión, y `Accept as new version` acaba en `PermissionError: … /ascmhl · retried on the next round · 0 files · 0 B` sin avanzar nunca. Causa: el contenedor sigue viendo la carpeta con su `ascmhl/` (desde el Mac no aparece), así que no era `missing` sino «sellado con todos los ficheros borrados»; el Accept no podía mover `ascmhl/` y el bucle revisión → error → revisión no tenía salida. Arreglo (D67): una carpeta sin ficheros es un proyecto borrado (`missing`, tarjeta con `Retry` y el botón de baja), y una carpeta vacía no se convierte en proyecto nuevo. El botón `Retire` pasa a `Forget permanently` (D68).

## Qué se hizo
- Entrevista con `AskUserQuestion`: qué queda en el NAS («nada» visto desde el Mac), cómo tratar una carpeta sin ficheros (como borrada, sin tocar el NAS) y el nombre del botón.
- `sealer.py`: `_has_files` (recorrido con los patrones de ignore por defecto; un error de lectura cuenta como «tiene ficheros»); `run_scan_cycle` descarta las carpetas vacías sin fila o `missing` (`_is_empty_shell`); `_scan_one` marca `missing` un proyecto seguido que se queda sin ficheros; `_scan_for_job` lanza `_FolderEmpty` y `run_job` cancela el trabajo y marca `missing`; `request_retry` exige ficheros.
- GUI: textos `Forget permanently`, `Forget and download MHL`, «Forgot X», «X forgotten»; la frase de un `missing` habla de los ficheros, no de la carpeta.
- Tests nuevos en `tests/test_missing.py` (carpeta vaciada → `missing` → olvidar → no reaparece hasta tener ficheros; `Retry` con los ficheros de vuelta; carpeta nueva vacía; trabajo que encuentra la carpeta vacía). 440 tests, 100 % de cobertura.

## Hallazgos
- Sin medir: por qué el NAS deja `ascmhl/` y por qué el contenedor (PUID sin root) no puede moverlo. Hipótesis: permisos POSIX de lo que escribió el contenedor frente al usuario de SMB, o la caché de directorios de macOS. Comprobar con `ls -la` por SSH sobre la carpeta (issue abierto). El arreglo no depende de la causa.
- Efecto al actualizar: un proyecto ya seguido que hoy no tenga ningún fichero pasará a `missing` en la primera ronda.

## Vault
Sin concepto nuevo: es política del producto. Se leyeron `MHL Sentinel`, `Archivo`, `Detección de cambios en un volumen de red` y `Permisos de carpeta compartida en QNAP (share, ACL y POSIX)`.

## Siguiente paso
Release patch `v0.6.2`; en el NAS, comprobar que el proyecto del limbo aparece como `missing` y darlo de baja con `Forget permanently`. Después, lo pendiente de la bitácora 06.
