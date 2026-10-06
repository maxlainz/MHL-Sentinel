# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Versionado: [SemVer](https://semver.org/lang/es/). Categorías: Añadido · Cambiado · Corregido · Decidido · Medido · Eliminado.

## [Unreleased]

### Cambiado
- Portada pulida (D77): el estado pasa a una barra superior fija, en todas las pantallas (pastilla «Archive OK» o «Archive not reachable», horario laboral con «hasta HH:MM», última ronda y, si hay, el trabajo en marcha con su porcentaje; la fecha de la raíz queda como tooltip de «Last round»); si el archivo no responde, una franja roja bajo la barra. Desaparece el bloque grande de estado.
- Portada: título «Projects» con una frase de recuento por grupo, buscador de ancho medio con botón «Search» y nota de resultados, y pastillas por grupo (All, Needs decision, Not sealed, Queue, Sealed) que muestran un solo grupo y recuerdan la elección. Cada sección lleva a la derecha para qué sirve y las filas van en columnas (estado, nombre y carpeta con el estado debajo, tamaño, botón).

## [0.9.0] - 2026-10-06 — Qué hay archivado

### Añadido
- Ficha de proyecto: panel «What's archived» con ficheros, tamaño, fecha de sellado, última verificación y la próxima aproximada; desglose por carpeta de primer nivel con barra proporcional; recuento por extensión; y lista de ficheros plegable con ruta, tamaño, xxh128 y filtro por nombre. Sale de la DB, sin leer el NAS (D75).
- Historial de manifiestos: cada generación indica los ficheros nuevos, modificados y borrados respecto a la anterior; las generaciones de añadido (parciales) no cuentan borrados (D75).

### Cambiado
- Portada: nuevo orden Needs your decision, Not sealed yet, Queue (antes «In progress»), Sealed y una línea final. Los sellados salen a la vista, el más reciente primero (por fecha de sellado), con los primeros visibles y el resto plegado; desaparece el plegable «All projects»; los ignorados quedan plegados en la línea final (D76).
- Portada: buscador siempre visible encima de todo; filtra por nombre en el navegador todas las secciones (también los ignorados), abre los plegables con coincidencias, oculta las secciones vacías y sobrevive a los refrescos en vivo. Esc lo limpia (D76).

### Decidido
- D75, D76: ver `docs/decisiones.md`.

## [0.8.0] - 2026-10-06 — Sellar ahora

### Añadido
- `Seal now` / `Accept now`: un Seal o Accept que espera a que acabe la jornada se puede arrancar ya, en la ficha del proyecto y en la Bandeja. Avisa de los GB que va a leer del NAS en horario laboral y pide confirmar; una vez en marcha no se pausa y `Cancel` sigue valiendo (D73).
- `Seal now` / `Update now` también en los trabajos que encola la app sola (proyecto nuevo terminado, ficheros añadidos a uno sellado), sin `Cancel` (D74).
- `Update now` en un proyecto con ficheros nuevos: lo pone al día sin esperar al reposo, avisando de que una copia en curso quedaría sellada a medias; se puede cancelar (D74).
- `Verify now` también en proyectos con ficheros nuevos; además de verificar, sella lo nuevo, y lo avisa (D74).

### Decidido
- D73, D74: ver `docs/decisiones.md`.

## [0.7.2] - 2026-10-06 — Etiquetas de Finder sin falsear

### Cambiado
- Etiquetas de Finder: con la opción encendida, la app es dueña de las etiquetas de cada carpeta de proyecto. Borra las que ya hubiera (también las del equipo) y corrige en la siguiente pasada fuera del horario laboral cualquier etiqueta cambiada a mano, así que la etiqueta siempre dice el estado real. Con la opción apagada ya no quita nada (D72).

### Decidido
- D72: ver `docs/decisiones.md`.

## [0.7.1] - 2026-10-05 — Mismo código que 0.7.0

### Corregido
- El commit de integración del #27 entró en `main` con un trailer de atribución (D64) y la CI de `main` quedó en rojo; `main` está protegida contra force-push, así que no se reescribe: esta versión, con el mismo código que la 0.7.0, deja `main` en verde. Al integrar un PR siempre se da un cuerpo explícito al commit.

### Decidido
- El tag `v0.7.0`, puesto por error antes del commit de release, se mueve a ese commit como excepción única (D71).

