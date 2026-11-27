#!/usr/bin/env python3
"""terminal-launcher.py: Terminal Launcher, a small GTK window that opens your saved terminals and
coding agents (Claude Code, omp, opencode, a plain shell, or any command), each in its own folder.

  Run:        python3 terminal-launcher.py                (or from the app menu: "Terminal Launcher")
  Quick:      python3 terminal-launcher.py --all          (every saved entry as tabs in one window)
              python3 terminal-launcher.py --all-windows  (every saved entry in its own window)
  At login:   python3 terminal-launcher.py --autostart    (only the entries marked "Start when I log in",
                                                           opened the way the launcher is set to open)
              add --dry-run to print what would open without opening anything
  Sounds:     python3 terminal-launcher.py --sounds       (plays each of its little sounds once)
  Install:   bash install.sh                             (adds the app-menu entry)
  Saved list: ~/.config/terminal-launcher/sessions.json
"""
import array
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

try:   # drawing the cat needs GTK's cairo bridge (python3-gi-cairo); without it the cat stays hidden
    gi.require_foreign("cairo")
    HAVE_CAIRO = True
except ImportError:
    HAVE_CAIRO = False

APP_ID = "net.vyechi.TerminalLauncher"
APP_NAME = "Terminal Launcher"
_XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
CONFIG = _XDG_CONFIG / "terminal-launcher" / "sessions.json"
OLD_CONFIG = _XDG_CONFIG / "claude-launcher" / "sessions.json"   # before the rename (Claude Launcher)
AUTOSTART = _XDG_CONFIG / "autostart" / f"{APP_ID}.desktop"
LOG = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "terminal-launcher" / "launcher.log"
PROJECTS = Path.home() / ".claude" / "projects"
DRY_RUN = False   # --dry-run: print what would be started instead of starting it

DEFAULTS = []   # a new install starts with an empty list


# ---------- storage ----------
def _migrate_old_config():
    """First run after the rename: copy the Claude Launcher list and settings over (old ones are kept)."""
    if CONFIG.exists() or not OLD_CONFIG.exists():
        return
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    for name in ("sessions.json", "settings.json"):
        old = OLD_CONFIG.with_name(name)
        if old.exists():
            shutil.copy2(old, CONFIG.with_name(name))


def load_sessions():
    _migrate_old_config()
    try:
        data = json.loads(CONFIG.read_text())
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("folder")]
    except FileNotFoundError:
        save_sessions(DEFAULTS)
        return [dict(d) for d in DEFAULTS]
    except (OSError, ValueError) as e:
        print(f"terminal-launcher: can't read {CONFIG}: {e}", file=sys.stderr)
    return []


def save_sessions(sessions):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(sessions, indent=2) + "\n")
    tmp.replace(CONFIG)
    sync_autostart(sessions)


def sync_autostart(sessions):
    """Keep one login entry in ~/.config/autostart: there while any entry is marked to start at
    login, gone when none is. It runs this script with --autostart."""
    if not any(s.get("autostart") for s in sessions):
        AUTOSTART.unlink(missing_ok=True)
        return
    script = Path(__file__).resolve()
    text = (f"[Desktop Entry]\nType=Application\nName={APP_NAME} (start at login)\n"
            f"Comment=Opens the terminals marked \"Start when I log in\" in {APP_NAME}\n"
            f"Exec=python3 \"{script}\" --autostart\nIcon={script.parent / 'assets' / 'terminal-launcher.svg'}\n"
            "Terminal=false\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n")
    if AUTOSTART.exists() and AUTOSTART.read_text() == text:
        return
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART.write_text(text)


# ---------- launching ----------
def terminal_argv(folder, inner):
    """argv that opens a terminal in `folder` running bash -lc `inner`."""
    if shutil.which("cosmic-term"):
        return ["cosmic-term", "-w", folder, "--", "bash", "-lc", inner]
    if shutil.which("gnome-terminal"):
        return ["gnome-terminal", f"--working-directory={folder}", "--", "bash", "-lc", inner]
    return ["x-terminal-emulator", "-e", "bash", "-lc", f"cd {shlex.quote(folder)} && {inner}"]


KINDS = [("claude", "Claude Code session"), ("omp", "omp session"), ("opencode", "opencode session"),
         ("terminal", "Terminal only"), ("command", "Run a command")]
AGENTS = {  # kind: (label, command for a new session, command to resume session <ID>)
    "claude": ("Claude", "claude", "claude --resume {sid}"),
    "omp": ("omp", "omp", "omp --resume {sid}"),
    "opencode": ("opencode", "opencode", "opencode --session {sid}"),
}


def _inner(entry):
    kind = entry.get("kind", "claude")
    if kind == "terminal":
        return "exec bash"
    if kind == "command":
        cmd = (entry.get("command") or "").strip() or "true"
        return f"{cmd}; echo; echo '[Command finished. This is a normal shell now.]'; exec bash"
    label, new, resume = AGENTS.get(kind, AGENTS["claude"])
    sid = (entry.get("session") or "").strip()
    cmd = resume.format(sid=shlex.quote(sid)) if sid else new
    return f"{cmd}; echo; echo '[{label} exited. This is a normal shell now.]'; exec bash"


def describe(entry):
    kind = entry.get("kind", "claude")
    if kind == "terminal":
        return "Terminal only"
    if kind == "command":
        return "$ " + (entry.get("command") or "")
    return entry.get("session") or f"(new {AGENTS.get(kind, AGENTS['claude'])[0]} session)"


_AGENT_VARS = ("CLAUDECODE", "AI_AGENT", "GIT_EDITOR", "COREPACK_ENABLE_AUTO_PIN")


