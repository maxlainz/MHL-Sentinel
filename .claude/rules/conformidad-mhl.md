# Conformidad con ASC MHL
*Norma del owner, 2026-10-01 (D1).*

- Todo manifiesto que escriba la app debe ser leído y verificado por la implementación de referencia (`ascmhl info`, `ascmhl-debug verify`, `ascmhl-debug xsd-schema-check`) en los tests. La referencia es el oráculo, no nuestra lectura de la spec.
- La versión de `ascmhl` va fijada en `pyproject.toml`; subirla es decisión del owner y lleva su `Dn`.
- Si un comportamiento de la referencia contradice la spec, se documenta en `docs/decisiones.md` y se abre issue upstream en `ascmitc/mhl`.
- Nunca se escribe una generación parcial: la generación se escribe en temporal y se renombra al final, solo cuando todos los ficheros del proyecto tienen hash fresco.

**Por qué:** el valor del proyecto es que sus manifiestos valgan en Silverstack, Hedge o cualquier verificador ASC MHL dentro de diez años.
