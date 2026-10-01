# Sin rutas absolutas de usuario
*Norma del owner, 2026-10-01 (D5).*

- Ningún archivo versionado contiene `/Users/<nombre>`, `/Volumes/<share>` reales ni IPs del estudio. Se usa `$HOME`, `git rev-parse --show-toplevel`, rutas dentro del contenedor (`/archive`, `/config`) o variables documentadas en `.env.example`.
- Automatizado, no confiado a la memoria: un hook `PostToolUse` bloquea la escritura si detecta `/Users/...`.

**Por qué:** el repo es público y debe funcionar en cualquier máquina y NAS.
