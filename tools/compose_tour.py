#!/usr/bin/env python3
"""compose_tour.py: turn the take that capture_media.py recorded (WORK_DIR/tour.mkv + tour.json)
into a vertical 1080x1920 video for YouTube Shorts, with sound: the app window in the middle, a
caption for each step above it, a ring where each click lands, an end card, and an audio track
with the app's own sounds (star, unstar, open, drop, tip, badge, chirp) at the moments the app asked
for them, over a quiet original music bed (tools/music.py) that dips a little under each sound.

    python3 tools/compose_tour.py WORK_DIR OUT.mp4 [--thumb THUMB.png] [--no-music]
    python3 tools/compose_tour.py WORK_DIR --preview 3 12.5 38      (a few frames as PNGs in WORK_DIR)

Writes H.264 (yuv420p, 30 fps, faststart) + AAC (48 kHz, 192 kb/s). While a dialog jumps into place
(the "hold" events in tour.json) the frame before it is held, so the jump never shows; nothing is
cut, so the sounds stay in time.
Sounds: the app makes its WAVs itself (`terminal-launcher.py --sounds --dry-run`, run here with
HOME and XDG_CACHE_HOME inside WORK_DIR, so it never writes to your own cache); each is mixed in at
its "sound" event, all with one gain that puts the loudest sound peak at SOUND_PEAK_DB.
Music: set to BED_LUFS (integrated, measured on the bed alone as it is in the mix, ducked), dipping
DUCK_DB around each sound. If the mix's true peak would pass CEILING_DB, everything is turned down
together. The finished MP4 is measured again (ebur128 and volumedetect) and the numbers printed,
with how far each sound stands over the bed. Mix and stems are left in WORK_DIR.
Needs ffmpeg, numpy, pycairo and librsvg's GObject bindings (gir1.2-rsvg-2.0) for the icon."""
import argparse
import json
import math
import os
import re
import subprocess
import sys

import cairo
import gi
import numpy as np

gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP = os.path.join(REPO, "terminal-launcher.py")
ICON = os.path.join(REPO, "assets", "terminal-launcher.svg")
sys.path.insert(0, HERE)
FPS = 30
W, H = 1080, 1920
RATE = 48000
FONT = "Open Sans"
END_CARD = 3.6
URL = "github.com/Vyeche/TerminalLauncher"

# Audio levels
SOUND_PEAK_DB = -3.0     # the loudest app sound's sample peak in the mix (dBFS)
BED_LUFS = -26.0         # the music bed alone, integrated (aim: -28 to -24)
DUCK_DB = 5.0            # how far the bed dips around each sound
CEILING_DB = -1.5        # the mix's true peak stays under this (dBTP); AAC adds a little

# Layout (px). YouTube draws its buttons along the right edge and the title over the bottom fifth,
# so the window stays inside APP_BOX (scaled to fit, centered): its cat bar ends near y 1540.
APP_BOX = (40, 300, 1000, 1240)       # x, y, max width, max height
BRAND_Y = 68
CAPTION_Y = 136

BG = (0x17 / 255, 0x14 / 255, 0x1F / 255)          # the icon's dark purple
ORANGE = (0xFF / 255, 0x9A / 255, 0x3C / 255)      # the icon's prompt
PURPLE = (0x8B / 255, 0x6C / 255, 0xFF / 255)      # the icon's cursor
GOLD = (0xE5 / 255, 0xA5 / 255, 0x0A / 255)        # the app's favorite star
TEXT = (0.97, 0.96, 0.99)
SOFT = (0.74, 0.71, 0.80)
CHIP = (0x2a / 255, 0x25 / 255, 0x36 / 255)


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def font(cr, size, bold=True):
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)


def _shape(cr, kind, x, y, size):
    """A ▶ (orange triangle) or ★ (gold star), drawn (Open Sans has neither), baseline at y."""
    h = size * 0.7
    if kind == "▶":
        cr.move_to(x, y - h)
        cr.line_to(x + size * 0.5, y - h / 2)
        cr.line_to(x, y)
        cr.close_path()
        return
    cx, cy, ro, ri = x + size * 0.38, y - h * 0.48, size * 0.40, size * 0.17
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        r = ro if k % 2 == 0 else ri
        (cr.move_to if k == 0 else cr.line_to)(cx + r * math.cos(a), cy + r * math.sin(a))
    cr.close_path()


