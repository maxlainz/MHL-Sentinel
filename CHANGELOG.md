# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Versionado: [SemVer](https://semver.org/lang/es/). Categorías: Añadido · Cambiado · Corregido · Decidido · Medido · Eliminado.

## [Unreleased]

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
