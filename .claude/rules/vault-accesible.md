# Sin vault accesible no se arranca una tarea de dominio
*Norma del owner, heredada de LMT-Composer (D60 allí), adoptada aquí el 2026-10-01 (D27).*

- Antes de arrancar un hito o una tarea con contenido de dominio (manifiestos, detección de cambios, política ante cambios, scheduler) se **comprueba que el vault responde** (`obsidian_list_vaults`) y se leen las notas que esa tarea necesita (lista en `CLAUDE.md`). Si no responde: **se para y se avisa al owner**. No se trabaja «con lo que se recuerda de la nota» ni «con lo que cita el repo».
- Lotes de 3–6 lecturas, nunca más. Si el servidor se cae a media tarea, se para y se avisa.

**Por qué:** en LMT-Composer un hito entero arrancó sin leer las notas de concepto porque el servidor se había caído, y la auditoría posterior encontró diez hallazgos concentrados exactamente ahí.
