---
name: release
description: Cut a version of MHL Sentinel at the end of a milestone - make ci, version bump, changelog, vault notes, bitácora, CLAUDE.md, annotated tag, CI on the tag.
---

# release

A milestone ends with: tag, changelog, bitácora entry, `CLAUDE.md` updated, vault notes written (rule `obsidian.md`). Hito 0 = `v0.0.1`, hito N = `v0.N.0` (rule `git.md`). In this order:

1. `make ci` green and every PR of the milestone merged into `main`. `main` is protected (rule `rama-main-protegida.md`): start a branch `chore/release-vX.Y.Z` from `origin/main` for the steps below. `gh issue list` read; issues closed by this milestone referenced in the commit body.
2. Bump `version` in `pyproject.toml` (single source of truth; `mhl_sentinel.__version__` reads it via `importlib.metadata`). Then `uv sync` so `uv.lock` follows.
3. `CHANGELOG.md`: move `[Unreleased]` into `## [X.Y.Z] - YYYY-MM-DD — <hito>`; keep an empty `[Unreleased]` with the six categories available.
4. Vault: every new concept of the milestone has a `#concepto #archivo` note, linked from `Archivo` and, if the project relies on it, from `MHL Sentinel` (skill `obsidian-vault`). If the vault does not answer, stop and tell the owner (rule `vault-accesible.md`).
5. `docs/bitacora/NN-*.md` entry with TL;DR and the notes written; `CLAUDE.md` state + next step; `docs/roadmap.md` if the plan moved.
6. `make leak-check` once more (rule `repo-publico.md`).
7. Commit `chore(release): vX.Y.Z` (body: what the milestone delivered, `Dn`/`Hn` cited) on the release branch, push it, open the PR, wait for CI, `gh pr merge --squash --delete-branch`. Then on an updated `main` (`git pull --ff-only`): `git tag -a vX.Y.Z -m "MHL Sentinel vX.Y.Z — <hito>" && git push --tags`.
8. Wait for CI on the tag: `gh run list --limit 3`, `gh run view <id>`. From hito 2 the `release.yml` job builds the multi-arch image and pushes `ghcr.io/maxlainz/mhl-sentinel:X.Y.Z`; check it with `docker pull`.

Never tag with CI red. Never rewrite a pushed tag. Never bump `ascmhl` in the same release as a milestone (rule `conformidad-mhl.md`: it needs its own `Dn`).
