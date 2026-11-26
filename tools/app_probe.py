#!/usr/bin/env python3
"""app_probe.py: run Terminal Launcher for tools/capture_media.py, always with --dry-run, and tell the
recorder two things a screen grab can't: which sounds the app asks for (and when), and where every
widget is. capture_media.py starts it inside its throwaway HOME on the hidden Xvfb display; don't run
it by hand against your own list.

    python3 tools/app_probe.py PROBE_DIR [more app args]

PROBE_DIR/sounds.jsonl   one line per sound the app would play: {"t": unix time, "name": "star"}.
                         (--dry-run keeps the app silent; this only notes the call.)
PROBE_DIR/cat.jsonl      one line per move the cat starts (its own idle blinks and swishes too):
                         {"t": unix time, "move": "blink"}, so a recording can be trimmed around one.
PROBE_DIR/layout.json    written whenever PROBE_DIR/request appears (the request is then removed):
                         every mapped widget of every window and open popover, with its box in
                         logical px inside its window (x, y, w, h), its type, text, tooltip, icon,
                         CSS classes, and the row and group around it. capture_media.py finds its
                         click targets in it, so a new layout needs no new coordinates. Its "cat"
                         part says where Byte is: spot (floor, margin, row, peek, away), x and y on
                         the window's stage, whether it's walking, and its and its bubble's boxes.
PROBE_DIR/cmd            a command for Byte, run on the app's own main loop (then removed; the
                         answer goes to PROBE_DIR/cmd.out). Only the cat's own methods, so a scene
                         can make it walk somewhere at a chosen moment:
                           {"op": "hold", "seed": 7}   seed its random choices and put off its own
                                                       wandering and chatter (blinks go on), and sit
                                                       it on the floor again the app's own way (the
                                                       recorder resizes the window after the app
                                                       first placed it); "clock": "10:00" (the
                                                       default) pins the hour Byte sees (the date
                                                       stays today's) and it greets again for that
                                                       hour, so the media never depends on when it
                                                       was captured (Byte naps 23:00-06:00)
                           {"op": "wander", "kind": "margin", "side": "right"}   Cat.wander()
                           {"op": "come_back", "side": "left"}                   Cat.come_back()
                           {"op": "visit", "row": "Docs site"}                   Cat.visit(that row)
                           {"op": "go", "x": 300, "y": 880, "spot": "floor"}     Cat.go()

Safety: it insists on the throwaway home (TL_PROBE_HOME must be set and equal HOME), always passes
--dry-run, and swaps the app's press_move_shortcut (the /dev/uinput virtual keyboard behind
"Move to monitor") for a stub that only logs, before any window exists. So no key press can ever
reach the real desktop, whatever the settings say.
Needs python3-gi with GTK 4 and libadwaita (the app's own needs)."""
import json
import os
import runpy
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "terminal-launcher.py")

if len(sys.argv) < 2:
    sys.exit(__doc__)
PROBE = os.path.abspath(sys.argv[1])
if not os.environ.get("TL_PROBE_HOME") or os.environ.get("TL_PROBE_HOME") != os.environ.get("HOME"):
    sys.exit("app_probe.py: only runs inside capture_media.py's throwaway HOME (TL_PROBE_HOME)")
os.makedirs(PROBE, exist_ok=True)
SOUNDS = os.path.join(PROBE, "sounds.jsonl")
CAT = os.path.join(PROBE, "cat.jsonl")
LAYOUT = os.path.join(PROBE, "layout.json")
REQUEST = os.path.join(PROBE, "request")
COMMAND = os.path.join(PROBE, "cmd")
sys.argv = [APP, "--dry-run"] + [a for a in sys.argv[2:] if a != "--dry-run"]


