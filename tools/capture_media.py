#!/usr/bin/env python3
"""capture_media.py: record Terminal Launcher's README screenshots and GIFs, and the raw take for
the video, on a HIDDEN Xvfb display with a made-up list in a throwaway home folder.

    python3 tools/capture_media.py WORK_DIR [probe] [stills] [drag] [tick] [add] [favorites] [cat] [tour]
                                                     (default: everything but probe)

  probe      WORK_DIR/probe-*.png: the window, the Add dialog and two menus with a cross on every
             target the scenes click, and WORK_DIR/layout-*.json. Run it first after the window's
             layout changes; it writes nothing in the repo.
  stills     docs/images/window.png (two favorites, Byte in its bar), window-dark.png, edit.png,
             badges.png (the Badges window), and WORK_DIR/window-2x.png for tools/social_preview.py
  drag       docs/images/gifs/drag.gif     (also a real test of dragging: exits 1 if the saved order
                                            isn't the one the drag asked for)
  tick       docs/images/gifs/tick-and-open.gif
  add        docs/images/gifs/add.gif
  favorites  docs/images/gifs/favorites.gif  (☆ on a row: it pops, glows and moves up; then another
                                              row is dragged into Favorites; checks both got starred)
  cat        docs/images/gifs/cat.gif    (the bottom of the window: Byte's own idle blink or swish,
                                          a pet, then a star that earns the First star badge)
  tour       WORK_DIR/tour.mkv + tour.json at 2x scale, for tools/compose_tour.py: captions, clicks,
             drags and the sounds the app asked for, each with its time in the take

Each scene seeds settings.json: every tip counts as seen and the badges it isn't about count as
earned (Polyglot is announced at startup otherwise), so the cat says what the scene is about.
Byte's idle moves are random and it naps from 23:00 to 06:00 by the real clock: capture by day.

No coordinates to measure: the app runs under tools/app_probe.py, which reports every widget's box
(WORK_DIR/probe/layout.json), and the scenes click widgets by what they are ("the ☆ of the row
Docs site", "the Save button"). WIN_W x WIN_H below sets the window size; raise WIN_H if a scene says
a row is off screen.

Nothing here touches your screen, your list or your terminals:
- Xvfb display :98 (override with DISPLAY_NUM=:N), driven with XTest (tools/xinput.py), with the
  tiny compositor tools/xcompositor.py so popovers get their see-through corners.
- The app always runs with --dry-run (▶ and Open only print), with HOME, XDG_CONFIG_HOME and
  XDG_CACHE_HOME inside WORK_DIR, under its own private D-Bus session bus (dbus-run-session; it
  can't reach your running launcher, and starts no portals), with no Wayland display, and with stub
  kitty, gnome-terminal and cosmic-term first on PATH (so even the dry-run's "is the kitty window
  there?" question never reaches your real kitty).
- "Move to monitor" (which presses keys on a /dev/uinput virtual keyboard, and that would reach the
  real desktop) stays off: settings.json never has it, tools/app_probe.py replaces the key press
  with a stub, and every scene ends by checking that the setting is still unset.
Needs Xvfb, ffmpeg, dbus-run-session, python3-gi (GTK 4, libadwaita) and pycairo (for probe)."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP = os.path.join(REPO, "terminal-launcher.py")
PROBE_PY = os.path.join(HERE, "app_probe.py")
IMAGES = os.path.join(REPO, "docs", "images")
sys.path.insert(0, HERE)
import xinput  # noqa: E402

DISPLAY = os.environ.get("DISPLAY_NUM", ":98")
TITLE = "Terminal Launcher"             # the main window's title
WIN_W, WIN_H = 760, 960                 # the main window (logical px): 8 rows above the cat's bar, and
                                        # wide enough for the Open in list (it reaches past the rows)
MAX_SCALE = 2                           # the tour is recorded at 2x
DLG_W, DLG_H = 580, min(780, WIN_H - 18)    # the Add/Edit dialog, centered over the main window
DLG = ((WIN_W - DLG_W) // 2, (WIN_H - DLG_H) // 2, DLG_W, DLG_H)
BADGES_BOX = ((WIN_W - 460) // 2, (WIN_H - 640) // 2, 460, 640)   # the Badges window, likewise
SCREEN = f"{max(2000, (WIN_W + 560) * MAX_SCALE)}x{max(1700, (WIN_H + 160) * MAX_SCALE)}"   # room for popovers
TIP_DELAY = 0.45                        # the app chimes a tip this long after the action that showed it
TIPS = ["star", "drag-star", "tick", "suggest-star"]   # the app's one-time tips (Window._tip keys)
TODAY = time.strftime("%Y-%m-%d")


def badges(*ids):
    """A settings.json "badges" value: these badges, earned today. Scenes seed the ones they don't
    want announced (Polyglot, for one, is announced at startup whenever the list has 3+ kinds)."""
    return {b: TODAY for b in ids}

# A made-up list: plausible projects, fake session IDs, folders under the fake HOME.
SESSIONS = [
    {"name": "Website redesign", "folder": "~/Projects/website", "kind": "claude",
     "session": "4f9c2a7e-1b3d-4c55-9e0a-2d6b8f13c7a1", "autostart": True},
    {"name": "API server", "folder": "~/Projects/api-server", "kind": "omp", "session": "a81d3c5e"},
    {"name": "Mobile app", "folder": "~/Projects/mobile-app", "kind": "opencode", "session": ""},
    {"name": "Dev server", "folder": "~/Projects/website", "kind": "command", "command": "npm run dev",
     "autostart": True},
    {"name": "Infra notes", "folder": "~/Notes/infra", "kind": "terminal"},
    {"name": "Docs site", "folder": "~/Projects/docs", "kind": "claude",
     "session": "7be20c94-d1f6-4a83-b2e5-90c1f4a6d3e8"},
    {"name": "Data pipeline", "folder": "~/Projects/pipeline", "kind": "omp", "session": ""},
    {"name": "htop", "folder": "~", "kind": "command", "command": "htop"},
]
FOLDERS = ["Projects/website", "Projects/api-server", "Projects/mobile-app", "Projects/docs",
           "Projects/pipeline", "Notes/infra"]
NEW_ENTRY = ("Server logs", "~/Projects/api-server")   # what the Add scenes type


def starred(sessions, *names):
    """A copy of the list with these entries starred (the app moves them to the top itself)."""
    return [dict(s, star=True) if s["name"] in names else dict(s) for s in sessions]


GTK_SETTINGS = """[Settings]
gtk-font-name=Open Sans 11
gtk-icon-theme-name=Cosmic
gtk-cursor-theme-name=Adwaita
gtk-cursor-theme-size=24
gtk-hint-font-metrics=true
gtk-enable-animations=true
gtk-enable-event-sounds=true
"""
BUS_CONF = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""   # no <servicedir>: nothing (portals, gvfs, ...) gets started on this bus
STUB = "#!/bin/sh\n# capture_media.py's stand-in: the app runs with --dry-run, so this only answers \"is it there?\"\nexit 0\n"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Rig:
    """Xvfb + compositor + throwaway home; starts and stops the app (under tools/app_probe.py)."""

    def __init__(self, work):
        self.work = os.path.abspath(work)
        if self.work.startswith(REPO + os.sep):
            sys.exit("capture_media.py: WORK_DIR must be outside the repo")
        self.home = os.path.join(self.work, "home")
        self.cfg = os.path.join(self.home, ".config")
        self.runtime = os.path.join(self.work, "runtime")
        self.bin = os.path.join(self.work, "bin")
        self.probe = os.path.join(self.work, "probe")
        for d in [self.cfg + "/terminal-launcher", self.cfg + "/gtk-4.0", self.home + "/.cache", self.runtime,
                  self.bin, self.probe]:
            os.makedirs(d, exist_ok=True)
        os.chmod(self.runtime, 0o700)
        for f in FOLDERS:
            os.makedirs(os.path.join(self.home, f), exist_ok=True)
        for t in ("kitty", "gnome-terminal", "cosmic-term"):
            path = os.path.join(self.bin, t)
            with open(path, "w") as f:
                f.write(STUB)
            os.chmod(path, 0o755)
        with open(os.path.join(self.cfg, "gtk-4.0", "settings.ini"), "w") as f:
            f.write(GTK_SETTINGS)
        self.bus_conf = os.path.join(self.work, "bus.conf")
        with open(self.bus_conf, "w") as f:
            f.write(BUS_CONF)
        if os.path.exists(f"/tmp/.X11-unix/X{DISPLAY[1:]}"):
            sys.exit(f"capture_media.py: display {DISPLAY} is already in use")
        self.xvfb = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", f"{SCREEN}x24", "-nolisten", "tcp"],
                                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(self.work, "xvfb.log"), "w"))
        for _ in range(50):
            if os.path.exists(f"/tmp/.X11-unix/X{DISPLAY[1:]}"):
                break
            time.sleep(0.1)
        time.sleep(0.3)
        self.comp = subprocess.Popen([sys.executable, os.path.join(HERE, "xcompositor.py"), DISPLAY, "#26262b"],
                                     stderr=open(os.path.join(self.work, "compositor.log"), "w"))
        self.X = xinput.Display(DISPLAY)
        self.X.root_cursor()
        self.app = None
        self.scale = 1
        self.cuts = []      # (from, to) wall-clock times when a dialog maps at 0,0 before it's moved

    # ---------- the app ----------
    def config(self, sessions, mode="kitty", settings=None):
        """Write the list and settings.json. All tips count as seen and Polyglot as earned unless
        `settings` says otherwise (e.g. {"tips_seen": []}); Move to monitor is never set."""
        st = {"open_mode": mode, "tips_seen": list(TIPS), "badges": badges("polyglot")}
        st.update(settings or {})
        if st.get("move_to_monitor"):
            sys.exit("capture_media.py: refusing to set move_to_monitor (it presses real keys)")
        st.pop("move_to_monitor", None)
        d = os.path.join(self.cfg, "terminal-launcher")
        with open(os.path.join(d, "sessions.json"), "w") as f:
            json.dump(sessions, f, indent=2)
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump(st, f)

    def sessions(self):
        with open(os.path.join(self.cfg, "terminal-launcher", "sessions.json")) as f:
            return json.load(f)

    def settings(self):
        try:
            with open(os.path.join(self.cfg, "terminal-launcher", "settings.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def start(self, sessions, mode="kitty", scale=1, dark=False, settings=None):
        self.stop()
        self.config(sessions, mode, settings)
        self.scale = scale
        for f in ("sounds.jsonl", "cat.jsonl", "layout.json", "request"):
            if os.path.exists(os.path.join(self.probe, f)):
                os.remove(os.path.join(self.probe, f))
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": self.home, "TL_PROBE_HOME": self.home,
               "XDG_CONFIG_HOME": self.cfg,
               "XDG_CACHE_HOME": os.path.join(self.home, ".cache"), "XDG_RUNTIME_DIR": self.runtime,
               "DISPLAY": DISPLAY, "GDK_BACKEND": "x11", "GDK_SCALE": str(scale),
               "GSETTINGS_BACKEND": "memory", "NO_AT_BRIDGE": "1", "GTK_A11Y": "none",
               "GDK_DEBUG": "no-portals", "GIO_USE_VFS": "local", "GTK_USE_PORTAL": "0",
               "LANG": "en_US.UTF-8", "XCURSOR_THEME": "Adwaita", "XCURSOR_SIZE": str(24 * scale)}
        if dark:
            env["ADW_DEBUG_COLOR_SCHEME"] = "prefer-dark"
        self.app = subprocess.Popen(
            ["dbus-run-session", f"--config-file={self.bus_conf}", "--", "python3", PROBE_PY, self.probe],
            env=env, start_new_session=True, stdout=open(os.path.join(self.work, "app.log"), "a"),
            stderr=subprocess.STDOUT)
        want = (0, 0, WIN_W * scale, WIN_H * scale)
        stable_since, end = None, time.time() + 20
        while time.time() < end:           # GTK may set its own size again just after mapping:
            win = self.wait_window(TITLE)  # keep putting it back until it stays
            if tuple(win[1:5]) != want:
                self.X.place(win[0], *want)
                stable_since = None
            elif stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since > 1.0:
                break
            time.sleep(0.05)
        else:
            sys.exit("capture_media.py: couldn't size the window")
        self.X.move(*self.p(*self.park()))
        time.sleep(1.2)

    def stop(self):
        if self.app and self.app.poll() is None:
            os.killpg(self.app.pid, signal.SIGTERM)
            try:
                self.app.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.app.pid, signal.SIGKILL)
        if self.app:
            self.check_safe()
        self.app = None

    def check_safe(self):
        if self.settings().get("move_to_monitor"):
            sys.exit("capture_media.py: STOP: move_to_monitor got set during a scene")
        plog = os.path.join(self.probe, "probe.log")
        if os.path.exists(plog) and "blocked" in open(plog).read():
            sys.exit(f"capture_media.py: STOP: the app tried a Move to monitor key press (blocked; see {plog})")

    def close(self):
        self.stop()
        self.X.close()
        for p in (self.comp, self.xvfb):
            p.terminate()
            p.wait()

    def wait_window(self, name, timeout=15, exclude=()):
        end = time.time() + timeout
        while time.time() < end:
            for w in self.X.windows():
                if w[5] == name and not w[6] and w[0] not in exclude:
                    return w
            time.sleep(0.005)
        sys.exit(f"capture_media.py: no window {name!r} (see {self.work}/app.log)")

    def place_dialog(self, name, since=None, box=DLG):
        """Move a dialog over the middle of the main window (with no window manager it opens at
        0,0). The frames in between are noted in self.cuts and left out of the recordings."""
        t0 = since or time.time()
        w = self.wait_window(name)
        s = self.scale
        want = (box[0] * s, box[1] * s, box[2] * s, box[3] * s)
        good_since, end = None, time.time() + 5
        while time.time() < end:           # GTK may move it back once more: keep it there until it stays
            if tuple(self.wait_window(name)[1:5]) != want:
                self.X.place(w[0], *want)
                good_since = None
            elif good_since is None:
                good_since = time.time()
            elif time.time() - good_since > 0.3:
                break
            time.sleep(0.01)
        self.cuts.append((t0, (good_since or time.time()) + 0.1))
        return w

    # ---------- where things are (from tools/app_probe.py) ----------
    def layout(self, save=None):
        """Every mapped widget, with its box in logical px on the screen."""
        req = os.path.join(self.probe, "request")
        open(req, "w").close()
        end = time.time() + 4
        while os.path.exists(req):
            if time.time() > end:
                sys.exit(f"capture_media.py: the probe didn't answer (see {self.probe}/probe.log, {self.work}/app.log)")
            time.sleep(0.02)
        with open(os.path.join(self.probe, "layout.json")) as f:
            dump = json.load(f)
        if "error" in dump:
            sys.exit(f"capture_media.py: the probe couldn't read the window: {dump['error']} "
                     f"(see {self.probe}/probe.log)")
        widgets = dump["widgets"]
        offs = {w[5]: (w[1] / self.scale, w[2] / self.scale) for w in self.X.windows() if not w[6]}
        for e in widgets:
            ox, oy = offs.get(e["win"], (0, 0))
            e["box"] = [e["box"][0] + ox, e["box"][1] + oy, e["box"][2], e["box"][3]]
        if save:
            with open(save, "w") as f:
                json.dump(widgets, f, indent=1)
        return widgets

    def find(self, win=TITLE, pop=None, nth=0, need=True, **crit):
        """The widget matching all of: win (window title, "*" for any), pop (True: in a popover),
        type (GType name, e.g. "GtkButton"), text, title, row, group, icon, tooltip (prefix),
        css and anc (contained in its CSS classes / ancestor types)."""
        hits = []
        for e in self.layout():
            if win != "*" and e["win"] != win:
                continue
            if pop is not None and e["pop"] != pop:
                continue
            ok = True
            for k, v in crit.items():
                if k in ("css", "anc"):
                    ok = v in e.get(k, [])
                elif k == "tooltip":
                    ok = (e.get("tooltip") or "").startswith(v)
                else:
                    ok = e.get(k) == v
                if not ok:
                    break
            if ok:
                hits.append(e)
        if len(hits) <= nth:
            if need:
                sys.exit(f"capture_media.py: can't find {dict(crit, win=win, pop=pop)} "
                         f"(run the probe scene and look at {self.work}/layout-*.json)")
            return None
        return hits[nth]

    def at(self, fx=0.5, fy=0.5, **crit):
        """A point inside a widget (its middle by default), in logical px on the screen."""
        x, y, w, h = self.find(**crit)["box"]
        return round(x + w * fx), round(y + h * fy)

    def row_part(self, name, part="text"):
        """A point on one list row: text (its title: nothing there shows a tooltip), tick, handle,
        star, more (⋮) or play (▶). Exits if it isn't on screen."""
        crit = {"text": dict(type="GtkLabel", text=name, row=name),
                "tick": dict(type="GtkCheckButton", row=name),
                "handle": dict(type="GtkImage", icon="list-drag-handle-symbolic", row=name),
                "star": dict(type="GtkToggleButton", css="star-btn", row=name),
                "more": dict(type="GtkMenuButton", row=name, pop=False),
                "play": dict(type="GtkButton", css="suggested-action", row=name)}[part]
        x, y = self.at(**crit)
        lx, ly, lw, lh = self.find(type="GtkScrolledWindow", pop=False)["box"]   # the list's viewport
        if not (ly + 8 < y < min(ly + lh, WIN_H) - 8 and 0 < x < WIN_W):
            sys.exit(f"capture_media.py: the {part} of {name!r} is outside the visible list at {x},{y}: "
                     "raise WIN_H or use a shorter list")
        return x, y

    def header(self, what):
        """A header bar control: add, folder, open (the split button), open-menu (its ▾), menu (☰)."""
        crit = {"add": dict(type="AdwButtonContent", text="Add"),
                "folder": dict(type="AdwButtonContent", text="Folder…"),
                "open": dict(type="GtkButton", anc="AdwSplitButton"),
                "open-menu": dict(type="GtkMenuButton", anc="AdwSplitButton"),
                "menu": dict(type="GtkMenuButton", icon="open-menu-symbolic")}[what]
        return self.at(pop=False, **crit)

    def cat(self, fx=0.5, fy=0.5):
        """A point on Byte, the cat in the bar at the bottom."""
        return self.at(fx=fx, fy=fy, type="GtkDrawingArea", pop=False)

    def badges_button(self):
        return self.at(type="GtkButton", tooltip="Your badges", pop=False)

    def cat_bar(self):
        """The cat's bar (logical px box)."""
        return self.find(css="cat-bar", pop=False)["box"]

    def cat_moves(self):
        """[(unix time, move)] of every move the cat started (its idle ones too)."""
        path = os.path.join(self.probe, "cat.jsonl")
        if not os.path.exists(path):
            return []
        return [(d["t"], d["move"]) for d in map(json.loads, open(path).read().split("\n")[:-1])]

    def park(self):
        """A resting place for the pointer that shows no tooltip: the window's title in the header."""
        if not self.app:
            return WIN_W // 2, 22
        e = self.find(type="GtkLabel", text=TITLE, anc="AdwWindowTitle", need=False)
        if not e:
            return WIN_W // 2, 22
        x, y, w, h = e["box"]
        return round(x + w / 2), round(y + h / 2)

    def popover_fits(self, win=TITLE):
        """Is every open popover inside the recorded window box? (One that sticks out gets cut off.)"""
        for e in self.layout():
            if e["pop"] and e["win"] == win and e["type"] in ("GtkPopoverMenu", "GtkPopover"):
                x, y, w, h = e["box"]
                if x < 0 or y < 0 or x + w > WIN_W or y + h > WIN_H:
                    return False
        return True

    def sounds(self):
        """[(unix time, name)] of every sound the app asked for since it started."""
        path = os.path.join(self.probe, "sounds.jsonl")
        if not os.path.exists(path):
            return []
        return [(d["t"], d["name"]) for d in map(json.loads, open(path).read().split("\n")[:-1])]

    # ---------- input, in logical px ----------
    def p(self, x, y):
        return x * self.scale, y * self.scale

    def glide(self, x, y, t=0.45):
        self.X.glide(*self.p(x, y), duration=t)

    def click(self, x, y, t=0.45, events=None):
        self.glide(x, y, t)
        time.sleep(0.06)
        when = time.time()
        if events is not None:
            events.append({"t": when, "kind": "click", "x": x, "y": y})
        self.X.click()
        return when

    def drag(self, src, dst, duration=1.6, events=None, settle=0.8):
        """Drag from src to dst (logical px). Returns the release time (when the app reacts)."""
        self.glide(*src, t=0.5)
        time.sleep(0.1)
        t0 = time.time()
        released = self.X.drag(*self.p(*src), *self.p(*dst), duration=duration, hold=0.15, settle=settle)
        if events is not None:
            events.append({"t": t0, "kind": "drag", "from": list(src), "to": list(dst), "release": released})
        return released

    def dlg(self, x, y):
        return DLG[0] + x, DLG[1] + y

    # ---------- capture ----------
    def still(self, path, box=None):
        s = self.scale
        x, y, w, h = box or (0, 0, WIN_W, WIN_H)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-draw_mouse", "0",
                        "-video_size", f"{w * s}x{h * s}", "-i", f"{DISPLAY}+{x * s},{y * s}",
                        "-frames:v", "1", "-update", "1", path], check=True)
        log(f"wrote {path}")

    def record(self, path, box=None):
        s = self.scale
        x, y, w, h = box or (0, 0, WIN_W, WIN_H)
        err = open(path + ".log", "w")
        p = subprocess.Popen(["ffmpeg", "-loglevel", "info", "-y", "-f", "x11grab", "-framerate", "30",
                              "-draw_mouse", "1", "-video_size", f"{w * s}x{h * s}",
                              "-i", f"{DISPLAY}+{x * s},{y * s}", "-c:v", "libx264rgb", "-qp", "0",
                              "-preset", "ultrafast", path], stdin=subprocess.PIPE, stderr=err)
        start = None
        for _ in range(100):
            time.sleep(0.05)
            m = re.search(r"start: ([\d.]+)", open(path + ".log").read())
            if m:
                start = float(m.group(1))
                break
        time.sleep(0.3)
        return p, start

    @staticmethod
    def stop_record(p):
        p.stdin.write(b"q")
        p.stdin.flush()
        p.wait(20)


