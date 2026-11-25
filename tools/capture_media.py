#!/usr/bin/env python3
"""capture_media.py: record Terminal Launcher's README screenshots and GIFs, and the raw take for
the video, on a HIDDEN Xvfb display with a made-up list in a throwaway home folder.

    python3 tools/capture_media.py WORK_DIR [stills] [drag] [tick] [add] [tour]   (default: all)

  stills  docs/images/window.png, window-dark.png, edit.png
  drag    docs/images/gifs/drag.gif     (also a real test of dragging: exits 1 if the saved order
                                         isn't the one the drag asked for; the ticks it keeps are
                                         checked by eye in WORK_DIR/drag-after.png)
  tick    docs/images/gifs/tick-and-open.gif
  add     docs/images/gifs/add.gif
  tour    WORK_DIR/tour.mkv + tour.json at 2x scale, for tools/compose_tour.py

WORK_DIR (outside the repo) holds the fake HOME, the config, the recordings and the logs. Nothing
here touches your screen, your list or your terminals:
- Xvfb display :98 (override with DISPLAY_NUM=:N), driven with XTest (tools/xinput.py), with the
  tiny compositor tools/xcompositor.py so popovers get their see-through corners.
- The app always runs with --dry-run (▶ and Open only print), with HOME, XDG_CONFIG_HOME and
  XDG_CACHE_HOME inside WORK_DIR, under its own private D-Bus session bus (dbus-run-session; it
  can't reach your running launcher, and starts no portals), with no Wayland display.
Needs Xvfb, ffmpeg, dbus-run-session and python3-gi (GTK 4, libadwaita)."""
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
IMAGES = os.path.join(REPO, "docs", "images")
sys.path.insert(0, HERE)
import xinput  # noqa: E402

