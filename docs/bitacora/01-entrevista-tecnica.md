# 01 — Entrevista técnica, primer commit y hito 0 (2026-10-01)

**TL;DR.** Entrevista técnica en cuatro rondas con `AskUserQuestion` → D28–D41. Correcciones del owner respecto a lo recomendado: el tiempo de estabilidad es de **168 h** y el uso normal es pulsar `Seal` a mano (el automático es red de seguridad); lo que se configura es el **horario laboral** y la app se detiene en él (no «ventana de inactividad»); sin throttle; los **MHL 1.x de origen sí** se tratan (verificar y heredar hash), contra el roadmap que los daba fuera de alcance; el nombre del estudio no aparece en ningún sitio. Contenedor en el NAS (QNAP x86-64). Primer commit en `main` y repo público `maxlainz/MHL-Sentinel`. Después, hito 0: ver «Qué se hizo».

## Entrevista técnica (D28–D41)
| Pregunta | Decisión |
|---|---|
| Cómo se producen las generaciones | Hasher propio con checkpoint + escritura con `mhllib` (D28) |
| Manifiesto raíz | Historial de solo referencias (D29) |
| Hash | xxh128 (D30) |
| Estabilidad | Configurable en horas, 168 h; `Seal` para cualquier proyecto sin manifiesto (D31) |
| Dónde corre | En el NAS, QNAP x86-64, Container Station (D32) |
| Horario | Se configura el horario laboral, L–V 09:00–19:00 por defecto; la app se detiene en él (D33) |
| Throttle | Ninguno; un lector secuencial (D34) |
| Auth | Sin login, solo LAN (D35) |
| Stack | Aceptado en bloque (D36) |
| Atribución en commits | No (D37) |
| CI | Push, PR y tags (D38) |
| MHL 1.x | Verificar y heredar hash al sellar; discrepancia → revisión (D39) |
| Nombre del estudio | No aparece; patrón en `leak-patterns.local.txt` (D40) |
| Publicar | Commit y repo público hoy (D41) |

## Qué se hizo
- `docs/decisiones.md` D28–D41; normas `git.md` y `ci.md` confirmadas; roadmap sin «borrador» y con D39 en hito 1; `contexto-archivo.md`, `README.md`, `.env.example`, `CHANGELOG.md` y cabecera de `arquitectura-watcher.md` alineados con las decisiones.
- `scripts/leak-patterns.local.txt` creado y añadido a `.gitignore` (no lo estaba); la bitácora 00 citaba un nombre de memoria con el nombre del estudio: corregido antes del primer commit.
- Primer commit en `main`; `gh repo create maxlainz/MHL-Sentinel --public`.

## Hallazgos
- **H6** — El fichero de patrones privados del leak-check no estaba en `.gitignore`: un `git add -A` lo habría publicado. Reproducir (antes del arreglo): `git check-ignore scripts/leak-patterns.local.txt` no devolvía nada. Corregido en este commit.

## Vault de Obsidian
Sin conceptos nuevos en la entrevista (todos los que toca ya tienen nota: `Historial ASC MHL anidado`, `Hash no criptográfico para integridad (xxHash)`, `Quiescencia de ficheros (settle time)`, `Ventana de inactividad (quiet hours)`, `MHL (Media Hash List)`). Lo que escriba el hito 0 se añade aquí al cerrar.

## Siguiente paso
Hito 0: `make fixtures` (archivo sintético), spike de `mhllib` (generación con hashes precalculados que valide con `ascmhl-debug verify` y `xsd-schema-check`), skill `release`. Cierre: tag `v0.0.1`.
