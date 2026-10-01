# Research: ASC MHL (spec v1.0) y la implementación de referencia `ascmhl` 1.2

*Informe de subagente Opus, 2026-10-01. En inglés (idioma del research). Las afirmaciones marcadas **[tested]** se comprobaron ejecutando `ascmhl 1.2`; **[source]** leyendo el código; **[spec]** leyendo el PDF. Lo que afecte a una decisión se verifica de nuevo antes de convertirse en `Dn` (norma `subagentes.md`).*

## TL;DR en español
1. Las historias anidadas existen y son el modelo previsto, pero `ascmhl create` en la raíz **rehashea todo** y escribe una generación nueva en **cada** proyecto. Hacerlo a diario es carísimo y engorda todas las historias.
2. Hay un atajo (`create <root> -sf <fichero>`) para una generación raíz solo con `<references>`, pero en 1.2 escribe `<hashes></hashes>` vacío y **no valida contra el XSD**. Para una raíz limpia hay que escribir el manifiesto con `mhllib`.
3. **ASC MHL no tiene operación "aceptar cambio"**: un fichero modificado queda `action="failed"` en todas las generaciones posteriores (exit 11) y uno borrado se reporta como missing para siempre (exit 10). Spec §5.6.4 Nota 2; issue #134 abierto. La app necesita una política (avisar, y con aprobación del operador archivar el `ascmhl/` viejo y empezar historia nueva, o registrar `failed` y seguir).
4. `create` escribe la generación **antes** de fallar: cada ejecución sobre un proyecto cambiado añade un manifiesto más.
5. No hay detección barata de cambios: `diff` solo compara conjuntos de rutas (ni tamaño ni mtime) y es O(ficheros × registros) (20k ficheros: 7,6 s; 40k: 42,6 s de CPU). Hay que hacer el fast path (size+mtime) en la app.
6. La historia no puede escribirse fuera del árbol (issue #131). En árbol de solo lectura `create` revienta con `PermissionError`.
7. Hash: **xxh128** (default de la referencia) para historias nuevas; respetar xxh64 donde ya exista. sha256 no es ASC MHL.
8. Escrituras no atómicas (sin temp+rename, sin fsync; issue #173 sobre SMB): lock por proyecto y copia de `ascmhl_chain.xml` antes de cada run.
9. `TZ=UTC` en el contenedor: `lastmodificationdate` se escribe con el offset UTC actual, no el del instante del fichero.
10. `._*`, `Thumbs.db`, `@eaDir`, `#recycle` **no** se ignoran por defecto; añadir patrones en el primer `create` (solo se pueden añadir, nunca quitar).
11. No existe ningún proyecto open-source de verificación ASC MHL programada o continua de un archivo: el nicho está libre.

---

## 1. The specification (v1.0, file format "2.0")
Sources: https://github.com/ascmitc/mhl-specification (Specification v1.0, 15 Mar 2022; Implementation Guidelines v1.0, 29 Mar 2023), XSDs at https://github.com/ascmitc/mhl/tree/master/xsd. XML carries `version="2.0"`, namespaces `urn:ASC:MHL:v2.0` and `urn:ASC:MHL:DIRECTORY:v2.0`. No spec revision after 1.0 found; spec repo idle since 2023-05.

### 1.1 Folder layout, names and chain
- History = `ascmhl/` directory at the root of the scope, with manifests + chain (§5.3.1). Optional `README.txt`.
- Manifest name (§6.3): `<NNNN>_<foldername>_<YYYY-MM-DD>_<HHMMSSZ>.mhl`, 4+ digits from 0001, **UTC**, timestamp at start of operation (reference computes it at commit time per history: minor deviation).
- Chain `ascmhl_chain.xml` (§7): `<ascmhldirectory>` with `<hashlist sequencenr="N"><path/><c4/></hashlist>`. Every manifest protected by **C4 (SHA-512 base58)**. Reference re-hashes every manifest on load: exit 31 on mismatch, 33 if missing (README's "chain not verified" is outdated).
- Collection `ascmhl_collection.xml` (§8): same schema, independent scopes; used by flatten ("packing lists").
- Manifests are immutable (§5.2).

### 1.2 Manifest structure (§6.4)
- `<hashlist version="2.0">` → `<creatorinfo>`, `<processinfo>`, `<hashes>`, optional `<metadata>`, optional `<references>`.
- Schema inconsistency: text + repo XSD make `<hashes>` optional ("at least one of hashes or references"); Appendix A XSD makes it mandatory. Empty `<hashes/>` is always invalid.
- `<creatorinfo>`: required `creationdate`, `hostname`, `tool[@version]`; optional `author` (repeatable; `email`, `phone`, `role`), `location`, `comment` (guidelines: use for absolute source/destination paths).
- `<processinfo>`: `process` ∈ {`in-place`, `transfer`, `flatten`}; optional `roothash`; optional `ignore/pattern`. Reference `create` always writes `in-place`.
- `<hash>`: `<path size creationdate lastmodificationdate>` relative POSIX path; then one or more of `c4|md5|sha1|xxh128|xxh3|xxh64`; optional `previousPath`, `metadata`. **No sha256** (issue #142). ARRI HDE emits sha256 in "ascmhl v2": non-conformant.
- `action` ∈ {`original`, `verified`, `failed`} (reference uses `new` internally and converts to `verified`). `failed` hashes must never be used as reference (§5.6.4).
- `<directoryhash>` (§6.5.2, App. G): `path` + `<content>` (hash of sorted child hashes, rename-invariant) + `<structure>` (hash of sorted `H(name||child_hash)`, changes on rename). Root stored as `<processinfo><roothash>`. **Note 1: for a child that is root of a nested history, content/structure "can be taken from the latest roothash of the nested history"** — the spec hook for a parent without re-hashing. Reference doesn't use it.
- Encoding (App. D): hex lowercase; xxh* seed 0 canonical big-endian = Python `xxhash.xxhNN().hexdigest()`; c4 = SHA-512 → base58, "c4" prefix, 90 chars.
- `<references>/<hashlistreference>`: `<path>` relative to scope (e.g. `A002R2EC/ascmhl/0002_….mhl`) + `<c4>`. No `<previousmhl>`; nesting only via references.

### 1.3 Nested histories (§5.3.2, §5.3.3, §5.6.2 Note 3; Guidelines §2.1.4)
- Closest history wins: a file's record lives in the nearest history; parents hold hashes only for files outside nested dirs + references.
- Propagation is one-way, down: a verification at a higher level creates generations in all nested histories and references them. Child updates do **not** propagate up; parent references become outdated.
- Creating a parent over existing children → parent manifest with references (§5.6.2 Note 3).
- A spec-conformant verify at parent level re-hashes everything (matches reference and Pomfort MediaVerify behaviour).
- **Can the top level reference project histories instead of re-hashing?** By the spec: yes (references only; directory hashes from child `roothash`). Semantics: "these child manifests exist and are intact (C4)", not "I verified the files today". By the reference CLI: only via the `-sf` trick, which emits an XSD-invalid empty `<hashes>`. Recommendation: write the parent manifest with `mhllib` (`MHLHashList` with `hash_list_references` + `roothash` derived from children) or patch the writer to omit empty `<hashes>`.

### 1.4 Ignore (§5.6.1.2, App. C)
gitignore syntax, stored per manifest. Defaults: `.DS_Store`, `ascmhl`, `ascmhl/`. `._*`, `Thumbs.db`, `.Spotlight-V100`, `.Trashes`, `.fseventsd`, `@eaDir`, `#recycle` are **not** ignored by default **[tested: `._a.mov` got hashed]**. Patterns can only be added, never removed (Guidelines §2.4).

### 1.5 Required operations (§5.6)
Create, Diff (paths only), Verify (must append a generation), History Append, Rename (`previousPath`), Flatten (single standalone manifest, no directory hashes, `failed` dropped, earliest hash per algorithm, not appended to history).

---

## 2. Reference implementation `ascmhl`
### 2.1 Package facts
Version **1.2** (2025-07-04); earlier 1.1 (2024-12), 1.0.4 (2024-08), 1.0 (2024-03). Python ≥ 3.11. MIT. Deps: `Click~=8.1.7` (blocks 8.3.3 security fix, issue #175), `lxml`, `packaging`, `pathspec`, `requests`, `xxhash`, `python-dateutil`, `importlib-metadata`. **The CLI phones GitHub on every run** (`cli.update.Updater` fetches releases/latest in a thread); calling the library avoids it. Entry points: `ascmhl` (create, diff, flatten, info) and `ascmhl-debug` (verify, xsd-schema-check, hash). Docs: https://ascmhl.readthedocs.io/ (lags master).

### 2.2 CLI flags (v1.2)
- `create ROOT`: `-v`, `-h {md5,sha1,xxh128,xxh3,xxh64,c4}` (repeatable, default xxh128), `-n` no directory hashes, `-dr` detect renaming, `-sf PATH` (repeatable, no completeness check), `-i`, `-ii`, `--author_*`, `--location`, `--comment`. No `--root_only`, no `--no_hash`, no sidecar/destination option.
- `diff ROOT`: `-v`, `-i`, `-ii`.
- `flatten ROOT DEST`: writes `packinglist_<root>_<date>_<time>Z.mhl` with `process=flatten`; walks nested histories; loses `lastmodificationdate` **[tested]**.
- `info [ROOT]`: `-v`, `-sf FILE`.
- `ascmhl-debug verify ROOT`: `-v`, `-i`, `-ii`, `-dh`, `-co`, `-ro` (only with `-dh -co`; still hashes every file), `-h`, `-sf`, `-pl FILE` (packing list). Never writes a generation.
- `ascmhl-debug xsd-schema-check FILE [-df] [-xsd PATH]` (default XSD path is relative; pass `-xsd`).
- Exit codes: 10 missing files, 11 verification failed, 12 directory hash failed, 20 single file not found, 21 new files (verify/diff), 30 no history, 31 modified manifest, 32 no chain, 33 missing manifest, 1 uncaught exception.

### 2.3 `create` on modified/added/deleted **[tested + source]**
- Added: hashed, `original`, exit 0.
- Modified: compared to the **first** hash of that algorithm; recorded `failed`; generation written; exit 11; stays `failed` forever.
- Deleted: in union of all generations → missing on every later run; generation written; exit 10.
- Unchanged: fully re-hashed, `verified`. Every generation lists every file (~230 B/file/generation; 4.6 MB per generation for 20k files).
- Format change: `-h xxh128` on an xxh64 history computes both in one read, verifies with xxh64, records xxh128 as `verified`.
- Size/mtime are recorded but never used for decisions; no check for a file changing during hashing.

### 2.4 Diff and cheap change detection
`diff` only reports new/missing paths; O(N × records) inner loop (20k files: 7.6 s; 40k: 42.6 s CPU). Do not use as a polling primitive. Own scan of `(relpath, size, mtime_ns)` against SQLite.

### 2.5 Renames
`-dr` matches new↔missing by hash, writes `previousPath`; records renamed file as `original` instead of `verified` (issue #157); duplicate-content files confuse it; O(new × missing). No `rename` command.

### 2.6 Python API (`mhllib`, usable but not stable)
| Module | Contents |
|---|---|
| `ascmhl.history` | `MHLHistory.load_from_path(root)` (recursive, child histories, chain C4 check), `load_from_packing_list_path`, `find_history_for_path`, `find_original_hash_entry_for_path`, `set_of_file_paths`, `write_new_generation` |
| `ascmhl.generator` | `MHLGenerationCreationSession(history, ignore_spec)`: `append_file_hash(path, size, mtime, fmt, hash)`, `append_multiple_format_file_hashes`, `append_multiple_format_directory_hashes`, `commit(creator_info, process_info)` (children bottom-up, references added to parents) |
| `ascmhl.hashlist` | `MHLHashList`, `MHLMediaHash`, `MHLHashEntry`, `MHLCreatorInfo`, `MHLProcessInfo`, `MHLProcess`, `MHLTool`, `MHLAuthor`, `MHLHashListReference` |
| `ascmhl.hashlist_xml_parser` / `chain_xml_parser` | parse (lxml iterparse) / write |
| `ascmhl.hasher` | `hash_file`, `multiple_format_hash_file` (1 MiB chunks), `DirectoryHashContext`, `hash_of_hash_list` |
| `ascmhl.traverse` | `post_order_lexicographic(root, pathspec)` |
| `ascmhl.ignore` | `MHLIgnoreSpec` |
| `ascmhl.commands` | plain functions `create_for_folder_subcommand`, `diff_entire_folder_against_full_history_subcommand`, `verify_entire_folder`, `flatten_history` |
Caveats: results only as log text + `click.ClickException`; module-global verbose flag (not thread-safe). Use a process per project or orchestrate on `MHLHistory` + `MHLGenerationCreationSession`.

### 2.7 Performance, threading, memory
Single-threaded, 1 MiB chunks. Measured single-core (M3 Ultra, cached 2 GB file): xxh64 ~8.3 GB/s, xxh3 ~8.9, xxh128 ~9.2, sha1 ~2.6, c4 ~1.6, md5 ~0.77; combined runs at the slowest. Whole history loaded in memory: 20k files ≈ 70–88 MB RSS. Writes not atomic (direct `open(path,"wb")`, chain rewritten in full); issue #173: on SMB re-reading a just-written manifest fails intermittently → `.mhl` written but chain not updated → every later load fails. Mitigation: lock per project, backup chain before each run.

### 2.8 Known issues
File symlinks followed; **directory symlink crashes `create`** (KeyError) **[tested]**; unreadable files raise (issue #160); read-only dir → uncaught PermissionError; zero-byte files written without `size` (issue #168); nested ignore issues (#153, #156, #174); flatten doesn't error on missing nested folder (#161); unmerged PR #169 lists 4 latent bugs. Timestamps written with the **current** UTC offset → run with `TZ=UTC`.

---

## 3. Hash algorithm
Spec has no default; reference defaults to xxh128, c4 for chain/references. Industry: Pomfort Silverstack/Offload Manager/MediaVerify default xxh64 (medium confidence); Hedge OffShoot always XXH64BE (reuses MHL checksums only if size+mtime match); YoYotta MD5 + xxHash64be; ARRI HDE offers md5/sha1/sha256/c4/xxh64/xxh3/xxh128. xxHash is non-cryptographic; 64-bit ≈ 312 expected collisions per 1e11 inputs, 128-bit none. For integrity (file vs its own earlier hash) what matters is P(corruption keeps hash) ≈ 2^-64; 128 bits removes it and makes hash-based rename/dedup safe.
**Recommendation:** xxh128 for new histories; keep xxh64 where it already exists (can add xxh128 in the same read); optionally c4 for tamper evidence (~5× CPU); avoid md5; never sha256.

## 4. Other tools, lessons
- Pomfort MediaVerify (batch verify of child histories, creates root history), SealVerify (legacy MHL + `.pfsl`), mhl-tool (legacy C, MIT). Hedge trusts an existing MHL hash only if size+mtime match.
- Small OSS: `ottomatic-io/ocopy` (xxh64, ASC MHL, skip on size+mtime), `lucuma13/mhl-suite`, `x43romp/ascmhl-docker` (just bundles CLIs), `sakerk/MASH` (Rust), `macvfx/MHL-Verify`. **No scheduled/continuous ASC MHL verifier exists.**
- General: cshatag (xattrs sha256 + mtime; states new/outdated/ok/timechange/**corrupt** = mtime same, hash differs), chkbit (same rule; split vs atom index; blake3; workers), bitrot (SQLite, process pool), scorch (hash/size/mode/mtime/inode/state DB), hashdeep (audit), par2 (repair; optional add-on).
- Lessons: cheap stat scan vs own index; "mtime/size changed" = modification event (policy); "stat unchanged but hash differs" on scheduled re-verify = **corruption** (ASC MHL alone marks both `failed`); full re-hash at a slow rate (X TB/day).

## 5. Legacy MHL 1.x
Flat XML `<folder>_<date>_<time>.mhl`, `<hashlist version="1.0|1.1">`, `<hash>` with `<file>`, `<size>`, `<lastmodificationdate>`, one of `md5|sha1|xxhash|xxhash64|xxhash64be|null`, `<hashdate>`. No chain/generations/nesting/directory hashes. Encoding traps: `xxhash` = XXH32 as 10-digit decimal; `xxhash64` = little-endian bytes (reversed); `xxhash64be` = canonical = ASC MHL `xxh64`. ASC MHL is not backward compatible; reference has no import (legacy files hashed as ordinary files). Recommendation: read legacy MHLs at onboarding to verify files and optionally seed the first generation (via mhllib or recording the check in `<comment>`).

## 6. Edge cases
- Read-only: `create` crashes. Sidecar pattern **[tested]**: `flatten` once where writable, then `ascmhl-debug verify -pl <packinglist> <root>` on the read-only tree (exit 0/11). `MHLHistory` derives scope root from `dirname(ascmhl)`, so true sidecars need own code or an overlay mount.
- Timestamps: `TZ=UTC`.
- Partial copies: no stable-file detection; implement quiescence (newest mtime older than N min, two identical stat snapshots, optional done-marker), re-stat after hashing.
- Concurrency: lock per project; never run root-level `create` while project-level runs are active; backup chain.
- Flat vs nested layout: histories at any depth; intermediate folders get only `directoryhash` in root manifest; adding a project later needs a new root generation (root `diff` still works via filesystem walk); moving a project between month folders breaks root references → re-reference, don't rely on `-dr`.

## Not verified
Codex default hash; Silverstack default (medium confidence); memory/time at millions of files (extrapolated); whether Pomfort/Hedge accept a hand-written references-only root manifest (spec-permitted §5.6.2 Note 3 / §6.5.2 Note 1, untested in their tools).
