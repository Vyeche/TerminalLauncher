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
# Stub sound players: they write down what they were asked to play instead of making a noise.
for p in pw-play paplay aplay; do printf '#!/bin/sh\necho "%s $*" >> "%s/played.log"\n' "$p" "$T" > "$T/bin/$p"; chmod +x "$T/bin/$p"; done
export PATH="$T/bin:$PATH" TL_PLAYED="$T/played.log"
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
# Move to monitor: with --dry-run it only prints the shortcut (it never touches /dev/uinput here)
out="$(printf '{"open_mode": "kitty", "move_to_monitor": "down"}' > "$CFG/settings.json"; python3 "$APP" --autostart --dry-run 2>&1)"
grep -q "would press Super+Shift+Alt+Down" <<< "$out" && ok "Move to monitor: a new kitty window is moved (dry-run)" \
  || { bad "Move to monitor didn't press the shortcut"; printf '%s\n' "$out" | sed 's/^/        /'; }
python3 - "$APP" <<'EOF' && ok "Move to monitor: a real 5 s deadline, and no press once focus is gone" || bad "Move to monitor timing"
# The real press_move_shortcut is replaced: nothing here touches /dev/uinput.
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location("tl", sys.argv[1]); tl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tl)
pressed = []
def fake_press(direction, still_focused=lambda: True):
    if not still_focused():
        return "skipped, it lost focus before the shortcut"
    pressed.append(direction)