## [0.7.0] - 2026-10-05 — Etiquetas de Finder

### Añadido
- Etiquetas de Finder por estado en las carpetas de proyecto, opcionales en Ajustes → Appearance: verde «MHL OK» (sellado), amarillo «MHL pendiente» (por sellar o sellando), rojo «MHL revisar» (pide revisión o error). Se escriben fuera del horario laboral, respetan las etiquetas del equipo y se quitan al apagar la opción; la app actualiza la fecha de la carpeta madre para que los Mac refresquen. El formato supone Samba con `streams_xattr`; falta comprobarlo en el NAS (D70).

## [0.6.4] - 2026-10-03 — Versión correcta en la imagen
### Corregido
- La imagen `0.6.3` se construyó desde un tag puesto antes del commit de la release y se presentaba como `0.6.2`; `0.6.4` lleva el mismo código con la versión correcta. El tag `v0.6.3` no se reescribe (norma `git.md`).

## [0.6.3] - 2026-10-03 — Permisos de lo que la app crea en el archivo
### Cambiado
- La imagen escribe por defecto con `UMASK 000` y `PGID 100` (everyone en QNAP): las carpetas y ficheros que la app crea en el archivo (`ascmhl/`, `ascmhl_superseded/`) quedan 777/666 y el equipo puede borrarlos o moverlos por SMB desde Mac y Windows. Compose, `.env.example` y README documentan `PUID`, `PGID` y `UMASK` (D69).

### Decidido
- D69: ver `docs/decisiones.md`.

## [0.6.2] - 2026-10-03 — Proyecto borrado que deja su ascmhl/
### Corregido
- Un proyecto borrado del NAS cuya carpeta sigue existiendo solo con su `ascmhl/` ya no se queda en bucle (revisión → `Accept` → `PermissionError` → error → revisión): una carpeta sin ficheros cuenta como proyecto desaparecido (`missing`), una carpeta vacía no aparece como proyecto nuevo y un trabajo que encuentra la carpeta vacía se cancela (D67).

### Cambiado
- El botón `Retire` se llama `Forget permanently` (y `Forget and download MHL`; «Forgot X» en el log) (D68).

### Decidido
- D67, D68: ver `docs/decisiones.md`.

## [0.6.1] - 2026-10-03 — Accept con MHL 1.x de origen y cobertura al 100 %
### Añadido
- Tests para todo el código: cobertura al 100 % de líneas y ramas (de 90 %), 435 tests (de 225). `make test` falla por debajo del 100 % (D66); `pytest-cov` en el grupo dev.
- `make attribution-check` en `make ci` y en la CI de cada PR (también al editar su descripción): falla si un commit o la descripción llevan trailers de atribución o enlaces de sesión (D64). Hook `PreToolUse` equivalente para los agentes.

### Corregido
- `Accept as new version` ya no vuelve a revisión cuando los ficheros borrados o modificados figuraban en un MHL 1.x de origen: los borrados quedan como aviso y los modificados se sellan sin heredar el hash de origen; `Append` solo contrasta los ficheros nuevos con el MHL 1.x (D65).

### Cambiado
- Historial de `main` reescrito para quitar atribuciones de los commits de #13 y `v0.6.0`; el tag `v0.6.0` apunta al commit nuevo, con el mismo contenido (D64).

### Decidido
- D64, D65, D66: ver `docs/decisiones.md`.

## [0.6.0] - 2026-10-02 — Proyectos desaparecidos
### Añadido
- Proyectos desaparecidos (D58–D62): estado `missing` desde el primer scan en que la carpeta falta; tarjeta en «Needs your decision» con `Retire` (diálogo: `Retire`, `Retire and download MHL`, `Cancel`; borra la fila, la caché de hashes y el espejo del historial, deja una línea en el log) y `Retry` (comprueba la carpeta al momento y, si está, recupera el estado y encola una verificación). Cortafuegos: una raíz que lista cero proyectos cuenta como archivo inaccesible. Una carpeta movida o renombrada con la misma cadena `ascmhl` se reconoce como el mismo proyecto.
- Espejo del historial `ascmhl/` de cada proyecto en `<config>/history/` tras cada generación y en el scan si difiere (D59); `GET /projects/{id}/history.zip`.
- `Verify now` en la ficha de un proyecto sellado (D63): verificación manual que salta el horario laboral tras un aviso de rendimiento; cancelable.

