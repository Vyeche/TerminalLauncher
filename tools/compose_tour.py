#!/usr/bin/env python3
"""compose_tour.py: turn the take that capture_media.py recorded (WORK_DIR/tour.mkv + tour.json)
into a vertical 1080x1920 video for YouTube Shorts: the app window in the middle, a caption for each
step above it, a ring where each click lands, and an end card. No music, no audio track.

    python3 tools/compose_tour.py WORK_DIR OUT.mp4 [--thumb THUMB.png]
    python3 tools/compose_tour.py WORK_DIR --preview 3 12.5 38      (a few frames as PNGs in WORK_DIR)

Writes H.264 (yuv420p, 30 fps, faststart). While a dialog jumps into place (the "hold" events in
tour.json) the frame before it is held, so the jump never shows.
Needs ffmpeg, numpy, pycairo and librsvg's GObject bindings (gir1.2-rsvg-2.0) for the icon."""
import argparse
import json
import math
import os
import subprocess
import sys

import cairo
import gi
import numpy as np

gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(REPO, "assets", "terminal-launcher.svg")
FPS = 30
W, H = 1080, 1920
FONT = "Open Sans"
END_CARD = 3.6
URL = "github.com/Vyeche/TerminalLauncher"

APP_W = 1000                     # the window, scaled from the 2x take
APP_X, APP_Y = (W - APP_W) // 2, 470
BRAND_Y = 150
CAPTION_Y = 270

BG = (0x17 / 255, 0x14 / 255, 0x1F / 255)          # the icon's dark purple
ORANGE = (0xFF / 255, 0x9A / 255, 0x3C / 255)      # the icon's prompt
PURPLE = (0x8B / 255, 0x6C / 255, 0xFF / 255)      # the icon's cursor
TEXT = (0.97, 0.96, 0.99)
SOFT = (0.74, 0.71, 0.80)


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def font(cr, size, bold=True):
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)


def centered(cr, text, y, colour=TEXT, alpha=1.0):
    """One line, centred. A leading "▶" (not in Open Sans) is drawn as a triangle in the accent colour."""
    play = text.startswith("▶")
    if play:
        text = text[1:]
    size = cr.get_font_matrix().xx
    tri = size * 0.62 if play else 0
    x = (W - cr.text_extents(text).x_advance - tri) / 2
    if play:
        h = size * 0.66
        for dx, dy, c, a in ((2, 3, (0, 0, 0), 0.5), (0, 0, ORANGE, 1.0)):
            cr.set_source_rgba(*c, a * alpha)
            cr.move_to(x + dx, y + dy - h)
            cr.line_to(x + dx + tri * 0.8, y + dy - h / 2)
            cr.line_to(x + dx, y + dy)
            cr.close_path()
            cr.fill()
        x += tri
    cr.set_source_rgba(0, 0, 0, 0.5 * alpha)
    cr.move_to(x + 2, y + 3)
    cr.show_text(text)
    cr.set_source_rgba(*colour, alpha)
    cr.move_to(x, y)
    cr.show_text(text)


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def icon(cr, x, y, size, alpha=1.0):
    h = Rsvg.Handle.new_from_file(ICON)
    cr.save()
    cr.push_group()
    r = Rsvg.Rectangle()
    r.x, r.y, r.width, r.height = x, y, size, size
    h.render_document(cr, r)
    cr.pop_group_to_source()
    cr.paint_with_alpha(alpha)
    cr.restore()


def background():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    glow = np.exp(-(((xx - W / 2) / 650) ** 2 + ((yy - 1000) / 700) ** 2))
    rgb = np.stack([BG[i] + glow * (0.10 * PURPLE[i]) for i in range(3)], -1)
    out = np.empty((H, W, 4), np.uint8)
    out[..., :3] = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)[..., ::-1]
    out[..., 3] = 255
    return out


