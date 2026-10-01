# CI
*Norma del owner, 2026-10-01 (D5, confirmada en D38).*

- El gate es local: `make ci` (lint + typecheck + tests + leak-check). Todo lo que corre en Actions es un target del Makefile, así local y CI están verdes o rojos a la vez.
- Actions corre en Linux (barato en GitHub Free): `ci.yml` en push a `main` y PRs; `release.yml` en tags `v*` construye la imagen multi-arch (amd64 + arm64) y la publica en GHCR.
- Nunca se taggea con CI rojo.

**Por qué:** a diferencia de las apps macOS del owner, aquí no hay runners caros; la CI continua detecta regresiones de conformidad MHL antes de publicar imagen.