DISPLAY = os.environ.get("DISPLAY_NUM", ":98")
WIN_W, WIN_H = 720, 770                 # the main window (logical px); fits 8 rows
DLG = (70, 9, 580, 752)                 # where the Add/Edit dialog goes, over the main window
ROW0, ROW_H = 267, 59                   # centre of the first row, row pitch
HANDLE_X, TICK_X, MORE_X = 104, 131, 566
OPEN_IN = (560, 104)                    # the "Open in" row
MODE_Y = {"kitty": 150, "tabs": 189, "windows": 228}   # its popover's items
MODE_X = 600
PARK = (360, 752)                       # a blank spot near the bottom, for the pointer

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
GTK_SETTINGS = """[Settings]
gtk-font-name=Open Sans 11
gtk-icon-theme-name=Cosmic
gtk-cursor-theme-name=Adwaita
gtk-cursor-theme-size=24
gtk-hint-font-metrics=true
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


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Rig:
    """Xvfb + compositor + throwaway home; starts and stops the app."""

    def __init__(self, work, screen="2000x1700"):
        self.work = os.path.abspath(work)
        if self.work.startswith(REPO + os.sep):
            sys.exit("capture_media.py: WORK_DIR must be outside the repo")
        self.home = os.path.join(self.work, "home")
        self.cfg = os.path.join(self.home, ".config")
        self.runtime = os.path.join(self.work, "runtime")
        for d in [self.cfg + "/terminal-launcher", self.cfg + "/gtk-4.0", self.home + "/.cache", self.runtime]:
            os.makedirs(d, exist_ok=True)
        os.chmod(self.runtime, 0o700)
        for f in FOLDERS:
            os.makedirs(os.path.join(self.home, f), exist_ok=True)
        with open(os.path.join(self.cfg, "gtk-4.0", "settings.ini"), "w") as f:
            f.write(GTK_SETTINGS)
        self.bus_conf = os.path.join(self.work, "bus.conf")
        with open(self.bus_conf, "w") as f:
            f.write(BUS_CONF)
        if os.path.exists(f"/tmp/.X11-unix/X{DISPLAY[1:]}"):
            sys.exit(f"capture_media.py: display {DISPLAY} is already in use")
        self.xvfb = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", f"{screen}x24", "-nolisten", "tcp"],
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
    def config(self, sessions, mode="kitty"):
        d = os.path.join(self.cfg, "terminal-launcher")
        with open(os.path.join(d, "sessions.json"), "w") as f:
            json.dump(sessions, f, indent=2)
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"open_mode": mode}, f)

    def sessions(self):
        with open(os.path.join(self.cfg, "terminal-launcher", "sessions.json")) as f:
            return json.load(f)

    def start(self, sessions, mode="kitty", scale=1, dark=False):
        self.stop()
        self.config(sessions, mode)
        self.scale = scale
        env = {"PATH": "/usr/bin:/bin", "HOME": self.home, "XDG_CONFIG_HOME": self.cfg,
               "XDG_CACHE_HOME": os.path.join(self.home, ".cache"), "XDG_RUNTIME_DIR": self.runtime,
               "DISPLAY": DISPLAY, "GDK_BACKEND": "x11", "GDK_SCALE": str(scale),
               "GSETTINGS_BACKEND": "memory", "NO_AT_BRIDGE": "1", "GTK_A11Y": "none",
               "GDK_DEBUG": "no-portals", "GIO_USE_VFS": "local", "GTK_USE_PORTAL": "0",
               "LANG": "en_US.UTF-8", "XCURSOR_THEME": "Adwaita", "XCURSOR_SIZE": str(24 * scale)}
        if dark:
            env["ADW_DEBUG_COLOR_SCHEME"] = "prefer-dark"
        self.app = subprocess.Popen(
            ["dbus-run-session", f"--config-file={self.bus_conf}", "--", "python3", APP, "--dry-run"],
            env=env, start_new_session=True, stdout=open(os.path.join(self.work, "app.log"), "a"),
            stderr=subprocess.STDOUT)
        want = (0, 0, WIN_W * scale, WIN_H * scale)
        stable_since, end = None, time.time() + 20
        while time.time() < end:           # GTK may set its own size again just after mapping:
            win = self.wait_window("Terminal Launcher")   # keep putting it back until it stays
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
        self.X.move(*self.p(*PARK))
        time.sleep(1.2)

    def stop(self):
        if self.app and self.app.poll() is None:
            os.killpg(self.app.pid, signal.SIGTERM)
            try:
                self.app.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.app.pid, signal.SIGKILL)
        self.app = None

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

    def place_dialog(self, name, since=None):
        """Move the dialog over the middle of the main window (with no window manager it opens at
        0,0). The frames in between are noted in self.cuts and left out of the recordings."""
        t0 = since or time.time()
        w = self.wait_window(name)
        s = self.scale
        want = (DLG[0] * s, DLG[1] * s, DLG[2] * s, DLG[3] * s)
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
        self.cuts.append((t0, good_since + 0.1))
        return w

    # ---------- input, in the window's logical px ----------
    def p(self, x, y):
        return x * self.scale, y * self.scale

    def glide(self, x, y, t=0.45):
        self.X.glide(*self.p(x, y), duration=t)

    def click(self, x, y, t=0.45, events=None):
        self.glide(x, y, t)
        time.sleep(0.06)
        if events is not None:
            events.append({"t": time.time(), "kind": "click", "x": x, "y": y})
        self.X.click()

    def row(self, i):
        return ROW0 + ROW_H * i

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


def to_gif(src, out, start=0.0, end=None, fps=15, width=None, cuts=()):
    """cuts: [(from, to)] in seconds of the recording, left out (a dialog's jump into place)."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    trim = ["-ss", f"{start:.2f}"] + (["-to", f"{end:.2f}"] if end else [])
    scale = f",scale={width}:-1:flags=lanczos" if width else ""
    drop = "+".join(f"between(t,{a - start:.3f},{b - start:.3f})" for a, b in cuts)
    sel = f"select='not({drop})',setpts=N/FRAME_RATE/TB," if drop else ""
    vf = (f"{sel}fps={fps}{scale},split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
          f"[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y"] + trim + ["-i", src, "-filter_complex", vf,
                    "-loop", "0", out], check=True)
    log(f"wrote {out} ({os.path.getsize(out) / 1e6:.2f} MB)")


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                 "csv=p=0", path], capture_output=True, text=True).stdout)