### Cambiado
- La carpeta ausente ya no es `error "folder missing"` reintentado sin fin; esquema de la DB a `user_version` 2 (`missing_since`, `state_before_missing`, `jobs.bypass_hours`), migración automática al arrancar.

### Corregido
- Tres tests dependían de la fecha real y fallaban al día siguiente de escribirse; ahora son deterministas.
- Revisión adversarial antes del PR: una carpeta que discovery no lista pero sigue en disco (listado fallido en SMB) ya no pasa a `missing` (se confirma con `lstat`); `Retire` espera a que pare un trabajo aún en marcha del proyecto; el trabajo en curso cuyo proyecto desaparece o se mueve se para; un trabajo pausado por el horario cede el hilo a un `Verify now`; el espejo nunca mezcla dos historiales (se aparta el antiguo) y un cerrojo serializa sus escrituras; el log muestra «Verified» y no «Verify now» al terminar; `Retire` funciona sin JS; avisos de `Retry`/`Verify now` coherentes con lo que pasa.

### Decidido
- D58–D63: ver `docs/decisiones.md` (entrevista del 2026-10-02).

## [0.5.1] - 2026-10-01 — Cancel en marcha
### Añadido
- `Cancel` también para un `Seal` o `Accept as new version` que ya se está leyendo, incluso pausado por el horario laboral (#10, D57): la app para en el siguiente fichero, no escribe nada, conserva los hashes ya calculados para el próximo `Seal` y devuelve el proyecto a `unsealed` o a revisión con su motivo.

### Decidido
- D57: Cancel en marcha mediante un evento de cancelación por trabajo en el supervisor; solo `seal`/`accept` manuales, nunca verificaciones ni `append` automáticos.

## [0.5.0] - 2026-10-01 — GUI «Bandeja»
### Añadido
- Ajuste de tema en Ajustes → *Appearance*: `Auto (follow the system)`, `Light` o `Dark` (D56). Campo `theme` en `config.yaml` (env `MHLS_THEME`), `auto` por defecto.

### Cambiado
- GUI nueva «Bandeja», la propuesta D elegida por el owner (D54, #5): pantalla principal como bandeja (frase de estado · decidir · sin sellar · en marcha · todo lo demás plegado), `Seal` y `Cancel` de un clic en la bandeja (`?from=inbox`), log de actividad (`GET /fragments/activity`), detalle con frase de estado e historial en línea de tiempo. Acabado claro y cálido, contenedor de hasta 1440 px, modo oscuro solo si el sistema lo pide, textos con contraste AA. Hoja y JS propios; se retira Pico.css. Documento en `docs/propuestas/frontend-D.md`.

### Corregido
- Revisión adversarial de la GUI (#5): el titular ya no dice «Every project is sealed» con proyectos en cola o leyéndose; el log marca en rojo las verificaciones con problemas y los sellados que acabaron en revisión; «Earlier» se ordena por hora de fin; la ficha se refresca al empezar y terminar un trabajo; el filtro conserva foco y cursor en los refrescos; la fecha de verificación vencida se anuncia como tal; mismo criterio cuando todo está ignorado.

### Decidido
- D55: `main` protegida en GitHub; todo cambio entra por PR con CI verde, releases incluidas. Norma `rama-main-protegida.md`.
- D56: el tema de la GUI se configura en Ajustes (auto / light / dark, auto por defecto) y vale para todos los navegadores del owner.

## [0.4.3] - 2026-10-01 — patch: Cancel
### Añadido
- Botón `Cancel` en la tarjeta del proyecto: retira un `Seal` o `Accept as new version` que espera en cola y devuelve el proyecto a su estado anterior (D53, #4). `POST /projects/{id}/cancel`.

### Decidido
- D53: solo se cancelan trabajos manuales en cola; los automáticos no (el siguiente ciclo los reencolaría) y los que están en marcha tampoco.

### Medido
- H19: primera pasada sobre el archivo real en el NAS: 95 proyectos, 32 050 ficheros, 11,8 TB, ~34 s (predicción de tiempo acertada, la de ficheros no). Bitácora 02.

## [0.4.2] - 2026-10-01
### Añadido
- Operación con auto-actualización (D52): recuperación al arrancar (temporales y manifiestos huérfanos apartados antes de cualquier trabajo, también en `run-once`), parada por debajo de 10 s (SIGTERM de Docker/Watchtower), checkpoint del WAL al cerrar, hueco de migraciones futuras de la DB; test `tests/test_recreate.py` (SIGKILL a mitad de sellado y reinicio sobre el mismo `/config`: trabajo reencolado, una sola generación, `verify` exit 0). Watchtower opcional en `deploy/docker-compose.yml`; sección de auto-actualización en `deploy/README.md` y README.
### Decidido
- D52: cada release mueve `latest` (norma `ci.md`); el contenedor debe aguantar ser recreado a lo bruto.

## [0.4.1] - 2026-10-01
### Corregido
- La imagen `0.4.0` no arrancaba (`ModuleNotFoundError: mhl_sentinel`): `uv sync` instalaba el paquete en modo editable apuntando a `/app/src`, que no existe en la etapa final. Ahora se instala con `--no-editable`. Lo detectó la prueba de humo del pipeline (H18).

## [0.4.0] - 2026-10-01 — Hito 4: raíz de referencias y verificación periódica (MVP)
### Añadido
- Hito 4: `rootmanifest.py` (historial de solo referencias en la raíz, D29/D43/D51), trabajo `verify` (relectura completa cada 90 días escalonada, regla mtime para distinguir corrupción, generación `verified` como prueba, D8/D23) y planificador de mantenimiento; GUI con fecha de la raíz, «verified» por proyecto y tabla de resultados por fichero en revisión. 155 tests. Prueba de humo del contenedor publicado en `release.yml`.
### Decidido
- D51: la raíz reinicia su historial cuando una referencia deja de existir; referencia todo proyecto con historial.
### Medido
- H14: la referencia resuelve las referencias de todas las generaciones y revienta con un assert si alguna apunta a un manifiesto inexistente. H15: compara patrones de ignore con rutas absolutas (`/_*` no casa). H16: `verify` en la raíz da exit 21 ante un fichero suelto entre proyectos. H17: Docker Desktop no respondía por permisos de macOS de VSCode (resuelto por el owner); la imagen se prueba además en GitHub.

## [0.3.0] - 2026-10-01 — Hitos 2 y 3: daemon, Docker y GUI
### Añadido
- Hito 2, daemon: `events.py` (bus entre hilos hacia SSE), `settings_ref.py`, `supervisor.py` (bucle asyncio + hilo hasher con puerta por horario laboral, D33; `Run scan now` en cualquier momento, D26; recuperación de trabajos al arrancar), `server.py` (uvicorn con SIGTERM limpio: termina el bloque, reencola y sale con 0), `mhl-sentinel serve`. Imagen `v0.1.0` publicada en GHCR por `release.yml` (amd64 + arm64) como prueba del pipeline.
- Hito 3, GUI: `web/` (FastAPI + Jinja2 + HTMX + SSE + Pico.css) con la pantalla única (D11), detalle de revisión (D46), Ajustes (D45) con guardado a YAML y recarga en caliente, `/healthz`, `/api/status`, `/api/projects`, `/events`. Probado de extremo a extremo sobre los fixtures: `Seal` desde la GUI → trabajo → manifiesto aceptado por `ascmhl-debug verify`.

## [0.1.0] - 2026-10-01 — Hito 1: núcleo sin GUI
### Añadido
- Hito 1, núcleo sin GUI: `config.py` (defaults < YAML < env `MHLS_`, horario laboral y zona horaria), `db.py` (SQLite WAL, rechazo de sistemas de ficheros de red), `schedule.py`, `discovery.py` (niveles, D18/D19/D49), `scanner.py` (snapshot y diff con regla de estabilidad), `legacy_mhl.py` (MHL 1.x con normalización de `xxhash64`), `hasher.py` (un lector, pausa/parada, caché por fichero), `sealer.py` (máquina de estados, seal/append/accept, comprobación y herencia de hashes legacy, manifiestos huérfanos), CLI `mhl-sentinel run-once|projects|settings`. 105 tests, e2e sobre fixtures validado por `ascmhl-debug verify`.
- Empaquetado para el hito 2: `deploy/Dockerfile` multi-stage, `entrypoint.sh` PUID/PGID, `docker-compose.yml`, guía QNAP, `release.yml` (GHCR multi-arch), assets de GUI vendorizados (Pico 2.1.1, htmx 2.0.11).
### Decidido
- D44–D50: MVP hasta el hito 4; bocetos de Ajustes y revisión; issues upstream aplazados; generaciones parciales al añadir (D48); detección solo por niveles (D49); historial apartado en `ascmhl_superseded/` (D50).
### Medido
- H13: la referencia carga `ascmhl/` en recursivo; un historial viejo dentro rompe `info`/`verify`.

## [0.0.1] - 2026-10-01 — Hito 0: arranque
### Añadido
- Proyecto Python (`pyproject.toml`, `uv`, Python 3.12) con `ascmhl==1.2` fijado como dependencia y oráculo (D1, D36); `mhl_sentinel.__version__` desde los metadatos del paquete.
- `make fixtures`: generador determinista de un archivo sintético (`scripts/make_fixtures.py`, salida gitignored) con proyectos de plantilla, carpetas a ignorar, basura de macOS y dos MHL 1.x de origen (xxhash64be y md5) con hashes reales; test de determinismo.
- Targets reales del Makefile (`setup`, `lint`, `format`, `typecheck`, `test`) sobre `uv`; `ci.yml` en push/PR (D38).
- Skill `release` (modelo: LMT-Composer) adaptada al stack.
- `src/mhl_sentinel/mhlwriter.py`: escritura de generaciones ASC MHL con hashes precalculados vía `mhllib`, raíz de solo referencias serializada con lxml, commit atómico (temporal + rename); 7 tests que validan cada manifiesto con `ascmhl-debug xsd-schema-check` y `verify`. XSD de la referencia (tag v1.2) copiados en `tests/xsd/`.
### Decidido
- D42: `mhllib` sirve para D28/D29 con escritura propia de la raíz y del commit. D43: la raíz no lleva `roothash` hasta el hito 4.
### Medido
- H7–H12: el escritor de la referencia siempre emite `<hashes>`; `append_multiple_format_file_hashes` está roto; `verify -dh` revienta sin `roothash` y compara contra todas las generaciones; `#recycle` sin escapar es un comentario gitignore; una referencia sin hijo corta la resolución del resto. Detalle en bitácora 01.
- Esqueleto del repo: router `CLAUDE.md`, normas en `.claude/rules/`, hooks en `.claude/settings.json`, docs de decisiones, roadmap, contexto del archivo, dos informes de research, bitácora 00.
- Normas `obsidian.md` y `vault-accesible.md`, skill `obsidian-vault`; sección «Notas de Obsidian clave» en el router.
### Decidido
- D1–D6: base ASC MHL con la referencia como oráculo; contenedor Docker con GUI mínima; repo público desde el día 0; workflow heredado de la familia de repos; git (Conventional Commits, SemVer); alcance funcional v1.
- D27: el vault de Obsidian es la base de conocimiento; área nueva `#archivo`, nota de proyecto `MHL Sentinel`, 9 conceptos documentados.
- D28–D41 (entrevista técnica): hasher propio + `mhllib`; raíz de solo referencias; xxh128; settle 168 h y `Seal` para cualquier proyecto sin manifiesto; contenedor en el NAS (QNAP x86-64); se configura el horario laboral y la app se detiene en él; sin throttle; sin login; stack en bloque (Python 3.12, FastAPI+HTMX, sqlite3, `MHLS_`); sin atribución en commits; CI en push/PR/tags; MHL 1.x de origen se verifican y heredan; el nombre del estudio no aparece; primer commit y repo público.
- D7–D26 (entrevista de producto): genérico desde el día 0; promesa integridad + completitud + prueba; añadir automático, modificar/borrar a revisión humana; GUI de una pantalla en inglés para producción; solo `ascmhl/` en el proyecto; sin informes, exclusión por tipo de fichero; preexistentes con botón `Seal`; revisión con `Accept as new version` / `Postpone`; detección por niveles de carpeta; exclusiones por defecto + `Ignore`; nombre MHL Sentinel; MIT; avisos en GUI (Apprise después); verificación cada 90 días; segunda copia fuera; ajustes en GUI; `Run scan now` solo escanea.
