# Despliegue

Imagen: `ghcr.io/maxlainz/mhl-sentinel` (amd64 y arm64), publicada por `release.yml` en cada tag `vX.Y.Z` (D38). Volúmenes: `/archive` (el directorio vigilado; la app escribe solo `<proyecto>/ascmhl/`) y `/config` (`config.yaml` + `state.db`, en disco **local** del host: ver la nota `SQLite sobre un volumen de red`; la app se niega a arrancar si detecta un sistema de ficheros de red). Puerto 8080. Variables en `.env.example`.

## Docker Compose (cualquier host)
```sh
cp .env.example .env   # ajusta MHLS_ARCHIVE_HOST_PATH, MHLS_CONFIG_HOST_PATH, MHLS_TIMEZONE, PUID/PGID
docker compose -f deploy/docker-compose.yml up -d
```

## QNAP Container Station (D32)
1. Container Station → Crear → *Create Application* (Compose) y pega `deploy/docker-compose.yml` sustituyendo las variables por valores: la ruta del share de archivo (`/share/<volumen>/<carpeta>`) y una carpeta local para `/config` (por ejemplo `/share/Container/mhl-sentinel`).
2. `PUID`/`PGID`: el usuario del NAS con permiso de escritura en el archivo (`id <usuario>` por SSH).
3. `MHLS_TIMEZONE`: la zona del estudio (el horario laboral se evalúa en ella).
4. Abre `http://<nas>:8080`. La GUI no lleva login (D35): no la expongas fuera de la LAN sin un proxy con autenticación.

## Construcción local
```sh
make docker-build                                   # mhl-sentinel:dev para la arquitectura del host
docker run --rm -p 8080:8080 -v "$PWD/tests/fixtures/archive:/archive" -v "$PWD/config:/config" mhl-sentinel:dev
```