def _clean_env():
    """Environment without agent internals, in case the launcher was started from a Claude shell
    (inherited CLAUDE_CODE_CHILD_SESSION would turn off transcript saving in every new session)."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("CLAUDE_", "IMPECCABLE_")) and k not in _AGENT_VARS}


def _spawn(argv, cwd):
    if DRY_RUN:
        print("would start:", shlex.join(argv), f"  (in {cwd})")
        return
    subprocess.Popen(argv, cwd=cwd, start_new_session=True, env=_clean_env(),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch(entry):
    """Open one session in its own terminal window."""
    folder = os.path.expanduser(entry["folder"])
    if not os.path.isdir(folder):
        return f"Folder not found: {folder}"
    _spawn(terminal_argv(folder, _inner(entry)), folder)
    return None


def launch_tabs(entries):
    """Open several sessions as tabs of ONE new terminal window (GNOME Terminal; COSMIC Terminal
    can't be told to open tabs from outside). Returns a list of error strings."""
    ok = [e for e in entries if os.path.isdir(os.path.expanduser(e["folder"]))]
    errs = [f"Folder not found: {e['folder']}" for e in entries if e not in ok]
    if not ok:
        return errs or ["Nothing to open"]
    if not shutil.which("gnome-terminal"):
        return errs + [x for x in (launch(e) for e in ok) if x]   # fall back to separate windows
    argv = ["gnome-terminal", "--window"]
    for n, e in enumerate(ok):
        if n:
            argv.append("--tab")
        argv += [f"--working-directory={os.path.expanduser(e['folder'])}",
                 "-e", "bash -lc " + shlex.quote(_inner(e))]
    _spawn(argv, os.path.expanduser(ok[0]["folder"]))
    return errs


# ---------- kitty: add tabs to an already-open window ----------
# The launcher starts its own kitty window with remote control on a private socket, so later
# launches can find that window and add tabs to it. Your normal kitty settings are not changed.
# The socket keeps its old name from before the rename (Claude Launcher), so a kitty window that is
# already open, and scripts that talk to it (kitty-relaunch.sh in NineLives), keep working.
# Tabs get no fixed title, so Claude's live title (with its working spinner) shows on each tab.
KITTY_SOCK = "unix:@claude-launcher-kitty"
# Prefer the official kitty build (newer: drag tabs, auto light/dark) over the older apt one.
_KITTY_APP = Path.home() / ".local" / "kitty.app" / "bin" / "kitty"
KITTY = str(_KITTY_APP) if _KITTY_APP.exists() else (shutil.which("kitty") or "kitty")


def _kitty_alive():
    try:
        return subprocess.run([KITTY, "@", "--to", KITTY_SOCK, "ls"], capture_output=True,
                              timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def kitty_attach(entries):
    """Add entries as tabs to the launcher's open kitty window, starting one if needed."""
    if not (os.path.exists(KITTY) or shutil.which(KITTY)):
        return ["kitty isn't installed yet: sudo apt install kitty"]
    ok = [e for e in entries if os.path.isdir(os.path.expanduser(e["folder"]))]
    errs = [f"Folder not found: {e['folder']}" for e in entries if e not in ok]
    if not ok:
        return errs or ["Nothing to open"]
    rest = ok
    if not _kitty_alive():
        first = ok[0]
        folder = os.path.expanduser(first["folder"])
        _spawn([KITTY, "-o", "allow_remote_control=socket-only", "--listen-on", KITTY_SOCK,
                "--directory", folder, "bash", "-lc", _inner(first)], folder)
        for _ in range(0 if DRY_RUN else 40):
            time.sleep(0.2)
            if _kitty_alive():
                break
        else:
            if not DRY_RUN:
                return errs + ["kitty started but isn't answering; try again"]
        if load_monitor():   # only a window we just opened; in the background so the UI stays responsive
            threading.Thread(target=move_when_focused, args=(_kitty_focused, "kitty window")).start()
        rest = ok[1:]
    for e in rest:
        name = e.get("name") or Path(e["folder"]).name
        if DRY_RUN:
            print("would add kitty tab:", name, "->", _inner(e))
            continue
        try:
            r = subprocess.run([KITTY, "@", "--to", KITTY_SOCK, "launch", "--type=tab",
                                "--cwd", os.path.expanduser(e["folder"]),
                                "bash", "-lc", _inner(e)], capture_output=True, text=True, timeout=5)
            if r.returncode:
                errs.append(f"{name}: {r.stderr.strip() or 'kitty refused'}")
        except (OSError, subprocess.TimeoutExpired) as ex:
            errs.append(f"{name}: {ex}")
    return errs


# ---------- settings ----------
SETTINGS = CONFIG.with_name("settings.json")
MODES = {"kitty": ("Add to my open kitty window", "Add {n} to kitty", "Add all to kitty"),
         "tabs": ("New window with tabs", "Open {n} in tabs", "Open all in tabs"),
         "windows": ("Separate windows", "Open {n} in windows", "Open all in windows")}


def load_settings():
    try:
        s = json.loads(SETTINGS.read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_setting(key, value):
    s = load_settings()
    s[key] = value
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s) + "\n")


def load_mode():
    m = load_settings().get("open_mode")
    return m if m in MODES else "tabs"


def save_mode(mode):
    save_setting("open_mode", mode)


# ---------- moving a new window to another monitor (COSMIC) ----------
# On Wayland an app can't choose its monitor; COSMIC puts new windows on the monitor it considers
# active. If "Move to monitor" is set, the launcher presses COSMIC's own "move window to monitor"
# shortcut (Super+Shift+Alt+arrow) through a short-lived virtual keyboard (/dev/uinput, which the
# logged-in user may write to). It only does this when the window it just opened has focus, so it
# can never move one of your other windows. Already on the far monitor? COSMIC then does nothing.
MONITOR_ROWS = [  # (setting value, choice label)
    ("", "Leave where the desktop puts them"),
    ("down", "Monitor below"),
    ("up", "Monitor above"),
    ("left", "Monitor to the left"),
    ("right", "Monitor to the right"),
]
_KEYS = {"meta": 125, "shift": 42, "alt": 56, "up": 103, "down": 108, "left": 105, "right": 106}


def load_monitor():
    m = load_settings().get("move_to_monitor") or ""
    return m if m in dict(MONITOR_ROWS) else ""


def press_move_shortcut(direction, still_focused=lambda: True):
    """Press Super+Shift+Alt+<direction> on a temporary virtual keyboard. Returns an error or None.
    `still_focused` is asked again after the keyboard's setup wait, just before the first key: if the
    window lost focus meanwhile, nothing is pressed (so another window is never moved)."""
    import fcntl
    import struct
    if DRY_RUN:
        print("would press Super+Shift+Alt+" + direction.capitalize())
        return None
    try:
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    except OSError as ex:
        return f"can't use /dev/uinput: {ex.strerror}"
    try:
        fcntl.ioctl(fd, 0x40045564, 1)                       # UI_SET_EVBIT EV_KEY
        for code in _KEYS.values():
            fcntl.ioctl(fd, 0x40045565, code)                # UI_SET_KEYBIT
        name = b"Terminal Launcher monitor move"
        os.write(fd, struct.pack("80sHHHHi256i", name, 0x06, 0x1, 0x1, 1, 0, *([0] * 256)))
        fcntl.ioctl(fd, 0x5501)                              # UI_DEV_CREATE
        time.sleep(0.6)                                      # let the desktop pick up the new keyboard
        if not still_focused():
            fcntl.ioctl(fd, 0x5502)                          # UI_DEV_DESTROY
            return "skipped, it lost focus before the shortcut"

        def ev(code, value):
            os.write(fd, struct.pack("qqHHi", 0, 0, 1, code, value))
            os.write(fd, struct.pack("qqHHi", 0, 0, 0, 0, 0))   # SYN_REPORT
            time.sleep(0.02)
        keys = [_KEYS["meta"], _KEYS["shift"], _KEYS["alt"], _KEYS[direction]]
        for k in keys:
            ev(k, 1)
        time.sleep(0.05)
        for k in reversed(keys):
            ev(k, 0)
        time.sleep(0.2)
        fcntl.ioctl(fd, 0x5502)                              # UI_DEV_DESTROY
        return None
    except OSError as ex:
        return f"virtual keyboard failed: {ex.strerror}"
    finally:
        os.close(fd)


def move_when_focused(is_focused, what, wait=5.0):
    """Wait up to `wait` seconds (a real deadline) for the window we opened to have focus, then move it.
    is_focused(timeout) answers within `timeout` seconds. Logs the outcome."""
    direction = load_monitor()
    if not direction:
        return
    if DRY_RUN:
        press_move_shortcut(direction)
        return
    deadline = time.monotonic() + wait
    while (left := deadline - time.monotonic()) > 0:
        if is_focused(left):
            if time.monotonic() > deadline:   # the answer came too late: leave the window be
                break
            err = press_move_shortcut(direction, lambda: is_focused(1.0))
            log(f"move {what} to monitor {direction}: " + (err or "ok"))
            return
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    log(f"move {what} to monitor {direction}: skipped, it never had focus")


def _kitty_focused(timeout=3.0):
    try:
        r = subprocess.run([KITTY, "@", "--to", KITTY_SOCK, "ls"], capture_output=True,
                           timeout=max(0.1, min(3.0, timeout)))
        return any(w.get("is_focused") for w in json.loads(r.stdout or b"[]"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


# ---------- reading Claude's own session files (for titles and the "recent" picker) ----------
def _read_head_tail(path, head=256 * 1024, tail=2 * 1024 * 1024):
    size = path.stat().st_size
    with open(path, "rb") as f:
        first = f.read(head)
        if size > head + tail:
            f.seek(size - tail)
        last = f.read()
    return first, last


def session_info(path):
    """(title, cwd) for one Claude session file, reading only its start and end."""
    try:
        first, last = _read_head_tail(path)
    except OSError:
        return None, None
    title = cwd = None
    first_prompt = None
    for chunk in (last, first):
        for line in reversed(chunk.splitlines()):
            if b'"custom-title"' in line or b'"ai-title"' in line:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t = d.get("customTitle") or d.get("aiTitle")
                if t and (not title or d.get("type") == "custom-title"):
                    title = t
                    if d.get("type") == "custom-title":
                        break
        if title:
            break
    for line in first.splitlines():
        if b'"cwd"' in line:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            cwd = cwd or d.get("cwd")
            if not first_prompt and d.get("type") == "user":
                c = d.get("message", {}).get("content")
                if isinstance(c, str) and not c.startswith("<"):
                    first_prompt = c.strip().splitlines()[0][:60] if c.strip() else None
            if cwd and first_prompt:
                break
    return title or first_prompt, cwd


def find_session_file(sid):
    if not sid or not PROJECTS.is_dir():
        return None
    hits = list(PROJECTS.glob(f"*/{sid}.jsonl"))
    return hits[0] if hits else None


def recent_sessions(limit=30):
    if not PROJECTS.is_dir():
        return []
    files = sorted(PROJECTS.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for p in files:
        title, cwd = session_info(p)
        if cwd:
            out.append({"session": p.stem, "folder": cwd, "title": title or p.stem[:8],
                        "when": GLib.DateTime.new_from_unix_local(int(p.stat().st_mtime)).format("%b %d %H:%M")})
    return out


# ---------- sounds ----------
# A few tiny, quiet tones, made here (no sound files, no dependencies) the first time they're needed
# and kept in ~/.cache/terminal-launcher/sounds/. They play with whichever of pw-play, paplay or aplay
# is installed. Off with ☰ → Sounds, or with the desktop's own "event sounds" switch.
SOUND_DIR = LOG.parent / "sounds"
SOUND_VERSION = 5   # bump when the notes change, so the cached files are made again
SOUND_PEAK = 0.18   # of full scale (about -15 dBFS): a soft tick, never an alert
SOUNDS = {  # name: notes as (start s, frequency Hz, length s, loudness 0..1[, glide to Hz])
    "star": [(0.0, 784, 0.16, 1.0), (0.07, 1175, 0.24, 0.8)],                       # G5 → D6, up
    "unstar": [(0.0, 659, 0.12, 0.8), (0.06, 494, 0.18, 0.7)],                      # E5 → B4, down
    "open": [(0.0, 523, 0.08, 0.7), (0.035, 784, 0.12, 0.6)],                       # a short pluck
    "drop": [(0.0, 330, 0.12, 0.9)],                                                 # a soft low tock
    "tip": [(0.0, 880, 0.18, 0.5), (0.09, 1109, 0.18, 0.45), (0.18, 1319, 0.3, 0.4)],  # A5 C♯6 E6
    "badge": [(0.0, 1047, 0.12, 0.6), (0.06, 1319, 0.12, 0.55), (0.12, 1568, 0.14, 0.5),
              (0.18, 2093, 0.35, 0.45)],                                             # a C-major sparkle
    "done": [(0.0, 659, 0.12, 0.7), (0.08, 988, 0.26, 0.6)],                       # E5 → B5: it worked
    "oops": [(0.0, 392, 0.14, 0.7), (0.09, 330, 0.24, 0.6)],                       # G4 → E4: it didn't
}


def warm(buf, rate=44100):
    """Make a sound rounder and cosier: soften the highs (a gentle low-pass) and add a tiny room (two
    quiet echoes), so it doesn't sound dry or sharp."""
    out, lp = [], 0.0
    for x in buf:
        lp += 0.28 * (x - lp)                       # roughly a 2.5 kHz low-pass
        out.append(lp)
    out += [0.0] * int(0.09 * rate)
    for delay, gain in ((0.031, 0.18), (0.067, 0.09)):
        d = int(delay * rate)
        for k in range(len(out) - 1, d - 1, -1):
            out[k] += gain * out[k - d]
    return out


def synth(notes, rate=44100):
    """Soft, warm marimba-like notes: a sine with a gently detuned twin and a quiet octave, a soft 8 ms
    start and a quick fade, then warm(); scaled so the loudest sample is SOUND_PEAK. A note with a
    fifth value glides to that frequency."""
    length = max(n[0] + n[2] for n in notes) + 0.02
    buf = [0.0] * int(length * rate)
    for start, freq, dur, amp, *glide in notes:
        s0 = int(start * rate)
        end = glide[0] if glide else freq
        phase = phase2 = 0.0
        for k in range(int(dur * rate)):
            t = k / rate
            f = freq + (end - freq) * t / dur
            phase += 2 * math.pi * f / rate
            phase2 += 2 * math.pi * f * 1.004 / rate     # a slightly detuned twin: warmth
            env = min(1.0, t / 0.008) * math.exp(-6.0 * t / dur)
            buf[s0 + k] += amp * env * (0.6 * math.sin(phase) + 0.4 * math.sin(phase2) + 0.12 * math.sin(2 * phase))
    buf = warm(buf, rate)
    peak = max(abs(x) for x in buf) or 1.0
    return [x / peak * SOUND_PEAK for x in buf]


def _toon(notes, rate=44100):
    """A cute, cartoon voice: clean round tones (a sine with a quiet octave, no noise) that glide and
    wobble a little, like a toy or a cozy game character. notes: [(start s, length s, pitch from,
    pitch to, loudness)]; each note eases in and out and gets a gentle vibrato."""
    total = max(st + ln for st, ln, *_ in notes) + 0.02
    out = [0.0] * int(total * rate)
    for st, ln, f_a, f_b, amp in notes:
        s0, n, phase = int(st * rate), int(ln * rate), 0.0
        for k in range(n):
            t, x = k / rate, k / max(1, n - 1)
            ease = x * x * (3 - 2 * x)
            f = (f_a + (f_b - f_a) * ease) * (1 + 0.012 * math.sin(2 * math.pi * 7 * t))
            phase += 2 * math.pi * f / rate
            env = min(1.0, t / 0.025) * min(1.0, (ln - t) / 0.09) ** 1.5
            out[s0 + k] += amp * env * (math.sin(phase) + 0.15 * math.sin(2 * phase))
    return out


def _meow():
    """A cute "mew~": a soft squeak that rises, settles, and a little "nya" on top."""
    return _toon([(0.0, 0.34, 590, 790, 1.0), (0.30, 0.20, 750, 670, 0.85), (0.56, 0.17, 850, 900, 0.6)])


def _chirp():
    """A bubbly "mrrp?": two quick notes going up, like a question."""
    return _toon([(0.0, 0.12, 540, 660, 0.9), (0.12, 0.18, 690, 900, 1.0)])


def _babble():
    """Chirpy little blips that bounce up and down, like the talking in cozy games, ending lower."""
    pitches = (690, 810, 630, 870, 750, 900, 720, 600)
    notes, t = [], 0.0
    for i, f in enumerate(pitches):
        ln = 0.075 if i % 3 else 0.105
        notes.append((t, ln, f, f * (1.08 if i % 2 else 0.94), 0.8))
        t += ln + 0.035
    return _toon(notes)


def _purr(rate=44100):
    """A soft, gentle purr: a warm hum that rises and falls in three breaths (no rumble)."""
    out = []
    breaths = ((0.0, 0.6, 1.0), (0.68, 1.28, 0.8), (1.36, 1.96, 0.9))
    for k in range(int(2.0 * rate)):
        t = k / rate
        breath = sum(g * math.sin(math.pi * (t - a) / (b - a)) for a, b, g in breaths if a <= t < b)
        pulse = 0.7 + 0.3 * math.sin(2 * math.pi * 18 * t)
        out.append(breath * pulse * (math.sin(2 * math.pi * 196 * t) + 0.35 * math.sin(2 * math.pi * 392 * t)))
    return out


# The cat's own voice: soft cartoon tones, made here like the other sounds
SOUND_MAKERS = {
    "meow": lambda: warm(_meow()),
    "chirp": lambda: warm(_chirp()),
    "purr": lambda: warm(_purr()),
    "babble": lambda: warm(_babble()),
}


def sound_file(name):
    """The WAV file for one of SOUNDS or SOUND_MAKERS, made on first use (peak SOUND_PEAK)."""
    path = SOUND_DIR / f"{name}-v{SOUND_VERSION}.wav"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        if name in SOUND_MAKERS:
            raw = SOUND_MAKERS[name]()
            peak = max(abs(x) for x in raw) or 1.0
            data = [x / peak * SOUND_PEAK for x in raw]
        else:
            data = synth(SOUNDS[name])
        samples = array.array("h", (int(x * 32767) for x in data))
        tmp = path.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(samples.tobytes())
        tmp.replace(path)
    return path


def play_sound(name):
    """Play one of SOUNDS in the background. Silent with --dry-run (so trying things never makes a
    noise) and when no player is installed."""
    if DRY_RUN or (name not in SOUNDS and name not in SOUND_MAKERS):
        return
    player = next((p for p in ("pw-play", "paplay", "aplay") if shutil.which(p)), None)
    if not player:
        return
    try:
        path = str(sound_file(name))
        argv = [player, "-q", path] if player == "aplay" else [player, path]
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def favorites_first(sessions):
    """The list with starred entries first, each part keeping its own order."""
    return [s for s in sessions if s.get("star")] + [s for s in sessions if not s.get("star")]


# ---------- Byte, the launcher's cat ----------
# A small drawn cat in a bar at the bottom of the window. You never have to look after it: it blinks,
# winks, swishes its tail, naps late at night, cheers when you do things, and says something now and
# then (the date, a word of the day, a joke, a tip). It also hands out badges for little quests.
# Everything here is made up or checkable in a shell; it never goes online.
CAT_WORDS = [  # (word, plain meaning): one per day
    ("daemon", "a program that quietly runs in the background, waiting for work."),
    ("shell", "the program that reads what you type in a terminal and runs it."),
    ("prompt", "the text before your cursor that says the shell is ready."),
    ("stdout", "where a program writes its normal output."),
    ("stderr", "where a program writes its errors, apart from the normal output."),
    ("pipe", "the | sign: it sends one command's output into the next command."),
    ("PATH", "the list of folders your shell looks in to find commands."),
    ("symlink", "a file that points to another file or folder."),
    ("cron", "a helper that runs commands on a schedule."),
    ("SSH", "a safe way to log in to another computer over the network."),
    ("PID", "the number that names a running program (a process)."),
    ("socket", "a meeting point two programs use to talk to each other."),
    ("glob", "a pattern like *.txt that matches file names."),
    ("exit code", "the number a program ends with: 0 means it went well."),
    ("dotfile", "a settings file whose name starts with a dot, so it's hidden by default."),
    ("kernel", "the core of the system, the part that talks to the hardware."),
    ("Wayland", "the newer way Linux apps put their windows on the screen."),
    ("TTY", "short for teletype; today it means a terminal device."),
]
CAT_JOKES = [
    "I don't chase mice. I chase cursors. They blink first.",
    "I napped on the keyboard. Now you have a folder called ffffffffff.",
    "Bash, zsh, fish... honestly, I'm only here for the fish.",
    "If it fits, I sit. Your terminal fits. Scooting over.",
    "Git push? I prefer git purr.",
    "Ctrl+Z won't undo my nap. I checked.",
    "I asked htop how it was doing. It told me everything at once.",
    "Your folders have lovely names. I'd sit in every one of them.",
    "A tab walks into a window. The window says: make yourself at ~.",
    "My nine lives? Eight are in terminal tabs. The ninth is napping.",
    "Every session here gets resumed. Nobody gets left behind. Not even me.",
    "I tried to sudo a nap. Turns out I already had permission.",
]
CAT_TIPS = [  # things you can check yourself in a shell or in the launcher
    "In bash, cd - takes you back to the folder you were just in.",
    "Ctrl+R in bash searches the commands you ran before.",
    "Tab finishes a file or command name for you. Press it twice to see the choices.",
    "!! in bash means the last command again. Try sudo !!",
    "Ctrl+L clears the terminal screen; your history stays.",
    "~ is short for your home folder, in every command.",
    "Ctrl+A jumps to the start of the line, Ctrl+E to the end.",
    "Ctrl+O here opens a shell in any folder, without adding it to the list.",
    "Drag a row into Favorites to star it. Drag it out to unstar it.",
    "Ctrl+Shift+Enter opens all your favorites at once.",
    "Tick a few rows, then Ctrl+Enter opens them together.",
    "Rows that open at login say \"starts at login\" under their name.",
]
BADGES = [  # (id, emoji, name, how to earn it; None = a secret until earned)
    ("first-star", "⭐", "First star", "Star a terminal."),
    ("collector", "🌟", "Collector", "Have 5 favorites at once."),
    ("tidy-paws", "🐾", "Tidy paws", "Drag a row to a new place."),
    ("squad", "🚀", "Squad", "Open 3 or more terminals at once."),
    ("regular", "☕", "Regular", "Open 25 terminals."),
    ("marathon", "🏃", "Marathon", "Open 100 terminals."),
    ("early-bird", "🐦", "Early bird", "Open a terminal before 7 in the morning."),
    ("night-owl", "🦉", "Night owl", "Open a terminal after 11 at night."),
    ("explorer", "🧭", "Explorer", "Try all three Open in choices."),
    ("polyglot", "🗣️", "Polyglot", "Keep 3 different kinds of entries in your list."),
    ("wayfinder", "🗺️", "Wayfinder", "Open a Folder… and keep it in the list."),
    ("curious", "🐱", "Curious cat", None),   # pet the cat 10 times
]
CAT_FUR, CAT_FUR_DARK, CAT_BELLY = (0.97, 0.64, 0.30), (0.86, 0.48, 0.18), (1.0, 0.91, 0.80)
CAT_LINE, CAT_PINK, CAT_EYE = (0.25, 0.20, 0.27), (0.98, 0.56, 0.64), (0.16, 0.13, 0.20)
CAT_GOLD = (1.0, 0.80, 0.20)


def cat_pose(**kw):
    """The cat's pose.
    eyes: open, happy (^ ^), wide, sleepy, closed, love (hearts), star (sparkles); blink_l/blink_r 0..1
    close one eye (a blink, a wink). mouth: w (ω), smile, o, meow. ears: -1 droop .. 1 perk (ear: a
    twitch). tail, look: -1..1. hop, paw: 0..1. blush: 0..1. squash: -1 stretch .. 1 squash. tilt: a
    wobble in radians. step: the walking phase (None when not walking). facing: 1 right, -1 left.
    sleep: asleep (closed eyes, drooping ears, z z z)."""
    p = {"eyes": "open", "blink_l": 0.0, "blink_r": 0.0, "mouth": "w", "ears": 0.0, "ear": 0.0, "tail": 0.0,
         "hop": 0.0, "paw": 0.0, "look": 0.0, "blush": 0.35, "squash": 0.0, "tilt": 0.0, "step": None,
         "facing": 1, "sleep": False}
    p.update(kw)
    return p


def _heart(cr, x, y, s):
    cr.move_to(x, y + s * 0.9)
    cr.curve_to(x - s * 1.6, y - s * 0.2, x - s * 0.6, y - s * 1.3, x, y - s * 0.4)
    cr.curve_to(x + s * 0.6, y - s * 1.3, x + s * 1.6, y - s * 0.2, x, y + s * 0.9)
    cr.close_path()


def _sparkle(cr, x, y, r):
    for i in range(8):
        a = i * math.pi / 4 - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.38
        (cr.move_to if i == 0 else cr.line_to)(x + rr * math.cos(a), y + rr * math.sin(a))
    cr.close_path()


def draw_cat(cr, size, p):
    """Draw Byte into a size × size square with cairo (130 units: the cat is about 100 tall, with room
    above it to hop and stretch). Big shiny eyes with moods, rosy cheeks, a round head, a curly tail,
    squash and stretch, and a wobble around its feet."""
    def ellipse(x, y, rx, ry):
        cr.save(); cr.translate(x, y); cr.scale(max(rx, 0.01), max(ry, 0.01)); cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.restore()

    def fill_stroke(fill, lw=2.6):
        cr.set_source_rgb(*fill); cr.fill_preserve()
        cr.set_source_rgb(*CAT_LINE); cr.set_line_width(lw); cr.stroke()

    p = dict(p)
    blush = float(p["blush"]) if not isinstance(p["blush"], bool) else (1.0 if p["blush"] else 0.35)
    ears = max(-1.0, min(1.0, p["ears"] + p.get("ear", 0.0) - (0.8 if p["sleep"] else 0.0)))
    cr.save()
    cr.scale(size / 130, size / 130)
    if p.get("facing", 1) < 0:                    # walking left: draw it mirrored
        cr.translate(130, 0); cr.scale(-1, 1)
    step = p.get("step")
    bob = 2.0 * abs(math.sin(step)) if step is not None else 0.0
    cr.translate(15, 27 - p["hop"] * 14 - bob)
    cr.set_line_join(1); cr.set_line_cap(1)      # round
    cr.translate(50, 98); cr.rotate(p["tilt"])   # the wobble and squash/stretch happen around the feet
    sq = p["squash"]
    cr.scale(1 + 0.14 * sq, 1 - 0.16 * sq)
    cr.translate(-50, -98)
    t = p["tail"] if step is None else 0.6 * math.sin(step * 0.5)    # tail: a curl that wags
    cr.move_to(64, 90)
    cr.curve_to(84, 94, 96 + 5 * t, 78, 90 + 8 * t, 64)
    cr.curve_to(86 + 8 * t, 56, 78 + 10 * t, 58, 82 + 9 * t, 64)
    cr.set_source_rgb(*CAT_LINE); cr.set_line_width(10.5); cr.stroke_preserve()
    cr.set_source_rgb(*CAT_FUR); cr.set_line_width(5.5); cr.stroke()
    ellipse(50, 80, 21, 17); fill_stroke(CAT_FUR)                      # a small round body
    ellipse(50, 83, 12, 11); cr.set_source_rgb(*CAT_BELLY); cr.fill()
    lift = 3.5 * math.sin(step) if step is not None else 0.0            # walking: the paws take turns
    ellipse(40, 96 - max(0.0, lift), 6.5, 4.2); fill_stroke(CAT_FUR)
    if p["paw"] <= 0:
        ellipse(60, 96 - max(0.0, -lift), 6.5, 4.2); fill_stroke(CAT_FUR)
    for side in (-1, 1):                                                 # ears: perk (1) or droop (-1)
        cr.save(); cr.translate(50 + side * 19, 22 + 2 * max(0, -ears))
        cr.rotate(side * (0.32 + 0.45 * max(0, -ears) - 0.12 * max(0, ears)))
        cr.move_to(-10, 9); cr.line_to(0, -13 - 3 * max(0, ears)); cr.line_to(10, 9); cr.close_path()
        fill_stroke(CAT_FUR)
        cr.move_to(-5, 6); cr.line_to(0, -6 - 2 * max(0, ears)); cr.line_to(5, 6); cr.close_path()
        cr.set_source_rgb(*CAT_PINK); cr.fill(); cr.restore()
    ellipse(50, 42, 31, 25); fill_stroke(CAT_FUR)                      # a big round head
    cr.set_source_rgb(*CAT_FUR_DARK); cr.set_line_width(2.4)
    for x in (44, 50, 56):
        cr.move_to(x, 19.5); cr.line_to(x, 25); cr.stroke()
    for x in (31, 69):                                                   # rosy cheeks
        ellipse(x, 51, 5.2, 3.0); cr.set_source_rgba(*CAT_PINK, 0.25 + 0.5 * blush); cr.fill()
    look = (p["look"] if step is None else 0.6) * 2.5
    for x, blink in ((38, p["blink_l"]), (62, p["blink_r"])):         # the eyes
        kind = "closed" if p["sleep"] else ("happy" if blink >= 0.5 and p["eyes"] == "open" and blink < 0.85
                                            else "closed" if blink >= 0.5 else p["eyes"])
        ex = x + look
        if kind in ("open", "wide", "sleepy"):
            rx, ry = (7.6, 9.4) if kind == "wide" else (6.6, 8.2)
            ry *= 1 - 0.8 * blink
            ellipse(ex, 43, rx, ry); cr.set_source_rgb(*CAT_EYE); cr.fill()
            ellipse(ex + 2.2, 39.5, 2.6 if kind == "wide" else 2.2, (2.8 if kind == "wide" else 2.4) * (1 - blink))
            cr.set_source_rgb(1, 1, 1); cr.fill()
            ellipse(ex - 2.4, 47, 1.2, 1.2 * (1 - blink)); cr.set_source_rgba(1, 1, 1, 0.85); cr.fill()
            if kind == "sleepy":                      # a soft, curved lid over the top half
                cr.move_to(ex - rx - 1.5, 44); cr.curve_to(ex - rx, 33, ex + rx, 33, ex + rx + 1.5, 44)
                cr.curve_to(ex + rx * 0.5, 46.5, ex - rx * 0.5, 46.5, ex - rx - 1.5, 44)
                cr.set_source_rgb(*CAT_FUR); cr.fill()
                cr.move_to(ex - rx - 0.5, 44); cr.curve_to(ex - rx * 0.4, 46.6, ex + rx * 0.4, 46.6, ex + rx + 0.5, 44)
                cr.set_source_rgb(*CAT_LINE); cr.set_line_width(2.2); cr.stroke()
        elif kind == "happy":                         # ^ ^
            cr.move_to(ex - 6, 45); cr.curve_to(ex - 3, 37, ex + 3, 37, ex + 6, 45)
            cr.set_source_rgb(*CAT_LINE); cr.set_line_width(2.8); cr.stroke()
        elif kind == "closed":                        # asleep or blinking: a soft downward arc
            cr.move_to(ex - 6, 42); cr.curve_to(ex - 3, 47, ex + 3, 47, ex + 6, 42)
            cr.set_source_rgb(*CAT_LINE); cr.set_line_width(2.6); cr.stroke()
        elif kind == "love":
            _heart(cr, ex, 43, 5.0); cr.set_source_rgb(*CAT_PINK); cr.fill_preserve()
            cr.set_source_rgb(*CAT_LINE); cr.set_line_width(1.4); cr.stroke()
        elif kind == "star":
            ellipse(ex, 43, 6.6, 8.2); cr.set_source_rgb(*CAT_EYE); cr.fill()
            _sparkle(cr, ex, 43, 5.4); cr.set_source_rgb(*CAT_GOLD); cr.fill()
    if p["paw"] > 0:                                  # the waving paw, in front of the cheek
        a = p["paw"]
        cr.save(); cr.translate(76 + 4 * a, 80 - 22 * a); cr.rotate(-0.5 * a)
        ellipse(0, 0, 6.2, 7.2); fill_stroke(CAT_FUR)
        cr.set_source_rgb(*CAT_LINE); cr.set_line_width(1.2)
        for dx in (-2.4, 0, 2.4):
            cr.move_to(dx, -6.5); cr.line_to(dx, -3.5); cr.stroke()
        cr.restore()
    cr.move_to(48, 52); cr.line_to(52, 52); cr.line_to(50, 54.5); cr.close_path()   # nose and mouth
    cr.set_source_rgb(*CAT_PINK); cr.fill()
    cr.set_source_rgb(*CAT_LINE); cr.set_line_width(1.8)
    if p["mouth"] == "w":
        cr.arc(47.6, 55, 2.4, 0.05 * math.pi, 0.95 * math.pi); cr.stroke()
        cr.arc(52.4, 55, 2.4, 0.05 * math.pi, 0.95 * math.pi); cr.stroke()
    elif p["mouth"] == "meow":                        # open, with a little tongue
        cr.move_to(45.5, 55.5); cr.curve_to(46, 63, 54, 63, 54.5, 55.5); cr.close_path()
        cr.set_source_rgb(0.55, 0.20, 0.28); cr.fill_preserve(); cr.set_source_rgb(*CAT_LINE); cr.stroke()
        ellipse(50, 60.2, 2.6, 1.6); cr.set_source_rgb(*CAT_PINK); cr.fill()
    elif p["mouth"] == "o":
        ellipse(50, 58, 2.4, 3); cr.fill()
    else:
        cr.arc(50, 54, 5, 0.15 * math.pi, 0.85 * math.pi); cr.stroke()
    cr.set_line_width(1.1)                            # whiskers
    for side in (-1, 1):
        for dy in (-1.5, 2):
            cr.move_to(50 + side * 13, 53 + dy * 0.6); cr.line_to(50 + side * 29, 51 + dy * 2.2); cr.stroke()
    cr.restore()
    if p["sleep"]:
        cr.save(); cr.scale(size / 130, size / 130)
        cr.select_font_face("Sans", 0, 1)
        for x, y, fs in ((96, 34, 12), (106, 23, 9), (114, 15, 7)):
            cr.set_font_size(fs); cr.move_to(x, y); cr.set_source_rgba(*CAT_LINE, 0.75); cr.show_text("z")
        cr.restore()


def cat_line(kind, now, extra=None):
    """One of the cat's lines. kind: greeting, word, joke or tip; the word and the joke change daily."""
    day = now.date().toordinal()
    if kind == "greeting":
        part = ("Good night" if now.hour < 5 else "Good morning" if now.hour < 12
                else "Good afternoon" if now.hour < 17 else "Good evening" if now.hour < 22 else "Good night")
        return f"{part}! It's {now.strftime('%A, %B')} {now.day}."
    if kind == "word":
        word, meaning = CAT_WORDS[day % len(CAT_WORDS)]
        return f"Word of the day: {word}, {meaning}"
    if kind == "joke":
        return CAT_JOKES[(day * 7) % len(CAT_JOKES)]
    return f"Did you know? {CAT_TIPS[(day * 5 + (extra or 0)) % len(CAT_TIPS)]}"


# ---------- where Byte goes ----------
CAT_SPEED = 220.0   # px per second when walking


def cat_step(pos, target, dt, speed=CAT_SPEED):
    """One frame of walking: move pos (x, y) toward target by at most speed × dt.
    Returns (x, y, arrived)."""
    dx, dy = target[0] - pos[0], target[1] - pos[1]
    dist = math.hypot(dx, dy)
    if dist <= max(speed * dt, 0.5):
        return target[0], target[1], True
    k = speed * dt / dist
    return pos[0] + dx * k, pos[1] + dy * k, False


def cat_route(pos, target, floor):
    """The way a cat gets somewhere without walking over the list: down to the floor, along the
    floor, then straight up (the margins and the window's edges are empty). Returns the waypoints."""
    (x, y), (tx, ty) = pos, target
    points = []
    if abs(y - floor) > 2 and abs(tx - x) > 2:
        points.append((x, floor))
        y = floor
    if abs(tx - x) > 2:
        points.append((tx, y))
    if abs(ty - y) > 2 or not points:
        points.append((tx, ty))
    return points


def cat_spot(kind, w, h, size, rng, list_box=None, side=None, inset=0):
    """A place for the cat in a w × h window, as (x, y) of its top-left corner, or None if there's no
    room for that kind. The app is drawn `inset` px in from the left and right (a see-through border
    the cat can step out into; 0 = none). kind: "floor" (along the app's bottom), "margin" (in the
    empty space beside the list, when the app is wide enough; list_box = the list's (x, y, width,
    height)), "peek" (half over the app's left or right edge), "beside" (outside the app, next to its
    side, in the see-through border) or "outside" (past the window's edge, gone for a while). side:
    "left" or "right", else chosen at random."""
    floor = max(0.0, h - size - 6)
    if kind == "floor":                          # with a side: only that half of the floor
        lo, hi = inset + 8, max(inset + 8, w - inset - size - 8)
        if side == "left":
            hi = max(lo, w / 2 - size)
        elif side == "right":
            lo = min(hi, w / 2)
        return rng.uniform(lo, hi), floor
    side = side or rng.choice(["left", "right"])
    if kind == "margin":
        if not list_box:
            return None
        lx, ly, lw, lh = list_box
        room = lx - inset if side == "left" else w - inset - (lx + lw)
        if room < size + 8:
            return None
        x = inset + (room - size) / 2 if side == "left" else lx + lw + (room - size) / 2
        top, bottom = max(ly, 8), min(ly + lh, floor) - size
        return (x, rng.uniform(top, bottom)) if bottom > top else (x, floor)
    if kind == "peek":
        return (inset - size * 0.45 if side == "left" else w - inset - size * 0.55), rng.uniform(h * 0.3, max(h * 0.3, floor))
    if kind == "beside":
        if inset < size + 8:
            return None
        return ((inset - size) / 2 if side == "left" else w - inset + (inset - size) / 2), floor
    if kind == "outside":
        return (-size - 24 if side == "left" else w + 24), floor
    raise ValueError(kind)


# ---------- Byte watches the launcher's kitty window ----------
# Every 2 seconds the window asks the launcher's own kitty window (its remote-control socket,
# read-only `kitty @ ls`) what each tab is doing, and Byte comments on what changed: a long build
# finishing, a command failing, a coding agent being done, a git push. It reads only whether the
# shell is at its prompt, the last command line, its exit status, the program in front and the tab
# title (kitty's shell integration reports these). It shows only a command's name and subcommand,
# never its arguments, and keeps nothing. Other terminals can't be asked, so this is kitty only.
AGENT_NAMES = {"claude": "Claude", "omp": "omp", "opencode": "opencode"}
WATCH_SUBCOMMANDS = {"git", "npm", "pnpm", "yarn", "bun", "cargo", "go", "make", "docker", "podman", "kubectl",
                     "pip", "uv", "poetry", "dotnet", "mvn", "gradle", "apt", "dnf", "flatpak", "systemctl", "just"}
WATCH_BUSY = ("build", "test", "install", "deploy", "compile", "check", "lint", "push", "pull", "update",
              "upgrade", "sync", "bundle", "release")


def short_cmd(cmdline):
    """'npm run build -- --watch' → 'npm run build'; 'sudo apt upgrade -y' → 'apt upgrade';
    'cd app && make test | tee log' → 'make test' (the last command of a chain, the first of a pipe).
    Only the program and its subcommand words: arguments (paths, values, maybe secrets) are never shown."""
    try:
        lex = shlex.shlex(cmdline or "", posix=True, punctuation_chars=";&|")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        tokens = (cmdline or "").split()
    chain = [[]]
    for t in tokens:
        if t in (";", "&&", "||", "&"):
            chain.append([])
        else:
            chain[-1].append(t)
    words = next((c for c in reversed(chain) if c), [])
    if "|" in words:
        words = words[:words.index("|")]
    while words and (words[0] in ("sudo", "env", "time", "nice", "command", "exec") or "=" in words[0]):
        words = words[1:]
    if not words:
        return ""
    prog = os.path.basename(words[0])[:24]
    out = [prog]
    if prog in WATCH_SUBCOMMANDS:
        for w in words[1:3]:
            if w.startswith("-") or not w.replace("-", "").replace(":", "").isalnum() or len(w) > 20:
                break
            out.append(w)
            if prog not in ("npm", "pnpm", "yarn", "bun") or w != "run":
                break
    return " ".join(out)


def parse_kitty_ls(data, names=None):
    """From `kitty @ ls` JSON keep only what Byte uses, per window id: a name for the tab, whether the
    shell is at its prompt, the short last command, its exit status, and the agent in front (if any).
    The name is the list entry whose folder the window is in (`names`: real folder path → name), else
    a title someone set on purpose, else the folder's name. Never kitty's automatic title: while a
    command runs that is the whole command line."""
    snap = {}
    for os_win in data if isinstance(data, list) else []:
        for tab in os_win.get("tabs", []):
            for w in tab.get("windows", []):
                fg = [os.path.basename(str((p.get("cmdline") or [""])[0])) for p in w.get("foreground_processes") or []]
                agent = next((AGENT_NAMES[n] for n in fg if n in AGENT_NAMES), None)
                cwd = str(w.get("cwd") or "").rstrip("/")
                name = (names or {}).get(os.path.realpath(cwd)) if cwd else None
                if not name and tab.get("title_overridden"):
                    name = str(tab.get("title") or "").strip()
                if not name and cwd:
                    name = "your home folder" if cwd == str(Path.home()) else os.path.basename(cwd)
                snap[w.get("id")] = {"tab": (name or "a tab")[:40], "prompt": bool(w.get("at_prompt")),
                                     "cmd": short_cmd(w.get("last_reported_cmdline") or ""),
                                     # tells one run from the next without keeping the command line
                                     "run": hash((w.get("last_reported_cmdline"), w.get("last_cmd_exit_status"))),
                                     "status": w.get("last_cmd_exit_status"), "agent": agent}
    return snap


def fmt_duration(s):
    s = int(s)
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60:02d} s" if s < 3600 else f"{s // 3600} h {s % 3600 // 60} min"


def watch_events(prev, cur, now):
    """What changed between two snapshots, as (kind, sound, text, important) for Byte to say. Also
    carries each window's start time ("since") and last failed command ("failed") forward in `cur`."""
    events = []
    for wid, c in cur.items():
        p = prev.get(wid)
        if p is None:
            c["since"] = now
            continue
        c["since"] = p.get("since", now)
        c["failed"] = p.get("failed")
        tab, cmd = c["tab"], c["cmd"] or "that command"
        if p["agent"] and not c["agent"]:
            events.append(("agent", "done", f"{p['agent']} in {tab} is done. It might want you.", True))
            continue
        if p["prompt"] and not c["prompt"]:                        # a command started
            c["since"] = now
            if not c["agent"] and any(b in cmd.split()[1:] or cmd.endswith(b) for b in WATCH_BUSY):
                events.append(("start", None, f"{cmd} is running in {tab}. I'll keep an eye on it.", False))
        elif c["prompt"] and not p["agent"] and (not p["prompt"] or c.get("run") != p.get("run")):
            # a command finished (or a quick one started and finished between two looks)
            took, status = (now - p.get("since", now)) if not p["prompt"] else 0, c["status"]
            if status == 127:
                events.append(("fail", "oops", f"{cmd.split()[0]} isn't a command here. A typo?", False))
            elif status not in (0, None):
                c["failed"] = cmd
                if took >= 3 or any(b in cmd for b in WATCH_BUSY):
                    events.append(("fail", "oops", f"✗ {cmd} failed in {tab} (exit {status}). You've got this.", True))
            elif status == 0:
                if p.get("failed") == cmd:
                    c["failed"] = None
                    events.append(("fixed", "done", f"Fixed it! {cmd} works now in {tab}.", True))
                elif cmd.startswith("git push"):
                    events.append(("push", "done", f"Pushed from {tab}. Off it goes!", True))
                elif cmd.startswith("git commit"):
                    events.append(("commit", None, f"Committed in {tab}. Nice and tidy.", False))
                elif took >= 20:
                    events.append(("done", "done", f"✓ {cmd} finished in {tab} ({fmt_duration(took)}).", True))
    return events


# ---------- UI ----------
# The drop line for dragging, the gold star, and the small animations: a star that pops, a row that
# glows softly when it becomes a favorite, and ▶ that gives a little press.
APP_CSS = """
row.drop-above { box-shadow: inset 0 2px @accent_color; }
row.drop-below { box-shadow: inset 0 -2px @accent_color; }
row.dragging { opacity: 0.4; }
.star-btn:checked { color: #e5a50a; background: none; }
@keyframes tl-star-pop { 0% { transform: scale(1); } 40% { transform: scale(1.45) rotate(-14deg); }
                         100% { transform: scale(1); } }
.star-pop { animation: tl-star-pop 420ms ease-out; }
@keyframes tl-glow { from { background-color: alpha(#e5a50a, 0.22); } to { background-color: transparent; } }
row.glow { animation: tl-glow 1300ms ease-out; }
@keyframes tl-press { 0% { transform: scale(1); } 45% { transform: scale(0.82); } 100% { transform: scale(1); } }
.press { animation: tl-press 260ms ease-out; }
.tl-page clamp { margin-bottom: FLOORpx; }
.cat-bubble { background-color: @popover_bg_color; color: @popover_fg_color; border-radius: 14px;
              padding: 7px 12px; margin: 4px; box-shadow: 0 1px 4px alpha(black, 0.28);
              font-style: italic; transition: opacity 400ms ease-out; }
.cat-bubble.faded { opacity: 0; }
@keyframes tl-pop { 0% { transform: scale(0.6); opacity: 0; } 60% { transform: scale(1.06); opacity: 1; }
                    100% { transform: scale(1); } }
.cat-bubble.pop { animation: tl-pop 320ms ease-out; }
.cat-bubble button { min-height: 26px; padding: 2px 10px; font-style: normal; }
window.tl-outside, window.tl-outside > dialog-host, window.tl-outside .tl-stage { background: transparent; box-shadow: none; }
window.tl-outside .tl-card { background-color: @window_bg_color; border-radius: 12px;
                             box-shadow: 0 2px 10px alpha(black, 0.35), 0 0 0 1px alpha(black, 0.08); }
"""
KIND_ICONS = {"claude": "chat-symbolic", "omp": "chat-symbolic", "opencode": "chat-symbolic",
              "terminal": "utilities-terminal-symbolic", "command": "system-run-symbolic"}
KIND_HELP = {
    "claude": "Opens the folder and resumes that Claude Code session (or starts a new one).",
    "omp": "Opens the folder and resumes that omp session (or starts a new one).",
    "opencode": "Opens the folder and resumes that opencode session (or starts a new one).",
    "terminal": "Just a shell in the folder.",
    "command": "Runs your command in the folder, then leaves the shell open.",
}
MODE_ROWS = [  # (mode key, choice label, explanation)
    ("kitty", "My kitty window", "As tabs in the launcher's kitty window (opens one if needed)."),
    ("tabs", "One new window", "One new GNOME Terminal window, with a tab for each."),
    ("windows", "Separate windows", "Each in its own COSMIC Terminal window."),
]


def short_path(p):
    p = os.path.expanduser(p)
    home = str(Path.home())
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


def row_subtitle(entry):
    kind = entry.get("kind", "claude")
    if kind == "terminal":
        what = "Terminal"
    elif kind == "command":
        what = "Runs: " + (entry.get("command") or "")
    else:
        label = AGENTS.get(kind, AGENTS["claude"])[0]
        sid = (entry.get("session") or "").strip()
        what = f"{label} · session {sid[:8]}" if sid else f"{label} · new session"
    if entry.get("autostart"):
        what += "  ·  starts at login"
    return f"{short_path(entry['folder'])}  ·  {what}"


class EditDialog(Adw.Window):
    def __init__(self, parent, entry, on_save):
        super().__init__(transient_for=parent, modal=True,
                         title="Edit terminal" if entry else "Add a terminal",
                         default_width=580, default_height=640)
        self.on_save = on_save
        self.recent = []
        entry = entry or {"name": "", "folder": "", "session": "", "kind": "claude"}

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel", tooltip_text="Close without saving")
        cancel.connect("clicked", lambda *_: self.close())
        self.save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"],
                                   tooltip_text="Save to your list")
        self.save_btn.connect("clicked", self._save)
        header.pack_start(cancel)
        header.pack_end(self.save_btn)
        tv.add_top_bar(header)

        page = Adw.PreferencesPage()

        what = Adw.PreferencesGroup(title="What should it open?")
        self.what_group = what
        self.kind = Adw.ComboRow(title="Type", model=Gtk.StringList.new([k[1] for k in KINDS]))
        self.kind.set_selected([k[0] for k in KINDS].index(entry.get("kind", "claude")))
        self.kind.connect("notify::selected", lambda *_: self._kind_changed())
        what.add(self.kind)
        page.add(what)

        self.pick_group = Adw.PreferencesGroup(
            title="Start from a recent Claude session",
            description="Picking one fills in the name, folder and session for you.")
        self.recent_row = Adw.ComboRow(title="Recent sessions", subtitle="Loading…",
                                       tooltip_text="Newest first, read from Claude Code's own session history")
        self.recent_row.set_model(Gtk.StringList.new(["Choose…"]))
        self.recent_row.connect("notify::selected", self._picked)
        self.pick_group.add(self.recent_row)
        page.add(self.pick_group)

        details = Adw.PreferencesGroup(title="Details")
        self.name = Adw.EntryRow(title="Name", text=entry.get("name", ""),
                                 tooltip_text="Shown in the list. Left empty, the folder name is used.")
        self.folder = Adw.EntryRow(title="Folder", text=entry.get("folder", ""),
                                   tooltip_text="Where the terminal starts. ~ means your home folder.")
        browse = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Choose a folder", css_classes=["flat"])
        browse.connect("clicked", self._browse)
        self.folder.add_suffix(browse)
        self.sid = Adw.EntryRow(title="Session ID (optional)", text=entry.get("session", ""),
                                tooltip_text="The ID you would pass to resume it (claude --resume, omp --resume, "
                                             "opencode --session). Leave empty to start a new session.")
        self.command = Adw.EntryRow(title="Command", text=entry.get("command", ""),
                                    tooltip_text="e.g. git status, npm run dev, htop")
        for r in (self.name, self.folder, self.sid, self.command):
            details.add(r)
        self.folder_hint = Gtk.Label(xalign=0, wrap=True, css_classes=["caption", "error"],
                                     margin_top=6, visible=False)
        details.add(self.folder_hint)
        page.add(details)

        startup = Adw.PreferencesGroup(title="Startup")
        self.autostart = Adw.SwitchRow(
            title="Start when I log in", active=bool(entry.get("autostart")),
            subtitle="Opens this automatically each time you log in to the PC, the way the launcher "
                     "is set to open (Open in).")
        startup.add(self.autostart)
        page.add(startup)

        for r in (self.folder, self.command):
            r.connect("changed", lambda *_: self._validate())
        self._kind_changed()
        tv.set_content(page)
        self.set_content(tv)

        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                      action=Gtk.CallbackAction.new(lambda *_: self.close() or True)))
        self.add_controller(esc)
        GLib.idle_add(self._load_recent)

    def _kind_name(self):
        return KINDS[self.kind.get_selected()][0]

    def _kind_changed(self):
        k = self._kind_name()
        self.what_group.set_description(KIND_HELP[k])
        self.sid.set_visible(k in AGENTS)
        self.pick_group.set_visible(k == "claude")   # only Claude Code's history is read
        self.command.set_visible(k == "command")
        self._validate()

    def _validate(self):
        folder = self.folder.get_text().strip()
        missing = bool(folder) and not os.path.isdir(os.path.expanduser(folder))
        (self.folder.add_css_class if missing else self.folder.remove_css_class)("error")
        self.folder_hint.set_label("That folder doesn't exist yet. You can still save it." if missing else "")
        self.folder_hint.set_visible(missing)
        ok = bool(folder) and (self._kind_name() != "command" or bool(self.command.get_text().strip()))
        self.save_btn.set_sensitive(ok)
        self.save_btn.set_tooltip_text("Save to your list" if ok else
                                       "Fill in a folder" + (" and a command" if self._kind_name() == "command" else ""))

    def _load_recent(self):
        self.recent = recent_sessions()
        labels = ["Choose…"] + [f"{r['title']}  ·  {Path(r['folder']).name}  ·  {r['when']}" for r in self.recent]
        self.recent_row.set_model(Gtk.StringList.new(labels))
        self.recent_row.set_subtitle(f"{len(self.recent)} sessions" if self.recent else "None found")
        return False

    def _picked(self, row, _pspec):
        i = row.get_selected()
        if 1 <= i <= len(self.recent):
            r = self.recent[i - 1]
            self.name.set_text(r["title"])
            self.folder.set_text(r["folder"])
            self.sid.set_text(r["session"])

    def _browse(self, _btn):
        dlg = Gtk.FileDialog(title="Choose the folder")
        start = self.folder.get_text().strip()
        if start and os.path.isdir(os.path.expanduser(start)):
            dlg.set_initial_folder(Gio.File.new_for_path(os.path.expanduser(start)))

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f:
                self.folder.set_text(f.get_path())
                if not self.name.get_text().strip():
                    self.name.set_text(Path(f.get_path()).name)
        dlg.select_folder(self, None, done)

    def _save(self, _btn):
        folder = self.folder.get_text().strip()
        k = self._kind_name()
        if not folder or (k == "command" and not self.command.get_text().strip()):
            self._validate()
            return
        e = {"name": self.name.get_text().strip() or Path(os.path.expanduser(folder)).name,
             "folder": folder, "kind": k}
        if self.autostart.get_active():
            e["autostart"] = True
        if k in AGENTS:
            e["session"] = self.sid.get_text().strip()
        elif k == "command":
            e["command"] = self.command.get_text().strip()
        self.on_save(e)
        self.close()


def click_rects(card, stage_at, surface_at, cat, size, bubble):
    """The parts of the window that take clicks while it has the see-through border, as whole-pixel
    (x, y, w, h) on the window's surface: the app's card (window coordinates), the cat at `cat` and
    its bubble box `bubble` (both on the stage, which starts at `stage_at`; None = leave it out).
    surface_at = where the window starts on its surface (GTK's shadow, if any)."""
    sx, sy = surface_at
    out = [(card[0] + sx, card[1] + sy, card[2], card[3])]
    ox, oy = stage_at[0] + sx, stage_at[1] + sy
    if cat:
        out.append((cat[0] + ox, cat[1] + oy, size, size))
    if bubble:
        out.append((bubble[0] + ox, bubble[1] + oy, bubble[2], bubble[3]))
    return [(math.floor(x), math.floor(y), math.ceil(w + x - math.floor(x)), math.ceil(h + y - math.floor(y)))
            for x, y, w, h in out]


def app_css():
    """APP_CSS with the room the list keeps at the bottom for Byte filled in."""
    return APP_CSS.replace("FLOORpx", f"{Cat.SIZE + 24}px")


class Cat:
    """Byte on the window: a drawn cat and a speech bubble, floating over everything on a Gtk.Overlay
    (under the toasts). It sits on the floor or in the margins beside the list, walks between them,
    peeks in from the edges and now and then goes outside for a while. Only the cat itself takes
    clicks; the bubble and the space around it let them through. `now` and `rng` can be replaced
    (tests pin them). Its timers run only while it's shown."""
    SIZE = 96

    def __init__(self, overlay, on_pet, now=None, rng=None):
        import random
        self.overlay = overlay
        self.now = now or datetime.now
        self.rng = rng or random.Random()
        self.pose = cat_pose()
        self.anims = []            # (start, length, kind)
        self.timers = []
        self.until = 0.0           # the bubble stays up until then
        self.next_talk = time.monotonic() + 45
        self.next_move = time.monotonic() + 8
        self.turn = 0
        self.visible = True
        self.list_box = lambda: None   # the Window gives the list's (x, y, w, h) on the overlay
        self.inset = 0             # the see-through border on each side (Byte can step out into it)
        self.on_moved = None       # the Window updates which parts of it take clicks
        self.bubble_box = None     # where the bubble was last put: (x, y, w, h) on the overlay
        self.x, self.y = 12.0, 0.0
        self.target = None         # (x, y) while walking
        self.path = []             # the waypoints after it
        self.spot = "floor"        # floor, margin, row, peek, away
        self.away_until = 0.0
        self.back_at = 0.0         # leave a "row" or "peek" spot by then
        self.facing = 1
        self.phase = 0.0
        self.last_frame = 0.0
        self.placed = False
        self.area = Gtk.DrawingArea(content_width=self.SIZE, content_height=self.SIZE,
                                    halign=Gtk.Align.START, valign=Gtk.Align.START,
                                    tooltip_text="Byte, the launcher's cat. Click to say hi.")
        self.area.set_draw_func(lambda _a, cr, w, h: draw_cat(cr, min(w, h), self._pose_now()))
        self.area.update_property([Gtk.AccessibleProperty.LABEL], ["Byte, the launcher's cat"])
        self.held = False          # being carried by the pointer
        self.placed_at = (0, 0)    # where the cat was last put on screen
        self.dragged = False       # the last press turned into a drag (so it isn't a poke)
        self.speed = CAT_SPEED
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: (self._cancel_scoot(), None if self.dragged else on_pet()))
        self.area.add_controller(click)
        carry = Gtk.GestureDrag()   # pick it up and put it somewhere else
        carry.connect("drag-begin", lambda *_: (setattr(self, "dragged", False), self._cancel_scoot()))
        carry.connect("drag-update", self._on_drag)
        carry.connect("drag-end", lambda *_: self.drop() if self.held else None)
        self.area.add_controller(carry)
        hover = Gtk.EventControllerMotion()   # rest the pointer on it and it makes room
        hover.connect("enter", lambda *_: self._arm_scoot())
        hover.connect("leave", lambda *_: self._cancel_scoot())
        self.area.add_controller(hover)
        self.scoot_timer = 0
        self.area.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        self.label = Gtk.Label(wrap=True, max_width_chars=34, lines=4, ellipsize=3, xalign=0, hexpand=True)
        self.text = ""             # the whole line (the label may still be typing it out)
        self.typing = 0
        self.asking = False        # a question with buttons is up
        self.bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["cat-bubble"],
                              halign=Gtk.Align.START, valign=Gtk.Align.START, can_target=False)
        top = Gtk.Box(spacing=6)
        top.append(self.label)
        self.close_btn = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"],
                                    valign=Gtk.Align.START, visible=False, tooltip_text="Never mind")
        self.close_btn.connect("clicked", lambda *_: self.close_question())
        top.append(self.close_btn)
        self.choices = Gtk.Box(spacing=6, visible=False)
        self.bubble.append(top)
        self.bubble.append(self.choices)
        overlay.add_overlay(self.area)
        overlay.add_overlay(self.bubble)
        overlay.connect("get-child-position", self._place)
        self.set_badge_count(0)
        self.say(cat_line("greeting", self.now()), 12)

    # ---------- where it is ----------
    def size(self):
        return max(1, self.overlay.get_width()), max(1, self.overlay.get_height())

    def in_border(self):
        """Is it sitting out in the see-through border (and still on the floor there)?"""
        w, h = self.size()
        mid, g = self.x + self.SIZE / 2, self.inset
        return g >= self.SIZE + 8 and (0 < mid < g or w - g < mid < w) and abs(self.y - (h - self.SIZE - 6)) <= 2

    def _place(self, _overlay, child, rect):
        w, h = self.size()
        if not self.placed and h > self.SIZE * 2:      # first time: sit on the floor, left
            self.x, self.y, self.placed = self.inset + 12.0, h - self.SIZE - 6.0, True
        if child is self.area:
            rect.x, rect.y, rect.width, rect.height = round(self.x), round(self.y), self.SIZE, self.SIZE
            self.placed_at = (rect.x, rect.y)
            return True
        if child is self.bubble:
            _min, nat = child.get_preferred_size()
            bw, bh = min(nat.width, max(80, w - 12)), nat.height
            cx = min(max(self.x, 0), w - self.SIZE)       # a cat outside still talks from the edge
            floor = h - self.SIZE - 6
            if self.y >= floor - 4:
                # on the floor: talk beside it, in the empty strip the list keeps at the bottom, so the
                # bubble (and its buttons) never covers a row
                right = cx + self.SIZE + 4
                bx = right if right + bw <= w - 6 else cx - bw - 4
                bx = min(max(bx, 6), max(6, w - bw - 6))
                by = h - bh - 8
            else:
                bx = min(max(cx + self.SIZE / 2 - bw / 2, 6), max(6, w - bw - 6))
                by = self.y - bh - 4
                if by < 6:
                    by = self.y + self.SIZE + 4
            rect.x, rect.y, rect.width, rect.height = round(bx), round(min(by, h - bh - 6)), bw, bh
            box = (rect.x, rect.y, bw, bh)
            if box != self.bubble_box:
                self.bubble_box = box
                if self.asking and self.on_moved:     # its buttons must take clicks out there too
                    GLib.idle_add(lambda: self.on_moved() and False)
            return True
        return False

    def _relayout(self):
        self.overlay.queue_allocate()
        if self.on_moved:
            self.on_moved()

    def go(self, x, y, spot):
        """Walk to (x, y) (or jump there when animations are off) and call it `spot`."""
        self.spot = spot
        if not Gtk.Settings.get_default().get_property("gtk-enable-animations"):
            self.x, self.y, self.target = x, y, None
            self._relayout()
            return
        _w, h = self.size()
        self.path = cat_route((self.x, self.y), (x, y), h - self.SIZE - 6)
        self._next_leg()
        self._ensure_frames()

    def _next_leg(self):
        self.target = self.path.pop(0) if self.path else None
        if self.target and abs(self.target[0] - self.x) > 1:
            self.facing = 1 if self.target[0] > self.x else -1

    def wander(self, kind=None, side=None):
        """Go somewhere: a kind of spot from cat_spot(), or one picked at random."""
        w, h = self.size()
        if kind is None:
            r = self.rng.random()
            if self.inset:   # room outside the app: it sits out there instead of leaving (the window's
                             # real edge is invisible, so walking off it would cut the cat off in mid-air)
                kind = "floor" if r < 0.35 else "margin" if r < 0.6 else "beside" if r < 0.9 else "peek"
            else:
                kind = "floor" if r < 0.45 else "margin" if r < 0.75 else "peek" if r < 0.9 else "outside"
        spot = cat_spot(kind, w, h, self.SIZE, self.rng, self.list_box(), side, self.inset)
        if kind == "outside":                      # remember the way out: it comes back the other way
            side = side or self.rng.choice(["left", "right"])
            self.exit_side = side
            spot = cat_spot(kind, w, h, self.SIZE, self.rng, side=side)
        if spot is None:
            kind, spot = "floor", cat_spot("floor", w, h, self.SIZE, self.rng, inset=self.inset)
        if kind == "outside":
            self.away_until = time.monotonic() + self.rng.uniform(20, 60)
            self.say(self.rng.choice(["Back in a bit.", "Going out for a stretch.", "I'll be right back."]), 4)
        if kind == "peek":
            self.back_at = time.monotonic() + self.rng.uniform(8, 16)
        self.go(*spot, "away" if kind == "outside" else kind)

    def come_back(self, side=None):
        """From outside: appear just past the edge across from the one it left by, and walk in (with
        the see-through border: hop in from beside the app, so it's never cut off in mid-air)."""
        w, h = self.size()
        gone = getattr(self, "exit_side", None)
        side = side or {"left": "right", "right": "left"}.get(gone) or self.rng.choice(["left", "right"])
        spot = cat_spot("beside", w, h, self.SIZE, self.rng, side=side, inset=self.inset)
        self.x, self.y = spot or cat_spot("outside", w, h, self.SIZE, self.rng, side=side)
        if spot:
            self.play("hop", 0.5)
        self.away_until = 0.0
        self._relayout()
        self.go(*cat_spot("floor", w, h, self.SIZE, self.rng, inset=self.inset), "floor")

    def visit(self, widget):
        """Go and sit beside a row (in the margin when there's room, else on the floor below it)."""
        if not self.visible or widget is None:
            return
        ok, b = widget.compute_bounds(self.overlay)
        if not ok:
            return
        w, h = self.size()
        if self.spot == "away":
            self.come_back()
        lx, g = b.get_x(), self.inset
        y = min(max(b.get_y() + b.get_height() / 2 - self.SIZE / 2, 6), h - self.SIZE - 6)
        if lx - g >= self.SIZE + 8:
            self.go(g + (lx - g - self.SIZE) / 2, y, "row")
        else:
            self.go(min(max(lx + 8, g + 8), w - g - self.SIZE - 8), h - self.SIZE - 6, "row")
        self.back_at = time.monotonic() + 15

    def on_scroll(self):
        if self.spot == "row":                       # the row moved away under it
            self.wander("floor")

    def _on_drag(self, gesture, dx, dy):
        if not self.held:
            if math.hypot(dx, dy) < 6:                # a click, not a drag (yet)
                return
            self.dragged = True
            self.pick_up()
        # GTK measures the offset in the cat's own coordinates, and the cat moves with the pointer: so
        # the pointer is at (where the cat was last placed + offset + where it was grabbed)
        ax, ay = self.placed_at
        self.drag_to(ax + dx, ay + dy)

    def pick_up(self):
        """Lifted by the pointer: it stops what it's doing, dangles and says so."""
        self.close_question()
        self.held, self.target, self.path = True, None, []
        self.spot = "held"
        self.say(self.rng.choice(["Wheee!", "Whoa, up we go!", "Hehe, careful!", "Mrrp! Where to?"]), 3)
        self._ensure_frames()

    def drag_to(self, x, y):
        w, h = self.size()                            # it can hang a little over the edges, not vanish
        if self.inset:                                # the window's real edge is invisible: stay whole
            self.x = min(max(x, 0), w - self.SIZE)
        else:
            self.x = min(max(x, -self.SIZE * 0.6), w - self.SIZE * 0.4)
        self.y = min(max(y, -self.SIZE * 0.2), h - self.SIZE - 6)
        self._relayout()

    def drop(self):
        """Let go: in a margin beside the list it sits there, at the app's edge it hangs and peeks
        there, outside the app (in the see-through border) or anywhere else it drops quickly to the
        floor. Then it stays a while before wandering."""
        self.held = False
        w, h = self.size()
        floor = h - self.SIZE - 6
        mid = self.x + self.SIZE / 2
        box = self.list_box()
        now = time.monotonic()
        self.next_move = now + 40
        g = self.inset
        if g and (mid < g or mid > w - g):            # out beside the app: down to the floor out there
            self.speed = CAT_SPEED * 2.6
            self.spot, self.path, self.target = "beside", [], (self.x, floor)
            if self.y < floor - 4:
                self.play("hop", 0.5)
        elif self.x < g - self.SIZE * 0.25 or self.x > w - g - self.SIZE * 0.75:
            self.spot, self.back_at = "peek", now + 40
        elif self.y < floor - 4 and box and (mid < box[0] or mid > box[0] + box[2]):
            self.spot, self.back_at = "margin", now + 40
        elif self.y < floor - 4:
            self.speed = CAT_SPEED * 2.6              # a quick drop to the floor
            self.spot, self.path, self.target = "floor", [], (self.x, floor)
            self.play("hop", 0.5)
        else:
            self.spot = "floor"
        self._ensure_frames()
        self._relayout()

    def passthrough(self, on):
        """While a row is dragged, the cat mustn't catch the drop."""
        self.area.set_can_target(not on)

    def _arm_scoot(self):
        self._cancel_scoot()
        if not self.asking:                           # not while it's asking you something
            self.scoot_timer = GLib.timeout_add(1200, self._scoot)

    def _cancel_scoot(self):
        if self.scoot_timer:
            GLib.source_remove(self.scoot_timer)
            self.scoot_timer = 0

    def _scoot(self):
        """The pointer rested on it without a click: it makes room."""
        self.scoot_timer = 0
        w, _h = self.size()
        self.wander("floor", side="right" if self.x < w / 2 else "left")
        return False

    # ---------- what it says ----------
    def say(self, text, hold=12, typed=False):
        """Say something now; the bubble pops up, stays for `hold` seconds, then fades out. typed=True
        types it out letter by letter (the bubble keeps its full size: the rest is drawn invisible)."""
        if self.asking:                  # a question is up: don't wipe it (the move still plays)
            return
        self._set_text(text, typed)
        self.area.set_tooltip_text(f"{text}\n\n{self.tip_base}" if hasattr(self, "tip_base") else text)
        self.until = time.monotonic() + hold
        self._pop()

    def _set_text(self, text, typed):
        """Show `text`; typed=True types it out with the cat's mouth moving (lip sync). The bubble is
        measured for the whole sentence first and keeps that size, so nothing jumps while it types."""
        self.text = text
        if self.typing:
            GLib.source_remove(self.typing)
            self.typing = 0
        self.label.set_size_request(-1, -1)
        self.label.set_label(text)
        self.talk = False
        if not typed or not Gtk.Settings.get_default().get_property("gtk-enable-animations"):
            return
        _min, nat = self.label.get_preferred_size()
        self.label.set_size_request(nat.width, nat.height)
        shown, ticks = [0], [0]

        def step():
            shown[0] = min(len(text), shown[0] + 2)
            ticks[0] += 1
            self.label.set_label(text[:shown[0]])
            if ticks[0] % 3 == 0:                     # the mouth opens and closes, about 11 times a second
                self.talk = not self.talk
            self.area.queue_draw()
            if shown[0] < len(text):
                return True
            self.typing = 0
            self.talk = False
            self.area.queue_draw()
            return False
        step()
        self.typing = GLib.timeout_add(30, step)

    def _pop(self):
        """Show the bubble with a little bounce."""
        self.bubble.remove_css_class("faded")
        self.bubble.set_visible(self.visible)
        self.bubble.remove_css_class("pop")
        if Gtk.Settings.get_default().get_property("gtk-enable-animations"):
            self.bubble.add_css_class("pop")
            GLib.timeout_add(400, lambda: self.bubble.remove_css_class("pop") and False)
        self._relayout()

    def ask(self, text, choices, hold=25):
        """A question with buttons: choices = [(label, callback or None)]. While it's up the bubble takes
        clicks, and the cat stays put and keeps quiet. A button, the × or `hold` seconds close it."""
        self.close_question()
        if self.spot == "away":
            self.come_back()
        self._set_text(text, True)
        for label, cb in choices:
            b = Gtk.Button(label=label, css_classes=["pill", "small"] + (["suggested-action"] if cb and not
                                                                          self.choices.get_first_child() else []),
                           tooltip_text=label)
            b.connect("clicked", lambda _b, cb=cb: (self.close_question(), cb and cb()))
            self.choices.append(b)
        self.asking = True
        self.choices.set_visible(True)
        self.close_btn.set_visible(True)
        self.bubble.set_can_target(True)
        self.target, self.path = None, []           # it stays where it is while you read
        self.until = time.monotonic() + hold
        self._pop()
        self._relayout()

    def close_question(self):
        if not self.asking:
            return
        self.asking = False
        child = self.choices.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.choices.remove(child)
            child = nxt
        self.choices.set_visible(False)
        self.close_btn.set_visible(False)
        self.bubble.set_can_target(False)
        self.until = time.monotonic() + 1.5
        self._relayout()

    def next_line(self):
        kinds = ["word", "joke", "tip", "greeting"]
        kind = kinds[self.turn % len(kinds)]
        self.turn += 1
        self.next_talk = time.monotonic() + 45
        self.say(cat_line(kind, self.now(), self.turn), 10)

    def set_badge_count(self, n):
        self.tip_base = f"Byte, the launcher's cat. 🏅 {n}/{len(BADGES)} badges. Click to say hi."
        self.area.set_tooltip_text(self.tip_base)

    # ---------- moving ----------
    def asleep(self):
        h = self.now().hour
        return h >= 23 or h < 6

    def play(self, kind, length):
        """Start one of the small moves: blink, wink, hop, wave, tail, ear, look, nod."""
        if not Gtk.Settings.get_default().get_property("gtk-enable-animations"):
            return
        self.anims.append((time.monotonic(), length, kind))
        self._ensure_frames()

    def _ensure_frames(self):
        if self.visible and not any(t[0] == "frame" for t in self.timers):
            self.last_frame = time.monotonic()
            self.timers.append(("frame", GLib.timeout_add(33, self._frame)))

    def _frame(self):
        now = time.monotonic()
        dt, self.last_frame = min(0.1, now - self.last_frame), now
        self.anims = [a for a in self.anims if now - a[0] < a[1]]
        if self.target:
            self.x, self.y, arrived = cat_step((self.x, self.y), self.target, dt, self.speed)
            self.phase += dt * 11
            if arrived:
                self._next_leg()
                if not self.target:
                    self.speed = CAT_SPEED
            self._relayout()
        self.area.queue_draw()
        if self.anims or self.target or self.held:
            return True
        self.timers = [t for t in self.timers if t[0] != "frame"]
        return False

    def _pose_now(self):
        p = dict(self.pose)
        p["facing"] = self.facing
        p["sleep"] = self.asleep() and not self.anims and not self.target
        if not p["sleep"] and self.now().hour == 22:   # the hour before its nap: heavy eyelids
            p["eyes"] = "sleepy"
        p["step"] = self.phase if self.target and self.speed == CAT_SPEED else None   # walking, not falling
        if self.typing:                                # talking: lip sync with the typing
            p["mouth"] = "meow" if getattr(self, "talk", False) else "w"
        if self.held:                                  # carried: wide eyes, perked ears, a swinging tail
            p.update(eyes="wide", mouth="o", ears=1.0, squash=-0.35, blush=0.8,
                     tail=math.sin(time.monotonic() * 7) * 0.9)
            return p
        if self.spot == "peek" and not self.target:   # peeking in from the edge looks inward
            w, _h = self.size()
            p["facing"] = 1 if self.x < w / 2 else -1
            p["look"] = 0.8 if self.x < w / 2 else -0.8
        now = time.monotonic()
        for start, length, kind in self.anims:
            f = min(1.0, (now - start) / length)
            bump = math.sin(math.pi * f)               # 0 → 1 → 0
            if kind == "blink":
                p["blink_l"] = p["blink_r"] = bump
            elif kind == "wink":
                p["blink_r"] = min(1.0, bump * 1.6); p["mouth"] = "smile"
            elif kind == "hop":
                p["hop"] = bump; p["mouth"] = "smile"; p["blush"] = True
                p["eyes"] = "happy"; p["squash"] = -0.5 * bump; p["ears"] = 0.6
            elif kind == "badge":                      # a badge: a big stretchy hop with sparkly eyes
                p["hop"] = bump; p["squash"] = -0.6 * bump; p["eyes"] = "star"; p["mouth"] = "smile"
                p["blush"] = 1.0; p["ears"] = 1.0
            elif kind == "wave":
                p["paw"] = min(1.0, bump * 1.5); p["tail"] = math.sin(f * 4 * math.pi) * 0.3
                p["mouth"] = "smile"; p["eyes"] = "happy"
            elif kind == "tail":
                p["tail"] = math.sin(f * 2 * math.pi)
            elif kind == "ear":
                p["ear"] = bump
            elif kind == "look":
                p["look"] = math.sin(f * 2 * math.pi)
            elif kind == "nod":
                p["hop"] = -0.25 * bump; p["blink_l"] = p["blink_r"] = 0.5 * bump
            elif kind == "wobble":                     # poked: it wobbles on its feet and settles
                p["tilt"] = 0.28 * math.sin(f * 5 * math.pi) * (1 - f); p["squash"] = 0.4 * bump
                p["eyes"] = "wide"; p["mouth"] = "o"; p["ears"] = 1.0
            elif kind == "meow":                       # meowing: the mouth open while the sound plays
                p["mouth"] = "meow" if f < 0.85 else "w"; p["eyes"] = "happy"; p["ears"] = 0.8
            elif kind == "love":                       # being petted: heart eyes and a happy blush
                p["eyes"] = "love"; p["mouth"] = "smile"; p["blush"] = True; p["tail"] = math.sin(f * 6 * math.pi) * 0.6
            elif kind == "surprise":
                p["mouth"] = "o"; p["ear"] = bump
        return p

    def _idle(self):
        """Once a second: maybe blink or do something small, wander now and then, talk now and then,
        and let the bubble fade when its time is up."""
        now = time.monotonic()
        w, h = self.size()
        if self.held:                                 # being carried: nothing else for now
            return True
        if self.asking:                               # a question is up: stay put and quiet
            if now > self.until:
                self.close_question()
            else:
                if not self.asleep() and self.rng.random() < 0.22:
                    self.play("blink", 0.18)
                return True
        if self.spot != "away" and not self.target and (self.x > w - self.SIZE * 0.4 or self.y > h - self.SIZE + 2) \
                and self.spot != "peek":
            self.wander("floor")                      # the window got smaller: back inside
        elif self.spot == "floor" and not self.target and abs(self.y - (h - self.SIZE - 6)) > 2:
            g = self.inset                            # it got bigger
            self.go(min(max(self.x, g + 8), max(g + 8, w - g - self.SIZE - 8)), h - self.SIZE - 6, "floor")
        elif self.spot == "beside" and not self.target and not self.in_border():
            self.wander("beside", "left" if self.x < w / 2 else "right")   # resized, or no border now
        if self.spot == "away":
            if now > self.away_until:
                self.come_back()
                self.say(self.rng.choice(["Back! I saw a bird.", "I'm back. Did I miss anything?",
                                          "Back from my walk. All quiet?"]), 6)
        elif not self.asleep() and not self.target:
            r = self.rng.random()
            if r < 0.22:
                self.play("blink", 0.18)
            elif r < 0.25:
                self.play(self.rng.choice(["tail", "ear", "look", "wink"]), 1.0)
            if self.spot in ("row", "peek") and now > self.back_at:
                self.wander("floor")
            elif now > self.next_move:
                self.next_move = now + self.rng.uniform(25, 60)
                self.wander()
        if now > self.until and self.bubble.get_visible() and not self.bubble.has_css_class("faded"):
            self.bubble.add_css_class("faded")
            GLib.timeout_add(450, lambda: (now > self.until and self.bubble.set_visible(False)) and False)
        if now > self.next_talk and self.spot != "away":
            self.next_line()
        self.area.queue_draw()
        return True

    def start(self):
        if not any(t[0] == "idle" for t in self.timers):
            self.timers.append(("idle", GLib.timeout_add(1000, self._idle)))

    def stop(self):
        for _name, tid in self.timers:
            GLib.source_remove(tid)
        self.timers = []
        self.anims = []
        self.target = None
        self.path = []
        self._cancel_scoot()

    def get_visible(self):
        return self.visible

    def set_visible(self, on):
        self.visible = on
        self.area.set_visible(on)
        self.bubble.set_visible(on and time.monotonic() < self.until)
        if not on:
            self.stop()

    def react(self, move, text, hold=8):
        """Say `text` with a little move. From outside it comes back first, so nothing is missed."""
        if self.spot == "away":
            self.come_back()
        self.say(text, hold)
        self.play(move, {"blink": 0.18, "hop": 0.5, "wave": 1.1, "nod": 0.5, "badge": 1.0}.get(move, 0.8))


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=800, default_height=680)
        self.sessions = load_sessions()
        if favorites_first(self.sessions) != self.sessions:   # starred entries live at the top
            self.sessions = favorites_first(self.sessions)
            save_sessions(self.sessions)
        self.mode = load_mode()
        settings = load_settings()
        self.sounds_on = settings.get("sounds", True) is not False
        self.tips_on = settings.get("tips", True) is not False
        self.cat_on = settings.get("cat", True) is not False and HAVE_CAIRO
        self.watch_on = settings.get("watch", True) is not False
        self.outside_on = settings.get("outside", False) is True   # off unless turned on: the desktop
        # draws its focus outline around the whole window, see-through border included
        self._gutter = 0             # the see-through border Byte can step out into (0 = none)
        self._region_set = False     # we gave the window its own input region
        self._region_rects = None    # ... made of these
        self._watch_prev = None      # the last kitty snapshot (None until kitty answers)
        self._watch_busy = False     # a `kitty @ ls` is running in the worker thread
        self._watch_skip = 0         # ticks to skip after kitty didn't answer (back off)
        self._watch_said = 0.0       # when Byte last commented on the terminal
        self._pending_sound = None   # (name, timer) of the one extra sound an action may add
        self.checked = set()
        self.play_btns = []
        self.groups = []
        self._quiet = False      # True while ticks are put back after a rebuild (no tips then)
        self._drag_from = None   # index of the row being dragged
        self._scroll_tick = 0    # timer that scrolls the list while a drag is near its top or bottom
        self._scroll_speed = 0
        self.toasts = Adw.ToastOverlay()

        # Window actions (menus and buttons use these)
        mode_act = Gio.SimpleAction.new_stateful("mode", GLib.VariantType.new("s"), GLib.Variant("s", self.mode))
        mode_act.connect("change-state", self._mode_changed)
        self.add_action(mode_act)
        for name, cb in (("edit", lambda i: self._edit(i)), ("move-up", lambda i: self._move(i, -1)),
                         ("move-down", lambda i: self._move(i, 1)), ("remove", lambda i: self._remove(i)),
                         ("launch", lambda i: self._launch(i)),
                         ("star", lambda i: self._set_star(self.sessions[i]))):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("i"))
            a.connect("activate", lambda _a, v, cb=cb: cb(v.get_int32()))
            self.add_action(a)
        self.favs_act = Gio.SimpleAction.new("open-favs", None)
        self.favs_act.connect("activate", lambda *_: self._open_many(favorites=True))
        self.add_action(self.favs_act)
        for name, on in (("sounds", self.sounds_on), ("tips", self.tips_on), ("cat", self.cat_on),
                         ("watch", self.watch_on), ("outside", self.outside_on)):
            a = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", on))
            a.connect("change-state", self._switch_changed)
            self.add_action(a)
        badges = Gio.SimpleAction.new("badges", None)
        badges.connect("activate", lambda *_: self._show_badges())
        self.add_action(badges)
        again = Gio.SimpleAction.new("tips-again", None)
        again.connect("activate", lambda *_: (save_setting("tips_seen", []),
                                              self._toast("Tips will show again, each once")))
        self.add_action(again)

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add = Gtk.Button(child=Adw.ButtonContent(icon_name="list-add-symbolic", label="Add"),
                         tooltip_text="Add a terminal to the list: an agent session, a folder, or a command")
        add.connect("clicked", lambda *_: self._edit(None))
        header.pack_start(add)
        quick = Gtk.Button(child=Adw.ButtonContent(icon_name="folder-open-symbolic", label="Folder…"),
                           tooltip_text="Open a terminal in any folder right now, without adding it to the list")
        quick.connect("clicked", self._open_folder)
        header.pack_start(quick)
        self.title = Adw.WindowTitle(title=APP_NAME)
        header.set_title_widget(self.title)

        menu = Gio.Menu()
        section = Gio.Menu()
        for key, label, _help in MODE_ROWS:
            section.append(label, f"win.mode::{key}")
        menu.append_section("Open in", section)
        favs = Gio.Menu()
        favs.append("Open favorites", "win.open-favs")
        menu.append_section(None, favs)
        primary = Gio.Menu()
        primary.append("Sounds", "win.sounds")
        primary.append("Tips", "win.tips")
        primary.append("Show tips again", "win.tips-again")
        cat_menu = Gio.Menu()
        cat_menu.append("Byte the cat", "win.cat")
        cat_menu.append("Byte watches my kitty tabs", "win.watch")
        cat_menu.append("Byte can leave the window", "win.outside")
        cat_menu.append("Badges…", "win.badges")
        primary.append_section(None, cat_menu)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=primary,
                                       tooltip_text="Sounds, tips, the cat and your badges"))
        self.open_btn = Adw.SplitButton(menu_model=menu, css_classes=["suggested-action"],
                                        dropdown_tooltip="Choose where terminals open, or open your favorites")
        self.open_btn.connect("clicked", lambda *_: self._open_many())
        header.pack_end(self.open_btn)
        tv.add_top_bar(header)
        # The app is drawn on a card; around it the window can have a see-through border that Byte steps
        # out into (☰ → Byte can leave the window). Clicks on the empty border go to what's behind.
        self.card = Gtk.Box(css_classes=["tl-card"], overflow=Gtk.Overflow.HIDDEN)
        tv.set_hexpand(True)
        self.card.append(tv)
        self.stage = Gtk.Overlay(css_classes=["tl-stage"])   # Byte walks on this, over the app and under the toasts
        self.stage.set_child(self.card)
        self.cat = Cat(self.stage, self._pet)
        self.cat.list_box = self._list_box
        self.cat.on_moved = self._update_input_region
        self.cat.set_badge_count(len(self._badges()))
        self.cat.set_visible(self.cat_on)
        if self.cat_on:
            self.cat.start()
        self._watch_timer = 0 if DRY_RUN else GLib.timeout_add_seconds(2, self._watch_tick)
        self.connect("close-request", lambda *_: (self.cat.stop(), self._watch_timer and
                                                  GLib.source_remove(self._watch_timer)) and False)
        stats = self._stats()
        if self.mode not in stats.get("modes", []):   # for the Explorer badge
            self._set_stat("modes", stats.get("modes", []) + [self.mode])

        self.page = Adw.PreferencesPage(css_classes=["tl-page"])
        how = Adw.PreferencesGroup()
        self.mode_row = Adw.ComboRow(title="Open in", model=Gtk.StringList.new([m[1] for m in MODE_ROWS]),
                                     tooltip_text="Where ▶ and the button at the top open terminals. Remembered.")
        self.mode_row.set_selected([m[0] for m in MODE_ROWS].index(self.mode))
        self.mode_row.connect("notify::selected", self._mode_row_changed)
        how.add(self.mode_row)
        self.monitor_row = Adw.ComboRow(
            title="Move to monitor", model=Gtk.StringList.new([m[1] for m in MONITOR_ROWS]),
            subtitle="For a new kitty window and this window (COSMIC, Super+Shift+Alt+arrow)",
            tooltip_text="Wayland lets the desktop pick the monitor. If set, the launcher presses COSMIC's "
                         "\"move window to monitor\" shortcut for a window it just opened, only while that "
                         "window has focus. Remembered.")
        self.monitor_row.set_selected([m[0] for m in MONITOR_ROWS].index(load_monitor()))
        self.monitor_row.connect("notify::selected",
                                 lambda r, _p: save_setting("move_to_monitor", MONITOR_ROWS[r.get_selected()][0]))
        how.add(self.monitor_row)
        self.page.add(how)
        self.group = None
        edge = Gtk.DropControllerMotion()   # dragging a row near the top or bottom scrolls the list
        edge.connect("motion", self._edge_motion)
        edge.connect("leave", lambda *_: self._edge_scroll(0))
        self.page.add_controller(edge)

        self.empty = Adw.StatusPage(icon_name="utilities-terminal-symbolic", title="No terminals yet",
                                    description="Add an agent session (Claude Code, omp, opencode), a folder to open a shell in, "
                                                "or a command you run often.")
        empty_btn = Gtk.Button(label="Add a terminal", halign=Gtk.Align.CENTER,
                               css_classes=["pill", "suggested-action"])
        empty_btn.connect("clicked", lambda *_: self._edit(None))
        self.empty.set_child(empty_btn)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        self.stack.add_named(self.page, "list")
        self.stack.add_named(self.empty, "empty")
        tv.set_content(self.stack)
        self.toasts.set_child(self.stage)
        self.set_content(self.toasts)
        self._apply_outside()
        self.set_default_size(800 + 2 * self._gutter, 680)
        for prop in ("maximized", "fullscreened"):        # full screen or maximized: no border then
            self.connect(f"notify::{prop}", lambda *_: self._apply_outside())
        self.connect("notify::is-active", lambda *_: self._update_input_region(force=True))

        keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        for trig, cb in (("<Control>n", lambda *_: self._edit(None) or True),
                         ("<Control>o", lambda *_: self._open_folder(None) or True),
                         ("<Control>Return", lambda *_: self._open_many() or True),
                         ("<Control><Shift>Return", lambda *_: self._open_many(favorites=True) or True)):
            keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trig),
                                           action=Gtk.CallbackAction.new(cb)))
        self.add_controller(keys)

        css = Gtk.CssProvider()
        css.load_from_string(app_css())
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._rebuild()
        if not self.sessions:                       # an empty list: Byte offers to help with the first one
            GLib.timeout_add(1500, lambda: self._offer(
                "get-started", "It looks like you're just getting started. Shall I open Add for you?",
                [("Yes, please", lambda: self._edit(None))]) and False)
        adj = self._vadjustment()
        if adj:                                     # the row Byte sat beside scrolls away: it moves
            adj.connect("value-changed", lambda *_: self.cat.on_scroll())

    # ---------- Byte outside the window ----------
    def _gutter_wanted(self):
        """How wide the see-through border on each side should be: room for Byte when it's shown and
        allowed out, none when maximized or full screen (there's nowhere to go then). (Tiling can't be
        told apart: COSMIC reports a floating window as tiled on all four edges.)"""
        if not (self.outside_on and self.cat_on) or self.is_maximized() or self.is_fullscreen():
            return 0
        return Cat.SIZE + 16

    def _apply_outside(self):
        """Put the border on or take it off. An open window keeps its size (the desktop decides that on
        Wayland), so the app gets narrower or wider by the border; a new window makes room for it."""
        g = self._gutter_wanted()
        if g == self._gutter:
            return
        self._gutter = g
        self.card.set_margin_start(g)
        self.card.set_margin_end(g)
        (self.add_css_class if g else self.remove_css_class)("tl-outside")
        self.cat.inset = g
        if self.cat.spot == "beside" and not g:      # it was out in the border: back in
            self.cat.wander("floor")
        self._update_input_region()

    def do_size_allocate(self, width, height, baseline):
        Adw.ApplicationWindow.do_size_allocate(self, width, height, baseline)
        self._update_input_region(force=True)       # GTK may have just set its own

    def _update_input_region(self, force=False):
        """With the border on, only the app, the cat (and its bubble while it asks something) take
        clicks; the rest of the border lets them through to whatever is behind the window. Sent only
        when it changes, unless force."""
        surface = self.get_surface()
        if surface is None or not HAVE_CAIRO:
            return
        import cairo
        if not self._gutter:
            if self._region_set:                      # back to the whole window
                self._region_set, self._region_rects = False, None
                surface.set_input_region(cairo.Region(cairo.RectangleInt(-1, -1, 1 << 15, 1 << 15)))
                self.queue_allocate()                 # GTK puts its own back (shadow and resize edges)
            return
        ok, card = self.card.compute_bounds(self)
        ok2, stage = self.stage.compute_bounds(self)
        if not (ok and ok2):
            return
        rects = click_rects((card.get_x(), card.get_y(), card.get_width(), card.get_height()),
                            (stage.get_x(), stage.get_y()), self.get_surface_transform(),
                            (self.cat.x, self.cat.y) if self.cat_on else None, Cat.SIZE,
                            self.cat.bubble_box if self.cat_on and self.cat.asking else None)
        if rects == self._region_rects and not force:
            return
        self._region_rects = rects
        region = cairo.Region(cairo.RectangleInt(*rects[0]))
        for r in rects[1:]:
            region.union(cairo.RectangleInt(*r))
        surface.set_input_region(region)
        self._region_set = True

    def _list_box(self):
        """Where the list is on the window, as (x, y, width, height), for Byte's margin spots."""
        boxes = [g.compute_bounds(self.stage) for g in self.groups]
        boxes = [b for ok, b in boxes if ok]
        if not boxes:
            return None
        x0, y0 = min(b.get_x() for b in boxes), min(b.get_y() for b in boxes)
        x1 = max(b.get_x() + b.get_width() for b in boxes)
        y1 = max(b.get_y() + b.get_height() for b in boxes)
        return x0, y0, x1 - x0, y1 - y0

    def _visit(self, entry):
        """Byte goes and sits beside this entry's row (after the list is rebuilt)."""
        if not self.cat_on:
            return

        def go():
            i = self._index(entry)              # looked up now: the list may have changed meanwhile
            if i is not None and i < len(self.rows):
                self.cat.visit(self.rows[i])
            return False
        # wait until GTK has laid the rebuilt list out (an idle callback comes too early after a drag,
        # so the row would be measured where it isn't yet)
        GLib.timeout_add(250, go)

    def _toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(msg), timeout=3))

    def _rebuild(self, flash=None):
        """Build the list again: starred entries in Favorites at the top, the rest under Terminals.
        `flash` is an entry whose row gets the star/glow animation once it's shown."""
        for g in self.groups:
            self.page.remove(g)
        self.groups = []
        self.play_btns = []
        self.star_btns = []
        self.rows = []
        self.checked = set()
        self.ticks = []
        n = len(self.sessions)
        nfav = sum(1 for s in self.sessions if s.get("star"))
        self.favs_act.set_enabled(bool(nfav))
        self.title.set_subtitle(f"{n} terminal{'s' if n != 1 else ''}" if n else "")
        fav_group = Adw.PreferencesGroup(
            title="★ Favorites",
            description="Starred terminals stay up here. Open them all with Open favorites in the ▾ menu "
                        "at the top (Ctrl+Shift+Enter).")
        self.group = Adw.PreferencesGroup(
            title="Terminals",
            description="▶ opens one now. Tick a few, then use the button at the top to open them together. "
                        "☆ adds one to your favorites." if not nfav else None)
        self.select_btn = Gtk.Button(label="Select all", css_classes=["flat"], valign=Gtk.Align.CENTER,
                                     tooltip_text="Tick every terminal (or clear the ticks)")
        self.select_btn.connect("clicked", self._toggle_all)
        (fav_group if nfav else self.group).set_header_suffix(self.select_btn)

        for i, s in enumerate(self.sessions):
            kind = s.get("kind", "claude")
            exists = os.path.isdir(os.path.expanduser(s["folder"]))
            row = Adw.ActionRow(title=GLib.markup_escape_text(s.get("name") or Path(s["folder"]).name),
                                subtitle=GLib.markup_escape_text(
                                    row_subtitle(s) if exists else f"Folder not found: {short_path(s['folder'])}"),
                                subtitle_lines=1, title_lines=1)
            if not exists:
                row.add_css_class("error")
            tick = Gtk.CheckButton(valign=Gtk.Align.CENTER,
                                   tooltip_text="Include in the button at the top (click the row to tick)")
            tick.connect("toggled", lambda b, i=i: self._tick(i, b.get_active()))
            self.ticks.append(tick)
            # chat-symbolic is in the Pop and COSMIC themes; plain Adwaita has chat-message-new-symbolic
            names = [KIND_ICONS.get(kind, "utilities-terminal-symbolic"), "chat-message-new-symbolic",
                     "utilities-terminal-symbolic"]
            icon = Gtk.Image(gicon=Gio.ThemedIcon.new_from_names(names),
                             tooltip_text=dict(KINDS).get(kind), css_classes=["dim-label"])
            row.add_prefix(icon)
            row.add_prefix(tick)
            row.set_activatable_widget(tick)  # clicking the row ticks it; ▶ is the only launch
            if n > 1:
                row.add_prefix(Gtk.Image(icon_name="list-drag-handle-symbolic", css_classes=["dim-label"],
                                         tooltip_text="Drag to move this row up or down the list"))
                self._add_drag(row, i)

            starred = bool(s.get("star"))
            menu = Gio.Menu()
            menu.append("Edit…", f"win.edit({i})")
            menu.append("Remove from favorites" if starred else "Add to favorites", f"win.star({i})")
            moves = Gio.Menu()   # moves stay inside Favorites or Terminals; the star moves between them
            if i > 0 and bool(self.sessions[i - 1].get("star")) == starred:
                moves.append("Move up", f"win.move-up({i})")
            if i < n - 1 and bool(self.sessions[i + 1].get("star")) == starred:
                moves.append("Move down", f"win.move-down({i})")
            if moves.get_n_items():
                menu.append_section(None, moves)
            danger = Gio.Menu()
            danger.append("Remove from list…", f"win.remove({i})")
            menu.append_section(None, danger)
            star = Gtk.ToggleButton(icon_name="starred-symbolic" if starred else "non-starred-symbolic",
                                    active=starred, valign=Gtk.Align.CENTER,
                                    css_classes=["flat", "circular", "star-btn"],
                                    tooltip_text="Remove from favorites" if starred
                                    else "Add to favorites (it moves to the top)")
            # connected after active= is set, so building the row doesn't star it; the rebuild waits for
            # idle so this button isn't destroyed inside its own signal
            star.connect("toggled", lambda _b, s=s: GLib.idle_add(lambda: self._set_star(s) and False))
            self.star_btns.append(star)
            row.add_suffix(star)
            more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                                  css_classes=["flat"],
                                  tooltip_text="Edit, star, move or remove (or drag the row to move it)")
            row.add_suffix(more)

            play = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                              css_classes=["suggested-action", "circular"], sensitive=exists)
            play.connect("clicked", lambda _b, i=i: self._launch(i))
            self.play_btns.append(play)
            row.add_suffix(play)
            (fav_group if starred else self.group).add(row)
            self.rows.append(row)
            if s is flash:
                GLib.idle_add(lambda r=row, b=star: (self._animate(r, "glow", 1300),
                                                     self._animate(b, "star-pop", 420)) and False)

        if nfav:
            self.groups.append(fav_group)
        if nfav < n:
            self.groups.append(self.group)
        for g in self.groups:
            self.page.add(g)
        self.stack.set_visible_child_name("list" if n else "empty")
        self.open_btn.set_sensitive(bool(n))
        self._update_open_label()
        if len({s.get("kind", "claude") for s in self.sessions}) >= 3:
            GLib.idle_add(lambda: self._award("polyglot") and False)

    def _animate(self, widget, css_class, ms):
        """Play one of the small CSS animations on a widget (skipped when the desktop turns animations off)."""
        if not Gtk.Settings.get_default().get_property("gtk-enable-animations"):
            return
        widget.remove_css_class(css_class)
        widget.add_css_class(css_class)
        GLib.timeout_add(ms + 50, lambda: widget.remove_css_class(css_class) and False)

    def _sound(self, name):
        if self.sounds_on and Gtk.Settings.get_default().get_property("gtk-enable-event-sounds"):
            play_sound(name)

    def _tip(self, key, text, button=None, on_click=None):
        """Show a tip once (ever), at the moment it's useful, with a soft chime. Returns True if shown."""
        if not self.tips_on:
            return False
        seen = load_settings().get("tips_seen", [])
        if not isinstance(seen, list):
            seen = []
        if key in seen:
            return False
        save_setting("tips_seen", seen + [key])
        if self.cat_on and not button:      # the cat says it (one voice); a tip with a button stays a toast
            self.cat.react("wink", f"Psst: {text[0].upper()}{text[1:]}", hold=14)
        else:
            t = Adw.Toast(title=GLib.markup_escape_text(f"Tip: {text}"), timeout=8)
            if button:
                t.set_button_label(button)
                t.connect("button-clicked", lambda *_: on_click())
            self.toasts.add_toast(t)
        self._later_sound("tip")
        return True

    def _later_sound(self, name):
        """The one extra sound an action may add (a tip chime or a badge sparkle), played 450 ms after
        the action's own sound so they don't clash. A badge's sparkle wins over a tip's chime."""
        if self._pending_sound:
            pending, timer = self._pending_sound
            if pending == "badge":
                return
            GLib.source_remove(timer)

        def fire():
            self._pending_sound = None
            self._sound(name)
            return False
        self._pending_sound = (name, GLib.timeout_add(450, fire))

    def _say(self, move, text):
        """A reaction: the cat says it (and moves) when it's shown, otherwise a toast."""
        if self.cat_on:
            self.cat.react(move, text)
        else:
            self._toast(text)

    # ---------- badges ----------
    def _badges(self):
        got = load_settings().get("badges")
        return got if isinstance(got, dict) else {}

    def _stats(self):
        st = load_settings().get("stats")
        return st if isinstance(st, dict) else {}

    def _set_stat(self, key, value):
        st = self._stats()
        st[key] = value
        save_setting("stats", st)

    def _bump(self, key, by=1):
        n = int(self._stats().get(key, 0)) + by
        self._set_stat(key, n)
        return n

    def _award(self, bid):
        """Give a badge once. The cat cheers (or a toast says it) and a sparkle plays. True if new."""
        got = self._badges()
        if bid in got or bid not in {b[0] for b in BADGES}:
            return False
        got[bid] = self.cat.now().strftime("%Y-%m-%d")
        save_setting("badges", got)
        emoji, name = next((b[1], b[2]) for b in BADGES if b[0] == bid)
        self.cat.set_badge_count(len(got))
        self._say("badge", f"{emoji} New badge: {name}! That's {len(got)} of {len(BADGES)}.")
        self._later_sound("badge")
        return True

    def _show_badges(self):
        got = self._badges()
        win = Adw.Window(transient_for=self, modal=True, title="Badges", default_width=460, default_height=600)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=f"{len(got)} of {len(BADGES)} found",
                                     description="Little quests: do things in the launcher and Byte hands "
                                                 "these out. No hurry, and nothing to look after.")
        for bid, emoji, name, how in BADGES:
            earned = bid in got
            if earned:
                when = datetime.strptime(got[bid], "%Y-%m-%d")
                sub = f"{how or 'You said hi to the cat a lot.'} Earned {when.strftime('%B')} {when.day}."
            else:
                sub = how or "A secret. Maybe say hi to the cat?"
            row = Adw.ActionRow(title=name if earned or how else "???", subtitle=sub)
            icon = Gtk.Label(label=emoji if earned else "🔒", css_classes=["title-1"] + ([] if earned else ["dim-label"]))
            row.add_prefix(icon)
            group.add(row)
        page.add(group)
        tv.set_content(page)
        win.set_content(tv)
        win.present()

    # ---------- Byte watches the launcher's kitty window ----------
    def _watch_tick(self):
        """Every 2 s: ask the launcher's kitty window what its tabs are doing (in a worker thread)."""
        if not (self.cat_on and self.watch_on) or self._watch_busy:
            return True
        if self._watch_skip:
            self._watch_skip -= 1
            return True
        self._watch_busy = True
        names = {os.path.realpath(os.path.expanduser(e["folder"])): e.get("name") or Path(e["folder"]).name
                 for e in self.sessions}

        def ask():
            snap = None
            try:
                r = subprocess.run([KITTY, "@", "--to", KITTY_SOCK, "ls"], capture_output=True, timeout=2)
                if r.returncode == 0:
                    snap = parse_kitty_ls(json.loads(r.stdout or b"[]"), names)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            GLib.idle_add(self._watch_apply, snap)
        threading.Thread(target=ask, daemon=True).start()
        return True

    def _watch_apply(self, snap):
        """Back on the main thread: compare with the last snapshot and let Byte react."""
        self._watch_busy = False
        if snap is None:                      # no launcher kitty window: ask less often
            self._watch_prev, self._watch_skip = None, 4
            return False
        if self._watch_prev is not None and self.cat_on and self.watch_on:
            now = time.monotonic()
            for _kind, sound, text, important in watch_events(self._watch_prev, snap, now):
                if not important and now - self._watch_said < 8:
                    continue                  # don't chatter: small things wait their turn
                self._watch_said = now
                ok = sound != "oops"
                self.cat.react("hop" if ok and sound else "surprise" if not ok else "look", text, hold=15)
                if sound:
                    self._sound(sound)
                break                         # one comment per look
        else:
            watch_events({}, snap, time.monotonic())   # just note when each window was first seen
        self._watch_prev = snap
        return False

    def _pet(self):
        """Poking the cat: it wobbles and meows, then asks what it can do (a tip, a joke, your badges).
        A few pokes in a row and it purrs instead. Ten pokes in all earn a secret badge."""
        now = time.monotonic()
        self._pokes = [t for t in getattr(self, "_pokes", []) if now - t < 6] + [now]
        self.cat.play("wobble", 0.7)
        if self._bump("pets") >= 10 and self._award("curious"):
            return
        if len(self._pokes) >= 3:                      # pet, pet, pet: purr
            self._pokes = []
            self.cat.close_question()
            self._sound("purr")
            self.cat.play("love", 2.0)
            self.cat.say(self.cat.rng.choice(["Purrrr…", "Mmm, purrrr.", "Prrrrr. That's the spot."]), 4)
            return
        self._sound("meow")
        GLib.timeout_add(250, lambda: self.cat.play("meow", 0.8) and False)   # after the wobble, in time with the mew
        name = self.cat.rng.choice(["Hi!", "Mrrp?", "Oh, hello!", "Meow!"])

        def tip():
            self._sound("babble")
            self.cat.say(cat_line("tip", self.cat.now(), int(now)), 14, typed=True)

        def joke():
            self._sound("babble")
            self.cat.say(cat_line("joke", self.cat.now(), int(now)) if self.cat.rng.random() < 0.5
                         else self.cat.rng.choice(CAT_JOKES), 12, typed=True)
        self.cat.ask(f"{name} What can I do for you?", [("A tip", tip), ("A joke", joke),
                                                         ("My badges", self._show_badges)])

    def _offer(self, key, text, choices):
        """A Clippy-style offer: the cat asks, with buttons, at the moment it's useful. Off with Tips.
        "Not now" holds it back for the rest of this session; "Don't ask again" for good. Without the
        cat it's a toast with the first choice as its button. Returns True if it was shown."""
        if not self.tips_on or key in getattr(self, "_declined", set()):
            return False
        seen = load_settings().get("tips_seen", [])
        if key in (seen if isinstance(seen, list) else []):
            return False
        if not self.cat_on:
            label, cb = choices[0]
            save_setting("tips_seen", list(seen) + [key])
            t = Adw.Toast(title=GLib.markup_escape_text(text), timeout=8, button_label=label)
            t.connect("button-clicked", lambda *_: cb())
            self.toasts.add_toast(t)
            self._later_sound("tip")
            return True
        self._declined = getattr(self, "_declined", set())
        self.cat.play("look", 0.8)
        self.cat.ask(text, choices + [("Not now", lambda: self._declined.add(key)),
                                      ("Don't ask again", lambda: save_setting(
                                          "tips_seen", list(load_settings().get("tips_seen", [])) + [key]))])
        self._later_sound("tip")
        return True

    def _switch_changed(self, act, value):
        act.set_state(value)
        on = value.get_boolean()
        name = act.get_name()
        if name == "cat" and on and not HAVE_CAIRO:
            act.set_state(GLib.Variant("b", False))
            self._toast("The cat needs python3-gi-cairo (sudo apt install python3-gi-cairo)")
            return
        save_setting(name, on)
        if name == "sounds":
            self.sounds_on = on
            self._sound("open")   # a sample, so you hear what you turned on
        elif name == "tips":
            self.tips_on = on
        elif name == "watch":
            self.watch_on = on
            self._watch_prev = None
            if on and self.cat_on:
                self.cat.react("look", "I'll keep an eye on the kitty tabs I open for you.")
        elif name == "outside":
            self.outside_on = on
            self._apply_outside()
            if on and self.cat_on and self._gutter:   # once the window has grown: show it off
                GLib.timeout_add(400, lambda: self.cat.wander("beside") and False)
        else:
            self.cat_on = on
            self.cat.set_visible(on)
            (self.cat.start if on else self.cat.stop)()
            self._apply_outside()

    def _index(self, entry):
        return next((k for k, s in enumerate(self.sessions) if s is entry), None)

    def _set_star(self, entry, on=None):
        """Star or unstar an entry. Starred ones move to the end of Favorites; unstarred ones to the
        top of Terminals."""
        i = self._index(entry)
        if i is None:
            return
        on = not entry.get("star") if on is None else on
        if bool(entry.get("star")) == on:
            return
        ticked = self._ticked()
        self.sessions.pop(i)
        if on:
            entry["star"] = True
        else:
            entry.pop("star", None)
        self.sessions.insert(sum(1 for s in self.sessions if s.get("star")), entry)
        save_sessions(self.sessions)
        self._rebuild_keeping(ticked, flash=entry if on else None)
        self._sound("star" if on else "unstar")
        if on:
            self._visit(entry)
        name = entry.get("name") or short_path(entry["folder"])
        if not on:
            self._say("ear", f"Removed {name} from favorites. Back with the others.")
            return
        awarded = self._award("first-star")
        if sum(1 for s in self.sessions if s.get("star")) >= 5:
            awarded = self._award("collector") or awarded
        if not awarded and not self._tip("star", "favorites stay at the top. Ctrl+Shift+Enter opens them all."):
            self._say("hop", f"★ Ooh, {name} is a favorite now!")

    def _ticked(self):
        """The ticked entries (not positions), taken before the list changes order."""
        return [self.sessions[k] for k in self.checked if k < len(self.sessions)]

    def _rebuild_keeping(self, ticked, flash=None):
        """Rebuild, ticking `ticked` (entries) again and keeping the scroll position."""
        adj = self._vadjustment()
        top = adj.get_value() if adj else None
        self._rebuild(flash=flash)
        self._quiet = True
        for k, s in enumerate(self.sessions):
            if any(s is t for t in ticked):
                self.ticks[k].set_active(True)
        self._quiet = False
        if adj:
            GLib.idle_add(lambda: adj.set_value(top) and False)

    def _add_drag(self, row, i):
        """Let a row be dragged onto another row to move it there."""
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)

        def prepare(_src, x, y):
            self._drag_from = i
            self._drag_hot = (x, y)
            return Gdk.ContentProvider.new_for_value(GObject.Value(GObject.TYPE_INT, i))

        def begin(_src, drag):
            self.cat.passthrough(True)
            snap = Gtk.WidgetPaintable(widget=row).get_current_image()
            row.add_css_class("dragging")
            icon = Gtk.DragIcon.get_for_drag(drag)
            pic = Gtk.Picture(paintable=snap, can_shrink=False, css_classes=["card"])
            pic.set_size_request(row.get_width(), row.get_height())
            icon.set_child(pic)
            drag.set_hotspot(int(self._drag_hot[0]), int(self._drag_hot[1]))

        def end(_src, _drag, _deleted):
            self.cat.passthrough(False)
            row.remove_css_class("dragging")
            self._drag_from = None
            self._edge_scroll(0)

        src.connect("prepare", prepare)
        src.connect("drag-begin", begin)
        src.connect("drag-end", end)
        row.add_controller(src)

        dst = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)

        def motion(_dst, _x, _y):
            frm = self._drag_from
            row.remove_css_class("drop-above")
            row.remove_css_class("drop-below")
            if frm is None or frm == i:
                return 0
            row.add_css_class("drop-below" if frm < i else "drop-above")
            return Gdk.DragAction.MOVE

        def leave(_dst):
            row.remove_css_class("drop-above")
            row.remove_css_class("drop-below")

        def drop(_dst, value, _x, _y):
            leave(None)
            frm = self._drag_from
            if frm is None or value != frm or frm == i:
                return False
            GLib.idle_add(self._move_to, frm, i)   # rebuild after the drag has finished
            return True

        dst.connect("enter", motion)
        dst.connect("motion", motion)
        dst.connect("leave", leave)
        dst.connect("drop", drop)
        row.add_controller(dst)

    def _move_to(self, i, j):
        """Move entry i to position j (dragged rows land where they were dropped). Dropped on a
        favorite, it becomes one; dropped among the rest, it stops being one. Ticks and the scroll
        position stay as they were."""
        if not (0 <= i < len(self.sessions) and 0 <= j < len(self.sessions) and i != j):
            return False
        moved, target = self.sessions[i], self.sessions[j]
        ticked = self._ticked()
        crossed = bool(moved.get("star")) != bool(target.get("star"))
        if target.get("star"):
            moved["star"] = True
        else:
            moved.pop("star", None)
        self.sessions.insert(j, self.sessions.pop(i))
        self.sessions = favorites_first(self.sessions)
        save_sessions(self.sessions)
        self._rebuild_keeping(ticked, flash=moved if crossed and moved.get("star") else None)
        self._visit(moved)
        awarded = self._award("tidy-paws")
        if not crossed:
            self._sound("drop")
            if not awarded and self.cat_on:
                self.cat.react("nod", self.cat.rng.choice(["Tidy!", "Nice spot for it.", "Neat."]))
            return False
        self._sound("star" if moved.get("star") else "unstar")
        if moved.get("star"):
            awarded = self._award("first-star") or awarded
            if sum(1 for s in self.sessions if s.get("star")) >= 5:
                awarded = self._award("collector") or awarded
        name = moved.get("name") or short_path(moved["folder"])
        if not awarded and not self._tip("drag-star", "drag a row into Favorites to star it, or out to unstar it."):
            self._say("hop" if moved.get("star") else "ear", f"★ {name} is a favorite now!" if moved.get("star")
                      else f"Removed {name} from favorites.")
        return False

    def _vadjustment(self):
        """The vertical scroll position of the list (Adw.PreferencesPage keeps its ScrolledWindow inside)."""
        w = self.page.get_first_child()
        while w and not isinstance(w, Gtk.ScrolledWindow):
            w = w.get_first_child()
        return w.get_vadjustment() if w else None

    def _edge_motion(self, ctl, _x, y):
        if self._drag_from is None:
            return
        h, zone = self.page.get_height(), 48
        self._edge_scroll(-(zone - y) if y < zone else (y - (h - zone)) if y > h - zone else 0)

    def _edge_scroll(self, speed):
        """Scroll by `speed` (pixels per step, sign = direction) until told 0."""
        self._scroll_speed = speed / 3
        if speed and not self._scroll_tick:
            def step():
                adj = self._vadjustment()
                if not self._scroll_speed or not adj:
                    self._scroll_tick = 0
                    return False
                adj.set_value(adj.get_value() + self._scroll_speed)
                return True
            self._scroll_tick = GLib.timeout_add(16, step)

    def _tick(self, i, on):
        (self.checked.add if on else self.checked.discard)(i)
        self._update_open_label()
        if on and len(self.checked) == 2 and not self._quiet:
            self._offer("tick", "It looks like you're picking a few. Open them together? (Ctrl+Enter)",
                        [("Open them", self._open_many)])

    def _toggle_all(self, _btn):
        on = len(self.checked) < len(self.sessions)
        for t in self.ticks:
            t.set_active(on)

    def _mode_row_changed(self, row, _pspec):
        key = MODE_ROWS[row.get_selected()][0]
        if key != self.mode:
            self.lookup_action("mode").change_state(GLib.Variant("s", key))

    def _mode_changed(self, act, value):
        act.set_state(value)
        self.mode = value.get_string()
        save_mode(self.mode)
        modes = self._stats().get("modes", [])
        if self.mode not in modes:
            self._set_stat("modes", modes + [self.mode])
            if len(set(modes + [self.mode])) >= len(MODES):
                self._award("explorer")
        idx = [m[0] for m in MODE_ROWS].index(self.mode)
        if self.mode_row.get_selected() != idx:
            self.mode_row.set_selected(idx)
        self._update_open_label()

    def _update_open_label(self):
        n = len(self.checked)
        _l, some, every = MODES[self.mode]
        self.open_btn.set_label(some.format(n=n) if n else every)
        help_ = dict((m[0], m[2]) for m in MODE_ROWS)[self.mode]
        self.mode_row.set_subtitle(help_)
        target = f"the {n} ticked" if n else "every terminal in the list"
        self.open_btn.set_tooltip_text(f"Open {target}  (Ctrl+Enter)\n{help_}")
        if getattr(self, "select_btn", None):
            self.select_btn.set_label("Clear" if n and n == len(self.sessions) else "Select all")
        how = "as a new tab in your kitty window" if self.mode == "kitty" else "in its own new window"
        for b in self.play_btns:
            b.set_tooltip_text(f"Open this one now, {how}")

    def _launch(self, i):
        s = self.sessions[i]
        if i < len(self.play_btns):
            self._animate(self.play_btns[i], "press", 260)
        errs = kitty_attach([s]) if self.mode == "kitty" else [e for e in [launch(s)] if e]
        name = s.get("name") or short_path(s["folder"])
        if errs:
            self._toast(errs[0])
            return
        awarded = self._count_opens([s])
        self._sound("open")
        self._visit(s)
        if s.get("opens", 0) >= 3 and not s.get("star") and not awarded and self._offer(
                "suggest-star", f"You open {name} a lot. Make it a favorite, so it stays at the top?",
                [("Star it", lambda: self._set_star(s, True))]):
            return
        if not awarded:
            self._say("wave", self.cat.rng.choice([f"Opened {name}. Off you go!", f"Opened {name}. Have fun!",
                                                   f"Opened {name}. Good luck in there!"]))

    def _count_opens(self, entries):
        """Count how often each entry is opened from the window (it's how the launcher knows which ones
        you use most), and give the badges that go with opening. Not counted with --dry-run. Saved
        without rebuilding the list. Returns True if a badge was given."""
        if DRY_RUN:
            return False
        for s in entries:
            s["opens"] = int(s.get("opens", 0)) + 1
        save_sessions(self.sessions)
        total = self._bump("opened", len(entries))
        hour = self.cat.now().hour
        awarded = False
        for bid, ok in (("regular", total >= 25), ("marathon", total >= 100),
                        ("early-bird", hour < 7), ("night-owl", hour >= 23)):
            if ok:
                awarded = self._award(bid) or awarded
        return awarded

    def _open_folder(self, _btn):
        dlg = Gtk.FileDialog(title="Open a terminal in…")
        dlg.set_initial_folder(Gio.File.new_for_path(str(Path.home() / "Documents")))

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if not f:
                return
            entry = {"name": Path(f.get_path()).name, "folder": f.get_path(), "kind": "terminal"}
            errs = kitty_attach([entry]) if self.mode == "kitty" else [e for e in [launch(entry)] if e]
            if errs:
                self._toast(errs[0])
                return
            t = Adw.Toast(title=GLib.markup_escape_text(f"Opened {short_path(entry['folder'])}"),
                          button_label="Keep in list", timeout=6)
            t.connect("button-clicked", lambda *_: self._save_quick(entry))
            self.toasts.add_toast(t)
        dlg.select_folder(self, None, done)

    def _save_quick(self, entry):
        self.sessions.append(entry)
        save_sessions(self.sessions)
        self._rebuild()
        if not self._award("wayfinder"):
            self._toast(f"Added {entry['name']} to the list")

    def _open_many(self, favorites=False):
        """Open the ticked entries (nothing ticked: all of them), or with favorites=True the starred ones."""
        if favorites:
            picked = [s for s in self.sessions if s.get("star")]
            if not picked:
                self._toast("No favorites yet: ☆ on a row adds one")
                return
        else:
            picked = [self.sessions[i] for i in sorted(self.checked)] or list(self.sessions)
        if not picked:
            return
        if self.mode == "kitty":
            errs, what = kitty_attach(picked), f"Added {len(picked)} tabs to kitty"
        elif self.mode == "tabs":
            errs, what = launch_tabs(picked), f"Opened {len(picked)} tabs in one window"
        else:
            errs, what = [e for e in (launch(s) for s in picked) if e], f"Opened {len(picked)} windows"
        if errs:
            self._toast(errs[0])
            return
        awarded = self._count_opens(picked)
        self._sound("open")
        if len(picked) >= 3:
            awarded = self._award("squad") or awarded
        if not awarded:
            self._say("wave", ("★ " if favorites else "") + what + (". A whole squad!" if len(picked) >= 3 else "."))

    def _edit(self, i):
        def on_save(entry):
            if i is None:
                self.sessions.append(entry)
            ticked = self._ticked()
            if i is not None:
                old = self.sessions[i]
                for key in ("star", "opens"):   # the dialog doesn't show these; keep them
                    if key in old:
                        entry[key] = old[key]
                self.sessions[i] = entry
                ticked = [entry if t is old else t for t in ticked]
            save_sessions(self.sessions)
            self._rebuild_keeping(ticked)
            self._say("nod", f"Saved {entry['name']}.")   # the cat says it (a toast would cover its line)
        EditDialog(self, None if i is None else self.sessions[i], on_save).present()

    def _move(self, i, d):
        j = i + d
        if 0 <= j < len(self.sessions) and bool(self.sessions[i].get("star")) == bool(self.sessions[j].get("star")):
            ticked = self._ticked()
            self.sessions[i], self.sessions[j] = self.sessions[j], self.sessions[i]
            save_sessions(self.sessions)
            self._rebuild_keeping(ticked)

    def _remove(self, i):
        s = self.sessions[i]
        name = s.get("name") or short_path(s["folder"])
        body = "It's only taken off this list."
        if s.get("kind", "claude") in AGENTS:
            body += " The session and its history stay where they are."
        dlg = Adw.MessageDialog(transient_for=self, heading=f"Remove “{name}”?", body=body)
        dlg.add_response("cancel", "Keep")
        dlg.add_response("remove", "Remove")
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")

        def resp(_d, r):
            if r == "remove":
                del self.sessions[i]
                save_sessions(self.sessions)
                self._rebuild()
                self._toast(f"Removed {name}")
        dlg.connect("response", resp)
        dlg.present()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        win = self.get_active_window()
        if win is None:
            win = Window(self)
            if load_monitor():   # move the launcher's own window once, if it gets focus within 5 s
                # GTK keeps this flag current on the main thread; the move's worker thread only reads
                # it (it never calls GTK), and checks it again just before pressing the shortcut
                state = {"active": False, "started": False, "until": time.monotonic() + 5.0}

                def focus_changed(w, _pspec):
                    state["active"] = w.is_active()
                    if state["active"] and not state["started"] and time.monotonic() < state["until"]:
                        state["started"] = True
                        threading.Thread(target=move_when_focused, daemon=True,
                                         args=(lambda _t=None: state["active"], "launcher window",
                                               max(0.1, state["until"] - time.monotonic()))).start()
                win.connect("notify::is-active", focus_changed)
        win.present()


