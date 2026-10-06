# 10 — Las etiquetas de Finder son de la app (2026-10-06)

**TL;DR**: con la opción de etiquetas encendida, la app es dueña de las etiquetas de cada carpeta de proyecto: borra las que hubiera, deja solo la de su estado y corrige en cada pasada fuera del horario laboral cualquier etiqueta cambiada a mano. Apagada, no toca nada (D72). Publicado como patch `v0.7.2`.

## Qué se hizo
- Entrevista: limpiar todas las etiquetas (no solo las de color ni solo las «MHL …»), corregir solo fuera del horario laboral, y con la opción apagada no tocar nada.
- `finder_tags.py`: `apply` deja exactamente `wanted(tag)` y sobrescribe también un atributo que no sabe leer; «sin etiqueta» se escribe como lista vacía. `TagSync` ya no recuerda lo escrito: relee todas las carpetas en cada llamada (un xattr pequeño por proyecto) y solo escribe si difiere; los fallos se avisan una vez por proyecto y etiqueta, y otra vez si vuelven tras un éxito. Fuera: `merged` y el borrado del atributo.
- Ajustes: el texto de ayuda explica que la app es dueña de las etiquetas.
- Tests: etiquetas del equipo borradas, etiquetas falseadas corregidas, atributo ilegible sobrescrito, relectura en cada pasada, nada con la opción apagada.

## Predicción (sin medir)
Que el Finder no muestre el color antiguo de la información de Finder de una carpeta cuando su lista de etiquetas está vacía. Añadido al #24 junto a la comprobación del formato.

## Siguiente paso
Medir en el NAS (#24) antes de activar la opción allí. Lo demás de la bitácora 08 sigue en pie.

## Vault
Sin acceso al vault en esta sesión (el MCP de Obsidian no estaba cargado). Pendiente: ampliar `Etiquetas de Finder (atributo _kMDItemUserTags)` con el color de la información de Finder como respaldo cuando no hay atributo de etiquetas (de memoria, sin verificar).
