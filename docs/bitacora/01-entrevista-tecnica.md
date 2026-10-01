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
- `docs/decisiones.md` D28–D43; normas `git.md` y `ci.md` confirmadas; roadmap sin «borrador» y con D39 en hito 1; `contexto-archivo.md`, `README.md`, `.env.example`, `CHANGELOG.md` y cabecera de `arquitectura-watcher.md` alineados con las decisiones.
- `scripts/leak-patterns.local.txt` creado y añadido a `.gitignore` (no lo estaba, H6); la bitácora 00 citaba un nombre de memoria con el nombre del estudio: corregido antes del primer commit.
- Primer commit en `main`; `gh repo create maxlainz/MHL-Sentinel --public`.
- **Hito 0**: proyecto Python (`pyproject.toml`, `uv`, Python 3.12, `ascmhl==1.2`); `make fixtures` (Sonnet: generador determinista, 5 proyectos, 71 ficheros, dos MHL 1.x de origen con hashes reales; test de determinismo); spike `mhllib` (Opus: `src/mhl_sentinel/mhlwriter.py` + 7 tests, verificado por mí reproduciendo H8 y H11 y leyendo el módulo); Makefile real; `ci.yml`; skill `release`; tag `v0.0.1`.

## Hallazgos
- **H6** — El fichero de patrones privados del leak-check no estaba en `.gitignore`: un `git add -A` lo habría publicado. Reproducir (antes del arreglo): `git check-ignore scripts/leak-patterns.local.txt` no devolvía nada. Corregido.
- **H7** — `hashlist_xml_parser.write_hash_list` de `ascmhl` 1.2 siempre emite `<hashes>`, y vacío no pasa el XSD; la raíz de solo referencias (D29) hay que serializarla aparte. Reproducir: `uv run pytest -q tests/test_spike_mhllib.py::test_reference_writer_cannot_omit_hashes`.
- **H8** — `MHLGenerationCreationSession.append_multiple_format_file_hashes` inicializa `hash_entries = [MHLHashEntry]` (la clase, no una instancia): inutilizable. Reproducir: `grep -n 'hash_entries = \[MHLHashEntry\]' .venv/lib/python3.12/site-packages/ascmhl/generator.py`. Rodeo: `append_file_hash` por formato.
- **H9** — `ascmhl-debug verify -dh` sobre una raíz sin `<roothash>` termina con exit 1 (`AttributeError`, `commands.py:718`). Reproducir: test `test_root_references_only`. Sin `-dh`, `verify` e `info -v` aceptan la raíz de solo referencias (exit 0).
- **H10** — `verify -dh` compara contra los hashes de directorio de **todas** las generaciones: tras añadir un fichero legítimo da exit 12 citando la generación 0001. Reproducir: `test_two_generations_from_precomputed_hashes` con `-dh`.
- **H11** — En sintaxis gitignore `#recycle` es un comentario: no excluye nada. Hay que escribir `\#recycle`. Reproducir: `uv run python -c "import pathspec;print(pathspec.PathSpec.from_lines('gitwildmatch',['#recycle']).match_file('#recycle/x'))"` → `False`.
- **H12** — `MHLHistory._resolve_hash_list_references` hace `return` en vez de `continue` ante una referencia sin hijo: deja sin resolver el resto. Reproducir: lectura de `history.py`. Consecuencia para D29: la raíz debe reconstruirse si un proyecto desaparece.
- Además (ya en el research, confirmado): la cadena no es atómica en la referencia; con nuestro orden de renombrado un fallo entre los dos deja un `.mhl` fuera de la cadena que `ascmhl` no detecta → issue #1 (hito 1). El actualizador del CLI no tiene interruptor: los tests lo cortan con `HTTPS_PROXY=http://127.0.0.1:9`.

## Vault de Obsidian
Sin conceptos nuevos. Ampliadas: `MHL (Media Hash List)` (escape de `\#recycle`, `<hashes>` opcional en el XSD del repo frente al apéndice A de la spec) y `Historial ASC MHL anidado` (la referencia 1.2 carga y verifica un padre de solo referencias; `-dh` falla sin `roothash`; una referencia huérfana corta la resolución). Actualizadas la nota de proyecto `MHL Sentinel` (publicado; horario laboral; MHL de origen) y el mapa `Archivo`.

## Siguiente paso
Hito 1 (núcleo sin GUI, `v0.1.0`): detección por niveles (D18/D19), scan incremental con snapshot SQLite, hasher secuencial con checkpoint y varios algoritmos por lectura (D28, D34, D39), lector de MHL 1.x de origen, generación por proyecto con `mhlwriter`, CLI `run-once`. Antes: leer en el vault las notas de detección y operación (lista en `CLAUDE.md`). Issues abiertos: #1 (manifiestos huérfanos), #2 (bugs de la referencia, ¿abrir upstream?), #3 (`roothash` de la raíz).
