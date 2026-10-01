# Delegar en subagentes
*Norma del owner, 2026-10-01 (D4).*

- La sesión principal orquesta; el trabajo pesado (research, implementación bien especificada, tests, docs largas, revisión adversarial) se delega.
- Modelo por tarea: Opus para research, diseño, código delicado y revisión; Sonnet para implementación bien especificada, tests y docs; Haiku para inventarios. Nunca por encima de Opus.
- El orquestador ejecuta él mismo los checks de cierre (`make ci`, bitácora, `CLAUDE.md`).
- Las entregas de subagentes se verifican contra fuentes primarias (spec ASC MHL, código de `ascmhl`) antes de convertirse en decisión.

**Por qué:** el contexto de la sesión principal es el recurso escaso; el research y los informes largos lo agotan.
