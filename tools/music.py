#!/usr/bin/env python3
"""music.py: the quiet music bed under the Terminal Launcher video, synthesized here from sine
partials (a soft kalimba, a felt bell, a warm pad, a round bass and a brushed shaker). No samples, no
third-party audio, no borrowed tunes. Deterministic: the same arguments give the same samples.

    python3 tools/music.py OUT.wav DURATION END_CARD_AT        write it alone, to listen to or measure

tools/compose_tour.py calls render() and sets its level and ducking under the app's sounds.

Key: G major pentatonic (G A B D E), so it sits under the app's own notes: the star chime (G5 → D6),
the open pluck (C5 → G5) and the tip chime (A5 C♯6 E6) land as color tones, never as clashes on a
held chord tone. Chords: G6 (G B D E), C6/9 (E G A D over a C bass), Em7 (E G B D) and D6sus2
(A B E over D): only the bass ever plays a C. Everything stays below D5 except the bell, which
keeps to A4-E5, under the chimes.
Tempo: about 92 BPM, nudged so that a whole number of 4/4 bars ends exactly where the end card starts.
    bars 1-2     intro: the pad swells in (G6, C6/9); the kalimba fades in on the second bar
    then         G6 C6/9 Em7 D6sus2 round and round: kalimba eighths in a lilting pattern with rests,
                 a round bass (beat 1, the "and" of 2, beat 3), a soft shaker on the off-beats, and
                 from bar 5 a slow four-bar bell phrase every four bars
    last bar     D6sus2, the lead-in
    end card     a rolled G6 chord (bass, kalimba, bell, pad) that rings out and fades to silence
"""
import math
import sys

import numpy as np

SR = 48000

# timbres: (partial ratio, amplitude, decay as a fraction of the note's ring time)
KALIMBA = [(1.0, 1.0, 1.0), (2.0, 0.10, 0.45), (5.4, 0.10, 0.22), (8.9, 0.035, 0.12)]   # a soft tine
BELL = [(1.0, 1.0, 1.0), (2.0, 0.30, 0.55), (3.01, 0.10, 0.35), (4.17, 0.05, 0.22)]     # a felt bell
PAD = [(1.0, 1.0, 1.0), (2.0, 0.35, 1.0), (3.0, 0.14, 0.9), (4.0, 0.06, 0.8)]          # warm, slow
BASS = [(1.0, 1.0, 1.0), (2.0, 0.22, 0.6), (3.0, 0.05, 0.4)]                           # round

# chord: (bass MIDI note, kalimba notes low to high, pad notes)
CHORDS = {
    "G6": (43, (55, 59, 62, 64, 67), (55, 59, 62, 64)),
    "C69": (48, (52, 55, 57, 62, 64), (52, 55, 57, 62)),
    "Em7": (40, (52, 55, 59, 62, 64), (52, 55, 59, 62)),
    "D6sus2": (38, (50, 57, 59, 64, 69), (50, 57, 59, 64)),
}
# the kalimba's eighths: which chord note (None = rest), and how hard
PATTERN = (0, 2, 4, None, 3, 1, 2, None)
ACCENT = (1.0, 0.6, 0.8, 0.0, 0.75, 0.55, 0.65, 0.0)
# the bell's four-bar phrase, in eighths from the phrase's start: (eighth, MIDI note, length in beats)
PHRASE = ((0, 74, 2.0), (4, 71, 2.0), (8, 76, 1.0), (10, 74, 1.0), (12, 71, 2.0),
          (16, 69, 2.0), (20, 71, 1.0), (22, 74, 1.0), (24, 71, 3.5))


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def note(buf, start, freq, length, timbre, ring, amp=1.0, attack=0.008, pan=0.0, detune=0.0):
    """Add one note: a raised-cosine attack, each partial fading at its own rate, a 30 ms tail, an
    equal-power pan (-1 left … 1 right). `detune` (cents) adds a second, slightly sharp voice."""
    n0 = int(round(start * SR))
    if n0 >= len(buf) or n0 < 0:
        return
    n = min(int(length * SR), len(buf) - n0)
    t = np.arange(n) / SR
    env = 0.5 - 0.5 * np.cos(np.pi * np.minimum(1.0, t / attack))
    tail = int(0.03 * SR)
    if n > tail:
        env[-tail:] *= np.linspace(1, 0, tail)
    s = np.zeros(n)
    for f in ([freq] if not detune else [freq, freq * 2 ** (detune / 1200)]):
        for ratio, a, r in timbre:
            if f * ratio > 14000:
                continue
            s += a * np.exp(-t / (ring * r)) * np.sin(2 * np.pi * f * ratio * t)
    s *= amp * env / (2 if detune else 1)
    ang = (pan + 1) * math.pi / 4
    buf[n0:n0 + n, 0] += s * math.cos(ang)
    buf[n0:n0 + n, 1] += s * math.sin(ang)


