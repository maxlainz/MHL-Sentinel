# Único punto de entrada. Todo lo que corre CI es un target aquí, así local y CI están verdes o rojos a la vez (norma ci.md).
.PHONY: setup lint format typecheck test leak-check ci fixtures docker-build run help

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

test:         # pytest (TZ=UTC: ascmhl escribe fechas con el offset actual, research spec §2.7)
	@TZ=UTC uv run pytest

leak-check:   # nada del estudio en el repo (norma repo-publico.md)
	@sh scripts/leak-check.sh

ci: leak-check lint typecheck test   # el gate de cada commit

fixtures:     # genera un archivo sintético en tests/fixtures/archive (gitignored)
	@uv run python scripts/make_fixtures.py

docker-build: # construye la imagen local — hito 2
	@echo "pendiente: hito 2"

run:          # arranca la app en local sobre los fixtures — hito 1
	@echo "pendiente: hito 1"