def to_gif(src, out, start=0.0, end=None, fps=15, width=None, cuts=(), want=None):
    """cuts: [(from, to)] in seconds of the recording, left out (a dialog's jump into place).
    want: (min, max) seconds; a GIF outside it gets a warning."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    trim = ["-ss", f"{start:.2f}"] + (["-to", f"{end:.2f}"] if end else [])
    scale = f",scale={width}:-1:flags=lanczos" if width else ""
    drop = "+".join(f"between(t,{a - start:.3f},{b - start:.3f})" for a, b in cuts)
    sel = f"select='not({drop})',setpts=N/FRAME_RATE/TB," if drop else ""
    vf = (f"{sel}fps={fps}{scale},split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
          f"[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y"] + trim + ["-i", src, "-filter_complex", vf,
                    "-loop", "0", out], check=True)
    mb, secs = os.path.getsize(out) / 1e6, duration(out)
    log(f"wrote {out} ({mb:.2f} MB, {secs:.1f} s)" + ("  <- over 2 MB: shorten it or lower fps" if mb > 2 else "")
        + (f"  <- want {want[0]}-{want[1]} s" if want and not want[0] <= secs <= want[1] else ""))


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                 "csv=p=0", path], capture_output=True, text=True).stdout)


# ---------- the small steps scenes share ----------
def menu_item(r, opener, item, events=None, win=TITLE, t=0.4, check=True):
    """Click `opener` (a point), then the popover item labeled `item`. The pointer heads for the
    item at once, so the opener's tooltip never shows. If `check` and the popover would stick out of
    the recorded window (and be cut off), close it and return False."""
    r.click(*opener, t=t, events=events)
    hit, end = None, time.time() + 2
    while hit is None and time.time() < end:
        hit = r.find(win=win, pop=True, type="GtkLabel", text=item, need=False)
        if hit is None:
            time.sleep(0.03)
    if hit is None:
        sys.exit(f"capture_media.py: no menu item {item!r}")
    x, y, w, h = hit["box"]
    r.glide(round(x + w / 2), round(y + h / 2), t=0.3)
    if check and not r.popover_fits(win):
        log(f"the menu with {item!r} sticks out of the window; closing it")
        r.X.key("Escape")
        time.sleep(0.3)
        return False
    time.sleep(0.12)
    r.click(round(x + w / 2), round(y + h / 2), t=0.05, events=events)
    return True


def set_mode(r, label, events=None):
    """Open in → one of "My kitty window", "One new window", "Separate windows"."""
    if not menu_item(r, r.at(type="AdwComboRow", title="Open in", fx=0.78), label, events):
        sys.exit("capture_media.py: the Open in list sticks out of the window: make WIN_W/WIN_H bigger")
    r.glide(*r.park(), t=0.25)                   # off the row (it has a tooltip) before it shows


def tick(r, name, events=None, t=0.35):
    when = r.click(*r.row_part(name, "tick"), t=t, events=events)
    time.sleep(0.15)
    r.glide(*r.row_part(name, "text"), t=0.15)    # off the box before its tooltip shows
    return when


def star(r, name, events=None, t=0.45):
    """☆ on a row. The list rebuilds under the pointer, so it moves straight onto the starred row's
    title (no tooltip there)."""
    when = r.click(*r.row_part(name, "star"), t=t, events=events)
    time.sleep(0.12)
    r.glide(*r.row_part(name, "text"), t=0.2)
    return when


def drag_onto(r, name, target, events=None, duration=1.6, settle=0.8):
    """Drag a row by its handle onto another row; it lands in that row's place (into Favorites if the
    target is a favorite). Returns the release time."""
    src = r.row_part(name, "handle")
    tx, ty = r.row_part(target, "text")
    released = r.drag(src, (src[0], ty - 6), duration=duration, events=events, settle=settle)
    r.glide(src[0] + 220, ty - 6, t=0.15)           # off the handle at once: its tooltip would be cut off
    time.sleep(0.1)
    return released


def add_terminal(r, events=None, autostart=False, beat=None, typing=1.0):
    """Add → Terminal only → name and folder → (Start when I log in) → Save."""
    dlg = "Add a terminal"
    ax, ay = r.header("add")
    r.glide(ax, ay)
    clicked = time.time()
    park = r.park()
    r.click(ax, ay, t=0.01, events=events)
    r.glide(*park, t=0.12)                          # off the button before its tooltip shows
    r.place_dialog(dlg, since=clicked)
    time.sleep(0.5)
    if not menu_item(r, r.at(win=dlg, pop=False, type="AdwComboRow", title="Type", fx=0.75), "Terminal only",
                     events, win=dlg, t=0.45):
        sys.exit("capture_media.py: the dialog's Type list sticks out of the window: make WIN_H bigger")
    time.sleep(0.4)
    r.click(*r.at(win=dlg, type="AdwEntryRow", title="Name", fx=0.35), events=events)
    r.X.type(NEW_ENTRY[0], delay=0.04 * typing)
    time.sleep(0.2)
    r.click(*r.at(win=dlg, type="AdwEntryRow", title="Folder", fx=0.35), t=0.3, events=events)
    r.X.type(NEW_ENTRY[1], delay=0.035 * typing)
    time.sleep(0.4)
    if autostart:
        if beat:
            beat()
        r.click(*r.at(win=dlg, type="GtkSwitch", row="Start when I log in"), events=events)
        time.sleep(0.7)
    r.click(*r.at(win=dlg, type="GtkButton", text="Save"), events=events)
    r.glide(*r.park(), t=0.4)


# ---------- scenes ----------
def scene_probe(r):
    """Crosses on every target the scenes use, in a few states, to check after a layout change."""
    import cairo

    def shot(name, points):
        png = os.path.join(r.work, f"probe-{name}.png")
        r.still(png, (0, 0, WIN_W + 260, WIN_H + 60))
        r.layout(save=os.path.join(r.work, f"layout-{name}.json"))
        surf = cairo.ImageSurface.create_from_png(png)
        cr = cairo.Context(surf)
        cr.set_line_width(2)
        cr.select_font_face("Open Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(11)
        for label, (x, y) in points.items():
            cr.set_source_rgb(1, 0.1, 0.4)
            cr.move_to(x - 8, y)
            cr.line_to(x + 8, y)
            cr.move_to(x, y - 8)
            cr.line_to(x, y + 8)
            cr.stroke()
            cr.move_to(x + 6, y - 5)
            cr.show_text(label)
        cr.set_source_rgba(0, 0.6, 1, 0.8)
        cr.rectangle(0.5, 0.5, WIN_W - 1, WIN_H - 1)    # what the recordings keep
        cr.stroke()
        surf.write_to_png(png)
        log(f"  {name}: " + ", ".join(f"{k} {v[0]},{v[1]}" for k, v in points.items()))

    def rows(names):
        pts = {}
        for n in names:
            for part in ("tick", "handle", "star", "more", "play", "text"):
                e = r.find(**{"text": dict(type="GtkLabel", text=n, row=n),
                              "tick": dict(type="GtkCheckButton", row=n),
                              "handle": dict(type="GtkImage", icon="list-drag-handle-symbolic", row=n),
                              "star": dict(type="GtkToggleButton", css="star-btn", row=n),
                              "more": dict(type="GtkMenuButton", row=n, pop=False),
                              "play": dict(type="GtkButton", css="suggested-action", row=n)}[part], need=False)
                if e:
                    x, y, w, h = e["box"]
                    pts[f"{n[:6]}.{part}"] = (round(x + w / 2), round(y + h / 2))
        return pts

    for name, lst in (("0fav", SESSIONS[:7]), ("2fav", starred(SESSIONS, "Website redesign", "API server"))):
        r.start(lst)
        pts = {k: r.header(k) for k in ("add", "folder", "open", "open-menu", "menu")}
        pts["cat"] = r.cat()
        pts["badges"] = r.badges_button()
        log(f"  cat bar at {r.cat_bar()}; list viewport at {r.find(type='GtkScrolledWindow', pop=False)['box']}")
        pts["open-in"] = r.at(type="AdwComboRow", title="Open in", fx=0.78)
        pts["park"] = r.park()
        pts.update(rows([s["name"] for s in lst]))
        shot(name, pts)
    r.click(*r.header("open-menu"), t=0.2)
    time.sleep(0.5)
    pts = {}
    for label in ("My kitty window", "One new window", "Separate windows", "Open favorites"):
        e = r.find(pop=True, type="GtkLabel", text=label, need=False)
        if e:
            x, y, w, h = e["box"]
            pts[label] = (round(x + w / 2), round(y + h / 2))
    log(f"  the ▾ menu fits in the window: {r.popover_fits()}")
    shot("open-menu", pts)
    r.X.key("Escape")
    time.sleep(0.3)
    r.click(*r.at(type="AdwComboRow", title="Open in", fx=0.78), t=0.2)
    time.sleep(0.5)
    pts = {}
    for label in ("My kitty window", "One new window", "Separate windows"):
        e = r.find(pop=True, type="GtkLabel", text=label, need=False)
        if e:
            x, y, w, h = e["box"]
            pts[label] = (round(x + w / 2), round(y + h / 2))
    log(f"  the Open in list fits in the window: {r.popover_fits()}")
    shot("open-in", pts)
    r.X.key("Escape")
    time.sleep(0.3)
    park = r.park()
    r.click(*r.header("add"), t=0.2)
    r.glide(*park, t=0.1)
    r.place_dialog("Add a terminal")
    time.sleep(0.6)
    d = "Add a terminal"
    pts = {"type": r.at(win=d, pop=False, type="AdwComboRow", title="Type", fx=0.75),
           "name": r.at(win=d, type="AdwEntryRow", title="Name", fx=0.35),
           "folder": r.at(win=d, type="AdwEntryRow", title="Folder", fx=0.35),
           "login": r.at(win=d, type="GtkSwitch", row="Start when I log in"),
           "save": r.at(win=d, type="GtkButton", text="Save")}
    shot("dialog", pts)
    r.stop()


def scene_stills(r):
    two = starred(SESSIONS, "Website redesign", "API server")
    seed = {"badges": badges("polyglot", "first-star")}
    r.start(two, settings=seed)
    r.still(os.path.join(IMAGES, "window.png"))
    r.start(two, dark=True, settings=seed)
    r.still(os.path.join(IMAGES, "window-dark.png"))
    r.start(two, scale=2, settings=seed)
    r.still(os.path.join(r.work, "window-2x.png"))
    r.start(two, settings=seed)
    menu_item(r, r.row_part("Website redesign", "more"), "Edit…", check=False)   # a still: no need to fit
    r.place_dialog("Edit terminal")
    r.glide(*r.dlg(DLG_W // 2, DLG_H - 8), t=0.2)   # a blank spot in the dialog (keeps it focused)
    time.sleep(1.0)
    r.still(os.path.join(IMAGES, "edit.png"), DLG)
    r.start(two, settings={"badges": badges("polyglot", "first-star", "tidy-paws", "explorer", "squad")})
    r.click(*r.badges_button(), t=0.2)
    r.glide(BADGES_BOX[0] + BADGES_BOX[2] // 2, BADGES_BOX[1] + 20, t=0.1)   # off the button: no tooltip
    r.place_dialog("Badges", box=BADGES_BOX)
    time.sleep(1.0)
    r.still(os.path.join(IMAGES, "badges.png"), BADGES_BOX)
    r.stop()


def scene_drag(r):
    lst = SESSIONS[:7]
    r.start(lst, settings={"badges": badges("polyglot", "tidy-paws")})   # the cat just nods
    tick(r, "Website redesign", t=0.1)
    tick(r, "Infra notes", t=0.1)
    r.glide(*r.park(), t=0.1)
    time.sleep(0.8)
    mkv = os.path.join(r.work, "drag.mkv")
    rec, _ = r.record(mkv)
    time.sleep(0.9)
    drag_onto(r, "Website redesign", "Dev server", duration=1.8)
    r.glide(*r.park(), t=0.5)
    time.sleep(1.8)
    r.stop_record(rec)
    after = os.path.join(r.work, "drag-after.png")
    r.still(after)
    order = [s["name"] for s in r.sessions()]
    want = [s["name"] for s in lst]
    want.insert(3, want.pop(0))
    ok = order == want
    log(f"drag test: saved order {order}")
    log(f"drag test: expected    {want} -> {'OK' if ok else 'WRONG'}")
    to_gif(mkv, os.path.join(IMAGES, "gifs", "drag.gif"))
    r.stop()
    if not ok:
        sys.exit("drag test FAILED: the saved order isn't what the drag asked for")
    log(f"drag test: look at {after}: 'Website redesign' (now 4th) and 'Infra notes' should be ticked, "
        "and the header should say 'Add 2 to kitty'")


def scene_tick(r):
    r.start(SESSIONS[:7], settings={"badges": badges("polyglot", "explorer")})
    mkv = os.path.join(r.work, "tick.mkv")
    rec, _ = r.record(mkv)
    time.sleep(0.3)
    for name in ("Website redesign", "API server", "Dev server"):
        tick(r, name, t=0.3)
        time.sleep(0.35)
    time.sleep(0.3)
    for label in ("One new window", "Separate windows"):
        set_mode(r, label)
        time.sleep(1.1)
    r.glide(*r.park(), t=0.4)
    time.sleep(0.5)
    r.stop_record(rec)
    r.stop()
    to_gif(mkv, os.path.join(IMAGES, "gifs", "tick-and-open.gif"), start=0.2)


def scene_add(r):
    r.start(SESSIONS[:7])
    mkv = os.path.join(r.work, "add.mkv")
    r.cuts = []
    rec, start = r.record(mkv)
    time.sleep(0.3)
    add_terminal(r)
    time.sleep(1.8)
    r.stop_record(rec)
    names = [s["name"] for s in r.sessions()]
    r.stop()
    log(f"add: saved list now ends with {names[-1]!r}; dialog jump cut at "
        f"{[(round(a - start, 2), round(b - start, 2)) for a, b in r.cuts]} s")
    to_gif(mkv, os.path.join(IMAGES, "gifs", "add.gif"), start=0.2,
           cuts=[(a - start, b - start) for a, b in r.cuts])


def scene_favorites(r):
    """☆ on Docs site (pop, glow, up into Favorites; Byte passes on the tip), then Data pipeline
    dragged in."""
    r.start(starred(SESSIONS[:7], "Website redesign"),
            settings={"tips_seen": [t for t in TIPS if t != "star"],
                      "badges": badges("polyglot", "first-star", "tidy-paws")})
    mkv = os.path.join(r.work, "favorites.mkv")
    rec, start = r.record(mkv)
    time.sleep(0.4)
    star(r, "Docs site", t=0.45)
    time.sleep(1.5)                                  # the pop (0.42 s) and the glow (1.3 s)
    drag_onto(r, "Data pipeline", "Docs site", duration=1.2)
    time.sleep(1.3)                                  # its glow
    r.glide(*r.park(), t=0.3)
    time.sleep(0.4)
    r.stop_record(rec)
    favs = [s["name"] for s in r.sessions() if s.get("star")]
    r.stop()
    want = ["Website redesign", "Data pipeline", "Docs site"]
    log(f"favorites: starred now {favs} (want {want}) -> {'OK' if favs == want else 'WRONG'}")
    to_gif(mkv, os.path.join(IMAGES, "gifs", "favorites.gif"), start=0.3, want=(5, 9))
    if favs != want:
        sys.exit("favorites test FAILED")


IDLE_MOVES = ("blink", "tail", "wink")      # the ones that read in a GIF (a "look" barely shows)


def scene_cat(r):
    """Byte up close (the bottom of the window): an idle blink or swish, a pet (chirp, wink, its next
    line), then a star that makes it hop and announce the First star badge."""
    r.start(SESSIONS)                                # all 8 rows: the list reaches down to the bar
    top = r.find(type="AdwActionRow", title="Data pipeline")["box"][1] - 8
    box = (0, int(top), WIN_W, WIN_H - int(top))
    mkv = os.path.join(r.work, "cat.mkv")
    rec, start = r.record(mkv, box)
    rest = r.row_part("Data pipeline", "text")
    r.glide(*rest, t=0.3)
    t0, idle = time.time(), None
    while time.time() - t0 < 15 and idle is None:    # wait for one of its own idle moves
        idle = next((t for t, m in r.cat_moves() if m in IDLE_MOVES and t > t0), None)
        time.sleep(0.05)
    if idle is None:
        log("the cat made no idle move in 15 s; going on without one")
        idle = time.time()
    time.sleep(max(0.0, idle + 1.0 - time.time()))
    r.click(*r.cat(), t=0.45)                        # pet: chirp, wink, its next line
    r.glide(*rest, t=0.25)                           # off the cat and its words (both have tooltips)
    time.sleep(2.0)
    star(r, "htop", t=0.5)                           # hop, blush, "New badge: First star!"
    time.sleep(2.3)
    r.stop_record(rec)
    moves = [m for t, m in r.cat_moves() if t > start]
    got = r.settings().get("badges", {})
    r.stop()
    log(f"cat: moves {moves}; badges now {sorted(got)}")
    to_gif(mkv, os.path.join(IMAGES, "gifs", "cat.gif"), start=max(0.0, idle - start - 0.8), want=(6, 9))
    if "first-star" not in got:
        sys.exit("cat test FAILED: no First star badge")


def scene_tour(r):
    """One continuous take at 2x for the video. tour.json gets the captions, clicks and drags, the
    sounds the scene expects (from what it clicked) and the sounds the app really asked for."""
    # Polyglot (announced at startup) and Tidy paws (the first drag) count as earned, so Byte greets
    # first and the badges that pop are First star and Explorer; the tick tip is the one tip left
    r.start(SESSIONS[:7], scale=2, settings={"tips_seen": [t for t in TIPS if t != "tick"],
                                             "badges": badges("polyglot", "tidy-paws")})
    mkv = os.path.join(r.work, "tour.mkv")
    events = []
    r.cuts = []

    def beat(text):
        events.append({"t": time.time(), "kind": "beat", "text": text})

    def expect(name, t):
        events.append({"t": t, "kind": "expect", "name": name})

    rec, start = r.record(mkv)
    t_rec = time.time()
    beat("Your terminals and coding agents,|one click away")
    for n in ("Website redesign", "API server", "Mobile app"):
        r.glide(*r.row_part(n, "text"), t=0.3)
    time.sleep(0.6)

    beat("New: ★ star your favorites")
    when = star(r, "Docs site", events, t=0.6)
    expect("star", when)
    expect("badge", when + TIP_DELAY)                # Byte: "New badge: First star!"
    time.sleep(2.3)

    beat("Or drag one into Favorites")
    released = drag_onto(r, "Data pipeline", "Docs site", events, duration=1.1, settle=0.5)
    expect("star", released)
    r.glide(*r.row_part("Data pipeline", "text"), t=0.3)
    time.sleep(0.6)

    beat("Open all your favorites at once")
    used_menu = menu_item(r, r.header("open-menu"), "Open favorites", events)
    if not used_menu:                                # the menu would be cut off: the shortcut instead
        r.glide(*r.park(), t=0.3)
        events.append({"t": time.time(), "kind": "keys", "keys": "Ctrl+Shift+Enter"})
        r.X.key("ctrl+shift+Return")
    expect("open", time.time())
    r.glide(*r.park(), t=0.35)
    time.sleep(1.3)

    beat("Meet Byte, the launcher's cat")
    cx, cy = r.cat()
    for k in range(2):
        expect("chirp", r.click(cx, cy, t=0.5 if k == 0 else 0.15, events=events))
        r.glide(cx + 4, cy - 70, t=0.2)              # off the cat (it has a tooltip), onto the list
        time.sleep(1.3)
        if k == 0:
            r.glide(cx + 2, cy - 4, t=0.2)

    beat("Tick a few,|open them together")
    for n in ("API server", "Dev server"):
        when = tick(r, n, events, t=0.4)
        time.sleep(0.2)
    expect("tip", when + TIP_DELAY)                  # Byte: "Psst: ticked rows open together…"
    time.sleep(0.6)
    ox, oy = r.header("open")
    expect("open", r.click(ox, oy, t=0.45, events=events))
    r.glide(*r.park(), t=0.25)
    time.sleep(0.8)

    beat("In kitty tabs, one window,|or separate windows")
    for label in ("One new window", "Separate windows"):   # kitty was the first; the rest is shown
        set_mode(r, label, events)
        time.sleep(0.5)
    expect("badge", time.time() - 0.4)               # Byte: "New badge: Explorer!" (roughly)
    time.sleep(0.9)

    beat("Add one in seconds")
    add_terminal(r, events, autostart=True, beat=lambda: beat("Start it when you log in"), typing=0.7)
    time.sleep(0.8)

    beat("Drag to change the order")
    released = drag_onto(r, NEW_ENTRY[0], "API server", events, duration=1.1, settle=0.5)
    expect("drop", released)
    r.glide(*r.park(), t=0.4)
    time.sleep(0.5)

    beat("Byte hands out badges|for little quests")
    bx, by = r.badges_button()
    clicked = r.click(bx, by, t=0.5, events=events)
    r.glide(BADGES_BOX[0] + BADGES_BOX[2] // 2, BADGES_BOX[1] + 20, t=0.12)   # off the button: no tooltip
    r.place_dialog("Badges", since=clicked, box=BADGES_BOX)
    time.sleep(2.3)
    events.append({"t": time.time(), "kind": "end"})
    r.stop_record(rec)

    # the sounds the app asked for, against the ones the scene expected
    got = [(t, n) for t, n in r.sounds() if t >= t_rec - 1]
    want = sorted((e["t"], e["name"]) for e in events if e["kind"] == "expect")
    log("sounds (take time): expected | the app asked for")
    for k in range(max(len(want), len(got))):
        a = f"{want[k][0] - start:6.2f} {want[k][1]:<6}" if k < len(want) else " " * 13
        b = f"{got[k][0] - start:6.2f} {got[k][1]:<6}" if k < len(got) else ""
        log(f"  {a} | {b}")
    if got and [n for _, n in want] != [n for _, n in got]:
        log("WARNING: the app's sounds differ from the expected ones; the video uses the app's")
    for t, n in got if got else want:
        events.append({"t": t, "kind": "sound", "name": n, "from": "app" if got else "expected"})
    log(f"badges after the take: {sorted(r.settings().get('badges', {}))}")
    final = r.sessions()
    r.stop()

    for e in events:
        e["t"] = round(e["t"] - start, 3)
        if "release" in e:
            e["release"] = round(e["release"] - start, 3)
    events += [{"t": round(a - start, 3), "kind": "hold", "until": round(b - start, 3)} for a, b in r.cuts]
    events.sort(key=lambda e: e["t"])
    with open(os.path.join(r.work, "tour.json"), "w") as f:
        json.dump({"video": "tour.mkv", "scale": 2, "window": [WIN_W, WIN_H], "events": events}, f, indent=1)
    secs = duration(mkv)
    log(f"wrote {mkv} ({secs:.1f} s) and tour.json; saved order: {[s['name'] for s in final]}")
    if not 35 <= secs + 3.6 <= 50:               # compose_tour.py adds a 3.6 s end card
        log(f"WARNING: with the end card the video would be {secs + 3.6:.1f} s; aim for 35-50 s")


SCENES = {"probe": scene_probe, "stills": scene_stills, "drag": scene_drag, "tick": scene_tick,
          "add": scene_add, "favorites": scene_favorites, "cat": scene_cat, "tour": scene_tour}


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        sys.exit(__doc__)
    names = sys.argv[2:] or [n for n in SCENES if n != "probe"]
    for n in names:
        if n not in SCENES:
            sys.exit(f"unknown scene {n!r}; choose from {', '.join(SCENES)}")
    for tool in ("Xvfb", "ffmpeg", "dbus-run-session"):
        if not shutil.which(tool):
            sys.exit(f"capture_media.py: needs {tool}")
    r = Rig(sys.argv[1])
    try:
        for n in names:
            log(f"scene {n}")
            SCENES[n](r)
    finally:
        r.close()


if __name__ == "__main__":
    main()
