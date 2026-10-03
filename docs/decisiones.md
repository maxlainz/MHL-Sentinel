# Decisiones

Una entrada por decisión: contexto, opciones, elección, fecha. Nunca se borra una decisión: se marca **sustituida por Dn**. Las decisiones vienen de la entrevista con el owner (norma `entrevista.md`) o de hallazgos medidos (`Hn`, en la bitácora).

## D1 — Base técnica: ASC MHL con la implementación de referencia como oráculo
- **Contexto**: el estudio exige manifiesto de checksums por proyecto archivado; el estándar vigente en postproducción es ASC MHL (spec 1.x, repo `ascmitc/mhl`, paquete PyPI `ascmhl`).
- **Opciones**: MHL 1.x legacy (Pomfort), formato propio, ASC MHL.
- **Elección**: ASC MHL. Todo manifiesto generado debe validar con `ascmhl`/`ascmhl-debug` (norma `conformidad-mhl.md`). Hallazgo de arranque: en `ascmhl 1.2` el subcomando `verify` solo existe en el CLI `ascmhl-debug`, no en `ascmhl` (ver bitácora 00, H1).
- 2026-10-01.

## D2 — Forma del producto: contenedor Docker con GUI mínima que vigila un directorio montado
- **Contexto**: el archivo vive en un NAS; la app debe correr en el NAS o en un host siempre encendido, sin instalación de escritorio.
- **Opciones**: CLI + cron, app de escritorio, contenedor con GUI web mínima.
- **Elección**: contenedor Docker, un solo proceso, GUI web mínima para estado y configuración. Volúmenes `/archive` (el directorio vigilado) y `/config` (estado local).
- 2026-10-01.

## D3 — Repo público en GitHub desde el día 0
- **Contexto**: el owner quiere el proyecto completamente público.
- **Elección**: público desde el primer push. Consecuencia: norma `repo-publico.md` (nada del estudio ni de clientes entra en el repo; fixtures sintéticos; `make leak-check`).
- 2026-10-01.

## D4 — Workflow heredado de la familia de repos del owner
- **Contexto**: el owner mantiene varios repos con el mismo método (router `CLAUDE.md`, normas en `.claude/rules/`, `docs/decisiones.md`, bitácora por sesión, subagentes, entrevista antes de suponer, predicción antes de medir).
- **Elección**: se hereda ese método tal cual; el router se adapta al stack (Python + Docker, sin app macOS que reinstalar).
- 2026-10-01.

## D5 — Git: Conventional Commits, SemVer, Keep a Changelog, hooks pull/push
- **Contexto**: igual que en los otros repos del owner.
- **Elección**: ver `.claude/rules/git.md`, `pull-y-push.md`, `ci.md`. **Pendiente de confirmar en entrevista**: trailers de atribución en commits (la familia los prohíbe; el harness los pide por defecto) y si CI corre en cada push o solo en tags.
- 2026-10-01.

## D6 — Alcance funcional de la v1 (decidido por el owner antes del arranque)
- Vigilar un directorio montado que contiene proyectos terminados, a veces bajo subcarpetas por año o mes y a veces sin categorizar.
- Definir dónde están los proyectos por niveles de carpeta (configurable).
- Crear un ASC MHL por proyecto; al detectar cambios, actualizarlo (nueva generación) o recrearlo.
- Horario de inactividad configurable para no generar lecturas en el servidor en horas de trabajo.
- Un ASC MHL en el nivel superior de la ruta de archivado que agrupe todos los proyectos.
- 2026-10-01.


## D7 — Audiencia: producto genérico desde el día 0
- **Contexto**: el owner lo necesita para su archivo, pero el repo es público.
- **Opciones**: estudio primero y público por si sirve; genérico desde el día 0; herramienta personal.
- **Elección**: genérico. El archivo del estudio es un ejemplo de configuración, no el diseño. Toda regla que dependa de cómo archiva un estudio concreto es configurable.
- 2026-10-01, entrevista de producto.

## D8 — Promesa del producto: integridad, completitud y prueba
- **Elección**: "Todo proyecto archivado tiene manifiesto ASC MHL; lo que hay hoy coincide bit a bit con lo archivado; no falta nada; y queda constancia fechada de cada verificación." Un fallo es cualquier desviación de esa frase. Descartadas: solo bit rot; solo manifiesto al cerrar.
- 2026-10-01.

## D9 — Política ante cambios en un proyecto cerrado
- **Contexto**: ASC MHL no "acepta" cambios (H3). El protocolo del estudio prevé reaperturas que añaden versiones, nunca sobrescriben.
- **Elección**: **añadir ficheros es normal**: nueva generación automática cuando el proyecto lleva un tiempo estable. **Modificar o borrar lo decide una persona**: el proyecto pasa a revisión y nada se sella hasta que producción decide (D17). Descartadas: todo manual; todo automático por reglas.
- 2026-10-01.

## D10 — Operador: producción, no técnico, mínima superficie
- **Elección**: la GUI la usa producción. Semáforos, textos claros, acciones de un clic; lo técnico (logs, YAML, hashes) no está en la pantalla principal.
- 2026-10-01.

## D11 — GUI: una sola pantalla
- **Elección**: cabecera de estado (archivo OK/KO, última ronda, próxima ventana), contadores (proyectos, en revisión, sin manifiesto), lista de proyectos con semáforo y fecha de sellado/verificación, y detalle al pulsar un proyecto en revisión. Botones: `Ejecutar ahora` (D26), `Ajustes` (D25). Boceto aprobado en la entrevista:
```
[ Archivo: OK · última ronda 02:14 · próxima ventana 22:00 ]
[ 94 proyectos · 3 en revisión · 1 sin manifiesto ]
  ● AAAA-MM_CLIENTE-CAMPANA   revisión: 2 ficheros modificados  [Ver]
  ● AAAA-MM_CLIENTE-CAMPANA   sellado 2026-09-30 · verificado 2026-09-30
[ Ejecutar ahora ]  [ Ajustes ]
```
- 2026-10-01.

