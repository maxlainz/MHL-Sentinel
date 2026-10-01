# Repo público: nada del estudio entra en el repo
*Norma del owner, 2026-10-01 (D3).*

- Nunca se versionan: nombres de clientes, campañas o productoras; listados reales del archivo; IPs, nombres de host o shares del NAS; credenciales; el protocolo de archivado del estudio (PDF) ni su texto literal; material de proyectos (vídeo, audio, LUTs, .drp).
- En docs, issues y tests se usa la nomenclatura de plantilla: `AAAA-MM_CLIENTE-CAMPANA`, `01_MASTERS`, etc. Los fixtures de test se generan sintéticamente (`tests/fixtures/`, script `make fixtures`).
- Lo que el estudio decidió en su protocolo se resume en `docs/contexto-archivo.md` en términos genéricos ("el estudio exige manifiesto por proyecto"), sin citar el documento.
- Antes de cada push: `make leak-check` (grep de patrones prohibidos sobre el árbol versionado).

**Por qué:** el repo es público desde el día 0; lo que entra en el historial de git no sale.
