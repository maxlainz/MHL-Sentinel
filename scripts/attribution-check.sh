#!/usr/bin/env sh
# Rechaza trailers de atribución y enlaces de sesión en los mensajes de commit de la rama y, si se pasa
# en PR_BODY, en la descripción del PR. Rango: ATTRIB_BASE..HEAD (por defecto origin/main..HEAD); si está
# vacío (push a main, rama recién creada) se revisa el último commit.
# Norma: .claude/rules/git.md (D5, D37, D64)
set -eu
root=$(git rev-parse --show-toplevel)
cd "$root"
pat='^(Co-authored-by|Signed-off-by|Claude-Session):|Generated (with|by) \[?Claude|claude\.ai/code/session_|noreply@anthropic\.com'
base=${ATTRIB_BASE:-origin/main}
if git rev-parse -q --verify "$base" >/dev/null && [ -n "$(git rev-list "$base..HEAD")" ]; then
  range="$base..HEAD"
else
  range="-1 HEAD"
fi
fail=0
# shellcheck disable=SC2086
if git log --format='%h %s%n%B' $range | grep -n -i -E "$pat"; then
  echo "attribution-check: FALLO — atribución en mensajes de commit ($range). Reescribe con git commit --amend o rebase." >&2
  fail=1
fi
if [ -n "${PR_BODY:-}" ] && printf '%s\n' "$PR_BODY" | grep -n -i -E "$pat"; then
  echo "attribution-check: FALLO — atribución en la descripción del PR. Edítala con gh pr edit." >&2
  fail=1
fi
[ "$fail" = 0 ] && echo "attribution-check: ok ($range)"
exit "$fail"