# ---------- scenes ----------
def scene_stills(r):
    r.start(SESSIONS)
    r.still(os.path.join(IMAGES, "window.png"))
    r.start(SESSIONS, dark=True)
    r.still(os.path.join(IMAGES, "window-dark.png"))
    r.start(SESSIONS)
    r.click(MORE_X, r.row(0))
    r.click(520, r.row(0) + 52, t=0.15)            # ⋮ → Edit…
    r.place_dialog("Edit terminal")
    r.glide(*r.dlg(290, 745), t=0.2)              # a blank spot in the dialog (keeps it focused)
    time.sleep(1.0)
    r.still(os.path.join(IMAGES, "edit.png"), DLG)


def scene_drag(r):
    r.start(SESSIONS)
    r.click(TICK_X, r.row(0), t=0.1)
    r.click(TICK_X, r.row(4), t=0.1)
    r.glide(*PARK, t=0.1)
    time.sleep(0.8)
    mkv = os.path.join(r.work, "drag.mkv")
    rec, _ = r.record(mkv)
    time.sleep(0.9)
    r.glide(HANDLE_X, r.row(0), t=0.6)
    time.sleep(0.15)
    x, y = r.p(HANDLE_X, r.row(0))
    tx, ty = r.p(HANDLE_X, r.row(3) - ROW_H // 2 + 10)   # the upper part of row 3: its line shows below the card
    r.X.drag(x, y, tx, ty, duration=1.8, hold=0.15, settle=0.9)
    time.sleep(0.2)
    r.glide(*PARK, t=0.5)
    time.sleep(1.8)
    r.stop_record(rec)
    after = os.path.join(r.work, "drag-after.png")
    r.still(after)
    order = [s["name"] for s in r.sessions()]
    want = [s["name"] for s in SESSIONS]
    want.insert(3, want.pop(0))
    ok = order == want
    log(f"drag test: saved order {order}")
    log(f"drag test: expected    {want} -> {'OK' if ok else 'WRONG'}")
    to_gif(mkv, os.path.join(IMAGES, "gifs", "drag.gif"))
    if not ok:
        sys.exit("drag test FAILED: the saved order isn't what the drag asked for")
    log(f"drag test: look at {after}: 'Website redesign' (now 4th) and 'Infra notes' should be ticked, "
        "and the header should say 'Add 2 to kitty'")


def scene_tick(r):
    r.start(SESSIONS)
    mkv = os.path.join(r.work, "tick.mkv")
    rec, _ = r.record(mkv)
    time.sleep(0.3)
    for i in (0, 1, 3):
        r.click(TICK_X, r.row(i), t=0.3)
        time.sleep(0.2)
        r.glide(230, r.row(i) + 8, t=0.15)        # onto the row's text: no tooltip there
        time.sleep(0.35)
    time.sleep(0.3)
    for mode in ("tabs", "windows"):
        r.click(*OPEN_IN, t=0.3)
        time.sleep(0.3)
        r.click(MODE_X, MODE_Y[mode], t=0.25)
        r.glide(MODE_X - 60, 160, t=0.15)         # off the row, so no tooltip
        time.sleep(1.1)
    r.glide(*PARK, t=0.4)
    time.sleep(0.5)
    r.stop_record(rec)
    to_gif(mkv, os.path.join(IMAGES, "gifs", "tick-and-open.gif"), start=0.2)


def add_terminal(r, events=None, autostart=False, beat=None):
    """Add → Terminal only → name and folder → (Start when I log in) → Save."""
    r.glide(45, 27)
    clicked = time.time()
    r.click(45, 27, t=0.01, events=events)
    r.glide(250, 120, t=0.12)                       # off the button before its tooltip shows
    r.place_dialog("Add a terminal", since=clicked)
    time.sleep(0.5)
    r.click(*r.dlg(440, 183), events=events)        # Type
    time.sleep(0.35)
    r.click(*r.dlg(485, 346), t=0.35, events=events)   # Terminal only
    time.sleep(0.4)
    r.click(*r.dlg(250, 282), events=events)        # Name
    r.X.type("Server logs", delay=0.04)
    time.sleep(0.2)
    r.click(*r.dlg(250, 337), t=0.3, events=events)    # Folder
    r.X.type("~/Projects/api-server", delay=0.035)
    time.sleep(0.4)
    if autostart:
        if beat:
            beat()
        r.click(*r.dlg(503, 464), events=events)    # Start when I log in
        time.sleep(0.6)
    r.click(*r.dlg(541, 27), events=events)         # Save
    r.glide(*PARK, t=0.4)


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
    log(f"add: saved list now ends with {names[-1]!r}; dialog jump cut at "
        f"{[(round(a - start, 2), round(b - start, 2)) for a, b in r.cuts]} s")
    to_gif(mkv, os.path.join(IMAGES, "gifs", "add.gif"), start=0.2,
           cuts=[(a - start, b - start) for a, b in r.cuts])


def scene_tour(r):
    """One continuous take at 2x for the video; captions and clicks logged to tour.json."""
    r.start(SESSIONS[:7], scale=2)
    mkv = os.path.join(r.work, "tour.mkv")
    events = []
    r.cuts = []

    def beat(text):
        events.append({"t": time.time(), "kind": "beat", "text": text})

    rec, start = r.record(mkv)
    beat("Your terminals and coding agents,|one click away")
    time.sleep(2.6)
    beat("Claude Code, omp, opencode,|a shell, or any command")
    for i in range(7):
        r.glide(420, r.row(i), t=0.32)
    time.sleep(0.3)
    beat("▶ opens one right away")
    r.glide(606, r.row(1), t=0.5)
    time.sleep(2.4)                                   # its tooltip says where it opens
    beat("Tick a few,|open them together")
    for i in (0, 1, 3):
        r.click(TICK_X, r.row(i), t=0.4, events=events)
        time.sleep(0.2)
        r.glide(230, r.row(i) + 8, t=0.15)        # onto the row's text: no tooltip there
        time.sleep(0.3)
    r.glide(510, 60, t=0.5)
    time.sleep(0.9)
    beat("In kitty tabs, one window,|or separate windows")
    for mode in ("tabs", "windows", "kitty"):
        r.click(*OPEN_IN, t=0.4, events=events)
        time.sleep(0.35)
        r.click(MODE_X, MODE_Y[mode], t=0.3, events=events)
        r.glide(MODE_X - 60, 160, t=0.2)
        time.sleep(0.9)
    beat("Add one in seconds")
    add_terminal(r, events, autostart=True, beat=lambda: beat("Start it when you log in"))
    time.sleep(2.0)
    beat("Drag to change the order")
    x, y = r.p(HANDLE_X, r.row(7))
    tx, ty = r.p(HANDLE_X, r.row(2) - ROW_H // 2 + 10)
    r.glide(HANDLE_X, r.row(7), t=0.5)
    time.sleep(0.1)
    events.append({"t": time.time(), "kind": "drag"})
    r.X.drag(x, y, tx, ty, duration=1.6, hold=0.15, settle=0.8)
    time.sleep(0.2)
    r.glide(*PARK, t=0.5)
    time.sleep(1.6)
    events.append({"t": time.time(), "kind": "end"})
    r.stop_record(rec)
    for e in events:
        e["t"] = round(e["t"] - start, 3)
    events += [{"t": round(a - start, 3), "kind": "hold", "until": round(b - start, 3)} for a, b in r.cuts]
    with open(os.path.join(r.work, "tour.json"), "w") as f:
        json.dump({"video": "tour.mkv", "scale": 2, "window": [WIN_W, WIN_H], "events": events}, f, indent=1)
    log(f"wrote {mkv} ({duration(mkv):.1f} s) and tour.json; saved order: {[s['name'] for s in r.sessions()]}")
    r.scale = 2
    r.still(os.path.join(r.work, "window-2x.png"))


SCENES = {"stills": scene_stills, "drag": scene_drag, "tick": scene_tick, "add": scene_add, "tour": scene_tour}


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        sys.exit(__doc__)
    names = sys.argv[2:] or list(SCENES)
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