## D12 — Idioma de la GUI: solo inglés
- **Elección**: inglés, sin i18n. Docs del repo en español (D4).
- 2026-10-01.

## D13 — Huella en el proyecto: solo `ascmhl/` según la spec
- **Contexto**: el protocolo del estudio pide `00_MANIFEST.mhl`; la spec exige `ascmhl/` con generaciones y chain.
- **Elección**: la app escribe únicamente `<proyecto>/ascmhl/`. El protocolo del estudio se actualiza fuera de este repo. Descartadas: mantener además un `00_MANIFEST.mhl` plano; manifiestos fuera del proyecto.
- 2026-10-01.

## D14 — La app no escribe nada más en el proyecto ni genera informes; exclusión por tipo de fichero
- **Contexto**: el protocolo pide anotar "MHL verificado por / fecha" en el README del proyecto.
- **Elección**: la app no toca el README ni genera informes. Para que notas humanas (README, checklists) puedan editarse sin invalidar el manifiesto, la configuración permite **excluir tipos de fichero** (p. ej. `*.md`, `*.txt`) de los manifiestos. Nota técnica: en ASC MHL los patrones de ignore solo pueden crecer (spec, Guidelines §2.4): la exclusión se fija al sellar por primera vez.
- 2026-10-01.

## D15 — Arranque sobre un archivo existente: solo los nuevos se sellan solos
- **Contexto**: el archivo del estudio tiene ~95 proyectos sin manifiesto (H4).
- **Elección**: los proyectos que ya existen cuando se instala la app aparecen como "sin manifiesto" y **no** se sellan automáticamente; se sellan con el botón `Sellar` (D16). Los que aparecen después de la instalación se sellan solos tras el tiempo de estabilidad. Descartadas: sellar todo lo estable automáticamente; dejar el backlog fuera de la app.
- 2026-10-01.

## D16 — Botón `Sellar` para proyectos sin manifiesto
- **Elección**: un clic en la GUI encola el sellado para la próxima ventana. Equivale a un `create` inicial. Nada se sella sin ese clic en proyectos preexistentes.
- 2026-10-01.

## D17 — Revisión: `Aceptar como nueva versión` y `Posponer`
- **Elección**: en un proyecto en revisión producción ve qué ficheros se modificaron o borraron y dos botones. **Aceptar como nueva versión**: la app guarda aparte el historial anterior y sella el proyecto tal como está hoy (historia nueva). **Posponer**: sigue en revisión, sin tocar nada. Descartadas: solo posponer; botón de restaurar desde snapshot (puede volver como guía, no como acción).
- 2026-10-01.

## D18 — Detección de proyectos: por niveles de carpeta, no por patrones de año o mes
- **Contexto**: el research proponía un modo "contenedores" con regex de año/mes. El owner lo corrige.
- **Elección**: el usuario selecciona la carpeta raíz y dice **a cuántos niveles por debajo están los proyectos**. Los niveles intermedios son transparentes, sean años, meses, clientes o lo que sean. Ejemplo: raíz = el share de archivo, "los proyectos están un nivel por debajo" → las carpetas-año se ignoran como contenedores y lo siguiente es proyecto. Sustituye la propuesta de `docs/research/arquitectura-watcher.md` §6.
- 2026-10-01.

## D19 — Exclusiones: defaults más botón `Ignorar`
- **Elección**: por defecto se ignoran en cualquier nivel las carpetas que empiezan por `_`, `@`, `#` o `.`. Todo lo demás al nivel de proyectos aparece como proyecto; si no lo es, producción pulsa `Ignorar` y la app lo recuerda. Descartadas: solo lista en ajustes; sin excepciones.
- 2026-10-01.

## D20 — Nombre: MHL Sentinel
- **Elección**: repo `MHL-Sentinel`, paquete `mhl-sentinel`, imagen `ghcr.io/maxlainz/mhl-sentinel`. Libre en GitHub y PyPI a 2026-10-01.
- 2026-10-01.

## D21 — Licencia: MIT
- **Elección**: MIT, como `ascmhl` y TrimPack. Coherente con D7.
- 2026-10-01.

## D22 — Avisos: GUI en v1; externos después
- **Elección**: la v1 muestra revisiones y corrupción en la GUI. El diseño deja un punto único de notificación para Apprise (email, ntfy, Telegram…) en una versión posterior.
- 2026-10-01.

## D23 — Verificación periódica: cada 90 días por proyecto, escalonada
- **Elección**: cada noche se releen unos pocos proyectos; en 90 días todo el archivo ha pasado. Solo en ventanas. Descartadas: anual; continua.
- 2026-10-01.

## D24 — Segunda copia fuera del NAS: fuera de alcance
- **Elección**: este proyecto vigila un solo directorio. La segunda copia no tiene nada que ver con él por ahora.
- 2026-10-01.

## D25 — Ajustes: todo configurable desde la GUI
- **Contexto**: D10 pide mínima superficie.
- **Elección**: toda la configuración se edita desde una página de Ajustes separada de la pantalla principal, con valores por defecto sensatos y lo avanzado plegado. La pantalla principal no cambia. No hace falta editar ficheros en el NAS.
- 2026-10-01.

## D26 — `Ejecutar ahora`: solo escanea
- **Elección**: el botón refresca la lista (scan barato) fuera del horario. Sellar y verificar esperan siempre a la ventana de inactividad. Descartadas: ronda completa saltando el horario; sin botón.
- 2026-10-01.

