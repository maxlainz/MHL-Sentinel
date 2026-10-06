# 11 — `Seal now` / `Accept now` (2026-10-06)

**TL;DR**: un `Seal` o `Accept as new version` que espera a que acabe la jornada se puede arrancar ya con `Seal now` / `Accept now`, en la ficha y en la Bandeja, tras confirmar los GB que se van a leer del NAS. Reutiliza el `bypass_hours` de `Verify now` (D63). Decisión D73.

## Qué se hizo
- Entrevista: el botón aparece solo en el proyecto en cola (o en marcha y pausado por el horario), junto a `Cancel`; pide confirmación con el tamaño; vale también para `Accept`.
- `sealer.request_start_now`: un trabajo manual `seal`/`accept_new_version` en cola pasa a `bypass_hours`. `Supervisor.request_start_now`: el trabajo en marcha se marca y cede el sitio (vuelve a la cola con sus checkpoints, D28) y el hasher lo retoma en el acto con la puerta siempre abierta. `notify_job_queued` también hace ceder a un trabajo que el hasher tomó justo antes de marcarlo (carrera encontrada en la implementación).
- Ruta `POST /projects/{id}/start-now`; diálogo de confirmación compartido con `Verify now`; botón solo en horario laboral.
- Tests: 470 (sello pausado a mitad que se retoma en horario laboral y valida con `ascmhl-debug verify`; rechazos; GUI). Cobertura 100 %.

## Siguiente paso
Comprobar en el NAS, tras la actualización de Watchtower, un `Seal now` de un proyecto pequeño en horario laboral; sirve también para medir MB/s del hasher (predicción en bitácora 02). Lo demás de la bitácora 10 sigue en pie.

## Vault
Sin conceptos nuevos: es una función de la app sobre conceptos existentes (`Ventana de inactividad (quiet hours)`).