class Take:
    def __init__(self, work):
        with open(os.path.join(work, "tour.json")) as f:
            self.meta = json.load(f)
        self.path = os.path.join(work, self.meta["video"])
        lw, lh = self.meta["window"]
        self.scale = self.meta["scale"]
        self.app_h = round(APP_W * lh / lw)
        self.k = APP_W / lw                       # output px per logical px
        ev = self.meta["events"]
        self.end = next(e["t"] for e in ev if e["kind"] == "end")
        self.beats = [e for e in ev if e["kind"] == "beat"]
        self.clicks = [e for e in ev if e["kind"] == "click"]
        self.holds = [(e["t"], e["until"]) for e in ev if e["kind"] == "hold"]

    def reader(self, start=0.0, length=None):
        args = ["ffmpeg", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", self.path]
        if length:
            args += ["-t", f"{length:.3f}"]
        args += ["-vf", f"fps={FPS},scale={APP_W}:{self.app_h}:flags=lanczos", "-pix_fmt", "bgra",
                 "-f", "rawvideo", "-"]
        return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def frame_at(self, t):
        p = self.reader(t, 0.2)
        buf = p.stdout.read(APP_W * self.app_h * 4)
        p.stdout.close()
        p.wait()
        return np.frombuffer(buf, np.uint8).reshape(self.app_h, APP_W, 4).copy()


def draw_window(cr, take, frame):
    ah = take.app_h
    for i in range(14, 0, -1):                     # a soft shadow
        cr.set_source_rgba(0, 0, 0, 0.022)
        rounded(cr, APP_X - i, APP_Y - i + 10, APP_W + 2 * i, ah + 2 * i, 16 + i)
        cr.fill()
    img = cairo.ImageSurface.create_for_data(memoryview(frame), cairo.FORMAT_RGB24, APP_W, ah, APP_W * 4)
    cr.save()
    rounded(cr, APP_X, APP_Y, APP_W, ah, 14)
    cr.clip()
    cr.set_source_surface(img, APP_X, APP_Y)
    cr.paint()
    cr.restore()


def draw_clicks(cr, take, t):
    for c in take.clicks:
        dt = t - c["t"]
        if 0 <= dt < 0.55:
            u = dt / 0.55
            x, y = APP_X + c["x"] * take.k, APP_Y + c["y"] * take.k
            cr.set_source_rgba(*ORANGE, 0.85 * (1 - u))
            cr.set_line_width(5)
            cr.arc(x, y, 14 + 34 * ease(u), 0, 2 * math.pi)
            cr.stroke()


def draw_brand(cr, alpha=1.0):
    font(cr, 40)
    name = "Terminal Launcher"
    tw = cr.text_extents(name).x_advance
    size = 54
    x = (W - tw - size - 18) / 2
    icon(cr, x, BRAND_Y - 42, size, alpha)
    cr.set_source_rgba(*TEXT, alpha)
    cr.move_to(x + size + 18, BRAND_Y)
    cr.show_text(name)


def draw_caption(cr, take, t):
    bs = take.beats
    i = max((k for k, b in enumerate(bs) if b["t"] <= t + 1e-6), default=0)
    b = bs[i]
    start = 0.0 if i == 0 else b["t"]
    nxt = bs[i + 1]["t"] if i + 1 < len(bs) else take.end + 0.3
    a = (1.0 if i == 0 else ease((t - start) / 0.25)) * ease((nxt - t) / 0.18)
    rise = 0 if i == 0 else (1 - ease((t - start) / 0.3)) * 16
    lines = b["text"].split("|")
    for size in (64, 60, 56, 52, 48):              # the widest line fits with 60 px either side
        font(cr, size)
        if max(cr.text_extents(line).x_advance for line in lines) <= W - 120:
            break
    y = CAPTION_Y + rise + (0 if len(lines) > 1 else 40)
    for line in lines:
        centered(cr, line, y + 50, alpha=a)
        y += 84


def end_card(cr, t_in):
    a = ease(t_in / 0.45)
    icon(cr, (W - 260) / 2, 560, 260, a)
    font(cr, 92)
    centered(cr, "Terminal Launcher", 960, alpha=a)
    font(cr, 44, bold=False)
    centered(cr, "Your terminals and coding agents,", 1060, colour=SOFT, alpha=a)
    centered(cr, "one click away", 1118, colour=SOFT, alpha=a)
    font(cr, 38, bold=False)
    centered(cr, "Free and open source · GTK 4 / libadwaita", 1230, colour=SOFT, alpha=a * 0.9)
    font(cr, 42)
    centered(cr, URL, 1360, colour=ORANGE, alpha=ease((t_in - 0.35) / 0.4))


def render(work, out, thumb=None, preview=None):
    take = Take(work)
    total = take.end + END_CARD
    bg = background()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    canvas = np.ndarray((H, W, 4), np.uint8, surf.get_data())

    def compose(t, frame):
        canvas[:] = bg
        surf.mark_dirty()
        cr = cairo.Context(surf)
        if t < take.end:
            draw_brand(cr)
            draw_window(cr, take, frame)
            draw_clicks(cr, take, t)
            draw_caption(cr, take, t)
        else:
            end_card(cr, t - take.end)
        surf.flush()

    def held(t):
        for a, b in take.holds:
            if a <= t < b:
                return a - 1 / FPS
        return None

    if preview:
        for t in preview:
            h = held(t)
            compose(t, take.frame_at(h if h is not None else t) if t < take.end else None)
            path = os.path.join(work, f"preview-{t:05.2f}.png")
            surf.write_to_png(path)
            print(path)
        return
    if thumb:
        make_thumb(take, bg, surf, canvas, thumb)
    enc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-tune", "animation",
         "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2", "-g", str(FPS * 2),
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         "-movflags", "+faststart", out], stdin=subprocess.PIPE)
    rd = take.reader(0, take.end)
    n_bytes = APP_W * take.app_h * 4
    last = np.zeros((take.app_h, APP_W, 4), np.uint8)
    hold_frame = None
    for f in range(int(round(total * FPS))):
        t = f / FPS
        if t < take.end:
            buf = rd.stdout.read(n_bytes)
            if len(buf) == n_bytes:
                cur = np.frombuffer(buf, np.uint8).reshape(take.app_h, APP_W, 4).copy()
                if held(t) is None:
                    last, hold_frame = cur, None
                elif hold_frame is None:
                    hold_frame = last
            compose(t, hold_frame if held(t) is not None and hold_frame is not None else last)
        else:
            compose(t, None)
        enc.stdin.write(canvas.tobytes())
    rd.stdout.close()
    rd.wait()
    enc.stdin.close()
    enc.wait()
    print(f"wrote {out}: {total:.1f} s, {os.path.getsize(out) / 1e6:.1f} MB")


def make_thumb(take, bg, surf, canvas, path):
    """A cover still: three ticked rows and 'Add 3 to kitty', with a big headline."""
    beat = next(b["t"] for b in take.beats if b["text"].startswith("In kitty"))
    frame = take.frame_at(beat - 0.2)
    canvas[:] = bg
    surf.mark_dirty()
    cr = cairo.Context(surf)
    draw_brand(cr)
    draw_window(cr, take, frame)
    font(cr, 72)
    centered(cr, "Your terminals & agents,", 330)
    centered(cr, "one click away", 420, colour=ORANGE)
    surf.flush()
    surf.write_to_png(path)
    print(f"wrote {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("work")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--thumb")
    ap.add_argument("--preview", type=float, nargs="+")
    a = ap.parse_args()
    if not a.out and not a.preview:
        ap.error("OUT.mp4 or --preview T ...")
    render(a.work, a.out, a.thumb, a.preview)


if __name__ == "__main__":
    sys.exit(main())
