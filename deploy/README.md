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

## Auto-actualización (D52)
La imagen `ghcr.io/maxlainz/mhl-sentinel:latest` apunta siempre a la última release. El `docker-compose.yml` incluye un servicio opcional de [Watchtower](https://containrrr.dev/watchtower/) que descarga `latest` una vez al día y recrea el contenedor. En QNAP Container Station se puede usar en su lugar la opción de «actualizar imagen» del propio contenedor, o cualquier herramienta que haga `docker compose pull && docker compose up -d`: el resultado es el mismo.

Qué garantiza la app cuando la recrean sin miramientos (SIGTERM con timeout corto o SIGKILL directo): ninguna generación queda a medias (se escriben en temporal y se renombran al final; un manifiesto que quede fuera de la cadena se aparta como `.orphan` al arrancar), el trabajo en curso vuelve a la cola y continúa con los hashes ya calculados (checkpoint por fichero), y `config.yaml` y `state.db` sobreviven en `/config`. Lo único que se pierde es el fichero que se estaba leyendo en ese momento, que se vuelve a leer. Dale a Watchtower un `WATCHTOWER_TIMEOUT` de 60 s para que el cierre sea limpio; con el valor por defecto (10 s) también funciona, solo que cerrando por SIGKILL.

## Construcción local
```sh
make docker-build                                   # mhl-sentinel:dev para la arquitectura del host
docker run --rm -p 8080:8080 -v "$PWD/tests/fixtures/archive:/archive" -v "$PWD/config:/config" mhl-sentinel:dev
```
