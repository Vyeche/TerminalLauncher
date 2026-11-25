#!/usr/bin/env bash
# check.sh: Terminal Launcher's checks, the same ones the Gauntlet runs on every pull request.
# Nothing touches your real list, your terminals or your kitty window: everything runs against a
# throwaway HOME and config, with stub terminals first on PATH, and the window test runs on a hidden
# X display (Xvfb).
#
#   bash scripts/check.sh           syntax, dry-run launches in every Open-in mode, the login entry,
#                                   no personal data, and the window test (skipped without xvfb-run)
#   bash scripts/check.sh --quick   the same without the window test
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="$REPO/terminal-launcher.py"
QUICK=0; [ "${1:-}" = --quick ] && QUICK=1
fails=0
ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }
skip() { printf '  skip  %s\n' "$1"; }
check() { local name="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$name"; else bad "$name"; fi; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export HOME="$T/home" XDG_CONFIG_HOME="$T/cfg" XDG_CACHE_HOME="$T/cache"
CFG="$XDG_CONFIG_HOME/terminal-launcher"
mkdir -p "$HOME/Projects/web site" "$CFG" "$T/bin"
# Stub terminals: present so every mode picks the same program on any machine, and harmless if run.
# The stub kitty answers "no such window" to `kitty @ ls`, so the real kitty is never asked.
for t in kitty cosmic-term gnome-terminal; do printf '#!/bin/sh\nexit 1\n' > "$T/bin/$t"; chmod +x "$T/bin/$t"; done
export PATH="$T/bin:$PATH"
unset WAYLAND_DISPLAY

echo "Syntax"
check "terminal-launcher.py compiles" python3 -m py_compile "$APP"
for s in "$REPO"/install.sh "$REPO"/scripts/*.sh; do check "bash -n $(basename "$s")" bash -n "$s"; done

echo "Dry-run launches"
P="$HOME/Projects/web site"
cat > "$CFG/sessions.json" <<EOF
[{"name": "Agent", "folder": "$P", "kind": "omp", "autostart": true},
 {"name": "Monitor", "folder": "$P", "kind": "command", "command": "htop", "autostart": true},
 {"name": "Resume", "folder": "$P", "kind": "claude", "session": "0a1b2c3d", "autostart": true},
 {"name": "Not at login", "folder": "$P", "kind": "terminal"}]
EOF
for mode in kitty tabs windows; do
  echo "{\"open_mode\": \"$mode\"}" > "$CFG/settings.json"
  out="$(python3 "$APP" --autostart --dry-run 2>&1)"; rc=$?
  [ $rc -eq 0 ] && ok "$mode: exits 0" || { bad "$mode: exit $rc"; printf '%s\n' "$out" | sed 's/^/        /'; }
  for want in "omp;" "htop;" "claude --resume 0a1b2c3d"; do
    grep -qF -- "$want" <<< "$out" && ok "$mode: runs $want" || bad "$mode: missing $want"
  done
  grep -q "Not at login" <<< "$out" && bad "$mode: opened an entry not marked for login" || ok "$mode: only login entries"
done
grep -q "kitty" <<< "$(echo '{"open_mode": "kitty"}' > "$CFG/settings.json"; python3 "$APP" --autostart --dry-run 2>&1)" \
  && ok "kitty mode names kitty" || bad "kitty mode doesn't name kitty"
check "--all --dry-run exits 0" python3 "$APP" --all --dry-run
check "--all-windows --dry-run exits 0" python3 "$APP" --all-windows --dry-run

echo "Login entry (save_sessions keeps the autostart file in step)"
python3 - "$APP" <<'EOF' && ok "created while marked, removed when none is" || bad "autostart file out of step"
import importlib.util, sys
spec = importlib.util.spec_from_file_location("tl", sys.argv[1]); tl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tl)
s = tl.load_sessions()
tl.save_sessions(s)
assert tl.AUTOSTART.exists(), "missing while entries are marked"
text = tl.AUTOSTART.read_text()
assert "--autostart" in text and "Exec=" in text, text
for e in s: e["autostart"] = False
tl.save_sessions(s)
assert not tl.AUTOSTART.exists(), "still there with nothing marked"
for e in s[:1]: e["autostart"] = True
tl.save_sessions(s)
EOF
if command -v desktop-file-validate >/dev/null; then
  check "desktop-file-validate the login entry" desktop-file-validate "$XDG_CONFIG_HOME/autostart/net.vyechi.TerminalLauncher.desktop"
else skip "desktop-file-validate (not installed)"; fi

echo "Privacy (this repo is public)"
cd "$REPO"
hits="$(git grep -n -I -E '/home/[a-z][a-z0-9_-]*/|/Users/[A-Za-z]' -- . ':!scripts/check.sh' 2>/dev/null)"
[ -z "$hits" ] && ok "no home-folder paths" || { bad "home-folder paths:"; printf '%s\n' "$hits" | sed 's/^/        /'; }
hits="$(git grep -n -I -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -- . ':!CODE_OF_CONDUCT.md' ':!LICENSE' 2>/dev/null \
        | grep -v -E 'noreply@anthropic\.com|users\.noreply\.github\.com|git@github\.com|@coderabbitai|actions/[a-z-]+@|/[a-z-]+\.yml@')"
[ -z "$hits" ] && ok "no e-mail addresses" || { bad "e-mail addresses:"; printf '%s\n' "$hits" | sed 's/^/        /'; }
if [ -s "$REPO/.private-words" ]; then
  pats="$(grep -v '^[[:space:]]*#' "$REPO/.private-words" | grep -v '^[[:space:]]*$' | paste -sd'|' -)"
  hits="$(git grep -l -I -i -E -- "$pats" 2>/dev/null)"
  [ -z "$hits" ] && ok "no private words" || { bad "private words in:"; printf '%s\n' "$hits" | sed 's/^/        /'; }
fi
grep -q '^DEFAULTS = \[\]' "$APP" && ok "DEFAULTS is empty" || bad "DEFAULTS isn't empty"

echo "Window (hidden display)"
if [ $QUICK -eq 1 ]; then skip "window test (--quick)"
elif ! command -v xvfb-run >/dev/null; then skip "window test (xvfb-run not installed)"
else
  if xvfb-run -a env GDK_BACKEND=x11 python3 - "$APP" > "$T/ui.log" 2>&1 <<'EOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("tl", sys.argv[1]); tl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tl)
from gi.repository import Adw, Gio
result = {}
def act(app):
    try:
        w = tl.Window(app)            # built, never shown
        names = lambda: [s["name"] for s in w.sessions]
        assert len(w.ticks) == 4 and len(w.play_btns) == 4
        rows = []
        def walk(x):
            if isinstance(x, Adw.ActionRow): rows.append(x)
            c = x.get_first_child()
            while c: walk(c); c = c.get_next_sibling()
        walk(w.group)
        kinds = {type(r.observe_controllers().get_item(k)).__name__
                 for r in rows for k in range(r.observe_controllers().get_n_items())}
        assert {"DragSource", "DropTarget"} <= kinds, kinds
        w.ticks[0].set_active(True)                     # tick "Agent", then drag it to the end
        assert "1" in w.open_btn.get_label(), w.open_btn.get_label()
        before = names()
        w._move_to(0, 3)
        assert names() == before[1:] + before[:1], names()
        assert [s["name"] for s in tl.load_sessions()] == names(), "order not saved"
        assert sorted(w.checked) == [3] and w.ticks[3].get_active(), "tick lost"
        w._move(3, -1)
        assert names()[2] == before[0], names()
        EditDialog = tl.EditDialog(w, None, lambda e: None)
        EditDialog.destroy()
        result["ok"] = True
    except Exception as e:
        import traceback; traceback.print_exc(); result["ok"] = False
    app.quit()
a = Adw.Application(application_id="net.example.TLCheck", flags=Gio.ApplicationFlags.NON_UNIQUE)
a.connect("activate", act); a.run([])
sys.exit(0 if result.get("ok") else 1)
EOF
  then ok "window builds; drag-to-move saves the order and keeps ticks; Move up/down; Add dialog"
  else bad "window test:"; sed 's/^/        /' "$T/ui.log" | tail -20; fi
fi

echo
if [ $fails -eq 0 ]; then echo "All checks passed."; else echo "$fails check(s) failed."; exit 1; fi
