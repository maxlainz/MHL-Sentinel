# Roadmap

Cada hito termina con: tag `v0.N.0`, `CHANGELOG`, bitácora, `CLAUDE.md` al día. Hito 0 = `v0.0.1`.

| Hito | Versión | Qué entra | Criterio de cierre |
|---|---|---|---|
| 0 Arranque | v0.0.1 | Decisiones D1–Dn de la entrevista, esqueleto del repo, fixtures sintéticos, **spike**: escribir una generación ASC MHL con hashes precalculados vía `mhllib` y validarla con `ascmhl-debug verify` | Spike verde o decisión alternativa registrada |
| 1 Núcleo sin GUI | v0.1.0 | Detección de proyectos por niveles (D18, D19); scan incremental con snapshot en SQLite; hasher secuencial con checkpoint por fichero y varios algoritmos por lectura (D28, D34); verificación y herencia de MHL 1.x de origen (D39); generación por proyecto con `mhllib`; CLI `run-once` | `make ci` verde; manifiesto de un proyecto de fixtures validado por la referencia |
| 2 Daemon y Docker | v0.2.0 | Horario laboral TZ-aware (D33), pausa/reanudación, SIGTERM limpio, imagen multi-arch en GHCR, compose de ejemplo y plantilla QNAP Container Station (D32) | Corre 24 h sobre fixtures en un contenedor sin intervención |
| 3 GUI mínima | v0.3.0 | Una pantalla (D11): estado, lista con semáforo, revisión, `Seal`, `Ignore`, `Run scan now`; Ajustes (D25); sin login (D35) | Boceto aprobado por el owner antes de implementar |
| 4 Manifiesto raíz y verificación | v0.4.0 | Historial de solo referencias en la raíz (D29); verificación cada 90 días escalonada (D23) con resultados en DB; comprobar que Silverstack/Hedge abren la raíz | Validado por la referencia; medido sobre el NAS real (predicción antes) |
| 5 Operación | v0.5.0 | Notificaciones (Apprise), plantilla Unraid/Synology, healthcheck de montaje stale | — |

Fuera de alcance por ahora: modo solo-lectura con sidecars, inotify como fuente primaria, throttle en bytes/s (D34), segunda copia (D24). Los MHL 1.x de origen sí entran (D39, hito 1).
