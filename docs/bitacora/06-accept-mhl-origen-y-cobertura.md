# 06 — Accept con MHL 1.x de origen y cobertura al 100 % (2026-10-03)

**TL;DR.** El owner reporta que tras borrar ficheros y pulsar `Accept as new version` el proyecto vuelve a revisión con los mismos ficheros. Causa: el resellado contrastaba otra vez el MHL 1.x de origen (D39), que seguía listando los borrados. Arreglado según D65: Accept acepta la carpeta como está (borrados → aviso; modificados → xxh128 sin heredar el hash de origen) y `Append` solo contrasta los ficheros nuevos. Después, tests para todo: cobertura del 90 % al 100 % de líneas y ramas, umbral en `make ci` (D66).

## Qué se hizo
- Reproducción antes del arreglo: proyecto con `02_OCF/offload.mhl` (MHL 1.1, md5), `Seal`, borrar un clip, scan → `needs_review`, Accept → otra vez `needs_review` con `1 files do not match their legacy MHL 1.x: 02_OCF/b.mov`.
- `sealer._run_seal`: con `accept`, los problemas frente al MHL 1.x pasan por `_accept_legacy_problems` (aviso en el log del trabajo; a los modificados se les quitan los formatos de origen antes de escribir, para no anotar un `verified` falso). `_run_append`: el contraste con el MHL 1.x se limita a `diff.added`.
- Tests del arreglo en `tests/test_sealer.py` (borrado, modificado, y `Seal` normal que sigue bloqueando); los manifiestos pasan `ascmhl-debug verify`.
- Cobertura: cuatro subagentes en paralelo por grupos de módulos, cada uno en ficheros de test propios. 435 tests (225 al empezar la sesión), 100 % de líneas y ramas, sin `pragma`. Ningún bug nuevo en `src/`. `pytest-cov` en el grupo dev; `fail_under = 100` en `pyproject.toml`; `make test` mide cobertura.

## Hallazgos
- `cli.WorkingHoursGate` fija `now_fn=utcnow` como argumento por defecto: parchear `cli.utcnow` no lo alcanza; los tests inyectan una puerta con reloj fijo.
- La CI y el contenedor de desarrollo corren como root: los tests de permisos con `chmod` no fallan; se usa un `os.scandir` falso.
- Varios agentes escribiendo `.coverage` a la vez lo corrompen: en paralelo, `COVERAGE_FILE` propio.

## Vault
Sin concepto nuevo: la política de Accept es del producto, no un concepto de archivo.

## Siguiente paso
`v0.6.1` (patch) y comprobar en el NAS que Watchtower la sube; repetir el Accept que falló. Después, el de la bitácora 04: primer `Seal` de un proyecto pequeño midiendo MB/s.
