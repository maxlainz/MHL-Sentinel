# MHL Sentinel

Servicio en contenedor Docker con GUI web mínima que vigila un directorio de archivo montado desde un NAS, detecta los proyectos terminados por reglas de carpeta, mantiene un historial **ASC MHL** por proyecto (crear, actualizar al cambiar, verificar periódicamente) y un manifiesto en la raíz que agrupa todos los proyectos, respetando un horario de inactividad para no cargar el servidor. No es una herramienta de offload ni de copia; la implementación de referencia `ascmhl` es el oráculo de conformidad.

**Este archivo es el router: lo único que un agente necesita leer para arrancar.** Sin conocimiento de dominio aquí; todo vive en el archivo al que apunta. Se actualiza en cada commit que cambie estructura, estado o comandos. El orden es el de lectura: primero cómo se trabaja (mapa, normas, skills, docs, comandos), y al final el estado, el siguiente paso y lo pendiente del owner, que cambian cada sesión.

## Mapa del repo
```
CLAUDE.md              este router
.claude/rules/         normas (una por archivo)        .claude/skills/   obsidian-vault (release pendiente)
.claude/settings.json  hooks: pull + issues al arrancar · bloqueo de rutas/IPs al escribir · push al cerrar (no-op sin remoto)
docs/decisiones.md     ADRs D1–Dn                      docs/bitacora/    una entrada por sesión (NN-slug.md)
docs/roadmap.md        hitos → tags v0.N.0              docs/contexto-archivo.md  el archivo real, anonimizado
docs/research/         informes de subagentes (inglés, TL;DR en español): spec ASC MHL · arquitectura del watcher
src/                   código (hito 1)                  tests/            pytest + fixtures sintéticos (make fixtures)
deploy/                Dockerfile, compose, plantillas NAS (hito 2)
scripts/leak-check.sh  nada del estudio en el repo      samples/          material local, gitignored
scripts/leak-patterns.local.txt  patrones privados del leak-check (gitignored, D40)
Makefile · CHANGELOG.md · README.md · .env.example
```

## Normas (`.claude/rules/`)
| Archivo | Qué manda |
|---|---|
| `entrevista.md` | Preguntar al owner antes de suponer (producto, alcance, UX, workflow); recomendación primero; bocetos en texto para la GUI (D4) |
| `repo-publico.md` | Nada del estudio ni de clientes entra en el repo; nomenclatura de plantilla; fixtures sintéticos; `make leak-check` antes de push (D3) |
| `conformidad-mhl.md` | Todo manifiesto valida con `ascmhl`/`ascmhl-debug`; versión de `ascmhl` fijada; nunca una generación parcial (D1) |
| `decisiones-y-bitacora.md` | ADR por decisión, bitácora por sesión, `Hn` citados desde el código, `CLAUDE.md` y `CHANGELOG` al día (D4) |
| `prediccion-antes-de-medir.md` | Predicción escrita antes de cada medida sobre el NAS; las cifras del research son hipótesis (D4) |
| `subagentes.md` | Orquestar y delegar; Opus para research/diseño/revisión, Sonnet para implementación, Haiku para inventarios (D4) |
| `git.md` | Conventional Commits, SemVer, Keep a Changelog, ramas `feat/<issue#>-slug`, sin trailers de atribución (D5, D37) |
| `pull-y-push.md` | Pull al abrir, push al cerrar; qué hacer si `main` divergió (D5) |
| `issues-abiertos.md` | Leer `gh issue list` antes de cualquier tarea (D5) |
| `problemas-al-issue.md` | Lo que se encuentra y no se arregla, a issue (sin datos del estudio) (D5) |
| `sin-rutas-absolutas.md` | Sin `/Users/...`, shares ni IPs; hook que bloquea (D5) |
| `ci.md` | Gate local `make ci`; Actions en Linux en push/PR y tags (D5, D38) |
| `obsidian.md` | El vault es la base de conocimiento: conceptos nuevos → nota `#concepto #archivo` antes de cerrar sesión; citar notas por título; una sola nota de proyecto, sin estado (D27) |
| `vault-accesible.md` | Sin vault que responda no arranca una tarea de dominio; lotes de 3–6 lecturas (D27) |

## Skills (`.claude/skills/`)
| Voy a… | Skill |
|---|---|
| cerrar un hito y cortar versión | `release` (pendiente de crear en hito 0, modelo: LMT-Composer) |
| leer el vault o escribir notas de concepto | `obsidian-vault` |

