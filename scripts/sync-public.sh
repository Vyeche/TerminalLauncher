#!/usr/bin/env bash
# sync-public.sh — copy this repo's committed files (HEAD) into a separate public clone, for when
# development happens in one repo (the workshop) and releases go to another (e.g. GitHub) that must
# not carry the workshop's history. Files deleted here are deleted there; .git there is untouched.
#
#   bash scripts/sync-public.sh DIR                 copy, then show what changed in DIR
#   bash scripts/sync-public.sh DIR -m "message"    copy and commit in DIR (DIR's own git identity)
# It never pushes: review, then `git -C DIR push`.
#
# Private words: put one pattern per line (grep -E, case-insensitive) in .private-words in this repo
# (git ignores it, so the words themselves are never published). The copy stops if any appears in
# the files or in the commit message.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${1:?usage: bash scripts/sync-public.sh DIR [-m MESSAGE]}"; shift
MSG=""
if [ "${1:-}" = -m ]; then MSG="${2:?-m needs a message}"; fi
[ -d "$DIR/.git" ] || { echo "$DIR is not a git repository" >&2; exit 2; }
[ "$(cd "$DIR" && pwd)" != "$REPO" ] || { echo "DIR is this repo" >&2; exit 2; }
if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
  echo "This repo has uncommitted changes: commit them first (only HEAD is copied)." >&2; exit 1
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
git -C "$REPO" archive --format=tar HEAD | tar -x -C "$TMP"

WORDS="$REPO/.private-words"
if [ -s "$WORDS" ]; then
  pats="$(grep -v '^[[:space:]]*#' "$WORDS" | grep -v '^[[:space:]]*$' | paste -sd'|' -)"
  if [ -n "$pats" ]; then
    if hits="$(grep -rIilE -- "$pats" "$TMP")"; then
      echo "Private words found; nothing copied:" >&2; sed "s|$TMP/|  |" <<< "$hits" >&2; exit 1
    fi
    if [ -n "$MSG" ] && grep -qiE -- "$pats" <<< "$MSG"; then
      echo "Private words in the commit message; nothing copied." >&2; exit 1
    fi
  fi
else
  echo "note: no .private-words file, so nothing was checked for private words" >&2
fi

# mirror: remove files DIR tracks that HEAD no longer has, then copy everything over
while IFS= read -r f; do
  [ -e "$TMP/$f" ] || rm -f -- "$DIR/$f"
done < <(git -C "$DIR" ls-files)
cp -a "$TMP"/. "$DIR"/

git -C "$DIR" add -A
if git -C "$DIR" diff --cached --quiet; then echo "No changes for $DIR."; exit 0; fi
git -C "$DIR" diff --cached --stat | tail -15
if [ -n "$MSG" ]; then
  git -C "$DIR" commit -q -m "$MSG"
  git -C "$DIR" log -1 --format='Committed %h as %an <%ae>: %s'
  echo "Review, then push: git -C \"$DIR\" push"
else
  echo "Staged in $DIR (not committed). Commit with -m, or there: git -C \"$DIR\" commit"
fi
