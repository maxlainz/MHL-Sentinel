# 00 — Arranque (2026-10-01)

**TL;DR.** Sesión de arranque: research con tres subagentes Opus (convenciones de los repos del owner, spec ASC MHL + referencia `ascmhl`, arquitectura del watcher), lectura del entorno real (archivo en NAS por SMB, protocolo de archivado del estudio) y esqueleto del repo con router, normas y hooks. Entrevista de producto hecha en cinco rondas con `AskUserQuestion` → D7–D26. Nombre `MHL Sentinel`, licencia MIT. Nada commiteado ni publicado: pendiente de la entrevista técnica.

## Qué se leyó
- Los `CLAUDE.md`, `.claude/`, `docs/` y git de los repos del owner → `maxlainz-github-conventions` (memoria) y este esqueleto.
- Spec ASC MHL v1.0 + Implementation Guidelines + código de `ascmhl` 1.2 → `docs/research/asc-mhl-spec-y-referencia.md`.
- Opciones de detección de cambios, scheduling, GUI, Docker, DB → `docs/research/arquitectura-watcher.md`.
- El archivo real y el protocolo del estudio (resumidos sin datos sensibles) → `docs/contexto-archivo.md`.

## Hallazgos
- **H1** — `ascmhl 1.2` (PyPI, 2025-07-04) no expone `verify` en el CLI `ascmhl`; está en `ascmhl-debug verify`. Reproducir: `ascmhl --help; ascmhl-debug --help`. El protocolo del estudio cita `ascmhl verify`: hay que corregirlo o envolverlo.
- **H2** — `ascmhl create` en la raíz de un árbol con historias anidadas rehashea todos los ficheros y escribe una generación en cada historia hija (bottom-up). Reproducir: `grep -n "walk_child_histories" $(python3 -c 'import ascmhl.generator as g;print(g.__file__)')` y el test del informe de spec.
- **H3** — ASC MHL no tiene operación "aceptar cambio": un fichero modificado queda `failed` en todas las generaciones posteriores; uno borrado se reporta missing para siempre (spec §5.6.4 Nota 2, issue ascmitc/mhl#134). Implica una política explícita en la app.
- **H4** — Hoy no hay ningún `ascmhl/` ni `.mhl` en el archivo real (find a profundidad 4). La app parte de cero; no hay que importar historias.
- **H5** — Ninguna herramienta open-source hace verificación ASC MHL programada o continua de un archivo (búsqueda en GitHub y web por los dos subagentes).

## Qué se hizo
- `git init` en el repo con nombre provisional; `.gitignore`, `.env.example`, `.claude/settings.json` (hooks pull/issues/rutas/push, todos no-op sin remoto), 12 normas en `.claude/rules/`, `docs/decisiones.md` (D1–D6 + pendientes), `docs/roadmap.md` (borrador), `docs/contexto-archivo.md`, dos informes de research, `CHANGELOG.md`, `README.md`, `Makefile` con `leak-check` real, `CLAUDE.md` router.
- Memoria de sesión: dos notas (disposición del archivo del estudio, convenciones de los repos del owner); no persistieron en el directorio de memoria de este proyecto.

## Entrevista de producto (respondida)
Cinco rondas. Correcciones del owner respecto a lo recomendado: producto genérico desde el día 0 (no "el estudio primero"); GUI solo en inglés; la app no genera informes ni toca el README, pero permite excluir tipos de fichero del manifiesto; los proyectos preexistentes no se sellan solos; **la detección es por niveles de carpeta, no por patrones de año/mes** (D18, corrige el research §6); ajustes todo en GUI; `Ejecutar ahora` solo escanea. Resultado: D7–D26.

## Vault de Obsidian (D27)
Área nueva `#archivo` con mapa `Archivo`. Notas creadas: `MHL Sentinel` (proyecto), `Historial ASC MHL anidado`, `Hash de directorio en ASC MHL`, `Hash no criptográfico para integridad (xxHash)`, `Detección de cambios en un volumen de red`, `Quiescencia de ficheros (settle time)`, `Distinguir corrupción de modificación por mtime`, `Ventana de inactividad (quiet hours)`, `SQLite sobre un volumen de red`. Editadas: `MHL (Media Hash List)` (área `#archivo`; corregido `ascmhl verify` → `ascmhl-debug verify`, el anidamiento real de la referencia, y añadidas las limitaciones de H3), `Inicio`, `Color`.

## Entrevista técnica (pendiente)
Lista al final de `docs/decisiones.md`.

## Siguiente paso
1. Entrevista técnica → D27+. 2. Primer commit, `gh repo create maxlainz/MHL-Sentinel --public`. 3. Hito 0: fixtures sintéticos + spike de `mhllib` (generación con hashes precalculados validada por `ascmhl-debug verify` y `xsd-schema-check`).