def note(line):
    with open(os.path.join(PROBE, "probe.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + line + "\n")


# ---------- patching the app's module before its window exists ----------
APP_GLOBALS = {}


def _dry_or_die():
    """The app sets DRY_RUN in its main block (after the patch): check it once it's running."""
    if APP_GLOBALS.get("DRY_RUN") is not True:
        note("refusing: the app isn't in --dry-run")
        os._exit(3)


def _patch(g):
    APP_GLOBALS.update(g=g)
    APP_GLOBALS["DRY_RUN"] = None
    real_play = g["play_sound"]

    def play_sound(name):
        APP_GLOBALS["DRY_RUN"] = g.get("DRY_RUN")
        _dry_or_die()
        with open(SOUNDS, "a") as f:
            f.write(json.dumps({"t": time.time(), "name": name}) + "\n")
        return real_play(name)          # returns at once under --dry-run

    def press_move_shortcut(direction):
        note(f"blocked a Move to monitor key press ({direction})")
        return None

    g["play_sound"] = play_sound
    g["press_move_shortcut"] = press_move_shortcut
    cat = g.get("Cat")
    if cat is not None and hasattr(cat, "play"):
        real_move = cat.play

        def play(self, kind, length):
            with open(CAT, "a") as f:
                f.write(json.dumps({"t": time.time(), "move": kind, "length": length}) + "\n")
            return real_move(self, kind, length)
        cat.play = play
    note("patched play_sound, press_move_shortcut" + (" and Cat.play" if cat is not None else ""))


def _hook(frame, event, _arg):
    """A profile hook that waits for the first call into the app after both functions and the
    Window class are defined (a class body counts as a call; Cat comes before Window), patches
    them, and removes itself."""
    if event == "call":
        g = frame.f_globals
        if g.get("__file__") == APP and "press_move_shortcut" in g and "play_sound" in g and "Window" in g:
            sys.setprofile(None)
            _patch(g)


# ---------- the layout dump ----------
def _layout():
    from gi.repository import Adw, Gtk   # the app has chosen GTK 4 / Adw 1 by now

    out = []

    def text_of(w):
        for getter in ("get_label", "get_text"):
            if isinstance(w, (Gtk.Label, Gtk.Button, Gtk.MenuButton, Adw.ButtonContent)) and hasattr(w, getter):
                try:
                    t = getattr(w, getter)()
                except TypeError:
                    continue
                if t:
                    return t
        return None

    def icon_of(w):
        if hasattr(w, "get_icon_name"):
            try:
                return w.get_icon_name()
            except TypeError:
                return None
        return None

    def walk(w, native, base, win_title, pop, anc, row, group):
        if not w.get_mapped():
            return
        if isinstance(w, Gtk.Popover) and w is not native:
            surf = w.get_surface()
            try:
                px, py = surf.get_position_x(), surf.get_position_y()   # relative to the parent's surface
            except AttributeError:
                px = py = 0
            base = (base[0] + px, base[1] + py)
            native, pop = w, True
        tname = type(w).__gtype__.name
        if isinstance(w, Adw.PreferencesRow):
            row = w.get_title()
        if isinstance(w, Adw.PreferencesGroup):
            group = w.get_title()
        ok, r = w.compute_bounds(native)
        if ok and r.get_width() > 0 and r.get_height() > 0:
            sx, sy = native.get_surface_transform()
            e = {"win": win_title, "pop": pop, "type": tname,
                 "box": [round(base[0] + sx + r.get_x(), 1), round(base[1] + sy + r.get_y(), 1),
                         round(r.get_width(), 1), round(r.get_height(), 1)],
                 "anc": anc[-6:], "row": row, "group": group, "css": list(w.get_css_classes())}
            if isinstance(w, Adw.PreferencesRow):
                e["title"] = w.get_title()
            t, i, tip = text_of(w), icon_of(w), w.get_tooltip_text()
            if t:
                e["text"] = t
            if i:
                e["icon"] = i
            if tip:
                e["tooltip"] = tip
            if isinstance(w, (Gtk.CheckButton, Gtk.ToggleButton, Gtk.Switch)):
                e["active"] = w.get_active()
            out.append(e)
        c = w.get_first_child()
        while c:
            walk(c, native, base, win_title, pop, anc + [tname], row, group)
            c = c.get_next_sibling()

    cat = None
    for win in Gtk.Window.list_toplevels():
        if win.get_mapped():
            walk(win, win, (0.0, 0.0), win.get_title() or "", False, [], None, None)
            c = getattr(win, "cat", None)
            if c is not None and hasattr(c, "spot"):
                def box(widget):
                    if not widget.get_mapped():
                        return None
                    ok, r = widget.compute_bounds(win)
                    sx, sy = win.get_surface_transform()
                    return [round(sx + r.get_x(), 1), round(sy + r.get_y(), 1),
                            round(r.get_width(), 1), round(r.get_height(), 1)] if ok else None
                cat = {"spot": c.spot, "x": round(c.x, 1), "y": round(c.y, 1), "walking": bool(c.target),
                       "facing": c.facing, "area": box(c.area), "bubble": box(c.bubble),
                       "bubble_shown": c.bubble.get_visible() and not c.bubble.has_css_class("faded"),
                       "says": c.label.get_label(), "stage": [c.size()[0], c.size()[1]]}
    tmp = LAYOUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"t": time.time(), "widgets": out, "cat": cat}, f)
    os.replace(tmp, LAYOUT)


