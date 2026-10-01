#!/usr/bin/env sh
# Busca en el árbol versionado (y sin versionar, salvo ignorados) patrones que no deben entrar en un repo público:
# rutas absolutas de usuario o de shares, IPs privadas y, si existe, los patrones privados de scripts/leak-patterns.local.txt
# (gitignored; una línea por patrón grep -E, p. ej. nombres de clientes).
# Norma: .claude/rules/repo-publico.md
set -eu
root=$(git rev-parse --show-toplevel)
cd "$root"
pat='/Users/[A-Za-z0-9_-]|/Volumes/[A-Za-z0-9_-]|(^|[^0-9])(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]+\.[0-9]+'
if [ -f scripts/leak-patterns.local.txt ]; then
  extra=$(grep -v '^\s*#' scripts/leak-patterns.local.txt | grep -v '^\s*$' | paste -sd'|' -)
  [ -n "$extra" ] && pat="$pat|$extra"
fi
files=$(git ls-files --cached --others --exclude-standard | grep -v -E '^(scripts/leak-check\.sh|scripts/leak-patterns\.local\.txt)$' || true)
[ -n "$files" ] || { echo "leak-check: sin ficheros"; exit 0; }
if echo "$files" | xargs grep -n -E -I "$pat" 2>/dev/null; then
  echo "leak-check: FALLO — hay rutas, IPs o nombres privados en el árbol (ver arriba)." >&2
  exit 1
fi
echo "leak-check: ok ($(echo "$files" | wc -l | tr -d ' ') ficheros)"