## D27 — El vault de Obsidian es la base de conocimiento; área nueva `#archivo`
- **Contexto**: el contrato del vault del owner (nota `Claude`) exige que toda investigación termine en notas de concepto reutilizables y que cada proyecto con repo tenga una nota readme. La investigación de arranque produjo conceptos que no son de este proyecto (historiales anidados, hashes, detección de cambios en red, quiescencia, ventanas, SQLite en red).
- **Opciones**: área `#color` (donde vivía `MHL (Media Hash List)`), `#homelab` (corre en el NAS), área nueva.
- **Elección**: área nueva **`#archivo`** con mapa `Archivo`; la nota `MHL (Media Hash List)` pasa a esa área y se corrige con lo verificado (H1, H2, H3). Nota de proyecto `MHL Sentinel`. Normas `obsidian.md` y `vault-accesible.md`; skill `obsidian-vault`. Documentar conceptos en el vault es condición de cierre de sesión.
- 2026-10-01.

## D28 — Generaciones: hasher propio y escritura con `mhllib`
- **Contexto**: `ascmhl create` rehashea todo el proyecto en cada ejecución, no se puede pausar al cerrarse la ventana y escribe la generación aunque falle (research spec §2.3; H2). Un largo de 5 TB son más de 12 h de lectura.
- **Opciones**: `ascmhl create` como subproceso; hasher propio + escritura con `mhllib`; híbrido (subproceso en hito 1, propio después).
- **Elección**: hasher propio con checkpoint por fichero en SQLite, que se detiene en horario laboral (D33) y continúa al día siguiente; la generación se escribe al final con `mhllib` (`MHLGenerationCreationSession.append_file_hash` con hashes precalculados) en temporal y renombrado (norma `conformidad-mhl.md`). `mhllib` no es API estable: se prueba en el spike del hito 0 y todo manifiesto se valida con `ascmhl-debug verify` y `xsd-schema-check`.
- 2026-10-01, entrevista técnica.

## D29 — Manifiesto raíz: solo referencias
- **Contexto**: la spec permite un historial padre cuyo manifiesto solo contenga `<references>` a los manifiestos de los hijos (§5.6.2 Nota 3, §6.5.2 Nota 1). La referencia solo lo produce con el atajo `-sf`, que escribe `<hashes>` vacío e inválido.
- **Opciones**: historial de referencias en la raíz; packing list plana (`flatten`); ambos.
- **Elección**: historial `ascmhl/` en la raíz del archivo cuyo manifiesto referencia el último manifiesto de cada proyecto (ruta + C4), sin releer ficheros; se reconstruye cuando cambia cualquier proyecto. Semántica: «estos proyectos tienen historial intacto a fecha X». Escrito con `mhllib`. Pendiente de probar en hito 4 que Silverstack/Hedge lo abren; si no, se revisa. Ver la nota `Historial ASC MHL anidado`.
- 2026-10-01.

## D30 — Hash: xxh128
- **Contexto**: el protocolo del estudio admite xxHash64 o MD5; `ascmhl` usa xxh128 por defecto; Silverstack/Hedge/YoYotta usan xxh64.
- **Opciones**: xxh128; xxh64 (reutilizable por Hedge al copiar); xxh128 + md5.
- **Elección**: xxh128 en los historiales nuevos. Misma velocidad que xxh64, colisiones nulas en la práctica (renombrado por hash seguro). Donde ya exista un historial con otro algoritmo se respeta y se añade xxh128 en la misma lectura. Ver la nota `Hash no criptográfico para integridad (xxHash)`.
- 2026-10-01.

## D31 — Tiempo de estabilidad: configurable en horas, 168 h por defecto; `Seal` vale para cualquier proyecto sin manifiesto
- **Contexto**: D15 decía que los proyectos nuevos se sellan solos «tras el tiempo de estabilidad». El owner matiza el uso real: lo normal será pulsar `Seal` a mano al terminar de archivar; el sellado automático es la red de seguridad por si alguien se olvida.
- **Opciones**: 1 h (research); 24 h; 72 h; 168 h.
- **Elección**: tiempo sin cambios configurable en horas en Ajustes, **168 h (7 días) por defecto**. El botón `Seal` (D16) está disponible para **todo** proyecto sin manifiesto, preexistente o nuevo en espera; pasado el plazo sin cambios, la app encola el sellado sola. Matiza D15 y D16.
- 2026-10-01.

## D32 — Dónde corre: en el NAS (QNAP x86-64, Container Station)
- **Contexto**: en el NAS las lecturas son locales (sin SMB ni cachés de atributos); en el Mac Studio compiten con Resolve y el Mac se apaga.
- **Elección**: contenedor en el propio NAS, un QNAP con CPU Intel/AMD (imagen amd64). Se publica igualmente para arm64 (D7). La plantilla de instalación del hito 2 se prueba primero en Container Station. Las estimaciones del research sobre SMB (`arquitectura-watcher.md` §1) dejan de ser el caso base; se mide igualmente (norma `prediccion-antes-de-medir.md`).
- 2026-10-01.

## D33 — Se configura el horario laboral, y durante él la app se detiene por completo
- **Contexto**: el research y D6 hablaban de «ventana de inactividad» (cuándo puede trabajar la app). El owner lo invierte: lo que se configura es el **horario laboral** del estudio, y en ese horario la app no toca el servidor.
- **Elección**: Ajustes → *Working hours*: días y franja (por defecto **lunes a viernes 09:00–19:00**, en la zona horaria `TZ`). Dentro del horario laboral no hay scan, hash ni verificación automáticos; solo la GUI y `Run scan now` (D26, scan barato explícito). Fuera del horario la app trabaja sin límite. Sustituye la terminología «quiet hours» en GUI, config y docs; el concepto de la nota `Ventana de inactividad (quiet hours)` es el mismo visto desde el complemento.
- 2026-10-01.

