#!/usr/bin/env python3
"""xinput.py: move the pointer, click, drag and type on a HIDDEN X display (Xvfb) with XTest, and
list or move its top-level windows with Xlib. Used by tools/capture_media.py to drive Terminal
Launcher for the README media. Refuses to touch the display you're sitting at.

    python3 tools/xinput.py :98 windows                    list top-level windows (id, x, y, w, h, name)
    python3 tools/xinput.py :98 click X Y                  move there and click
    python3 tools/xinput.py :98 drag X1 Y1 X2 Y2           press, move in small steps, release
    python3 tools/xinput.py :98 type "Some text"           type text (US keyboard layout)
    python3 tools/xinput.py :98 key ctrl+n Return          press keys (X keysym names joined by +)

Or import it: `X = xinput.Display(":98")`, then X.click(x, y), X.drag(...), X.type("..."), ...
On Xvfb with no window manager, keys go to the window under the pointer."""
import ctypes
import os
import sys
import time

_x11 = ctypes.CDLL("libX11.so.6")
_xtst = ctypes.CDLL("libXtst.so.6")
_x11.XOpenDisplay.restype = ctypes.c_void_p
_x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
_x11.XDefaultRootWindow.restype = ctypes.c_ulong
_x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
_x11.XStringToKeysym.restype = ctypes.c_ulong
_x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
_x11.XKeysymToKeycode.restype = ctypes.c_ubyte
_x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_x11.XKeycodeToKeysym.restype = ctypes.c_ulong
_x11.XKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_int]
_x11.XFlush.argtypes = [ctypes.c_void_p]
_x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
_x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
_x11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                            ctypes.POINTER(ctypes.c_uint)]
_x11.XFree.argtypes = [ctypes.c_void_p]
_x11.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)]
_x11.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
_x11.XMoveResizeWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint, ctypes.c_uint]
_xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
_xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
_xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]


class _Attrs(ctypes.Structure):   # XWindowAttributes
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int), ("height", ctypes.c_int),
                ("border_width", ctypes.c_int), ("depth", ctypes.c_int), ("visual", ctypes.c_void_p),
                ("root", ctypes.c_ulong), ("c_class", ctypes.c_int), ("bit_gravity", ctypes.c_int),
                ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
                ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
                ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
                ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int),
                ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
                ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", ctypes.c_int),
                ("screen", ctypes.c_void_p)]


_x11.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_Attrs)]

KEYSYMS = {" ": "space", "~": "asciitilde", "/": "slash", "-": "minus", "_": "underscore",
           ".": "period", ",": "comma", ":": "colon", "'": "apostrophe", "&": "ampersand"}
ALIAS = {"ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L", "enter": "Return",
         "esc": "Escape", "tab": "Tab", "backspace": "BackSpace"}