def centered(cr, text, y, color=TEXT, alpha=1.0):
    """One line, centered, with a soft shadow. ▶ and ★ in it are drawn as shapes (accent colors)."""
    size = cr.get_font_matrix().xx
    parts = re.split(r"([▶★])", text)
    glyph_w = {"▶": size * 0.62, "★": size * 0.84}
    width = sum(glyph_w[p] if p in glyph_w else cr.text_extents(p).x_advance for p in parts if p)
    for dx, dy, col, a in ((2, 3, (0, 0, 0), 0.5), (0, 0, None, 1.0)):
        x = (W - width) / 2
        for p in parts:
            if not p:
                continue
            if p in glyph_w:
                _shape(cr, p, x + dx, y + dy, size)
                cr.set_source_rgba(*(col or (ORANGE if p == "▶" else GOLD)), a * alpha)
                cr.fill()
                x += glyph_w[p]
            else:
                cr.set_source_rgba(*(col or color), a * alpha)
                cr.move_to(x + dx, y + dy)
                cr.show_text(p)
                x += cr.text_extents(p).x_advance


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
        self.work = work
        self.path = os.path.join(work, self.meta["video"])
        lw, lh = self.meta["window"]
        bx, by, bw, bh = APP_BOX
        self.k = min(bw / lw, bh / lh)                 # output px per logical px
        self.app_w, self.app_h = int(round(lw * self.k / 2)) * 2, int(round(lh * self.k / 2)) * 2
        self.app_x, self.app_y = (W - self.app_w) // 2, by
        ev = self.meta["events"]
        self.end = next(e["t"] for e in ev if e["kind"] == "end")
        self.beats = [e for e in ev if e["kind"] == "beat"]
        self.clicks = [e for e in ev if e["kind"] == "click"]
        self.keys = [e for e in ev if e["kind"] == "keys"]
        self.sounds = [e for e in ev if e["kind"] == "sound"]
        self.holds = [(e["t"], e["until"]) for e in ev if e["kind"] == "hold"]

    def reader(self, start=0.0, length=None):
        args = ["ffmpeg", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", self.path]
        if length:
            args += ["-t", f"{length:.3f}"]
        args += ["-vf", f"fps={FPS},scale={self.app_w}:{self.app_h}:flags=lanczos", "-pix_fmt", "bgra",
                 "-f", "rawvideo", "-"]
        return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def frame_at(self, t):
        p = self.reader(t, 0.2)
        buf = p.stdout.read(self.app_w * self.app_h * 4)
        p.stdout.close()
        p.wait()
        return np.frombuffer(buf, np.uint8).reshape(self.app_h, self.app_w, 4).copy()


def draw_window(cr, take, frame):
    ax, ay, aw, ah = take.app_x, take.app_y, take.app_w, take.app_h
    for i in range(14, 0, -1):                     # a soft shadow
        cr.set_source_rgba(0, 0, 0, 0.022)
        rounded(cr, ax - i, ay - i + 10, aw + 2 * i, ah + 2 * i, 16 + i)
        cr.fill()
    img = cairo.ImageSurface.create_for_data(memoryview(frame), cairo.FORMAT_RGB24, aw, ah, aw * 4)
    cr.save()
    rounded(cr, ax, ay, aw, ah, 14)
    cr.clip()
    cr.set_source_surface(img, ax, ay)
    cr.paint()
    cr.restore()


def draw_clicks(cr, take, t):
    for c in take.clicks:
        dt = t - c["t"]
        if 0 <= dt < 0.55:
            u = dt / 0.55
            x, y = take.app_x + c["x"] * take.k, take.app_y + c["y"] * take.k
            cr.set_source_rgba(*ORANGE, 0.85 * (1 - u))
            cr.set_line_width(5)
            cr.arc(x, y, 14 + 34 * ease(u), 0, 2 * math.pi)
            cr.stroke()


def draw_keys(cr, take, t):
    """A keycap chip under the caption while a shortcut is pressed (only if the take used one)."""
    for e in take.keys:
        dt = t - e["t"]
        if -0.2 <= dt < 1.6:
            a = ease((dt + 0.2) / 0.2) * ease((1.6 - dt) / 0.3)
            font(cr, 38)
            label = e["keys"].replace("+", " + ")
            tw = cr.text_extents(label).x_advance
            x, y = (W - tw - 48) / 2, take.app_y - 86
            cr.set_source_rgba(*CHIP, 0.95 * a)
            rounded(cr, x, y, tw + 48, 62, 14)
            cr.fill()
            cr.set_source_rgba(*TEXT, a)
            cr.move_to(x + 24, y + 44)
            cr.show_text(label)


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
    centered(cr, "Your terminals and coding agents,", 1060, color=SOFT, alpha=a)
    centered(cr, "one click away", 1118, color=SOFT, alpha=a)
    font(cr, 38, bold=False)
    centered(cr, "Free and open source · GTK 4 / libadwaita", 1230, color=SOFT, alpha=a * 0.9)
    font(cr, 42)
    centered(cr, URL, 1360, color=ORANGE, alpha=ease((t_in - 0.35) / 0.4))


# ---------- audio ----------
def sound_files(work):
    """{name: WAV path}, made by the app itself in WORK_DIR's throwaway home (never your own cache)."""
    home = os.path.join(work, "home")
    os.makedirs(home, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
           "XDG_CACHE_HOME": os.path.join(home, ".cache"), "LANG": "en_US.UTF-8"}
    out = subprocess.run(["python3", APP, "--sounds", "--dry-run"], env=env, capture_output=True, text=True,
                         check=True).stdout
    files = dict(line.split(": ", 1) for line in out.strip().splitlines() if ": " in line)
    for name, path in files.items():
        if not os.path.realpath(path).startswith(os.path.realpath(home) + os.sep):
            sys.exit(f"compose_tour.py: the app put its {name} sound outside WORK_DIR ({path})")
    return files


def decode(path):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "f32le", "-ac", "2",
                          "-ar", str(RATE), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).astype(np.float64)


