#!/usr/bin/env python3
"""xcompositor.py: a tiny compositing manager for a HIDDEN Xvfb display, so GTK 4 popovers, menus,
tooltips and the drag icon get their see-through corners instead of black boxes in recordings.
Used by tools/capture_media.py; no window manager, no effects, no shadows of its own.

    python3 tools/xcompositor.py :98 [#RRGGBB]     runs until killed; the colour is the background

Every top-level window is redirected off screen (XComposite) and drawn onto the overlay window
about 60 times a second with XRender, windows with an alpha channel blended "over" the ones below.
It doesn't claim _NET_WM_CM_S0, so GTK keeps drawing its windows square, with no shadow margins:
the crops in capture_media.py stay at the window's own size."""
import ctypes
import signal
import sys
import time

if len(sys.argv) < 2 or not sys.argv[1].startswith(":") or sys.argv[1] in (":0", ":1"):
    sys.exit(__doc__)

x11 = ctypes.CDLL("libX11.so.6")
xcomp = ctypes.CDLL("libXcomposite.so.1")
xfix = ctypes.CDLL("libXfixes.so.3")
xr = ctypes.CDLL("libXrender.so.1")
VP, UL, I = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int


class Attrs(ctypes.Structure):   # XWindowAttributes
    _fields_ = [("x", I), ("y", I), ("width", I), ("height", I), ("border_width", I), ("depth", I),
                ("visual", VP), ("root", UL), ("c_class", I), ("bit_gravity", I), ("win_gravity", I),
                ("backing_store", I), ("backing_planes", UL), ("backing_pixel", UL), ("save_under", I),
                ("colormap", UL), ("map_installed", I), ("map_state", I), ("all_event_masks", ctypes.c_long),
                ("your_event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", I), ("screen", VP)]


class Direct(ctypes.Structure):
    _fields_ = [(n, ctypes.c_short) for n in ("red", "redMask", "green", "greenMask", "blue", "blueMask",
                                              "alpha", "alphaMask")]


class PictFormat(ctypes.Structure):
    _fields_ = [("id", UL), ("type", I), ("depth", I), ("direct", Direct), ("colormap", UL)]


class PictAttrs(ctypes.Structure):
    _fields_ = [("repeat", I), ("alpha_map", UL), ("alpha_x_origin", I), ("alpha_y_origin", I),
                ("clip_x_origin", I), ("clip_y_origin", I), ("clip_mask", UL), ("graphics_exposures", I),
                ("subwindow_mode", I), ("poly_edge", I), ("poly_mode", I), ("dither", UL),
                ("component_alpha", I)]


class Color(ctypes.Structure):
    _fields_ = [("red", ctypes.c_ushort), ("green", ctypes.c_ushort), ("blue", ctypes.c_ushort),
                ("alpha", ctypes.c_ushort)]


def sig(lib, name, res, *args):
    f = getattr(lib, name)
    f.restype, f.argtypes = res, list(args)
    return f


XOpenDisplay = sig(x11, "XOpenDisplay", VP, ctypes.c_char_p)
XDefaultRootWindow = sig(x11, "XDefaultRootWindow", UL, VP)
XDefaultScreen = sig(x11, "XDefaultScreen", I, VP)
XDefaultVisual = sig(x11, "XDefaultVisual", VP, VP, I)
XDisplayWidth = sig(x11, "XDisplayWidth", I, VP, I)
XDisplayHeight = sig(x11, "XDisplayHeight", I, VP, I)
XQueryTree = sig(x11, "XQueryTree", I, VP, UL, ctypes.POINTER(UL), ctypes.POINTER(UL),
                 ctypes.POINTER(ctypes.POINTER(UL)), ctypes.POINTER(ctypes.c_uint))
XGetWindowAttributes = sig(x11, "XGetWindowAttributes", I, VP, UL, ctypes.POINTER(Attrs))
XFree = sig(x11, "XFree", I, VP)
XFlush = sig(x11, "XFlush", I, VP)
XSync = sig(x11, "XSync", I, VP, I)
XCreatePixmap = sig(x11, "XCreatePixmap", UL, VP, UL, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint)
ERRH = ctypes.CFUNCTYPE(I, VP, VP)
XSetErrorHandler = sig(x11, "XSetErrorHandler", VP, ERRH)
XCompositeRedirectSubwindows = sig(xcomp, "XCompositeRedirectSubwindows", None, VP, UL, I)
XCompositeGetOverlayWindow = sig(xcomp, "XCompositeGetOverlayWindow", UL, VP, UL)
XFixesCreateRegion = sig(xfix, "XFixesCreateRegion", UL, VP, VP, I)
XFixesSetWindowShapeRegion = sig(xfix, "XFixesSetWindowShapeRegion", None, VP, UL, I, I, I, UL)
XRenderFindVisualFormat = sig(xr, "XRenderFindVisualFormat", ctypes.POINTER(PictFormat), VP, VP)
XRenderFindStandardFormat = sig(xr, "XRenderFindStandardFormat", ctypes.POINTER(PictFormat), VP, I)
XRenderCreatePicture = sig(xr, "XRenderCreatePicture", UL, VP, UL, ctypes.POINTER(PictFormat), UL,
                           ctypes.POINTER(PictAttrs))