def _cat():
    from gi.repository import Gtk
    for win in Gtk.Window.list_toplevels():
        c = getattr(win, "cat", None)
        if c is not None and hasattr(c, "wander"):
            return win, c
    raise RuntimeError("no window with a cat")


def _command(cmd):
    """Run one command for Byte (only its own methods). Returns a short answer."""
    import random
    win, c = _cat()
    op = cmd.get("op")
    if op == "hold":
        c.rng = random.Random(cmd.get("seed", 7))
        clock = cmd.get("clock", "10:00")
        if clock:
            from datetime import datetime
            hh, mm = (int(v) for v in clock.split(":"))
            c.now = lambda: datetime.now().replace(hour=hh, minute=mm)
            c.say(APP_GLOBALS["g"]["cat_line"]("greeting", c.now()), 12)
        c.next_move = c.next_talk = time.monotonic() + 1e6
        c.placed = False                 # Cat._place() puts it on the floor, left, as at startup
        c._relayout()
        return "held"
    if op == "wander":
        c.wander(cmd.get("kind"), side=cmd.get("side"))
    elif op == "come_back":
        c.come_back(side=cmd.get("side"))
    elif op == "visit":
        rows = [r for r in getattr(win, "rows", []) if r.get_title() == cmd["row"]]
        if not rows:
            raise RuntimeError(f"no row {cmd['row']!r}")
        c.visit(rows[0])
    elif op == "go":
        c.go(float(cmd["x"]), float(cmd["y"]), cmd.get("spot", "floor"))
    else:
        raise RuntimeError(f"unknown op {op!r}")
    return f"{op}: spot {c.spot}, target {c.target}"


def _poll():
    if "g" in APP_GLOBALS:
        APP_GLOBALS["DRY_RUN"] = APP_GLOBALS["g"].get("DRY_RUN")
    _dry_or_die()
    if os.path.exists(REQUEST):
        try:
            _layout()
        except Exception as ex:   # noqa: BLE001  (keep the app running; tell the recorder instead)
            note(f"layout failed: {ex!r}")
            with open(LAYOUT + ".tmp", "w") as f:
                json.dump({"t": time.time(), "error": repr(ex)}, f)
            os.replace(LAYOUT + ".tmp", LAYOUT)
        try:
            os.remove(REQUEST)
        except OSError:
            pass
    if os.path.exists(COMMAND):
        try:
            with open(COMMAND) as f:
                answer = {"ok": _command(json.load(f))}
        except Exception as ex:   # noqa: BLE001
            note(f"command failed: {ex!r}")
            answer = {"error": repr(ex)}
        os.remove(COMMAND)     # first, so a next command written after the reply isn't removed unread
        with open(COMMAND + ".out.tmp", "w") as f:
            json.dump(answer, f)
        os.replace(COMMAND + ".out.tmp", COMMAND + ".out")
    return True


if __name__ == "__main__":
    from gi.repository import GLib   # no version to choose for GLib; the app picks GTK 4 itself

    GLib.timeout_add(80, _poll)
    sys.setprofile(_hook)
    runpy.run_path(APP, run_name="__main__")
