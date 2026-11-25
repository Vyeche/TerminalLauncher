#!/usr/bin/env bash
# publish-wiki.sh — publish docs/wiki/*.md to Terminal Launcher's GitHub wiki, which is a git repo of
# its own next to the code: https://github.com/Vyeche/TerminalLauncher.wiki.git. docs/wiki/ is the
# source: edit pages there, then run this. Pages edited in the web UI are overwritten, and pages that
# are not in docs/wiki/ are removed.
#   bash scripts/publish-wiki.sh --dry-run   show what would change; push nothing
#   bash scripts/publish-wiki.sh             publish
# Another wiki repo (a fork, a test copy): WIKI_URL=<git url>. Branch: WIKI_BRANCH, else the wiki's
# own default branch, else master on GitHub (its wikis use master) and main elsewhere.
# GitHub creates a repository's wiki repo only when its first page is saved in the web UI: turn the
# wiki on (Settings → Features → Wikis), save any first page, then run this.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/docs/wiki"
DRY=0
case "${1:-}" in --dry-run) DRY=1 ;; "") ;; *) echo "usage: bash scripts/publish-wiki.sh [--dry-run]" >&2; exit 2 ;; esac

WIKI="${WIKI_URL:-https://github.com/Vyeche/TerminalLauncher.wiki.git}"
if [ -n "${WIKI_BRANCH:-}" ]; then
  BRANCH="$WIKI_BRANCH"
else
  BRANCH="$(git ls-remote --symref "$WIKI" HEAD 2>/dev/null | sed -n 's|^ref: refs/heads/\([^[:space:]]*\)[[:space:]]*HEAD$|\1|p' | head -1)"
  if [ -z "$BRANCH" ]; then case "$WIKI" in *github.com[:/]*) BRANCH=master ;; *) BRANCH=main ;; esac; fi
fi
SHA="$(git -C "$REPO" rev-parse --short HEAD)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

echo "Wiki: $WIKI ($BRANCH)"
if git ls-remote --exit-code "$WIKI" "refs/heads/$BRANCH" >/dev/null 2>&1; then
  git clone -q --branch "$BRANCH" "$WIKI" "$tmp/wiki"
else
  echo "  (no pages yet: this is the first publish)"
  git init -q -b "$BRANCH" "$tmp/wiki"
  git -C "$tmp/wiki" remote add origin "$WIKI"
fi
find "$tmp/wiki" -maxdepth 1 -name '*.md' -delete
cp "$SRC"/*.md "$tmp/wiki/"
git -C "$tmp/wiki" add -A
if git -C "$tmp/wiki" diff --cached --quiet; then
  echo "The wiki already matches docs/wiki/."
  exit 0
fi
git -C "$tmp/wiki" diff --cached --stat | sed 's/^/  /'
if [ "$DRY" = 1 ]; then echo "Dry run: nothing pushed."; exit 0; fi
git -C "$tmp/wiki" commit -q -m "Update wiki from Terminal Launcher $SHA (docs/wiki)"
git -C "$tmp/wiki" push -q origin "HEAD:$BRANCH"
echo "Published."
