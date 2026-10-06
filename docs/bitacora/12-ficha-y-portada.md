# 12 — Qué hay archivado en la ficha, y portada con buscador

**TL;DR**: la ficha de proyecto enseña qué hay archivado (resumen y fechas, desglose por carpeta y por tipo, lista de ficheros con su hash y filtro) y qué trae cada generación (D75). La portada lleva un buscador siempre visible y los sellados por fecha, del más reciente al más antiguo (D76). Todo sale de la base de datos de la app: no lee el NAS más que antes. 492 tests, cobertura al 100 %.

## Qué se hizo
- D75: panel «What's archived» y cambios por generación. Una generación escrita por la app es completa (lista todo, con entradas de directorio) o parcial (`append`, solo los nuevos); «removed» solo se cuenta en las completas. La app nunca escribe «modified» ni «removed»; aparecen solo con manifiestos de otras herramientas.
- D76: orden Needs your decision → Not sealed yet → Queue → Sealed (por fecha) → línea final con ignorados plegados; buscador fuera del fragmento que refresca SSE.
- Sin comprobar en un navegador: el CSS y el JS del buscador no tienen tests.

## Release
`v0.9.0` (PR #34 y el de release). Sin conceptos nuevos para el vault: son cambios de GUI.

## Siguiente paso
Comprobar que Watchtower sube `v0.9.0` y mirar ambas pantallas en el navegador con el archivo real.
