# MHL Sentinel

Servicio en contenedor Docker, con GUI web mínima, que vigila un directorio de archivo de proyectos terminados montado desde un NAS y mantiene un historial **ASC MHL** por proyecto. **Promesa**: todo proyecto archivado tiene manifiesto; lo que hay hoy coincide bit a bit con lo archivado; no falta nada; y queda constancia fechada de cada verificación. Respeta un horario de inactividad configurable para no cargar el servidor en horas de trabajo.

**TL;DR.** Estado: **MVP (`v0.4.0`)**: contenedor con GUI, sellado, revisión, raíz de referencias y verificación periódica. Imagen `ghcr.io/maxlainz/mhl-sentinel`. Instalación en `deploy/README.md`. Pendiente de medir sobre un NAS real. Ver `CHANGELOG.md`, `docs/roadmap.md` y, para trabajar en el repo, `CLAUDE.md`.

## Qué es, y qué no es
- Es un vigilante de integridad para archivos de proyectos **terminados**: pocos cambios, muchos TB, lecturas caras.
- Usa la implementación de referencia de ASC MHL (`ascmhl`) como oráculo: los manifiestos que escribe deben validar con ella y abrirse en Silverstack, Hedge o cualquier verificador ASC MHL.
- **No** es una herramienta de offload ni de copia (eso lo hacen Silverstack, OffShoot, YoYotta). **No** repara ficheros. **No** decide qué se archiva.

## Cómo funciona (previsto)
1. Detecta los proyectos del archivo por nivel de carpeta: eliges la raíz y a cuántos niveles por debajo están los proyectos; lo intermedio (años, meses, clientes) es transparente. Exclusiones por defecto y botón `Ignore`.
2. Escanea de forma incremental (tamaño y mtime contra un snapshot en SQLite). Durante el horario laboral configurado (por defecto L–V 09:00–19:00) no toca el servidor; fuera de él trabaja a tope con un solo lector.
3. Cualquier proyecto sin manifiesto se sella con el botón `Seal`; un proyecto nuevo que lleva 7 días sin cambios (configurable) se sella solo como red de seguridad. Hash xxh128; si el proyecto trae MHL 1.x de origen (volcado de tarjetas), los verifica y hereda su hash en el manifiesto nuevo. Ficheros añadidos generan una nueva generación automática; ficheros modificados o borrados dejan el proyecto en revisión hasta que una persona pulsa `Accept as new version` o `Postpone`. Solo escribe `<proyecto>/ascmhl/`; nada más en el proyecto.
4. Mantiene en la raíz del archivo un historial de solo referencias a los manifiestos de cada proyecto, sin releer ficheros.
5. Cada 90 días re-verifica cada proyecto, escalonado y solo en ventanas, para detectar corrupción silenciosa.
6. Una GUI de una sola pantalla, en inglés, para producción: estado, lista de proyectos con semáforo, revisión, `Run scan now` y ajustes.

## Instalación
Imagen multi-arch (amd64, arm64) en `ghcr.io/maxlainz/mhl-sentinel`. Dos volúmenes: `/archive` (el directorio vigilado; la app solo escribe `<proyecto>/ascmhl/`) y `/config` (`config.yaml` + `state.db`, en disco **local**, nunca en un share de red). La GUI (puerto 8080) no lleva login: pensada para la LAN; tras un proxy con autenticación si se expone fuera.

`docker-compose.yml` (también en `deploy/`):
```yaml
services:
  mhl-sentinel:
    image: ghcr.io/maxlainz/mhl-sentinel:latest
    container_name: mhl-sentinel
    restart: unless-stopped
    init: true
    stop_grace_period: 60s          # SIGTERM: termina el bloque en curso y guarda el checkpoint; nunca una generación a medias
    ports:
      - "8080:8080"
    environment:
      PUID: "1000"                  # usuario del NAS con permiso de escritura en el archivo (id <usuario>)
      PGID: "1000"
      MHLS_TIMEZONE: "Europe/Madrid"   # zona del horario laboral; el proceso corre en UTC
    volumes:
      - "/ruta/del/archivo:/archive"          # el archivo vigilado
      - "/ruta/local/mhl-sentinel:/config"    # config.yaml + state.db: disco LOCAL
```
```sh
docker compose up -d
# luego http://<host>:8080 → Settings (niveles, horario laboral) → Seal en un proyecto pequeño
```
El tag `latest` sigue siempre a la última release: el contenedor puede actualizarse solo con Watchtower o con `docker compose pull && docker compose up -d` (servicio Watchtower opcional en `deploy/docker-compose.yml`). La app está hecha para que la recreen sin avisar: nunca deja una generación a medias y retoma el trabajo donde iba. Guía para QNAP Container Station, auto-actualización y variables en `deploy/README.md` y `.env.example`.

## Desarrollo
`make setup` · `make fixtures` · `make run` (GUI en http://localhost:8080 sobre el archivo sintético) · `make ci`.

## Cómo se trabaja
El método (router, normas, decisiones, bitácora, entrevista antes de suponer, predicción antes de medir) está en `CLAUDE.md` y `.claude/rules/`.

## Licencia
MIT. Ver `LICENSE`.
