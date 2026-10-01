# Fixtures sintéticos

`scripts/make_fixtures.py` genera un archivo de mentira, determinista (misma semilla, mismos bytes y mtimes), en `tests/fixtures/archive/`. Se borra y recrea en cada ejecución. Solo nomenclatura de plantilla: nada del estudio (norma `repo-publico.md`).

```
archive/
  2024/
    2024-03_CLIENTE-A_CAMPANA-UNO/   spot típico (01_MASTERS, 03_GRADE, 05_DELIVERABLES) + .DS_Store y ._junk.mov
    2024-11_CLIENTE-B_CAMPANA-DOS/   02_OCF con dos .mhl 1.x reales (xxhash64be y md5) + 01_MASTERS
  2025/
    2025-01_CLIENTE-C_LARGO/         04_VFX/seq_0001 con 40 frames + máster de 2 MB
    2025-06_CLIENTE-D_CAMPANA-TRES/  solo 00_README.md (caso límite)
  _RESOURCES/  @Recycle/  #snapshot/  ignorados por defecto (D19)
  SIN-CATEGORIA_CLIENTE-E/           proyecto en la raíz (profundidad 1)
  .DS_Store                          basura en la raíz
```

Regenerar:

```sh
uv run python scripts/make_fixtures.py [--out PATH] [--seed 1] [--size small|medium]
```

`make fixtures` envolverá este comando. La salida está en `.gitignore`; nunca se versiona.
