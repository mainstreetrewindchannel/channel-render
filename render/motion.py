"""Smooth camera moves over stills (newspaper pages, photographs), drawn frame by frame in Python.

Why not ffmpeg zoompan: it moves in whole source pixels, which shows as jitter on slow moves and text.
Here every frame samples the source with sub-pixel accuracy (PIL EXTENT transform) from a mip level
close to the output size, then adds a light film look (grain, vignette) and overlays.

Shot kinds:
  newspaper: start on the whole page, glide into `box`, then a highlighter sweeps across `mark`.
  image:     a slow push/pan across a photograph, filling the frame.
"""
import math
import subprocess

import numpy as np
from PIL import Image, ImageFile

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True  # a few missing bytes at the end of a big LoC scan is harmless
BG = (22, 22, 28)
DRIFT = 0.035   # a newspaper shot slowly tightens by this much after arriving
HILITE = np.array([242, 196, 70], dtype=np.float32) / 255.0


def ease(u):
    u = min(max(u, 0.0), 1.0)
    return (1 - math.cos(math.pi * u)) / 2


class Source:
    """An image with a mip pyramid, sampled by rectangles in full-resolution coordinates."""

    def __init__(self, path, out_w):
        im = Image.open(path).convert("RGB")
        self.w, self.h = im.size
        self.levels = [(1, im)]
        f = 1
        while im.size[0] > out_w * 2.2:
            im = im.reduce(2)
            f *= 2
            self.levels.append((f, im))

    def sample(self, rect, size):
        x0, y0, x1, y1 = rect
        scale = (x1 - x0) / size[0]           # source pixels per output pixel
        f, im = self.levels[0]
        for lf, lim in self.levels:
            if lf <= max(1.0, scale / 1.2):
                f, im = lf, lim
        r = (x0 / f, y0 / f, x1 / f, y1 / f)
        return im.transform(size, Image.EXTENT, r, resample=Image.BICUBIC, fillcolor=BG)


