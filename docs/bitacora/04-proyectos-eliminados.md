# 04 — Proyectos eliminados: `missing`, `Retire`/`Retry`, espejo del historial y `Verify now` (2026-10-02)

**TL;DR.** El owner plantea la casuística de un proyecto que desaparece entero del archivo (expurgo o accidente, común con los años). Entrevista con `AskUserQuestion` en tres rondas (D58–D63): estado `missing` al primer scan con cortafuegos mínimo (raíz vacía o ilegible), espejo del `ascmhl/` de cada proyecto en `/config/history/` para que el historial sobreviva al borrado, `Retire` que borra todo salvo una línea de log (con opción de descargar el MHL antes), `Retry` que comprueba la carpeta al momento, reconocimiento de carpetas movidas por la cadena, y un `Verify now` por proyecto que salta el horario con aviso. Implementado en la misma sesión (núcleo con Opus, web con Sonnet, revisión adversarial con Opus) en la rama `claude/sweet-franklin-e9ot1a`.

## Entrevista (resumen; detalle en D58–D63)
- Detección: el owner elige «al primer scan» (no dos fases) y el cortafuegos mínimo (solo raíz vacía o ilegible, no un umbral del 20 %).
- Conservación: espejo del historial (recomendación aceptada) frente a reconstruir desde la DB o solo DB.
- Bandeja: `Retire` y `Retry`; el owner descarta «Olvidar» porque `Retire` ya lo borra todo: «no hay que conservar restos de cosas que se sobreentiende se han eliminado a conciencia». Popup con `Retire`, `Retire and download MHL`, `Cancel`. Única huella: una línea en el log de actividad.
- Sin botón de baja anticipada en un proyecto sellado: se borra la carpeta y se da de baja cuando salte.
- Reaparición: verificación automática y vuelta a `sealed` si cuadra. Carpeta movida: mismo proyecto.
- Nuevo de la entrevista: `Verify now` por proyecto, saltándose el horario laboral con aviso de rendimiento (sin «Verify all»).
- Boceto aprobado: tarjeta roja «not on disk since …» con `Retire`/`Retry`; popup con la advertencia de que la carpeta del archivo no se toca.

## Hallazgos
- Revisión adversarial (Opus) sobre el árbol antes del commit, 8 arreglos con test de regresión: (1) un listado SMB fallido de una carpeta-año habría marcado `missing` todos sus proyectos y, al volver, encolado teras de verificación: ahora `missing` exige un `lstat` que confirme que la carpeta no existe; (2) `Retire` rechaza mientras un trabajo del proyecto sigue parándose; (3) un `Verify now` en horario no arrancaba si otro trabajo estaba pausado por la puerta: el trabajo pausado cede el hilo (vuelve a la cola con sus checkpoints); (4) el espejo podía mezclar dos historiales: lo que ya no está en disco se aparta a `ascmhl_superseded/` y un `RLock` evita que scan y hasher espejen a la vez; (5–8) textos del log y de los avisos, y `Retire` sin JavaScript. Quedan como riesgo bajo, anotados en el informe: verify perdido si el proyecto vuelve en `changed`, raíz sin regenerar si todos los sellados desaparecen a la vez.
- Prueba manual end-to-end sobre fixtures (CLI y Chromium con Playwright): borrar un proyecto → `missing` y espejo en `/config/history`; diálogo `Retire and download MHL` → zip con cadena y manifiesto, baja, aviso, línea de log, espejo borrado; carpeta movida de año y renombrada → mismo proyecto; carpeta devuelta → verificación automática y `sealed`.
- Tres tests de la línea base fallaban el día siguiente de escribirse por depender de la fecha real (`test_sealer` ×2, `test_web` ×1); se han hecho deterministas en este cambio. Lección: todo `now` en tests va inyectado.

## Vault
Notas leídas: `Historial ASC MHL anidado`, `MHL Sentinel`, `Detección de cambios en un volumen de red`. Sin concepto nuevo de archivo: «proyecto desaparecido» es política de la app, no concepto de dominio; el espejo del historial es mecánica de copia de seguridad. La nota `MHL Sentinel` no cambia (los conceptos en que se apoya son los mismos).

## Siguiente paso
Abrir PR de la rama, integrar y cortar `v0.6.0` (skill `release`). Después, lo que quedó de la sesión 03: primer `Seal` en el NAS y MB/s del hasher, backlog de 95, MediaVerify (#3), Apprise (D22).
