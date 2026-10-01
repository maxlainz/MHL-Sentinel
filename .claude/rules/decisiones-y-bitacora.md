# Decisiones y bitácora
*Norma del owner, 2026-10-01 (D4).*

- Una entrada `Dn` en `docs/decisiones.md` por decisión: contexto, opciones, elección, fecha. Nunca se borra una decisión; se marca **sustituida por Dm**. Nunca se renumera.
- Una entrada `docs/bitacora/NN-slug.md` por sesión de trabajo, con TL;DR al principio y "Siguiente paso" al final.
- Los hallazgos medidos se numeran `Hn` y se citan desde el código (`# H3: ...`). Ningún número entra en docs sin el comando que lo reproduce y su fecha.
- `CLAUDE.md` refleja el estado actual: se actualiza en cada commit que cambie estructura, estado o comandos.
- Cierre de sesión: conceptos nuevos documentados en el vault (norma `obsidian.md`) y la bitácora nombra las notas escritas.
- `CHANGELOG.md` (Keep a Changelog, categorías en español: Añadido / Cambiado / Corregido / Decidido / Medido / Eliminado) se actualiza en cada commit con cambio visible; `[Unreleased]` siempre existe.

**Por qué:** la próxima sesión arranca leyendo el router y la última bitácora; si no están al día, se repite trabajo o se reabre lo decidido.
