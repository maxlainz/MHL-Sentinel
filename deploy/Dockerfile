# syntax=docker/dockerfile:1.7
# MHL Sentinel — imagen multi-arch (amd64, arm64). D32, D36. Construir desde la raíz del repo:
#   docker build -f deploy/Dockerfile -t mhl-sentinel:dev .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
# --no-editable: el paquete se copia a site-packages; la etapa final solo lleva .venv, no /app/src (H18)
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/maxlainz/MHL-Sentinel" \
      org.opencontainers.image.description="ASC MHL integrity sentinel for a NAS archive of finished projects" \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update && apt-get install -y --no-install-recommends tini gosu && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /archive /config
COPY --from=build /app/.venv /app/.venv
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" \
    TZ=UTC \
    PYTHONUNBUFFERED=1 \
    MHLS_ARCHIVE_ROOT=/archive \
    MHLS_CONFIG_DIR=/config \
    MHLS_PORT=8080 \
    PUID=1000 PGID=100 UMASK=000
VOLUME ["/archive", "/config"]
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('MHLS_PORT','8080')+'/healthz',timeout=5)" || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["mhl-sentinel", "serve"]