class Display:
    def __init__(self, name):
        real = {os.environ.get("REAL_DISPLAY", ""), ":0", ":1"}
        if not name.startswith(":") or name in real:
            sys.exit(f"xinput.py: refusing display {name!r}: use the hidden Xvfb display (e.g. :98)")
        self.d = _x11.XOpenDisplay(name.encode())
        if not self.d:
            sys.exit(f"xinput.py: cannot open display {name}")
        self.root = _x11.XDefaultRootWindow(self.d)
        self.pos = (0, 0)

    def flush(self):
        _x11.XSync(self.d, 0)

    # ---------- pointer ----------
    def move(self, x, y):
        _xtst.XTestFakeMotionEvent(self.d, -1, int(x), int(y), 0)
        self.flush()
        self.pos = (int(x), int(y))

    def glide(self, x, y, duration=0.4, steps=None):
        """Move the pointer smoothly (eased) so a recording shows it travel."""
        x0, y0 = self.pos
        steps = steps or max(2, int(duration * 60))
        for k in range(1, steps + 1):
            u = k / steps
            u = u * u * (3 - 2 * u)
            self.move(x0 + (x - x0) * u, y0 + (y - y0) * u)
            time.sleep(duration / steps)

    def button(self, down, b=1):
        _xtst.XTestFakeButtonEvent(self.d, b, bool(down), 0)
        self.flush()

    def click(self, x=None, y=None, glide=0.0):
        if x is not None:
            (self.glide(x, y, glide) if glide else self.move(x, y))
            time.sleep(0.05)
        self.button(True)
        time.sleep(0.06)
        self.button(False)

    def drag(self, x1, y1, x2, y2, duration=1.2, hold=0.25, settle=0.4):
        """Press at (x1, y1), move to (x2, y2) in many small steps, release: a real drag.
        Returns the time of the release (time.time()), when a drop happens."""
        self.move(x1, y1)
        time.sleep(0.15)
        self.button(True)
        time.sleep(hold)
        for k in range(1, 7):                      # first a few pixels, past GTK's drag threshold
            self.move(x1, y1 + (3 * k if y2 >= y1 else -3 * k))
            time.sleep(0.03)
        sx, sy = self.pos
        steps = max(10, int(duration * 60))
        for k in range(1, steps + 1):
            u = k / steps
            u = u * u * (3 - 2 * u)
            self.move(sx + (x2 - sx) * u, sy + (y2 - sy) * u)
            time.sleep(duration / steps)
        time.sleep(settle)
        self.button(False)
        return time.time()

    # ---------- keyboard ----------
    def _code(self, name):
        sym = _x11.XStringToKeysym(ALIAS.get(name.lower(), name).encode())
        kc = _x11.XKeysymToKeycode(self.d, sym) if sym else 0
        if not kc:
            raise ValueError(f"unknown key: {name}")
        return kc, sym

    def key(self, combo):
        parts = [self._code(p)[0] for p in combo.split("+")]
        for kc in parts:
            _xtst.XTestFakeKeyEvent(self.d, kc, True, 0)
            self.flush()
            time.sleep(0.02)
        for kc in reversed(parts):
            _xtst.XTestFakeKeyEvent(self.d, kc, False, 0)
            self.flush()
            time.sleep(0.02)

    def type(self, text, delay=0.06):
        shift = self._code("Shift_L")[0]
        for ch in text:
            kc, sym = self._code(KEYSYMS.get(ch, ch))
            shifted = _x11.XKeycodeToKeysym(self.d, kc, 0) != sym
            if shifted:
                _xtst.XTestFakeKeyEvent(self.d, shift, True, 0)
            _xtst.XTestFakeKeyEvent(self.d, kc, True, 0)
            _xtst.XTestFakeKeyEvent(self.d, kc, False, 0)
            if shifted:
                _xtst.XTestFakeKeyEvent(self.d, shift, False, 0)
            self.flush()
            time.sleep(delay)

    # ---------- windows ----------
    def windows(self):
        """Mapped top-level windows, bottom to top: [(id, x, y, w, h, name)]."""
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        kids, n = ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
        _x11.XQueryTree(self.d, self.root, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(kids),
                        ctypes.byref(n))
        out = []
        for i in range(n.value):
            w = kids[i]
            a = _Attrs()
            _x11.XGetWindowAttributes(self.d, w, ctypes.byref(a))
            if a.map_state != 2 or a.width < 50 or a.height < 50:   # IsViewable, skip tiny helpers
                continue
            name = ctypes.c_char_p()
            _x11.XFetchName(self.d, w, ctypes.byref(name))
            out.append((w, a.x, a.y, a.width, a.height, (name.value or b"").decode(errors="replace"),
                        bool(a.override_redirect)))
        if kids:
            _x11.XFree(kids)
        return out

    def place(self, win, x, y, w=None, h=None):
        if w:
            _x11.XMoveResizeWindow(self.d, win, int(x), int(y), int(w), int(h))
        else:
            _x11.XMoveWindow(self.d, win, int(x), int(y))
        self.flush()

    def root_cursor(self, name="left_ptr", theme="Adwaita", size=24):
        """Give the bare Xvfb root window a normal arrow (instead of X's default cross), so a
        recording shows the usual pointer wherever no app sets its own."""
        os.environ.setdefault("XCURSOR_THEME", theme)
        os.environ.setdefault("XCURSOR_SIZE", str(size))
        xc = ctypes.CDLL("libXcursor.so.1")
        xc.XcursorLibraryLoadCursor.restype = ctypes.c_ulong
        xc.XcursorLibraryLoadCursor.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        xc.XcursorSetTheme.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        xc.XcursorSetDefaultSize.argtypes = [ctypes.c_void_p, ctypes.c_int]
        xc.XcursorSetTheme(self.d, theme.encode())
        xc.XcursorSetDefaultSize(self.d, size)
        cur = xc.XcursorLibraryLoadCursor(self.d, name.encode())
        _x11.XDefineCursor.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        _x11.XDefineCursor(self.d, self.root, cur)
        self.flush()

    def close(self):
        _x11.XCloseDisplay(self.d)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    X = Display(sys.argv[1])
    cmd, rest = sys.argv[2], sys.argv[3:]
    if cmd == "windows":
        for w in X.windows():
            print(*w)
    elif cmd == "click":
        X.click(int(rest[0]), int(rest[1]))
    elif cmd == "move":
        X.move(int(rest[0]), int(rest[1]))
    elif cmd == "drag":
        X.drag(*map(int, rest[:4]))
    elif cmd == "type":
        X.type(" ".join(rest))
    elif cmd == "key":
        for c in rest:
            X.key(c)
            time.sleep(0.05)
    else:
        sys.exit(__doc__)
    X.close()