XRenderFreePicture = sig(xr, "XRenderFreePicture", None, VP, UL)
XRenderComposite = sig(xr, "XRenderComposite", None, VP, I, UL, UL, UL, I, I, I, I, I, I, ctypes.c_uint,
                       ctypes.c_uint)
XRenderFillRectangle = sig(xr, "XRenderFillRectangle", None, VP, I, UL, ctypes.POINTER(Color), I, I,
                           ctypes.c_uint, ctypes.c_uint)

PICT_OP_SRC, PICT_OP_OVER = 1, 3
CP_SUBWINDOW_MODE, INCLUDE_INFERIORS = 1 << 8, 1
SHAPE_INPUT, IS_VIEWABLE, REDIRECT_MANUAL, PICT_STANDARD_RGB24 = 2, 2, 1, 1


@ERRH
def _ignore(_d, _e):   # windows come and go between listing and drawing: ignore BadWindow & co.
    return 0


def main():
    d = XOpenDisplay(sys.argv[1].encode())
    if not d:
        sys.exit(f"xcompositor.py: cannot open display {sys.argv[1]}")
    XSetErrorHandler(_ignore)
    hexc = (sys.argv[2] if len(sys.argv) > 2 else "#202024").lstrip("#")
    bg = Color(*(int(hexc[i:i + 2], 16) * 257 for i in (0, 2, 4)), 0xFFFF)
    scr = XDefaultScreen(d)
    root = XDefaultRootWindow(d)
    W, H = XDisplayWidth(d, scr), XDisplayHeight(d, scr)
    XCompositeRedirectSubwindows(d, root, REDIRECT_MANUAL)
    overlay = XCompositeGetOverlayWindow(d, root)
    empty = XFixesCreateRegion(d, None, 0)
    XFixesSetWindowShapeRegion(d, overlay, SHAPE_INPUT, 0, 0, empty)   # clicks go through it
    root_fmt = XRenderFindVisualFormat(d, XDefaultVisual(d, scr))
    pa = PictAttrs(subwindow_mode=INCLUDE_INFERIORS)
    out = XRenderCreatePicture(d, overlay, root_fmt, CP_SUBWINDOW_MODE, ctypes.byref(pa))
    back_pm = XCreatePixmap(d, root, W, H, 24)
    back = XRenderCreatePicture(d, back_pm, XRenderFindStandardFormat(d, PICT_STANDARD_RGB24), 0, None)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    fmts = {}
    while True:
        t0 = time.monotonic()
        r, p = UL(), UL()
        kids, n = ctypes.POINTER(UL)(), ctypes.c_uint()
        XRenderFillRectangle(d, PICT_OP_SRC, back, ctypes.byref(bg), 0, 0, W, H)
        if XQueryTree(d, root, ctypes.byref(r), ctypes.byref(p), ctypes.byref(kids), ctypes.byref(n)):
            for i in range(n.value):
                w = kids[i]
                if w == overlay:
                    continue
                a = Attrs()
                if not XGetWindowAttributes(d, w, ctypes.byref(a)) or a.map_state != IS_VIEWABLE:
                    continue
                key = a.visual
                if key not in fmts:
                    f = XRenderFindVisualFormat(d, a.visual)
                    fmts[key] = (f, bool(f) and f.contents.direct.alphaMask != 0)
                f, alpha = fmts[key]
                if not f:
                    continue
                pic = XRenderCreatePicture(d, w, f, CP_SUBWINDOW_MODE, ctypes.byref(pa))
                bw = a.border_width
                XRenderComposite(d, PICT_OP_OVER if alpha else PICT_OP_SRC, pic, 0, back, 0, 0, 0, 0,
                                 a.x, a.y, a.width + 2 * bw, a.height + 2 * bw)
                XRenderFreePicture(d, pic)
            if kids:
                XFree(kids)
        XRenderComposite(d, PICT_OP_SRC, back, 0, out, 0, 0, 0, 0, 0, 0, W, H)
        XSync(d, 0)
        time.sleep(max(0.0, 1 / 60 - (time.monotonic() - t0)))


if __name__ == "__main__":
    main()