def autostart_main(argv):
    """--autostart: open the entries marked to start at login, the way the launcher is set to open."""
    picked = [s for s in load_sessions() if s.get("autostart")]
    if not picked:
        return []
    if not DRY_RUN:
        time.sleep(5)   # give the desktop a moment to finish starting
    mode = load_mode()
    if mode == "kitty":
        return kitty_attach(picked)
    if mode == "tabs":
        return launch_tabs(picked)
    return [e for e in (launch(s) for s in picked) if e]


def log(msg):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    args = sys.argv[1:]
    DRY_RUN = "--dry-run" in args
    if "--sounds" in args:   # hear the launcher's sounds, one after another
        for name in list(SOUNDS) + list(SOUND_MAKERS):
            print(f"{name}: {sound_file(name)}")
            play_sound(name)
            time.sleep(0.8)
        sys.exit(0)
    if "--autostart" in args:
        errs = autostart_main(args)
        if not DRY_RUN:
            log("login start: " + ("; ".join(errs) if errs else "ok"))
        for e in errs:
            print(e, file=sys.stderr)
        sys.exit(1 if errs else 0)
    if "--all" in args or "--all-windows" in args:  # quick launch, no launcher window
        if "--all-windows" in args:
            errs = [e for e in (launch(s) for s in load_sessions()) if e]
        else:
            errs = launch_tabs(load_sessions())
        for e in errs:
            print(e, file=sys.stderr)
        sys.exit(1 if errs else 0)
    sys.exit(App().run([sys.argv[0]]))
