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
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

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


def press_move_shortcut(direction):
    """Press Super+Shift+Alt+<direction> on a temporary virtual keyboard. Returns an error or None."""
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


def move_when_focused(is_focused, what):
    """Wait up to 5 s for the window we opened to have focus, then move it. Logs the outcome."""
    direction = load_monitor()
    if not direction:
        return
    if DRY_RUN:
        press_move_shortcut(direction)
        return
    for _ in range(25):
        if is_focused():
            err = press_move_shortcut(direction)
            log(f"move {what} to monitor {direction}: " + (err or "ok"))
            return
        time.sleep(0.2)
    log(f"move {what} to monitor {direction}: skipped, it never had focus")


def _kitty_focused():
    try:
        r = subprocess.run([KITTY, "@", "--to", KITTY_SOCK, "ls"], capture_output=True, timeout=3)
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
SOUND_VERSION = 1   # bump when the notes change, so the cached files are made again
SOUND_PEAK = 0.18   # of full scale (about -15 dBFS): a soft tick, never an alert
SOUNDS = {  # name: notes as (start s, frequency Hz, length s, loudness 0..1)
    "star": [(0.0, 784, 0.16, 1.0), (0.07, 1175, 0.24, 0.8)],                       # G5 → D6, up
    "unstar": [(0.0, 659, 0.12, 0.8), (0.06, 494, 0.18, 0.7)],                      # E5 → B4, down
    "open": [(0.0, 523, 0.08, 0.7), (0.035, 784, 0.12, 0.6)],                       # a short pluck
    "drop": [(0.0, 330, 0.12, 0.9)],                                                 # a soft low tock
    "tip": [(0.0, 880, 0.18, 0.5), (0.09, 1109, 0.18, 0.45), (0.18, 1319, 0.3, 0.4)],  # A5 C♯6 E6
}


def synth(notes, rate=44100):
    """Soft marimba-like notes (a sine plus a quiet octave, 4 ms attack, quick decay), scaled so the
    loudest sample is SOUND_PEAK."""
    length = max(start + dur for start, _f, dur, _a in notes) + 0.02
    buf = [0.0] * int(length * rate)
    for start, freq, dur, amp in notes:
        s0 = int(start * rate)
        for k in range(int(dur * rate)):
            t = k / rate
            env = min(1.0, t / 0.004) * math.exp(-7.0 * t / dur)
            buf[s0 + k] += amp * env * (math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(4 * math.pi * freq * t))
    peak = max(abs(x) for x in buf) or 1.0
    return [x / peak * SOUND_PEAK for x in buf]


def sound_file(name):
    """The WAV file for one of SOUNDS, made on first use."""
    path = SOUND_DIR / f"{name}-v{SOUND_VERSION}.wav"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        samples = array.array("h", (int(x * 32767) for x in synth(SOUNDS[name])))
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
    if DRY_RUN or name not in SOUNDS:
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


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=720, default_height=640)
        self.sessions = load_sessions()
        if favorites_first(self.sessions) != self.sessions:   # starred entries live at the top
            self.sessions = favorites_first(self.sessions)
            save_sessions(self.sessions)
        self.mode = load_mode()
        settings = load_settings()
        self.sounds_on = settings.get("sounds", True) is not False
        self.tips_on = settings.get("tips", True) is not False
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
        for name, on in (("sounds", self.sounds_on), ("tips", self.tips_on)):
            a = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", on))
            a.connect("change-state", self._switch_changed)
            self.add_action(a)
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
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=primary,
                                       tooltip_text="Sounds and tips"))
        self.open_btn = Adw.SplitButton(menu_model=menu, css_classes=["suggested-action"],
                                        dropdown_tooltip="Choose where terminals open, or open your favorites")
        self.open_btn.connect("clicked", lambda *_: self._open_many())
        header.pack_end(self.open_btn)
        tv.add_top_bar(header)

        self.page = Adw.PreferencesPage()
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
        self.toasts.set_child(tv)
        self.set_content(self.toasts)

        keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        for trig, cb in (("<Control>n", lambda *_: self._edit(None) or True),
                         ("<Control>o", lambda *_: self._open_folder(None) or True),
                         ("<Control>Return", lambda *_: self._open_many() or True),
                         ("<Control><Shift>Return", lambda *_: self._open_many(favorites=True) or True)):
            keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trig),
                                           action=Gtk.CallbackAction.new(cb)))
        self.add_controller(keys)

        css = Gtk.CssProvider()
        css.load_from_string(APP_CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._rebuild()

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
        t = Adw.Toast(title=GLib.markup_escape_text(f"Tip: {text}"), timeout=8)
        if button:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: on_click())
        self.toasts.add_toast(t)
        # a tip usually comes with another sound (a star, an open): chime after it, not over it
        GLib.timeout_add(450, lambda: self._sound("tip") and False)
        return True

    def _switch_changed(self, act, value):
        act.set_state(value)
        on = value.get_boolean()
        save_setting(act.get_name(), on)
        if act.get_name() == "sounds":
            self.sounds_on = on
            self._sound("open")   # a sample, so you hear what you turned on
        else:
            self.tips_on = on

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
        name = entry.get("name") or short_path(entry["folder"])
        if not on:
            self._toast(f"Removed {name} from favorites")
        elif not self._tip("star", "favorites stay at the top. Ctrl+Shift+Enter opens them all."):
            self._toast(f"★ {name} is a favorite")

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
            snap = Gtk.WidgetPaintable(widget=row).get_current_image()
            row.add_css_class("dragging")
            icon = Gtk.DragIcon.get_for_drag(drag)
            pic = Gtk.Picture(paintable=snap, can_shrink=False, css_classes=["card"])
            pic.set_size_request(row.get_width(), row.get_height())
            icon.set_child(pic)
            drag.set_hotspot(int(self._drag_hot[0]), int(self._drag_hot[1]))

        def end(_src, _drag, _deleted):
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
        if not crossed:
            self._sound("drop")
            return False
        self._sound("star" if moved.get("star") else "unstar")
        name = moved.get("name") or short_path(moved["folder"])
        if not self._tip("drag-star", "drag a row into Favorites to star it, or out to unstar it."):
            self._toast(f"★ {name} is a favorite" if moved.get("star") else f"Removed {name} from favorites")
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
            self._tip("tick", "ticked rows open together: the top button, or Ctrl+Enter.")

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
        self._count_opens([s])
        self._sound("open")
        if s.get("opens", 0) >= 3 and not s.get("star") and self._tip(
                "suggest-star", f"you open {name} a lot. Star it to keep it at the top?",
                button="Star it", on_click=lambda: self._set_star(s, True)):
            return
        self._toast(f"Opened {name}")

    def _count_opens(self, entries):
        """Count how often each entry is opened from the window (it's how the launcher knows which ones
        you use most). Not counted with --dry-run. Saved without rebuilding the list."""
        if DRY_RUN:
            return
        for s in entries:
            s["opens"] = int(s.get("opens", 0)) + 1
        save_sessions(self.sessions)

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
        self._count_opens(picked)
        self._sound("open")
        self._toast(("★ " if favorites else "") + what)

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
            self._toast(f"Saved {entry['name']}")
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
            if load_monitor():   # move the launcher's own window once, after it first gets focus
                def first_focus(w, _pspec):
                    if w.is_active():
                        w.disconnect(handler)
                        # it has focus now, so the shortcut can only reach this window
                        threading.Thread(target=move_when_focused, args=(lambda: True, "launcher window"),
                                         daemon=True).start()
                handler = win.connect("notify::is-active", first_focus)
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
        for name in SOUNDS:
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
