# Único punto de entrada. Todo lo que corre CI es un target aquí, así local y CI están verdes o rojos a la vez.
# Los targets de código quedan como placeholder hasta el hito 1 (stack pendiente de la entrevista: ver docs/decisiones.md).
.PHONY: setup lint typecheck test leak-check ci fixtures docker-build run help

help:
	@grep -E '^[a-z-]+:.*#' Makefile | sed 's/:.*#/ —/'

setup:        # instala dependencias (uv sync --group dev) — hito 1
	@echo "pendiente: hito 1"

lint:         # ruff check + ruff format --check — hito 1
	@echo "pendiente: hito 1"

typecheck:    # mypy --strict — hito 1
	@echo "pendiente: hito 1"

test:         # pytest -q — hito 1
	@echo "pendiente: hito 1"

leak-check:   # nada del estudio en el repo (norma repo-publico.md)
	@sh scripts/leak-check.sh

ci: leak-check lint typecheck test   # el gate de cada commit

fixtures:     # genera un archivo sintético en tests/fixtures/archive — hito 0
	@echo "pendiente: hito 0"

docker-build: # construye la imagen local — hito 2
	@echo "pendiente: hito 2"

run:          # arranca la app en local sobre los fixtures — hito 1
	@echo "pendiente: hito 1"