## Docs
| Archivo | Leer cuando… |
|---|---|
| `docs/decisiones.md` | Antes de tocar arquitectura, alcance, herramientas o workflow (D1–D41; entrevistas de producto y técnica cerradas) |
| `docs/contexto-archivo.md` | Vas a tocar detección de proyectos, exclusiones, política ante cambios, o necesitas saber qué exige el estudio |
| `docs/research/asc-mhl-spec-y-referencia.md` | Vas a escribir o leer manifiestos, usar `mhllib`, elegir hash, o dudas de qué hace `ascmhl` ante un cambio |
| `docs/research/arquitectura-watcher.md` | Vas a tocar scan, scheduler, hasher, GUI, Docker o la DB |
| `docs/roadmap.md` | Dudas de secuencia o de qué entra en cada hito |
| `docs/bitacora/` | Quieres saber qué pasó en cada sesión y qué se midió (`Hn`) |

## Notas de Obsidian clave (vault `my-vault`, citar por título)
- Contrato y mapas: `Claude`, `Inicio`, `Archivo`.
- Proyecto: `MHL Sentinel` (qué es y en qué conceptos se apoya; sin estado).
- Manifiestos: `MHL (Media Hash List)`, `Historial ASC MHL anidado`, `Hash de directorio en ASC MHL`, `Hash no criptográfico para integridad (xxHash)`.
- Detección y operación: `Detección de cambios en un volumen de red`, `Quiescencia de ficheros (settle time)`, `Distinguir corrupción de modificación por mtime`, `Ventana de inactividad (quiet hours)`, `SQLite sobre un volumen de red`.
- Vecinas: `Limpieza de RAID (RAID scrubbing)` (homelab), `Entrega de spots: color y formato` y `QC técnico de un máster` (color).
- Por hito: hito 0 y 4 (spike `mhllib`, manifiesto raíz, verify) leen las de manifiestos; hito 1 (scan, hasher, MHL 1.x de origen) lee las de detección y operación más `MHL (Media Hash List)`.

## Comandos
```sh
make ci           # leak-check + lint + typecheck + test — el gate de cada commit (código: placeholders hasta hito 1)
make leak-check   # nada del estudio en el árbol (rutas, IPs, patrones privados en scripts/leak-patterns.local.txt)
make fixtures     # archivo sintético para tests (hito 0)
make run          # app en local sobre fixtures (hito 1)
make docker-build # imagen local (hito 2)
git tag -a vX.Y.Z # release: ver skill `release` (pendiente)
```
Requisitos previstos: Python ≥ 3.12, `uv`, Docker. `ascmhl` 1.2 fijado como dependencia y oráculo. Configuración local en `.env` (plantilla `.env.example`).

## Referencia anclada (D1)
ASC MHL Specification v1.0 (2022-03-15) y Implementation Guidelines v1.0 (2023-03-29), `ascmitc/mhl-specification` · `ascmhl` **1.2** (PyPI 2025-07-04, Python ≥ 3.11, MIT). Subir versión es decisión del owner.

## Estado y siguiente paso
- **Estado (2026-10-01, bitácora 01)**: entrevistas de producto (D7–D26) y técnica (D28–D41) cerradas; **primer commit en `main` y repo público `maxlainz/MHL-Sentinel`**. Decisiones clave del día: hasher propio + `mhllib`; raíz de solo referencias; xxh128; se configura el horario laboral y la app se detiene en él (L–V 09:00–19:00); `Seal` manual como uso normal, automático a los 7 días; contenedor en el NAS (QNAP x86-64); MHL 1.x de origen se verifican y heredan. Sin código todavía. Hallazgos H1–H6.
- **Después**: hito 0 → `make fixtures` + spike `mhllib` (generación con hashes precalculados que valide con `ascmhl-debug verify` y `xsd-schema-check`) + skill `release`; cierre con tag `v0.0.1`. Luego hito 1 (ver `docs/roadmap.md`).

## Pendiente del owner (2026-10-01)
- Fuera del repo: actualizar el protocolo del estudio (`ascmhl/` en vez de `00_MANIFEST.mhl`; xxh128; `ascmhl-debug verify`; verificación trimestral por la app; `Seal` al terminar de archivar).
- Mantener `scripts/leak-patterns.local.txt` con nombres de clientes y hosts (hoy solo el estudio).