tl.press_move_shortcut = fake_press
t0 = time.monotonic()                                   # a slow focus query can't stretch the wait
tl.move_when_focused(lambda timeout: time.sleep(min(timeout, 3.0)) or False, "test", wait=1.0)
assert time.monotonic() - t0 < 1.6 and not pressed, time.monotonic() - t0
tl.move_when_focused(lambda timeout: time.sleep(timeout + 0.2) or True, "test", wait=0.5)
assert not pressed, "moved on a focus answer that came after the deadline"
answers = iter([True, False])                          # focused at first, gone just before the press
tl.move_when_focused(lambda timeout: next(answers, False), "test", wait=1.0)
assert not pressed, "pressed although focus moved away"
tl.move_when_focused(lambda timeout: True, "test", wait=1.0)
assert pressed == ["down"], pressed
log = tl.LOG.read_text()
assert "skipped, it never had focus" in log and "lost focus" in log, log
EOF
echo '{"open_mode": "kitty"}' > "$CFG/settings.json"
check "--all-windows --dry-run exits 0" python3 "$APP" --all-windows --dry-run
[ "$(python3 "$APP" --sounds --dry-run | grep -c '\.wav$')" -ge 5 ] && [ ! -s "$T/played.log" ] \
  && ok "--sounds --dry-run makes the sound files and plays nothing" || bad "--sounds --dry-run"

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
from gi.repository import Adw, Gio, GLib
result = {}
def act(app):
    try:
        import os, wave, array
        from gi.repository import Gtk
        css = Gtk.CssProvider(); errors = []
        css.connect("parsing-error", lambda _p, _s, e: errors.append(e.message))
        css.load_from_string(tl.APP_CSS)
        assert not errors, f"CSS: {errors}"
        w = tl.Window(app)            # built, never shown
        names = lambda: [s["name"] for s in w.sessions]
        saved = lambda: [(s["name"], bool(s.get("star"))) for s in tl.load_sessions()]
        played = lambda: open(os.environ["TL_PLAYED"]).read() if os.path.exists(os.environ["TL_PLAYED"]) else ""
        def heard(name):                                 # players run in the background: give them 2 s
            import time
            for _ in range(40):                          # run GTK's loop meanwhile: the tip chime waits on it
                if f"{name}-v1.wav" in played(): return True
                GLib.MainContext.default().iteration(False); time.sleep(0.05)
            return False
        assert len(w.ticks) == 4 and len(w.play_btns) == 4 and len(w.star_btns) == 4
        assert [g.get_title() for g in w.groups] == ["Terminals"], "no Favorites group without favorites"
        rows = []
        def walk(x):
            if isinstance(x, Adw.ActionRow): rows.append(x)
            c = x.get_first_child()
            while c: walk(c); c = c.get_next_sibling()
        for g in w.groups: walk(g)
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
        assert heard("drop"), "no drop sound"
        w._move(3, -1)
        assert names()[2] == before[0] and w.ticks[2].get_active(), (names(), "Move up lost the tick")

        # Favorites: ☆ moves an entry to the top, in its own group, keeping ticks
        agent = w.sessions[2]                            # "Agent", ticked
        w._set_star(agent)
        assert w.sessions[0] is agent and agent.get("star"), names()
        assert [g.get_title() for g in w.groups] == ["★ Favorites", "Terminals"]
        assert w.star_btns[0].get_active() and w.ticks[0].get_active() and sorted(w.checked) == [0]
        assert saved()[0] == ("Agent", True), saved()
        assert heard("star") and heard("tip"), "no star/tip sound"
        w._set_star(w.sessions[2])                       # the next one goes to the end of Favorites
        second = names()[1]
        assert [s.get("star", False) for s in w.sessions] == [True, True, False, False], names()
        w._set_star(agent)                               # unstarred: top of Terminals
        assert names()[:2] == [second, "Agent"] and not agent.get("star"), names()
        assert heard("unstar"), "no unstar sound"
        w._move_to(3, 0)                                 # dragged onto a favorite: starred
        assert w.sessions[0].get("star") and sum(1 for s in w.sessions if s.get("star")) == 2, saved()
        w._move_to(0, 3)                                 # dragged down among the rest: unstarred
        assert sum(1 for s in w.sessions if s.get("star")) == 1 and not w.sessions[-1].get("star"), saved()
        top = names()
        w._move(1, -1)                                   # Move up doesn't cross into Favorites
        assert names() == top, names()
        seen = tl.load_settings().get("tips_seen", [])
        assert {"star", "drag-star"} <= set(seen), seen
        assert w._tip("star", "again") is False, "a tip showed twice"

        # The Edit dialog keeps the star and the open count
        cap = {}
        class FakeDialog:
            def __init__(self, _parent, _entry, on_save): cap["save"] = on_save
            def present(self): pass
        real, tl.EditDialog = tl.EditDialog, FakeDialog
        w.sessions[0]["opens"] = 7
        w._edit(0); cap["save"]({"name": "Renamed", "folder": w.sessions[0]["folder"], "kind": "terminal"})
        tl.EditDialog = real
        assert w.sessions[0]["name"] == "Renamed" and w.sessions[0].get("star") and w.sessions[0]["opens"] == 7

        # Opening counts; the third ▶ on one that isn't starred suggests starring it, once
        w.mode = "windows"                               # stub cosmic-term: nothing really opens
        last = len(w.sessions) - 1
        for _ in range(3):
            w._launch(last)
        assert w.sessions[last].get("opens") == 3, w.sessions[last]
        assert "suggest-star" in tl.load_settings()["tips_seen"]
        assert heard("open"), "no open sound"
        w.ticks[1].set_active(True); w.ticks[2].set_active(True)
        assert "tick" in tl.load_settings()["tips_seen"], "no tip for ticking two"
        import time
        assert heard("tip"), "no tip chime"
        ctx = GLib.MainContext.default()                 # let the delayed tip chime (450 ms) play first
        end = time.time() + 1.2
        while time.time() < end:
            ctx.iteration(False); time.sleep(0.02)
        time.sleep(0.3); n = len(played().splitlines())
        w.lookup_action("sounds").change_state(GLib.Variant("b", False))
        w._open_many(favorites=True)
        end = time.time() + 1.2
        while time.time() < end:
            ctx.iteration(False); time.sleep(0.02)
        assert len(played().splitlines()) == n, "sound played while Sounds is off"
        assert tl.load_settings()["sounds"] is False

        # The sounds themselves: short, valid and quiet
        for name in tl.SOUNDS:
            with wave.open(str(tl.sound_file(name))) as f:
                assert f.getnchannels() == 1 and f.getframerate() == 44100
                frames = f.readframes(f.getnframes())
                assert f.getnframes() / 44100 < 0.6, name
            peak = max(abs(x) for x in array.array("h", frames)) / 32767
            assert 0.05 < peak <= tl.SOUND_PEAK + 0.001, (name, peak)

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
  then ok "window: drag and Move up/down keep ticks; favorites (☆, drag in/out, Edit keeps it);"
       ok "        opens counted; tips once; sounds valid, quiet and off when Sounds is off; CSS loads"
  else bad "window test:"; sed 's/^/        /' "$T/ui.log" | tail -20; fi
fi

echo
if [ $fails -eq 0 ]; then echo "All checks passed."; else echo "$fails check(s) failed."; exit 1; fi