def write_wav(path, x):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "f32le", "-ac", "2", "-ar", str(RATE),
                    "-i", "-", "-c:a", "pcm_f32le", path], input=x.astype(np.float32).tobytes(), check=True)


def loudness(path):
    """(integrated LUFS, true peak dBTP, sample peak dBFS) of a file's audio."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af",
                          "ebur128=peak=true,volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    summ = out[out.rfind("Summary:"):]
    i = float(re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summ).group(1))
    tp = float(re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summ).group(1))
    mx = float(re.search(r"max_volume:\s+(-?[\d.]+|-inf) dB", out).group(1))
    return i, tp, mx


def duck_envelope(n, times, depth_db):
    """Gain (1 = untouched) that dips depth_db around each sound: down over 60 ms from 80 ms before
    it, held 0.6 s, back up over 0.5 s (like a sidechain compressor, from the known times)."""
    t = np.arange(n) / RATE
    d = np.zeros(n)
    for at in times:
        a0, a1, h1, r1 = at - 0.08, at - 0.02, at + 0.6, at + 1.1
        shape = np.clip((t - a0) / (a1 - a0), 0, 1) * np.clip((r1 - t) / (r1 - h1), 0, 1)
        d = np.maximum(d, shape * shape * (3 - 2 * shape))
    return 1 - (1 - 10 ** (-depth_db / 20)) * d


def rms_db(x):
    return 20 * math.log10(max(1e-9, float(np.sqrt(np.mean(x ** 2)))))


def mix(take, work, total, music=True):
    """Write WORK_DIR/mix.wav (and stem-sounds.wav, stem-music.wav); returns (path, report lines)."""
    n = int(round(total * RATE))
    files = sound_files(work)
    snd = np.zeros((n + RATE * 2, 2))
    cache = {}
    for s in take.sounds:
        if s["name"] not in files:
            sys.exit(f"compose_tour.py: the app has no sound {s['name']!r}")
        x = cache.setdefault(s["name"], decode(files[s["name"]]))
        i = int(round(s["t"] * RATE))
        snd[i:i + len(x)] += x[: len(snd) - i]
    snd = snd[:n]
    peak = np.abs(snd).max() or 1.0
    snd *= 10 ** (SOUND_PEAK_DB / 20) / peak
    report = [f"{len(take.sounds)} sounds: " + ", ".join(f"{s['name']}@{s['t']:.2f}" for s in take.sounds)]
    bed = np.zeros_like(snd)
    if music:
        import music as bedmusic                 # tools/music.py
        m = bedmusic.render(total, take.end)[:n]
        m = np.pad(m, ((0, n - len(m)), (0, 0)))
        m *= duck_envelope(n, [s["t"] for s in take.sounds], DUCK_DB)[:, None]
        tmp = os.path.join(work, "stem-music.wav")
        write_wav(tmp, m)
        i_m = loudness(tmp)[0]
        bed = m * 10 ** ((BED_LUFS - i_m) / 20)
        report.append(f"music: {bedmusic.describe(total, take.end)}; ducked {DUCK_DB:.0f} dB around each sound")
    raw = snd + bed
    out = os.path.join(work, "mix.wav")
    write_wav(out, raw)
    _, tp, _ = loudness(out)
    if tp > CEILING_DB:                          # turn everything down together, keeping the balance
        g = 10 ** ((CEILING_DB - 0.2 - tp) / 20)
        snd, bed, raw = snd * g, bed * g, raw * g
        write_wav(out, raw)
        report.append(f"mix turned down {20 * math.log10(g):.1f} dB to keep the true peak under {CEILING_DB} dBTP")
    write_wav(os.path.join(work, "stem-sounds.wav"), snd)
    write_wav(os.path.join(work, "stem-music.wav"), bed)
    if music:
        i_b, tp_b, _ = loudness(os.path.join(work, "stem-music.wav"))
        report.append(f"music bed alone: {i_b:.1f} LUFS integrated, true peak {tp_b:.1f} dBTP")
        report.append("each sound over the bed (RMS of the 0.3 s from its start, sound alone vs ducked bed):")
        for s in take.sounds:
            i = int(s["t"] * RATE)
            seg = slice(i, min(n, i + int(0.3 * RATE)))
            report.append(f"  {s['t']:6.2f}  {s['name']:<7} {rms_db(snd[seg]) - rms_db(bed[seg]):+5.1f} dB")
    return out, report


# ---------- video ----------
def render(work, out, thumb=None, preview=None, music=True):
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
            draw_keys(cr, take, t)
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
    audio, report = mix(take, work, total, music)
    enc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-tune", "animation",
         "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2", "-g", str(FPS * 2),
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         "-c:a", "aac", "-b:a", "192k", "-ar", str(RATE), "-shortest", "-movflags", "+faststart", out],
        stdin=subprocess.PIPE)
    rd = take.reader(0, take.end)
    n_bytes = take.app_w * take.app_h * 4
    last = np.zeros((take.app_h, take.app_w, 4), np.uint8)
    hold_frame = None
    for f in range(int(round(total * FPS))):
        t = f / FPS
        if t < take.end:
            buf = rd.stdout.read(n_bytes)
            if len(buf) == n_bytes:
                cur = np.frombuffer(buf, np.uint8).reshape(take.app_h, take.app_w, 4).copy()
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
    i, tp, mx = loudness(out)
    print(f"wrote {out}: {total:.1f} s, {os.path.getsize(out) / 1e6:.1f} MB")
    for line in report:
        print("audio: " + line)
    print(f"audio: final MP4 {i:.1f} LUFS integrated, true peak {tp:.1f} dBTP, sample peak {mx:.1f} dBFS"
          + ("" if tp < -1.0 else "  <- over -1 dBTP: lower CEILING_DB"))
    if not 35 <= total <= 50:
        print(f"WARNING: the video is {total:.1f} s; aim for 35-50 s")
    print("beats:")
    for b in take.beats:
        print(f"  {b['t']:6.2f}  {b['text'].replace('|', ' ')}")
    print(f"  {take.end:6.2f}  end card")


def make_thumb(take, bg, surf, canvas, path):
    """A cover still: two favorites at the top of the list, with a big headline."""
    beat = next((b["t"] for b in take.beats if "favorites at once" in b["text"]), take.beats[-1]["t"])
    frame = take.frame_at(max(0.0, beat - 0.2))
    canvas[:] = bg
    surf.mark_dirty()
    cr = cairo.Context(surf)
    draw_brand(cr)
    draw_window(cr, take, frame)
    font(cr, 40)
    centered(cr, "New: ★ Favorites and Byte the cat", 142, color=GOLD)
    font(cr, 58)
    centered(cr, "Your terminals & agents,", 210)
    centered(cr, "one click away", 274, color=ORANGE)
    surf.flush()
    surf.write_to_png(path)
    print(f"wrote {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("work")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--thumb")
    ap.add_argument("--preview", type=float, nargs="+")
    ap.add_argument("--no-music", action="store_true")
    a = ap.parse_args()
    if not a.out and not a.preview:
        ap.error("OUT.mp4 or --preview T ...")
    render(a.work, a.out, a.thumb, a.preview, music=not a.no_music)


if __name__ == "__main__":
    sys.exit(main())
