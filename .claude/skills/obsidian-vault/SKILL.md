---
name: obsidian-vault
description: Read the owner's Obsidian vault (MCP id my-vault) safely, find notes by title, and write or extend concept notes of the #archivo area as part of closing a session.
---

# obsidian-vault

Contract: rule `.claude/rules/obsidian.md` and the vault note `Claude`.

## Reading
- Vault is flat: a note titled `X` is at path `X.md`. Find titles with `obsidian_search_vault` (`mode: filename` or `both`).
- Read with `obsidian_read_note` (`vault: my-vault`, `path: "X.md"`, `max_chars` up to 25000).
- **Batch size 3–6.** A batch of 16 parallel reads killed the server on 2026-09-09. If a read hangs, stop calling the MCP and tell the owner it needs a restart.
- Start-of-session set: `Inicio`, `Archivo`, `MHL Sentinel`. Task sets are listed in `CLAUDE.md` ("Notas de Obsidian clave").

## Writing (concept notes of the area: allowed; deleting or merging: only with permission)
- Concept note template: one-sentence definition + `Relacionado: [[…]]` · **Definición** · **Propiedades** (opt.) · **Fórmulas o medida** (opt.) · **Limitaciones** · **Fuentes**. Tags on the first line under the title: `#concepto #archivo`.
- Title in the owner's natural Spanish, English term in parentheses when it is the one in use.
- Forbidden inside: `Dnn`/`Hnn` ids, repo paths, project figures, "the app does…". Generic numeric examples are fine. Verify what can be verified before writing; mark what comes from memory.
- Create with `obsidian_create_note` (fails if it exists: good). Edit only with `if_match` = the `etag` from a fresh read; the MCP replaces whole notes.
- After creating a note: add it to the `Archivo` map (its family) and, if the project relies on it, to `MHL Sentinel` with one linking line. Every `[[Title]]` must point to an existing note: search before linking.
- Session close checklist: every new concept from this session has a note; `Archivo` and `MHL Sentinel` link it; the bitácora entry names the notes written.

## Failure modes
- Read returns `ok:false` or hangs > 60 s → stop, report to owner, do not retry in a loop.
- Edit rejected for etag mismatch → re-read, merge by hand, edit again. Never force.
