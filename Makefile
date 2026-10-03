# Único punto de entrada. Todo lo que corre CI es un target aquí, así local y CI están verdes o rojos a la vez (norma ci.md).
.PHONY: setup lint format typecheck test leak-check attribution-check ci fixtures docker-build run help

help:
	@grep -E '^[a-z-]+:.*#' Makefile | sed 's/:.*#/ —/'

setup:        # instala dependencias (uv sync, incluye el grupo dev)
	@uv sync

lint:         # ruff check + ruff format --check
	@uv run ruff check . && uv run ruff format --check .

format:       # ruff format (escribe)
	@uv run ruff format . && uv run ruff check --fix .

typecheck:    # mypy --strict (config en pyproject.toml)
	@uv run mypy

test:         # pytest con cobertura al 100 % de líneas y ramas (D66; TZ=UTC: ascmhl escribe fechas con el offset actual, research spec §2.7)
	@TZ=UTC uv run pytest --cov --cov-report=term

leak-check:   # nada del estudio en el repo (norma repo-publico.md)
	@sh scripts/leak-check.sh

attribution-check: # sin trailers de atribución ni enlaces de sesión en commits ni en PR_BODY (norma git.md, D64)
	@sh scripts/attribution-check.sh

ci: leak-check attribution-check lint typecheck test   # el gate de cada commit

fixtures:     # genera un archivo sintético en tests/fixtures/archive (gitignored)
	@uv run python scripts/make_fixtures.py

docker-build: # construye la imagen local mhl-sentinel:dev (deploy/Dockerfile)
	@docker build -f deploy/Dockerfile -t mhl-sentinel:dev .

run:          # arranca la app en local sobre los fixtures (GUI en http://localhost:8080)
	@mkdir -p config && MHLS_ARCHIVE_ROOT=tests/fixtures/archive MHLS_CONFIG_DIR=config TZ=UTC uv run mhl-sentinel serve