## D34 — Sin límite de velocidad: un lector secuencial, a tope fuera del horario
- **Elección**: no hay throttle en bytes/s en la v1; la protección del servidor es D33. Un solo fichero a la vez (secuencial, lo que mejor trata a un RAID de discos). Si una medida (`Hn`) demuestra que hace falta, se añade un límite en Ajustes avanzados.
- 2026-10-01.

## D35 — GUI sin login, solo LAN
- **Elección**: sin autenticación en v1, como la mayoría de apps de NAS. Se documenta cómo ponerla tras un proxy inverso con login si se expone fuera de la LAN. Descartadas: HTTP Basic opcional; login obligatorio.
- 2026-10-01.

## D36 — Stack técnico (aceptado en bloque)
- **Elección**: Python 3.12 (`python:3.12-slim`); `uv` para dependencias; `ruff` + `mypy --strict` + `pytest`; GUI FastAPI + Jinja2 + HTMX + SSE (`sse-starlette`) + Pico.css vendorizado, sin build de Node; `sqlite3` de la librería estándar con WAL y migraciones por `user_version`, en `/config/state.db` en disco local (la app se niega a arrancar si `/config` está en un sistema de ficheros de red); configuración defaults < `/config/config.yaml` < variables de entorno con prefijo `MHLS_` (pydantic-settings), la GUI escribe el YAML; imagen multi-arch amd64 + arm64 en GHCR; PUID/PGID y `user:` soportados; `tini`; `/healthz`. Detalle en `docs/research/arquitectura-watcher.md` §3–§5. Cualquier punto se revisa si un hito lo contradice, con su `Dn`.
- 2026-10-01.

## D37 — Sin atribución en commits (confirma D5)
- **Elección**: sin trailers `Co-Authored-By` ni `Signed-off-by`, igual que el resto de repos del owner. `includeCoAuthoredBy: false`.
- 2026-10-01.

## D38 — CI en cada push y PR, y en tags (confirma D5)
- **Elección**: `ci.yml` (lint, tipos, tests, leak-check) en push a `main` y PRs; `release.yml` en tags `v*` construye y publica la imagen. Todo son targets del Makefile.
- 2026-10-01.

## D39 — MHL 1.x de origen: siempre se tienen en cuenta; verificar y heredar el hash al sellar
- **Contexto**: los proyectos pueden contener manifiestos MHL 1.x (los que Silverstack/Hedge dejan al volcar tarjetas). Son de origen y tienen historial: prueban la cadena desde el rodaje. ASC MHL no los importa (research spec §5). El roadmap los daba «fuera de alcance»; el owner lo corrige.
- **Opciones**: fuera de alcance; solo verificar; verificar y heredar; verificar, heredar y vigilar.
- **Elección**: al sellar, la app localiza los `.mhl` 1.x dentro del proyecto, comprueba cada fichero que listan en la misma lectura que calcula el xxh128 (sin coste extra) y anota en el manifiesto nuevo el hash de origen junto al xxh128 (p. ej. `xxh64` con `action="verified"`, `md5` si es lo que trae). Si algún fichero no coincide o falta, el proyecto pasa a revisión (D9, D17) y no se sella. Cuidado con las codificaciones de 1.x (`xxhash64` little-endian vs `xxhash64be`): ver la nota `MHL (Media Hash List)`. Entra en el hito 1 (el hasher se diseña ya para varios algoritmos por lectura).
- 2026-10-01.

## D40 — El nombre del estudio no aparece en el repo
- **Elección**: nada público. Autor: Max Lainz (LICENSE, `pyproject`). El estudio se cita como «un estudio de postproducción». El nombre va a `scripts/leak-patterns.local.txt` (gitignored) para que `make leak-check` lo bloquee.
- 2026-10-01.

## D41 — Primer commit y repo público hoy
- **Elección**: tras registrar la entrevista técnica: `make leak-check`, primer commit en `main` y `gh repo create maxlainz/MHL-Sentinel --public`. Desde ahí funcionan los hooks de pull/push y los issues.
- 2026-10-01.

## D42 — Resultado del spike del hito 0: `mhllib` sirve, con escritura propia de la raíz y del commit
- **Contexto**: D28 y D29 dependían de que la librería de `ascmhl` 1.2 aceptara hashes precalculados y pudiera escribir una raíz de solo referencias.
- **Resultado (H7–H12)**: las generaciones de proyecto se escriben con el modelo y el escritor de `ascmhl` (`MHLGenerationCreationSession.append_file_hash`, un formato por llamada) sin que relea ningún fichero; los hashes de directorio y el `roothash` salen de `DirectoryHashContext` y coinciden con los de `ascmhl create`. La raíz de solo referencias **no** la puede escribir el escritor de la referencia (siempre emite `<hashes>`): se serializa con lxml en `src/mhl_sentinel/mhlwriter.py`; la cadena sí la escribe `ascmhl`. El `commit` es propio (temporal + fsync + rename, manifiesto antes que cadena) porque el de la librería escribe directo en la ruta final. Usa dos métodos privados de `MHLHistory`; aceptable mientras `ascmhl` esté fijado (D1). Todo valida con `xsd-schema-check` y `ascmhl-debug verify` (exit 0), con los XSD del commit del tag v1.2 copiados en `tests/xsd/`.
- 2026-10-01, hito 0.

## D43 — La raíz no lleva `roothash` por ahora
- **Contexto**: el XSD del repo lo marca opcional y la referencia carga y verifica la raíz sin él; solo `verify -dh` revienta (H9, bug de la referencia). Derivarlo de los `roothash` de los hijos (spec §6.5.2 Nota 1) exige calcular hashes de directorio para las carpetas intermedias (años).
- **Elección**: sin `roothash` en la raíz hasta el hito 4, donde se mide si Silverstack/Hedge lo echan en falta. Issue #3.
- 2026-10-01.