def shaker(buf, start, amp, rng, pan=0.4):
    """A short brushed tick: band-limited noise with a quick swell and fall."""
    n0 = int(round(start * SR))
    n = int(0.07 * SR)
    if n0 + n > len(buf):
        return
    noise = rng.standard_normal(n)
    noise = np.diff(noise, prepend=0.0)                      # tilt it bright
    noise = np.convolve(noise, np.ones(6) / 6, mode="same")  # but not harsh
    t = np.arange(n) / SR
    env = np.minimum(1.0, t / 0.008) * np.exp(-t / 0.018)
    s = noise * env * amp
    ang = (pan + 1) * math.pi / 4
    buf[n0:n0 + n, 0] += s * math.cos(ang)
    buf[n0:n0 + n, 1] += s * math.sin(ang)


def tempo(end_card_at, target=92.0):
    """(bpm, bars): the whole number of bars before the end card whose tempo is nearest target."""
    best = None
    for bars in range(4, 64):
        bpm = 4 * 60 * bars / end_card_at
        if 84 <= bpm <= 100 and (best is None or abs(bpm - target) < abs(best[0] - target)):
            best = (bpm, bars)
    return best or (target, max(4, round(end_card_at * target / 240)))


def progression(bars):
    """G6 C69 Em7 D6sus2 round and round (the first two bars are the intro), ending on D6sus2."""
    seq = [["G6", "C69", "Em7", "D6sus2"][i % 4] for i in range(bars)]
    if seq[-1] != "D6sus2":
        if seq[-2] == "D6sus2":
            seq[-2] = "Em7"
        seq[-1] = "D6sus2"
    return seq


def render(duration, end_card_at):
    """The bed as float stereo at SR (peak 0.5): `duration` seconds, resolving on G at `end_card_at`."""
    bpm, bars = tempo(end_card_at)
    beat = 60.0 / bpm
    bar = 4 * beat
    rng = np.random.default_rng(20261125)
    buf = np.zeros((int(math.ceil(duration * SR)) + SR, 2))
    chords = progression(bars)
    for b, name in enumerate(chords):
        t0 = b * bar
        bass, kal, pad = CHORDS[name]
        intro = b < 2
        for k, m in enumerate(pad):           # the pad: swells in, then sits quietly underneath
            note(buf, t0, hz(m), bar + 0.5, PAD, 2.6, 0.085 if intro else 0.05,
                 attack=bar * 0.9 if b == 0 else 0.3, pan=-0.25 + 0.17 * k, detune=4.0)
        if b >= 1:                            # the kalimba (fading in over the second bar)
            for i, k in enumerate(PATTERN):
                if k is None:
                    continue
                fade = (i + 1) / 8 if b == 1 else 1.0
                note(buf, t0 + i * beat / 2, hz(kal[k]), 0.9, KALIMBA, 0.32, 0.2 * ACCENT[i] * fade,
                     pan=-0.4 if i % 2 == 0 else 0.4)
        if not intro:                         # the bass, and the shaker on the off-beats
            for at, a in ((0.0, 0.5), (1.5, 0.26), (2.0, 0.38)):
                note(buf, t0 + at * beat, hz(bass), beat * 1.5, BASS, 0.55, a, attack=0.015)
            for i in range(4):
                shaker(buf, t0 + (i + 0.5) * beat, 0.05 if i % 2 else 0.035, rng)
        if b >= 4 and (b - 4) % 4 == 0 and b + 4 <= bars:   # the bell phrase, every four bars
            for eighth, m, beats in PHRASE:
                note(buf, t0 + eighth * beat / 2, hz(m), beats * beat + 0.6, BELL, 0.5, 0.075, pan=0.15)
    t0 = bars * bar                           # the end: a rolled G6 chord that rings out
    note(buf, t0, hz(43), 3.6, BASS, 1.4, 0.5)
    for i, m in enumerate((55, 59, 62, 64, 67)):
        note(buf, t0 + i * 0.04, hz(m), 3.6, KALIMBA, 1.1, 0.2, pan=-0.3 + 0.15 * i)
    note(buf, t0 + 0.2, hz(71), 3.4, BELL, 1.0, 0.07, pan=0.1)
    for m in (55, 59, 62):
        note(buf, t0, hz(m), 3.8, PAD, 2.6, 0.06, attack=0.06, detune=4.0)
    buf = buf[: int(round(duration * SR))]
    fade = min(len(buf), int(0.7 * SR))
    buf[len(buf) - fade:] *= np.linspace(1, 0, fade)[:, None] ** 2
    peak = np.abs(buf).max() or 1.0
    return buf * (0.5 / peak)


def describe(duration, end_card_at):
    bpm, bars = tempo(end_card_at)
    return (f"{bpm:.1f} BPM, G major pentatonic, {bars} bars + the end chord: "
            + " ".join(progression(bars)) + " | G6")


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    import subprocess
    out, dur, end_at = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    x = render(dur, end_at).astype(np.float32)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "f32le", "-ac", "2", "-ar", str(SR), "-i", "-",
                    "-c:a", "pcm_s16le", out], input=x.tobytes(), check=True)
    print(f"wrote {out}: {describe(dur, end_at)}")


if __name__ == "__main__":
    main()
