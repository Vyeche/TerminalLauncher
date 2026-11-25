#!/usr/bin/env python3
"""social_preview.py: build the GitHub social preview image (1280x640, shown when the repo link is
shared): the icon, the name, the tagline and a screenshot of the window with its made-up list.

    python3 tools/social_preview.py SCREENSHOT.png [OUT.png]     default OUT: docs/images/social-preview.png

SCREENSHOT is the 2x window still that `tools/capture_media.py WORK_DIR tour` leaves at
WORK_DIR/window-2x.png (docs/images/window.png works too, a little softer).
Upload the result under the repository's Settings → General → Social preview → Edit.
Needs pycairo and librsvg's GObject bindings (gir1.2-rsvg-2.0). Font: Open Sans."""
import math
import os
import sys

import cairo
import gi

gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ICON = os.path.join(REPO, "assets", "terminal-launcher.svg")
if len(sys.argv) < 2:
    sys.exit(__doc__)
SHOT = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "docs", "images", "social-preview.png")

W, H = 1280, 640
FONT = "Open Sans"
BG = (0x17 / 255, 0x14 / 255, 0x1F / 255)          # the icon's dark purple
ORANGE = (0xFF / 255, 0x9A / 255, 0x3C / 255)      # the icon's prompt
PURPLE = (0x8B / 255, 0x6C / 255, 0xFF / 255)      # the icon's cursor
FG = (0.97, 0.96, 0.99)
MUTED = (0.74, 0.71, 0.80)


def text(cr, x, y, s, size, colour, bold=False):
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    cr.set_source_rgb(*colour)
    cr.move_to(x, y)
    cr.show_text(s)


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def main():
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surface)
    cr.set_source_rgb(*BG)
    cr.paint()
    glow = cairo.RadialGradient(940, 330, 40, 940, 330, 520)   # a faint purple glow behind the window
    glow.add_color_stop_rgba(0, *PURPLE, 0.16)
    glow.add_color_stop_rgba(1, *PURPLE, 0.0)
    cr.set_source(glow)
    cr.paint()

    # the window, scaled smoothly, with rounded corners and a soft shadow
    shot = cairo.ImageSurface.create_from_png(SHOT)
    sw, sh = shot.get_width(), shot.get_height()
    scale = 576 / sh
    dw, dh = sw * scale, sh * scale
    x0, y0 = W - dw - 56, (H - dh) / 2
    for i in range(16, 0, -1):
        cr.set_source_rgba(0, 0, 0, 0.03)
        rounded(cr, x0 - i, y0 - i + 8, dw + 2 * i, dh + 2 * i, 12 + i)
        cr.fill()
    cr.save()
    rounded(cr, x0, y0, dw, dh, 12)
    cr.clip()
    cr.translate(x0, y0)
    cr.scale(scale, scale)
    cr.set_source_surface(shot, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_BEST)
    cr.paint()
    cr.restore()

    # the words
    tx = 64
    r = Rsvg.Rectangle()
    r.x, r.y, r.width, r.height = tx - 8, 92, 120, 120
    Rsvg.Handle.new_from_file(ICON).render_document(cr, r)
    text(cr, tx, 292, "Terminal", 66, FG, bold=True)
    text(cr, tx, 366, "Launcher", 66, FG, bold=True)
    text(cr, tx, 432, "Your terminals and coding agents,", 27, FG)
    text(cr, tx, 468, "one click away", 27, ORANGE, bold=True)
    text(cr, tx, 530, "Claude Code · omp · opencode · any shell", 20, MUTED)
    text(cr, tx, 560, "kitty tabs or windows · start at login", 20, MUTED)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    surface.write_to_png(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