## D44 — MVP = hitos 1 a 4, publicado en GHCR
- **Elección**: el owner pide seguir sin parar hasta tener en GHCR una imagen con núcleo, daemon, GUI, raíz de solo referencias y verificación periódica. Cada hito sigue cortando su tag (`v0.1.0` … `v0.4.0`). La comprobación con MediaVerify (issue #3) y hacer público el paquete en GHCR son acciones del owner.
- 2026-10-01.

## D45 — Boceto de Ajustes aprobado
```
Settings                                    [ Back ]
Archive
  Root folder      /archive   (set by the container mount)
  Projects are  [1]  level(s) below the root
  Ignore folders starting with   _  @  #  .
  Exclude file types from manifests   [*.md, *.txt]
Working hours (the app stays idle during them)
  Days   [x]Mon [x]Tue [x]Wed [x]Thu [x]Fri [ ]Sat [ ]Sun
  From [09:00]  to [19:00]      Time zone: Europe/Madrid
Sealing
  Auto-seal new projects after [168] hours without changes
  Re-verify every project every [90] days
▸ Advanced (collapsed)
  Hash algorithm  xxh128 (fixed)   Scan interval [60] min   Log level [info]
[ Save ]
```
- 2026-10-01.

## D46 — Boceto del detalle de revisión aprobado
```
● AAAA-MM_CLIENTE-CAMPANA              needs review
  Sealed 2026-03-10 · 3 generations · 142 files · 28.4 GB
  Changed since the last seal:
    modified   01_MASTERS/spot_30s_v2.mov        (size 1.2 GB → 1.3 GB, 2026-09-28)
    deleted    05_DELIVERABLES/spot_30s_old.mp4
    added      05_DELIVERABLES/spot_30s_v3.mp4   (added files are fine)
  [ Accept as new version ]   [ Postpone ]
  Accept: the current history is kept aside and the project is sealed again as it is today. Postpone: nothing changes.
```
- 2026-10-01.

## D47 — Issues upstream en `ascmitc/mhl`: todavía no
- **Elección**: los bugs H7–H12 quedan documentados en este repo y en el vault; se revisa tras el MVP (issue #2 abierto como recordatorio).
- 2026-10-01.

## D48 — Ficheros añadidos: generación parcial; lectura completa solo al sellar y al verificar
- **Contexto**: en ASC MHL cada hash lleva `action` `original`, `verified` o `failed`. Si al añadir ficheros a un proyecto sellado escribiéramos una generación con todos los ficheros usando los hashes de la caché, marcaríamos `verified` lo que no se ha vuelto a leer. Releer todo el proyecto por cada añadido (un largo: horas) es lo que hace la referencia y lo que D28 quería evitar.
- **Opciones**: releer todo; usar caché y marcar `verified`; generación parcial.
- **Elección**: la generación de un `append` lista **solo los ficheros añadidos** como `original`, sin hashes de directorio ni `roothash` (la spec lo permite: la referencia lo hace con `-sf`; la completitud en `verify` es la unión de todas las generaciones). El sellado inicial y la verificación de 90 días (D23) sí leen todo y escriben generación completa. Se comprueba en tests que `ascmhl-debug verify` acepta el historial tras una generación parcial.
- 2026-10-01, hito 1.

## D49 — Detección solo por niveles, sin excepciones
- **Contexto**: con «proyectos 1 nivel por debajo» un proyecto dejado directamente en la raíz se interpreta como contenedor y sus subcarpetas salen como proyectos falsos (test de `discovery.py`, hito 1). Opciones: marcador `ascmhl/` + botón `Treat as project`; solo niveles; heurística por contenido.
- **Elección**: solo niveles. La GUI muestra las «entradas fuera de sitio» (ficheros o carpetas en la raíz o en niveles intermedios que no encajan) para que producción ordene el archivo; la app no adivina. Confirma D18.
- 2026-10-01.

## D50 — El historial apartado por `Accept as new version` va a `<proyecto>/ascmhl_superseded/<fecha>/`
- **Contexto**: la referencia carga `ascmhl/` en recursivo (H13): un historial viejo dentro de `ascmhl/superseded/` rompe `info` y `verify`. Opciones: carpeta hermana en el proyecto; `/config` del contenedor; raíz del archivo.
- **Elección**: carpeta hermana `ascmhl_superseded/<AAAA-MM-DDTHHMMSSZ>/`, excluida del manifiesto nuevo por patrón de ignore por defecto. Viaja con el proyecto y sigue siendo verificable a mano. Matiza D13: la app escribe `ascmhl/` y, solo tras un Accept, `ascmhl_superseded/`.
- 2026-10-01, hito 1.

## D51 — El historial de la raíz se reinicia cuando una referencia deja de existir
- **Contexto**: la referencia resuelve las referencias de **todas** las generaciones de un historial y revienta (assert) si alguna apunta a un manifiesto que ya no existe (H14). Tras un `Accept as new version` (D17, D50) o cuando un proyecto desaparece, las generaciones antiguas de la raíz quedan colgando. Los manifiestos son inmutables: no se pueden reescribir.
- **Elección**: en ese caso la raíz aparta su historial a `<raíz>/ascmhl_superseded/<fecha>/` y empieza de nuevo en 0001. La raíz referencia todo proyecto con historial (incluidos los que están en revisión o con añadidos), no solo los `sealed`, para no regenerarla en cada cambio de estado; se regenera cuando cambia el conjunto referenciado o cualquier generación hija. Lleva patrones de ignore `_*`, `@*`, `\#*`, `.*` (H15: la referencia compara con rutas absolutas, `/_*` no casa nunca). Un fichero suelto entre proyectos hace que `verify` sobre la raíz dé exit 21 (H16): consecuencia de D49, el archivo debe estar ordenado.
- 2026-10-01, hito 4.

## D52 — Auto-actualización con Watchtower: tag `latest` siempre y contenedor recreable a lo bruto
- **Contexto**: el owner quiere que el contenedor se actualice solo (Watchtower o equivalente): esas herramientas descargan `latest`, paran el contenedor con SIGTERM y un timeout corto (10 s por defecto), lo matan con SIGKILL si no sale, y lo recrean. `/config` persiste; `/archive` es compartido.
- **Elección**: (1) cada release mueve `latest` (norma `ci.md`); (2) la app es segura ante SIGKILL en cualquier punto: generaciones en temporal + rename, trabajos reencolados al arrancar, manifiestos huérfanos y temporales limpiados antes de arrancar el supervisor, `stop()` por debajo de 10 s; demostrado por `tests/test_recreate.py`; (3) `deploy/docker-compose.yml` lleva las etiquetas de Watchtower y `stop_grace_period`, y la guía documenta `WATCHTOWER_TIMEOUT`; (4) las migraciones de la DB avanzan solas al arrancar y nunca se baja de versión.
- 2026-10-01.

## D53 — Botón `Cancel` para un Seal o Accept en cola
- **Contexto**: el owner sella a mano (D16) y el trabajo espera a la ventana fuera de horario (D33). No había forma de retirarlo salvo `Ignore`, que deja de vigilar la carpeta (issue #4).
- **Opciones**: (1) cancelar solo trabajos manuales (`seal`, `accept_new_version`) en cola; (2) también los automáticos (`append`), inútil porque el siguiente ciclo los vuelve a encolar; (3) abortar un trabajo en marcha, que exige parar el hilo hasher a mitad.
- **Elección**: (1). `sealer.request_cancel`: el job pasa a `cancelled`, el proyecto vuelve a `unsealed` o `needs_review` (conservando el motivo de revisión) y los hashes ya calculados se conservan en la caché (D28). Un trabajo parado por el horario laboral vuelve a la cola y por tanto también se puede cancelar. Abortar un trabajo en marcha queda para otro issue si hace falta.
- 2026-10-01 (petición del owner).

## D54 — Frontend: se integra la propuesta D («Bandeja») con un acabado más claro y refinado
- **Contexto**: cuatro prototipos independientes del frontend (issue #5, bitácora 03): A parte de relevo, B tablero con mapa del archivo, C tabla única estilo media pool, D bandeja de triaje. El owner los probó en local con capturas.
- **Elección**: D. Portada = frase de estado + solo lo que pide decisión (Seal/Cancel de un clic), resto plegado, log de actividad. Cambios pedidos antes de integrar: contenedor más ancho (menos apretado) y un acabado menos oscuro y denso: es una app profesional que no se usa en la sala de color, así que el aspecto es claro y refinado (el modo oscuro sigue al sistema, no es el predeterminado). Después, revisión adversarial y corrección antes de enseñarla de nuevo. Las ramas A, B y C se borran.
- 2026-10-01.

## D55 — `main` protegida: todo entra por PR con CI verde
- **Contexto**: hasta el MVP se commiteaba directo en `main` (permitido en D5 para el hito 0 y tolerado después). Con la app instalada en el NAS y auto-actualizable (D52), el owner pide dejar de trabajar en `main` y bloquear la rama.
- **Elección**: protección de rama en GitHub (PR obligatorio, check `ci` obligatorio, rama al día, sin force-push ni borrado, también para el administrador). Sin revisor obligatorio: el owner trabaja solo y la revisión la hacen los agentes antes del PR. Las releases pasan por una rama `chore/release-vX.Y.Z` y el tag se pone sobre `main` ya integrado. Norma `rama-main-protegida.md`; `git.md`, `pull-y-push.md` y la skill `release` actualizadas.
- 2026-10-01.

## D56 — Tema de la GUI configurable (auto / light / dark, auto por defecto)
- **Contexto**: el owner pidió un acabado claro y refinado (D54), pero su Mac está en modo oscuro, así que con «seguir al sistema» como única opción veía siempre la versión oscura.
- **Opciones**: solo seguir al sistema; forzar siempre claro; ajuste en la GUI; conmutador en el navegador (`localStorage`).
- **Elección**: ajuste `theme` en Ajustes → *Appearance* con tres valores, `auto` (sigue al sistema, por defecto), `light` y `dark`, guardado en `config.yaml` como el resto (D25, D36; env `MHLS_THEME`). Al ser una app de una sola persona, el ajuste vale para todos sus navegadores y no depende de cookies. La página pone `data-theme` en `<html>` solo cuando no es `auto`; funciona sin JS.
- 2026-10-01.

## D57 — `Cancel` también en marcha
- **Contexto**: D53 dejó fuera abortar un trabajo en marcha. Con el backlog del NAS, un `Seal` lanzado por error puede tardar horas (y quedarse en `hashing` pausado durante el horario laboral) sin forma de retirarlo; el owner lo pidió (issue #10).
- **Opciones**: (1) abortar al siguiente fichero conservando los hashes ya hechos (D28) y devolver el proyecto a su estado anterior; (2) dejar terminar el fichero en curso y descartar también los hashes; (3) cancelar cualquier trabajo, verificaciones y `append` automáticos incluidos.
- **Elección**: (1). El supervisor tiene un evento de cancelación por trabajo (se limpia al empezar cada uno); `Supervisor.request_cancel(project_id)` solo lo levanta si el trabajo en curso es de ese proyecto y es un `seal`/`accept_new_version` manual. El hasher ve «parar o cancelar» como una sola señal y la comprueba en cada bloque, en cada frontera de fichero (también cuando el siguiente sale de la caché) y mientras espera a que acabe el horario laboral. Al saltar: trabajo `cancelled`, proyecto a `unsealed` o a `needs_review` conservando `review_reason`, sin generación ni temporales (la generación solo se escribe tras el último fichero). Si coinciden SIGTERM y Cancel, gana Cancel. `sealer.request_cancel` sigue siendo solo para trabajos en cola y rechaza uno en marcha, para que ningún camino deje un trabajo a medio cancelar. Verificaciones y `append` automáticos nunca se cancelan.
- 2026-10-01 (decisión del owner en el issue #10).

## D58 — Proyecto desaparecido: estado `missing` al primer scan, cortafuegos solo con raíz vacía
- **Contexto**: con los años un proyecto entero puede borrarse del archivo (expurgo o accidente); de ahí la utilidad del historial MHL de todo el archivo. Hasta ahora la carpeta ausente pasaba a `error "folder missing"`, se reintentaba sin fin y, como el `ascmhl/` vivía dentro de la carpeta, el historial desaparecía con ella: solo quedaban los hashes en la DB y las generaciones apartadas de la raíz (referencias y C4, sin hashes de ficheros).
- **Opciones**: (1) «no encontrado» durante 3 rondas o 7 días antes de pedir decisión, con cortafuegos si faltan más del 20 %; (2) eliminado al primer scan; (3) solo manual. Cortafuegos: umbral del 20 %; solo raíz vacía o ilegible; ninguno.
- **Elección**: (2) con cortafuegos mínimo. Estado nuevo `missing` desde el primer scan en que la carpeta no está (`missing_since`, se conserva el estado anterior en `state_before_missing`, la fila y sus `sealed_files` no se borran, los trabajos en cola se cancelan). Entra en «Needs your decision» con `Retire` y `Retry` (D60, D61). Si la raíz no se lee o lista cero proyectos teniendo la DB proyectos, la ronda cuenta como «archivo inaccesible» y ningún estado cambia. Si la carpeta vuelve (ronda o `Retry`), recupera su estado y, si tiene historial, se encola una verificación automática: si cuadra queda `sealed` sin preguntar; si no, revisión (D17). Un proyecto `missing` no se verifica ni lo referencia la raíz (D51).
- 2026-10-02 (entrevista con `AskUserQuestion`).

## D59 — Espejo del historial de cada proyecto en `/config/history/`
- **Contexto**: el historial MHL debe sobrevivir al borrado del proyecto para poder descargarlo al dar de baja (D60) y para reconocer una carpeta movida (D62). Analogía: la librería de Silverstack vive aparte de las tarjetas.
- **Opciones**: (1) copia espejo de `ascmhl/` en `/config`; (2) reconstruir un MHL desde la DB al detectar la eliminación (válido pero no es el original firmado en cadena); (3) solo la DB.
- **Elección**: (1). Tras cada generación (seal, append, accept, verify) y en el scan cuando `ascmhl/` existe y el espejo falta o su cadena difiere (también historiales de otras herramientas), la app copia `ascmhl/` a `<config>/history/<proyecto>/ascmhl/` (KB; escritura atómica). Un `Accept as new version` aparta el espejo igual que el historial. El espejo nunca sustituye a la verificación de ficheros; es copia de seguridad del historial.
- 2026-10-02.

## D60 — `Retire` borra el registro y el espejo; único rastro, una línea en el log
- **Contexto**: qué conservar de un proyecto que el estudio ha borrado a conciencia.
- **Opciones**: (1) `Retire` + `Retry` conservando una sección «Dados de baja» con historial descargable; (2) añadir «Olvidar»; (3) solo informativo, baja automática.
- **Elección**: del owner: `Retire` (dar de baja) y `Retry`. **Confirmar la baja borra todo**: la fila del proyecto y lo que cuelga de ella, la caché de hashes y el espejo del historial; no se conservan restos de lo que se ha eliminado a conciencia. Antes de confirmar, un diálogo ofrece `Retire`, `Retire and download MHL` (zip del `ascmhl/` espejado) o `Cancel`. El único rastro es una línea en el log de actividad («retired X», con fecha), que es registro de lo que hizo la app, no un resto del proyecto. La raíz se regenera sin el proyecto (D51). No hay botón de baja anticipada en un proyecto sellado: se borra la carpeta y se da de baja desde la Bandeja cuando salte.
- 2026-10-02.

## D61 — `Retry` comprueba la carpeta al momento; la verificación espera a la ventana
- **Elección**: `Retry` hace un único `stat` de la carpeta ahora mismo, también en horario laboral (coste despreciable). Si está, el proyecto recupera su estado, la tarjeta desaparece y la verificación del historial se encola para fuera de horario. Si no está, sigue `missing` con aviso. Descartado: esperar a la siguiente ronda.
- 2026-10-02.

## D62 — Carpeta movida o renombrada con la misma cadena: mismo proyecto, se continúa el historial
- **Contexto**: un proyecto puede cambiar de carpeta-año o de nombre; para la app es un `missing` más un proyecto nuevo con historial ajeno.
- **Elección**: si aparece una carpeta nueva con `ascmhl/` cuya `ascmhl_chain.xml` es byte a byte la del espejo de un proyecto `missing`, es el mismo proyecto con ruta nueva: la fila se renombra, el espejo se mueve, sin tarjeta en la Bandeja; una línea en el log. Descartado: baja + proyecto nuevo (más clics).
- 2026-10-02.

## D63 — `Verify now` por proyecto, salta el horario laboral con aviso
- **Contexto**: salió en la entrevista: poder verificar un proyecto a demanda (tras un `Retry`, ante una sospecha), sin esperar a la verificación escalonada de 90 días (D23).
- **Opciones**: (1) por proyecto, se salta el horario con aviso de rendimiento; (2) por proyecto, solo adelanta en la cola nocturna; (3) además uno global «Verify all».
- **Elección**: (1). Botón `Verify now` en la ficha de un proyecto `sealed`. En horario laboral avisa («Reads N GB from the NAS during working hours and can slow everyone down») y pide confirmar; fuera de horario se encola delante de todo. Trabajo `verify` manual con `bypass_hours`; cancelable como un Seal (D53, D57): al cancelar el proyecto vuelve a `sealed` sin generación. Sin «Verify all».
- 2026-10-02.

## D64 — Atribuciones fuera de GitHub: historial reescrito y doble barrera
- **Contexto**: el commit de #13 y la release `v0.6.0` entraron en `main` con trailers `Co-authored-by: Claude` y `Claude-Session:`, y las descripciones de #13 y #14 con un pie «Generated by Claude Code» y enlace de sesión, pese a D37 (el `includeCoAuthoredBy: false` de `.claude/settings.json` no cubre las sesiones web ni el texto de los PR).
- **Opciones**: (1) dejar el historial y solo blindar; (2) reescribir `main` y el tag, y blindar.
- **Elección**: del owner, (2). Se reescribieron los mensajes de los dos commits (árbol idéntico), se abrió la protección de `main` el tiempo justo de un `push --force-with-lease` y se restauró igual; el tag `v0.6.0` se recreó sobre el commit nuevo (la imagen se reconstruyó con el mismo contenido). Excepción puntual a D5/D55 y a «nunca se reescribe un tag publicado»; no sienta precedente. Las descripciones de los PR se editaron. Barreras: `make attribution-check` dentro de `make ci` (mensajes de commit de la rama y `PR_BODY`; la CI también corre al editar un PR) y un hook `PreToolUse` que deniega `git commit|tag` y `gh pr|issue|release` con atribución en el comando.
- 2026-10-02.

## D65 — `Accept as new version` acepta también lo que no cuadra con un MHL 1.x de origen
- **Contexto**: con un MHL 1.x de origen en el proyecto (D39), borrar ficheros que ese manifiesto lista y pulsar `Accept as new version` devolvía el proyecto a revisión con exactamente los mismos ficheros: el sellado nuevo volvía a comprobar el MHL 1.x. Un `Append` posterior habría tropezado igual.
- **Opciones**: (1) Accept acepta todo: borrados → aviso; modificados → se sellan con xxh128 sin heredar el hash de origen; (2) solo los borrados; (3) Accept ignora el MHL 1.x.
- **Elección**: del owner, (1). Accept significa «acepto la carpeta como está ahora». Los ficheros intactos siguen heredando el hash de origen (`verified`); uno que ya no coincide nunca se anota como `verified` con un hash que falló. Lo aceptado queda como `warning` en el log del trabajo. `Append` solo contrasta con el MHL 1.x los ficheros nuevos (lo ya sellado respondió entonces, o se aceptó). Un `Seal` normal sigue bloqueando como en D39. Matiza D39.
- 2026-10-03.

## D66 — Cobertura de tests al 100 % (líneas y ramas) como parte de `make ci`
- **Contexto**: tras el fallo de D65 el owner pide tests para todo lo que no los tenga. La cobertura era del 90 % (líneas y ramas) y 17 funciones no las ejecutaba ningún test (arranque y señales del servidor, app de emergencia, parada del supervisor, entre otras).
- **Opciones**: (1) tests para esas 17 funciones y un umbral en `make ci`; (2) solo esas 17; (3) todo al ~100 % y umbral.
- **Elección**: del owner, (3). Cobertura al 100 % de líneas y ramas de `src/`, sin `# pragma: no cover`, y `fail_under = 100` en `pyproject.toml`: `make test` (y con él `make ci` y la CI) falla si un camino nuevo llega sin su test. Las ramas de error se alcanzan con `monkeypatch` del colaborador que falla; los fallos de permisos con un `os.scandir` falso, porque la CI corre como root.
- 2026-10-03.

## D67 — Una carpeta sin ficheros es un proyecto borrado
- **Contexto**: el owner borra un proyecto del NAS; desde el Mac la carpeta ya no aparece, pero el contenedor la sigue viendo con su `ascmhl/` (el NAS no lo eliminó, probablemente por permisos). Para la app era un proyecto sellado al que le habían borrado todos los ficheros: revisión; `Accept as new version` intentaba apartar `ascmhl/`, fallaba con `PermissionError`, el proyecto pasaba a `error` («retried on the next round») y la ronda siguiente lo devolvía a revisión: un bucle sin salida.
- **Opciones**: (1) tratarla como borrada (`missing`, D58) e ignorarla mientras siga vacía; (2) lo mismo y además intentar borrar del NAS la carpeta vacía al dar de baja; (3) seguir en revisión con un botón «olvidar» en lugar de Accept.
- **Elección**: del owner, (1). Un proyecto son sus ficheros: una carpeta cuyo recorrido no encuentra ninguno (los ignorados por defecto, como `.DS_Store`, `ascmhl/` y `ascmhl_superseded/`, no cuentan; un error de lectura sí cuenta como «tiene ficheros») es la carpeta desaparecida de D58: `missing` desde la primera ronda, tarjeta con `Retry` y `Forget permanently`. Una carpeta vacía que no sigue la app (nueva, o de un proyecto ya olvidado) no es un proyecto nuevo, y una `missing` vacía no «vuelve», hasta que tenga ficheros. Un trabajo que arranca y encuentra la carpeta vacía acaba `cancelled` y deja el proyecto `missing`. `Retry` comprueba también que haya ficheros. La app no toca la carpeta del NAS.
- 2026-10-03 (`AskUserQuestion`).

## D68 — `Retire` pasa a llamarse `Forget permanently`
- **Contexto**: el owner, ante el bucle de D67: lo que necesitaba era «olvidar permanentemente».
- **Elección**: del owner. Solo cambian los textos de la GUI: `Forget permanently`, `Forget and download MHL`, «Forgot X» en el log, «X forgotten» al confirmar. Rutas, nombres internos y trabajo `retire` no cambian (las líneas de log antiguas se siguen leyendo). D60 sigue en vigor con el nombre nuevo.
- 2026-10-03.

---

## Pendiente de entrevista
Prioridad del NAS al llegar al backlog de 95 (¿«Seal all»?), notificaciones (D22).
