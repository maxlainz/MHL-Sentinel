# 03 — Patch `v0.4.3` (Cancel), frontend «Bandeja» `v0.5.0` y `main` protegida (2026-10-01)

**TL;DR.** Dos encargos del owner en paralelo: (1) poder cancelar un `Seal` programado para fuera de horario, llevado a producción como patch `v0.4.3` (D53, issue #4, PR #6); (2) rehacer el frontend entero: cuatro subagentes Opus con libertad creativa producen cuatro propuestas independientes (ramas `feat/5-frontend-a..d`, documento con bocetos en texto en `docs/propuestas/`), se levantan en local en cuatro puertos y el owner elige (issue #5).

## Cancel (D53)
- `sealer.request_cancel` + `sealer.cancellable_job`: solo un `seal` o `accept_new_version` **manual** en cola. El job pasa a `cancelled`, el proyecto vuelve a `unsealed` o `needs_review` (conservando `review_reason`), los hashes de la caché (D28) se conservan. Un trabajo parado por el horario laboral vuelve a la cola y por tanto también se puede cancelar.
- No se cancelan trabajos automáticos (el siguiente ciclo los reencolaría) ni en marcha (exigiría abortar el hilo hasher; otro issue si hace falta).
- `POST /projects/{id}/cancel`; botón `Cancel` en la tarjeta. Tests en `test_sealer.py` y `test_web.py`; 161 tests.
- Vault: sin conceptos nuevos (cancelar un trabajo en cola es mecánica de la app, no concepto de archivo).

## Frontend (#5)
- Restricciones dadas a los subagentes: sin build de Node (D36), inglés (D12), mismas rutas y eventos SSE, sin tocar `sealer`/`supervisor`/`db`, `make ci` verde, boceto en texto de cada pantalla (D4).
- Direcciones sugeridas (con libertad para desviarse): A libre, B lenguaje de Silverstack/Hedge o tablero tranquilo, C tabla densa con panel lateral y tema oscuro, D bandeja de entrada / triaje.
- Resultado (2026-10-01): las cuatro ramas pasan `make ci` (163–165 tests) y respetan el contrato; todas retiran Pico CSS por una hoja propia y cambian «idle window» por «working hours» (D33). A y B parten de `1539310` (antes del Cancel) y no muestran el botón hasta rebasar; C y D parten del commit del Cancel.
  - **A** «Turno de noche / parte de relevo»: palabra grande Resting / On shift, barra de 24 h, estanterías por urgencia, Seal en fila, columna con cola y últimos 7 días.
  - **B** «Turno de noche / tablero»: frase + tira de 24 h, veredicto y mapa del archivo (un cuadrado por proyecto, por año), bandeja de lo rojo, filtros en la URL.
  - **C** «Media Pool»: una sola tabla ordenable y filtrable, ficha en panel lateral, barra de estado fija, oscuro por defecto.
  - **D** «Bandeja»: frase de estado, solo lo que pide decisión (Seal/Cancel de un clic), resto plegado, log de actividad al estilo Hedge.
- Demo local: cada rama levantada desde su worktree en los puertos 8101–8104 con una copia de los fixtures, `run-once --seal-all`, estados sembrados (sellado+verificado, revisión, en cola manual, sin manifiesto, ignorado) y horario laboral 00:00–23:59 para congelar el estado. Capturas con Chrome sin cabeza a través de un proxy que responde 404 a `/events` (la conexión SSE impide que Chrome dé la página por cargada). Comparativa entregada al owner como artifact.
- Elección del owner (D54): **D**, con dos cambios: contenedor más ancho y acabado claro y refinado («no tan oscuro denso gamer; es una app profesional pero no se usa en la sala de color»). El agente de D rebasó sobre `main` (entra el Cancel), ensanchó a 1440 px y pasó a tema claro cálido por defecto con oscuro suavizado siguiendo al sistema. Ramas A, B y C borradas sin push.
- Revisión adversarial (Opus, solo lectura) sobre la rama D: 10 hallazgos, todos corregidos con tests (170 → 174). Los dos graves: el titular decía «Every project is sealed» con proyectos en cola o leyéndose, y el log de actividad mostraba «Verified X» aunque la verificación hubiera encontrado corrupción (el job acaba `done` al mandar a revisión). Lo segundo se detecta ahora por `sealer.REVIEW_LOG_PREFIX`, compartido entre sealer y GUI; un campo explícito en `jobs` sería más robusto y queda como mejora.
- Tema configurable (D56): el owner vio el prototipo en oscuro porque su Mac está en modo oscuro; pidió auto / claro / oscuro en Ajustes, auto por defecto. Campo `theme` en `config.yaml` (`MHLS_THEME`).
- Integración: PR #8 (squash) con la CI verde; release `v0.5.0` por rama `chore/release-v0.5.0` siguiendo la norma nueva. En `docs/roadmap.md` la GUI entra como hito 4b `v0.5.0` y Operación pasa a `v0.6.0`.

## `main` protegida (D55)
El owner pidió «dejar de trabajar en main y bloquear la rama, como norma». Norma `rama-main-protegida.md`; protección activa en GitHub (PR obligatorio, check `ci`, rama al día, sin force-push ni borrado, también para el administrador); `git.md`, `pull-y-push.md` y la skill `release` actualizadas (PR #7).

## Vault
Sin conceptos nuevos de archivo: el trabajo de la sesión es de interfaz y de workflow (cancelar un trabajo en cola, protección de rama, tema de la GUI), no de dominio.

## Siguiente paso
Comprobar en el NAS que Watchtower ha subido `v0.5.0` (`/healthz` → `version`), primer `Seal` de un proyecto pequeño y medida de MB/s del hasher (predicción en bitácora 02), backlog de 95 proyectos a ritmo de ventanas, MediaVerify sobre la raíz (#3). Mejora pendiente de la GUI: resultado explícito del trabajo en `jobs` en vez de detectar la revisión por el texto del log.