class Look:
    def __init__(self, w, h, grain=5.0, vignette=0.18, seed=7):
        rng = np.random.default_rng(seed)
        self.grain = [rng.normal(0, grain, (h // 2, w // 2, 1)).astype(np.float32).repeat(2, 0).repeat(2, 1) for _ in range(6)]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
        self.vig = (1 - vignette * np.clip(d, 0, 1) ** 2.2)[..., None].astype(np.float32)

    def apply(self, arr, i):
        a = arr.astype(np.float32) * self.vig + self.grain[i % len(self.grain)]
        return np.clip(a, 0, 255)


def fit_rect(cx, cy, w, aspect):
    h = w / aspect
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def lerp_rect(a, b, u):
    return tuple(a[k] + (b[k] - a[k]) * u for k in range(4))


def overlay(arr, rgba, alpha_mul=1.0):
    if rgba is None or alpha_mul <= 0:
        return arr
    a = rgba[..., 3:4] * (alpha_mul / 255.0)
    return arr * (1 - a) + rgba[..., :3] * a


def encoder(out, w, h, fps, draft):
    return subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
         "-i", "-", "-c:v", "libx264", "-preset", "veryfast" if draft else "medium", "-crf", "24" if draft else "17",
         "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)


def render(kind, path, out, dur, show, w=1920, h=1080, fps=30, draft=False, overlays=()):
    """overlays: list of (rgba ndarray, t_in, t_out) composited over the frames, faded 0.4 s."""
    src = Source(path, w)
    aspect = w / h
    frames = max(1, round(dur * fps))
    look = Look(w, h)
    pw, ph = src.w, src.h

    if kind == "newspaper":
        # whole page with a margin, centred
        full_h = ph * 1.06
        start = fit_rect(pw / 2, ph / 2, max(full_h * aspect, pw * 1.06), aspect)
        def box_rect(box, pad):
            x0, y0, x1, y1 = [v * s for v, s in zip(box, (pw, ph, pw, ph))]
            bw, bh = (x1 - x0) * (1 + pad), (y1 - y0) * (1 + pad)
            return fit_rect((x0 + x1) / 2, (y0 + y1) / 2, max(bw, bh * aspect), aspect)

        def drifted(r, levels=1):   # where a shot ends after its slow settle(s) (see the drift in the frame loop)
            return fit_rect((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, (r[2] - r[0]) * (1 - DRIFT) ** levels, aspect)

        end = box_rect(show.get("box", [0, 0, 1, 1]), show.get("pad", 0.08))
        move = min(dur * 0.62, show.get("move", 4.2))
        cf = show.get("continue_from")
        if cf:   # same page as the beat before: pick up exactly where that shot stopped
            start = drifted(box_rect(cf["box"], cf.get("pad", 0.08)), cf.get("levels", 1))
            if list(cf["box"]) == list(show.get("box", [0, 0, 1, 1])):
                end, move = start, 0.0
            else:
                move = min(dur * 0.5, 2.6)
        elif show.get("from_box"):   # continue from an earlier framing on the same page instead of the full page
            fx0, fy0, fx1, fy1 = [v * s for v, s in zip(show["from_box"], (pw, ph, pw, ph))]
            start = fit_rect((fx0 + fx1) / 2, (fy0 + fy1) / 2, max((fx1 - fx0) * 1.08, (fy1 - fy0) * 1.08 * aspect), aspect)
        mark = show.get("mark")
        mark_px = [v * s for v, s in zip(mark, (pw, ph, pw, ph))] if mark else None
        mark_t0 = move + 0.25 if move > 0 else 0.3
    else:
        # photo: cover the frame, push or pan
        cover_w = min(pw, ph * aspect)
        fx, fy = show.get("focus", [0.5, 0.45])
        z = show.get("zoom", 1.14)
        cx, cy = fx * pw, fy * ph
        def clamp(r):
            x0, y0, x1, y1 = r
            dx = max(0, -x0) - max(0, x1 - pw)
            dy = max(0, -y0) - max(0, y1 - ph)
            return (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        wide = clamp(fit_rect(cx, cy, cover_w, aspect))
        tight = clamp(fit_rect(cx, cy, cover_w / z, aspect))
        m = show.get("motion", "in")
        if m == "in":
            start, end = wide, tight
        elif m == "out":
            start, end = tight, wide
        else:
            off = (cover_w - cover_w / z) / 2
            dx, dy = {"left": (off, 0), "right": (-off, 0), "up": (0, off / aspect), "down": (0, -off / aspect)}.get(m, (off, 0))
            mid = fit_rect(cx, cy, cover_w / z, aspect)
            start = clamp((mid[0] + dx, mid[1] + dy, mid[2] + dx, mid[3] + dy))
            end = clamp((mid[0] - dx, mid[1] - dy, mid[2] - dx, mid[3] - dy))
        move = dur
        mark_px = None

    enc = encoder(out, w, h, fps, draft)
    for i in range(frames):
        t = i / fps
        if kind == "newspaper":
            if move > 0 and t <= move:
                rect = lerp_rect(start, end, ease(t / move))
            else:   # a slow drift after arriving keeps the frame alive
                u = (t - move) / max(0.01, dur - move)
                cx, cy = (end[0] + end[2]) / 2, (end[1] + end[3]) / 2
                rect = fit_rect(cx, cy, (end[2] - end[0]) * (1 - DRIFT) ** u, aspect)
        else:
            rect = lerp_rect(start, end, ease(t / move) * 0.15 + (t / move) * 0.85)
        frame = np.asarray(src.sample(rect, (w, h)), dtype=np.float32)
        if mark_px and t >= mark_t0:
            u = ease((t - mark_t0) / 0.7)
            sx = w / (rect[2] - rect[0])
            mx0 = int((mark_px[0] - rect[0]) * sx)
            mx1 = int((mark_px[0] + (mark_px[2] - mark_px[0]) * u - rect[0]) * sx)
            my0 = int((mark_px[1] - rect[1]) * sx)
            my1 = int((mark_px[3] - rect[1]) * sx)
            mx0, mx1, my0, my1 = max(0, mx0), min(w, mx1), max(0, my0), min(h, my1)
            if mx1 > mx0 and my1 > my0:
                reg = frame[my0:my1, mx0:mx1]
                frame[my0:my1, mx0:mx1] = reg * (0.45 + 0.55 * HILITE)
        frame = look.apply(frame, i)
        fin = not show.get("continue_from")
        fout = not show.get("hold_out")
        if not show.get("nofade") and (kind == "newspaper" or show.get("fade", True)):
            fade = min(1.0, t / 0.35 if fin else 1.0, (dur - t) / 0.35 if fout else 1.0)
            if fade < 1:
                frame = frame * fade + np.array(BG, dtype=np.float32) * (1 - fade) if kind == "newspaper" else frame * fade
        for rgba, t_in, t_out in overlays:
            if t_in <= t <= t_out:
                a = min(1.0, (t - t_in) / 0.4 if fin else 1.0, (t_out - t) / 0.4 if fout else 1.0)
                frame = overlay(frame, rgba, a)
        enc.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError(f"encoder failed for {out}")
    return out


def load_rgba(path):
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.float32)
