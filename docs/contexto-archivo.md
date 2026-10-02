# Contexto: el archivo que se vigila

Resumen genérico del entorno real (sin nombres, rutas ni IPs: norma `repo-publico.md`). Es lo que la app debe soportar por defecto; todo lo demás se configura.

## Disposición
- NAS QNAP con CPU x86-64 y share SMB para las estaciones. La app corre en un contenedor en el propio NAS (Container Station, D32): lee el disco en local, sin pasar por SMB.
- Estructura: `ARCHIVE/<AAAA>/<AAAA-MM>_<CLIENTE>-<CAMPANA>/`. Los proyectos están **siempre a profundidad 2**; el nivel 1 son carpetas-año. Para la app eso es simplemente "los proyectos están un nivel por debajo de la raíz" (D18): los niveles intermedios son transparentes, no se interpretan como años.
- Carpetas del nivel 1 que **no** son años ni proyectos: utilidades del NAS (`@Recycle`, `@eaDir`, `#snapshot`), y carpetas de soporte del estudio con prefijo `_` (`_RESOURCES`, `_MIGRACION`). Regla por defecto: ignorar lo que empiece por `_`, `@`, `#` o `.` (D19).
- Orden de magnitud a 2026-10: ~100 proyectos, de unos pocos GB a varios TB cada uno, ~5 TB/año de crecimiento. Un proyecto típico de spot: 20–200 ficheros, 5–30 GB. Un largo: 1–5 TB, miles de ficheros (secuencias EXR).
- Dentro de cada proyecto: `00_README.md` y subcarpetas numeradas (`01_MASTERS` … `11_IA`). Las que no aplican no existen.
- A fecha de arranque **no hay ningún `ascmhl/` ni `.mhl` en el archivo**: la app parte de cero.

## Lo que exige el protocolo de archivado del estudio (resumido, sin citar el documento)
- Todo proyecto archivado lleva manifiesto de checksums ASC MHL; sin manifiesto no está archivado.
- Hash: el protocolo admite xxHash64 o MD5. **Decidido (D30)**: la app escribe xxh128; el protocolo del estudio se actualiza.
- Convención de nombre de manifiesto en la raíz del proyecto: `00_MANIFEST.mhl`. Choca con la estructura `ascmhl/` de la spec. **Decidido (D13)**: la app escribe solo `ascmhl/`; el protocolo del estudio se actualiza.
- Verificación anual de todo el archivo, con fecha anotada en el README del proyecto. **Decidido**: la app verifica cada 90 días (D23) y no toca el README (D14); los `*.md` pueden excluirse del manifiesto.
- Reapertura: se añaden ficheros como versión nueva, nunca se sobrescribe; se genera manifiesto nuevo. Para la app: un proyecto que cambia es normal y esperable, no un error.
- Los proyectos pueden contener manifiestos MHL 1.x de origen (los del volcado de tarjetas con Silverstack/Hedge). Son parte de la cadena de custodia: la app los verifica y hereda su hash al sellar (D39).
- Snapshots inmutables en el NAS (≥ 90 días). El NAS tiene RAID. Segunda copia fuera del NAS pendiente de decidir (Glacier Deep Archive o discos).

## Consecuencias para el diseño
- inotify no es fiable como fuente de verdad (cambios hechos desde clientes SMB pueden no llegar al contenedor): scan programado con snapshot.
- El coste de lecturas en horario de trabajo es el requisito central: en horario laboral (L–V 09:00–19:00 por defecto, D33) la app no hace scan, hash ni verificación (salvo un `Verify now` confirmado tras aviso, D63); fuera de él trabaja a tope con un solo lector (D34).
- Lo normal es que producción pulse `Seal` al terminar de archivar; el sellado automático a los 7 días sin cambios es la red de seguridad (D31).
- Ficheros borrados o modificados en un proyecto cerrado son una anomalía: revisión humana (D9, D17).
- Un proyecto entero que desaparece es raro pero ocurre con los años (expurgo o accidente): pasa a `missing` y producción lo da de baja (`Retire`, borra todo salvo una línea de log) o reintenta (`Retry`); el historial se espeja en `/config/history/` para poder descargarlo antes (D58–D62).
- Ficheros añadidos (reapertura, versiones nuevas) son normales: nueva generación automática (D9).
- Los ~95 proyectos preexistentes no se sellan solos: botón `Sellar` (D15, D16).
