# XSD de ASC MHL (copia fijada)

Copia sin modificar de los esquemas de la implementación de referencia, para que `ascmhl-debug xsd-schema-check -xsd ...` corra en los tests sin red (el paquete `ascmhl` de PyPI no los incluye).

- Origen: https://github.com/ascmitc/mhl/tree/0fb61f1e4c7c1c3ff422449aa6f091ce0d3b7687/xsd
- Commit: `0fb61f1e4c7c1c3ff422449aa6f091ce0d3b7687` (tag `v1.2`, la versión fijada en `pyproject.toml`, D1).
- Descargado el 2026-10-01 desde `https://raw.githubusercontent.com/ascmitc/mhl/<commit>/xsd/<fichero>`.

| Fichero | SHA-256 |
|---|---|
| `ASCMHL.xsd` | `ddb8ddb6ba3d0c954dd9fcc150d432fa43257f21d7de69c30594d09c6dbf258f` |
| `ASCMHLDirectory.xsd` | `775b198d6b3234a81cb0460c3914d474f57c29b7f719f84cd9f4fac5a0a9b8b8` |
| `ASCMHLDirectory__combined.xsd` | `6e7663e2f3fa8f02c85aadd7eb6148a66a4ff25278b063d0975707f02ab6b3e1` |

Notas:
- `ASCMHL.xsd` del repo marca `<hashes>` como `minOccurs="0"` (el XSD del Apéndice A de la spec lo hace obligatorio, research spec §1.2). El manifiesto raíz de solo referencias (D29) valida contra este.
- `ASCMHLDirectory.xsd` importa `ASCMHL.xsd` desde una URL remota (`master`). Para validar la cadena se usa `ASCMHLDirectory__combined.xsd`, que importa antes la copia local, así que la URL remota no se descarga.
- Actualizar estos ficheros solo junto con una subida de versión de `ascmhl` (decisión del owner).
