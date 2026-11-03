"""Main Street Rewind signature intro: "The Record Becomes the Road".

Spec: studio repo docs/intro-spec.md (6.00 s, 180 frames @ 30 fps).
  f000-018  cream folio opens left to right on navy, caption FROM THE ORIGINAL RECORD
  f018-048  ONE genuine archival source in the window (the only per-episode part)
  f048-072  window goes cream, navy diagonal shutter sweeps right to left (the rewind)
  f072-108  the two triangles of the mark slide in from the right, sun fades in
  f108-150  mark moves into the horizontal lockup, MAIN STREET / REWIND reveal left to right
  f150-180  static hold, hard cut to chapter one

The logo is never redrawn: every brand layer is cut from the approved export
assets/brand/export/lockup-horizontal-dark.png (and the stacked one for Shorts).
Sound is synthesized here (paper, click, soft reverse, two original piano notes), so it can't be claimed.

Usage:
  python intro.py --studio <studio root> --out intro.mp4 [--variant full|bumper|short]
                  [--source film.mp4 --start 520.0 --crop x0,y0,x1,y1]   (crop in 0-1 of the source frame)
"""
import argparse
import os
import subprocess
import tempfile
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FPS = 30
NAVY = (27, 35, 64)
DEEP = (18, 25, 51)
CREAM = (243, 234, 215)
RED = (228, 87, 46)
MUSTARD = (242, 177, 52)


# ---------------------------------------------------------------- easing
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def prog(f, f0, f1):
    return clamp((f - f0) / float(f1 - f0))


def ease_out(t):
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def lerp(a, b, t):
    return a + (b - a) * t


# ---------------------------------------------------------------- brand layers
def alpha_bbox_cols(a):
    return np.where(a.max(axis=0) > 8)[0]


def runs(mask):
    """(start, end) of consecutive True runs."""
    out, s = [], None
    for i, v in enumerate(mask):
        if v and s is None:
            s = i
        if not v and s is not None:
            out.append((s, i))
            s = None
    if s is not None:
        out.append((s, len(mask)))
    return out


class Brand:
    def __init__(self, studio, stacked=False):
        name = "lockup-stacked-dark.png" if stacked else "lockup-horizontal-dark.png"
        img = Image.open(os.path.join(studio, "assets", "brand", "export", name)).convert("RGBA")
        img = img.crop(img.getbbox())
        self.lock = img
        a = np.array(img)[:, :, 3]
        if stacked:
            rows = runs(a.max(axis=1) > 8)
            # first run(s) = mark, last run = the text line
            self.mark_box = (0, 0, img.width, rows[-2][1] if len(rows) > 1 else rows[0][1])
            self.text_boxes = [(0, rows[-1][0], img.width, rows[-1][1])]
            mark = img.crop(self.mark_box)
            mark = mark.crop(mark.getbbox())
            mb = img.crop(self.mark_box).getbbox()
            self.mark_box = (mb[0], mb[1], mb[2], mb[3])
        else:
            cols = runs(a.max(axis=0) > 8)
            # mark = runs before the widest empty gap; text = after it
            gaps = [(cols[i + 1][0] - cols[i][1], i) for i in range(len(cols) - 1)]
            _, gi = max(gaps)
            mx1 = cols[gi][1]
            tx0 = cols[gi + 1][0]
            self.mark_box = (0, 0, mx1, img.height)
            mark = img.crop(self.mark_box)
            mb = mark.getbbox()
            self.mark_box = (mb[0], mb[1], mb[2], mb[3])
            ta = a[:, tx0:]
            rows = runs(ta.max(axis=1) > 8)
            self.text_boxes = [(tx0, r0, img.width, r1) for r0, r1 in rows]
        self.mark = img.crop(self.mark_box)
        m = np.array(self.mark)
        # split the two triangles at the clearest vertical gap in the middle of the mark
        w = m.shape[1]
        colsum = m[:, :, 3].astype(float).sum(axis=0)
        lo, hi = int(w * 0.35), int(w * 0.65)
        self.split = lo + int(np.argmin(colsum[lo:hi]))
        A = m.copy()
        A[:, self.split:, 3] = 0
        B = m.copy()
        B[:, :self.split, 3] = 0
        self.A, self.B = Image.fromarray(A), Image.fromarray(B)
        # the same front triangle without its sun (sun pixels -> sky red), for the sun fade
        rgb = A[:, :, :3].astype(float)
        d = np.sqrt(((rgb - np.array(MUSTARD)) ** 2).sum(axis=2))
        k = np.clip(1 - (d - 40) / 80, 0, 1)[:, :, None]
        ns = A.copy()
        ns[:, :, :3] = (rgb * (1 - k) + np.array(RED) * k).astype(np.uint8)
        self.A_nosun = Image.fromarray(ns)


def scaled(img, s):
    w, h = max(1, round(img.width * s)), max(1, round(img.height * s))
    return img.resize((w, h), Image.LANCZOS)


def paste_center(canvas, img, cx, cy, alpha=1.0):
    if alpha <= 0:
        return
    if alpha < 1:
        a = np.array(img)
        a[:, :, 3] = (a[:, :, 3] * alpha).astype(np.uint8)
        img = Image.fromarray(a)
    canvas.alpha_composite(img, (round(cx - img.width / 2), round(cy - img.height / 2)))


def reveal_lr(img, t):
    """Left-to-right mask reveal with a soft 6% edge."""
    if t >= 1:
        return img
    a = np.array(img)
    w = a.shape[1]
    x = np.arange(w)[None, :]
    edge = max(1.0, w * 0.06)
    m = np.clip((t * (w + edge) - x) / edge, 0, 1)
    a[:, :, 3] = (a[:, :, 3] * m).astype(np.uint8)
    return Image.fromarray(a)


# ---------------------------------------------------------------- caption type
def tracked_text(text, font, tracking_px, fill):
    widths = [font.getlength(c) for c in text]
    total = sum(widths) + tracking_px * (len(text) - 1)
    asc, desc = font.getmetrics()
    img = Image.new("RGBA", (int(total) + 4, asc + desc + 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 2
    for c, w in zip(text, widths):
        d.text((x, 2), c, font=font, fill=fill)
        x += w + tracking_px
    return img, asc


# ---------------------------------------------------------------- the episode source (1 s)
def source_frames(src, start, crop, size, n):
    """n frames from a film (or one still repeated with a 1.8% linear push), cover-fit to size."""
    W, H = size
    frames = []
    if src and os.path.splitext(src)[1].lower() in (".mp4", ".mov", ".mkv", ".webm", ".avi", ".mpg"):
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(start), "-i", src, "-frames:v", str(n),
                              "-r", str(FPS), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                "-of", "csv=p=0", src], capture_output=True, text=True, check=True).stdout.split(",")
        sw, sh = int(probe[0]), int(probe[1])
        arr = np.frombuffer(raw, np.uint8).reshape(-1, sh, sw, 3)
        imgs = [Image.fromarray(x) for x in arr]
        push = [1.0] * n
    else:
        still = Image.open(src).convert("RGB") if src else Image.new("RGB", size, DEEP)
        imgs = [still] * n
        push = [1.0 + 0.018 * i / max(1, n - 1) for i in range(n)]
    for i in range(n):
        im = imgs[min(i, len(imgs) - 1)]
        if crop:
            x0, y0, x1, y1 = crop
            im = im.crop((round(x0 * im.width), round(y0 * im.height), round(x1 * im.width), round(y1 * im.height)))
        # cover fit, then the still push
        s = max(W / im.width, H / im.height) * push[i]
        im = im.resize((max(W, round(im.width * s)), max(H, round(im.height * s))), Image.LANCZOS)
        l, t = (im.width - W) // 2, (im.height - H) // 2
        frames.append(im.crop((l, t, l + W, t + H)))
    return frames


# ---------------------------------------------------------------- full 16:9 intro
def full_frames(studio, src, start, crop):
    W, H = 1920, 1080
    brand = Brand(studio)
    font = ImageFont.truetype(os.path.join(studio, "assets", "brand", "fonts", "Jost-500.ttf"), 46)
    cap_img, _ = tracked_text("FROM THE ORIGINAL RECORD", font, 46 * 0.22, CREAM)

    # folio geometry
    fx, fy, fw, fh = 0.11 * W, 0.05 * H, 0.78 * W, 0.78 * H
    border = 14
    win = (round(fx + border), round(fy + border), round(fx + fw - border), round(fy + fh - border))
    src_imgs = source_frames(src, start, crop, (win[2] - win[0], win[3] - win[1]), 30)

    # final lockup: 66% of frame width, centred
    LW = 0.66 * W
    sf = LW / brand.lock.width
    lock_x0, lock_y0 = (W - LW) / 2, (H - brand.lock.height * sf) / 2
    mb = brand.mark_box
    mark_cx_f = lock_x0 + (mb[0] + mb[2]) / 2 * sf
    mark_cy_f = lock_y0 + (mb[1] + mb[3]) / 2 * sf
    # centred mark in shot 4: 42% of frame height
    s4 = 0.42 * H / brand.mark.height
    A4, B4, A4n = scaled(brand.A, s4), scaled(brand.B, s4), scaled(brand.A_nosun, s4)
    text_layers = []
    for (x0, y0, x1, y1) in brand.text_boxes:
        layer = scaled(brand.lock.crop((x0, y0, x1, y1)), sf)
        text_layers.append((layer, lock_x0 + x0 * sf, lock_y0 + y0 * sf))
    final_mark = scaled(brand.mark, sf)

    out = []
    for f in range(180):
        c = Image.new("RGBA", (W, H), NAVY + (255,))
        d = ImageDraw.Draw(c)
        if f < 72:
            # ---- folio, source, shutter
            rv = ease_out(prog(f, 0, 16))
            if rv > 0:
                d.rectangle([fx, fy, fx + fw * rv, fy + fh], fill=CREAM)
                if f < 48:
                    wx1 = min(win[2], fx + fw * rv - border)
                    if wx1 > win[0]:
                        d.rectangle([win[0], win[1], wx1, win[3]], fill=DEEP)
            if 18 <= f < 48:
                c.paste(src_imgs[f - 18], (win[0], win[1]))
            # caption + red rule
            ca = prog(f, 8, 17) * (1 - prog(f, 48, 58))
            paste_center(c, cap_img, W / 2, 0.905 * H, ca)
            ra = ease_out(prog(f, 12, 18))
            if ra > 0 and f < 58:
                rx0 = 0.44 * W
                d.rectangle([rx0, 0.94 * H, rx0 + 0.12 * W * ra, 0.94 * H + 0.0055 * H], fill=RED)
            if f >= 48:
                t = ease_in_out(prog(f, 48, 72))
                xt, xb = lerp(1.05 * W, -0.05 * W, t), lerp(0.97 * W, -0.13 * W, t)
                d.polygon([(xt, -2), (W + 4, -2), (W + 4, H + 2), (xb, H + 2)], fill=NAVY)
        elif f < 108:
            # ---- the road resolves: triangles slide in from the right, sun fades in
            ta = ease_out(prog(f, 72, 96))
            tb = ease_out(prog(f, 72, 104))
            fade = prog(f, 72, 78)
            sun = prog(f, 99, 108)
            cx, cy = W / 2, H / 2
            paste_center(c, B4, cx + 0.19 * W * (1 - tb), cy, fade)
            paste_center(c, A4n, cx + 0.13 * W * (1 - ta), cy, fade * (1 - sun))
            paste_center(c, A4, cx + 0.13 * W * (1 - ta), cy, fade * sun)
        else:
            # ---- the name arrives, then hold
            t = ease_out(prog(f, 108, 132))   # logo lands before the words arrive
            s = lerp(s4, sf, t)
            cx = lerp(W / 2, mark_cx_f, t)
            cy = lerp(H / 2, mark_cy_f, t)
            m = final_mark if t >= 1 else scaled(brand.mark, s)
            paste_center(c, m, cx, cy)
            for i, (layer, x, y) in enumerate(text_layers):
                r0, r1 = (121, 141) if i == 0 else (127, 147)
                rt = ease_out(prog(f, r0, r1))
                if rt > 0:
                    c.alpha_composite(reveal_lr(layer, rt), (round(x), round(y)))
        out.append(c.convert("RGB"))
    return out, (W, H)


# ---------------------------------------------------------------- 2 s bumper and 1 s Shorts sting
def logo_only_frames(studio, size, n, assemble, move, sun_at, stacked):
    W, H = size
    brand = Brand(studio, stacked=stacked)
    LW = (0.66 if not stacked else 0.86) * W
    sf = LW / brand.lock.width
    lx0 = (W - LW) / 2
    ly0 = (H - brand.lock.height * sf) / 2 if not stacked else 0.40 * H - brand.lock.height * sf / 2
    mb = brand.mark_box
    mcx, mcy = lx0 + (mb[0] + mb[2]) / 2 * sf, ly0 + (mb[1] + mb[3]) / 2 * sf
    s0 = sf * (1.25 if not stacked else 1.0)
    start_c = (W / 2, H / 2) if not stacked else (mcx, mcy)
    A, B, An = scaled(brand.A, s0), scaled(brand.B, s0), scaled(brand.A_nosun, s0)
    texts = [(scaled(brand.lock.crop(b), sf), lx0 + b[0] * sf, ly0 + b[1] * sf) for b in brand.text_boxes]
    out = []
    for f in range(n):
        c = Image.new("RGBA", (W, H), NAVY + (255,))
        if f < assemble[1]:
            ta = ease_out(prog(f, assemble[0], assemble[1] - 2))
            tb = ease_out(prog(f, assemble[0], assemble[1]))
            fade = prog(f, assemble[0], assemble[0] + 4)
            sun = prog(f, *sun_at)
            off = 0.10 * W
            paste_center(c, B, start_c[0] + off * 1.4 * (1 - tb), start_c[1], fade)
            paste_center(c, An, start_c[0] + off * (1 - ta), start_c[1], fade * (1 - sun))
            paste_center(c, A, start_c[0] + off * (1 - ta), start_c[1], fade * sun)
        else:
            t = ease_out(prog(f, move[0], move[1]))
            s = lerp(s0, sf, t)
            m = scaled(brand.mark, s) if t < 1 else scaled(brand.mark, sf)
            sun = prog(f, *sun_at)
            if sun < 1:
                mm = Image.alpha_composite(
                    Image.alpha_composite(Image.new("RGBA", brand.mark.size), brand.B),
                    Image.blend(brand.A_nosun, brand.A, sun))
                m = scaled(mm, s)
            paste_center(c, m, lerp(start_c[0], mcx, t), lerp(start_c[1], mcy, t))
            for i, (layer, x, y) in enumerate(texts):
                lag = 0 if stacked else 6   # 16:9: words wait until the logo has landed
                rt = ease_out(prog(f, move[0] + lag + 3 * i, move[1] + 2 + 3 * i))
                if rt > 0:
                    c.alpha_composite(reveal_lr(layer, rt), (round(x), round(y)))
        out.append(c.convert("RGB"))
    return out


# ---------------------------------------------------------------- sound (all synthesized, 48 kHz)
SR = 48000


def band_noise(n, lo, hi, seed):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    X = np.fft.rfft(x)
    fr = np.fft.rfftfreq(n, 1 / SR)
    X[(fr < lo) | (fr > hi)] = 0
    y = np.fft.irfft(X, n)
    return y / (np.abs(y).max() + 1e-9)


def piano(freq, dur, end_by):
    n = int(SR * dur)
    t = np.arange(n) / SR
    y = np.zeros(n)
    Bc = 0.0004
    for k in range(1, 9):
        fk = k * freq * np.sqrt(1 + Bc * k * k)
        if fk > SR / 2:
            break
        amp = 1 / k ** 1.35
        y += amp * np.sin(2 * np.pi * fk * t) * np.exp(-t * (0.9 + 0.55 * k))
    att = np.clip(t / 0.008, 0, 1)
    y *= att
    ham = band_noise(n, 800, 5000, int(freq)) * np.exp(-t / 0.006) * 0.08
    y += ham
    rel = np.clip((end_by - t) / 0.25, 0, 1)   # gone by end_by
    return y * rel / np.abs(y).max()


def place_sig(buf, sig, at, gain):
    i = int(at * SR)
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i] * gain


def reverb(x, secs=0.9, mix=0.14):
    n = int(SR * secs)
    ir = np.random.default_rng(7).standard_normal(n) * np.exp(-np.arange(n) / SR / (secs / 5))
    ir = ir / np.abs(ir).sum() * 6
    wet = np.fft.irfft(np.fft.rfft(x, len(x) + n) * np.fft.rfft(ir, len(x) + n))[: len(x)]
    return x + mix * wet


def sound(variant):
    dur = {"full": 6.0, "bumper": 2.0, "short": 1.0}[variant]
    n = int(SR * dur)
    buf = np.zeros(n)
    if variant == "full":
        # paper feed 0.00-0.60: dry paper slide
        m = int(0.6 * SR)
        tt = np.arange(m) / SR
        grain = np.abs(band_noise(m, 3, 40, 3))
        paper = band_noise(m, 1800, 7500, 1) * (0.55 + 0.45 * grain) * np.sin(np.pi * tt / 0.6) ** 0.7
        place_sig(buf, paper, 0.0, 0.10)
        # archive click 0.60-0.867: one restrained mechanical click
        m = int(0.267 * SR)
        tt = np.arange(m) / SR
        click = band_noise(m, 2500, 9000, 2) * np.exp(-tt / 0.0025) * 0.9
        thump = np.sin(2 * np.pi * 95 * tt) * np.exp(-tt / 0.03) * 0.8
        tail = band_noise(m, 600, 3000, 4) * np.exp(-tt / 0.04) * 0.15
        place_sig(buf, click + thump + tail, 0.6, 0.30)
        # soft reverse movement 1.60-2.50: low paper/transport, falling
        m = int(0.9 * SR)
        tt = np.arange(m) / SR
        low = band_noise(m, 80, 1400, 5) * np.sin(np.pi * tt / 0.9) ** 1.5
        hiss = band_noise(m, 1400, 4000, 6) * np.sin(np.pi * tt / 0.9) ** 2 * np.clip(1 - tt / 0.9, 0, 1) * 0.35
        place_sig(buf, low + hiss, 1.6, 0.09)
        # the signature: C4 then G4
        place_sig(buf, piano(261.63, 0.933, 0.933), 3.4, 0.42)
        place_sig(buf, piano(392.00, 1.433, 1.433), 4.1, 0.40)
    elif variant == "bumper":
        place_sig(buf, piano(261.63, 0.8, 0.8), 0.35, 0.42)
        place_sig(buf, piano(392.00, 1.25, 1.2), 0.75, 0.40)
    else:
        place_sig(buf, piano(261.63, 0.45, 0.45), 0.0, 0.40)
        place_sig(buf, piano(392.00, 0.68, 0.68), 0.3, 0.38)
    buf = reverb(buf)
    buf = buf / (np.abs(buf).max() + 1e-9) * 0.5
    return buf


def write_wav(path, x):
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    st = np.stack([pcm, pcm], axis=1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(st.tobytes())


# ---------------------------------------------------------------- encode
def encode(frames, size, wav, out):
    W, H = size
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-i", wav,
                          "-af", "loudnorm=I=-20:TP=-3:LRA=11",
                          "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
                          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", out], stdin=subprocess.PIPE)
    for fr in frames:
        p.stdin.write(fr.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError("ffmpeg failed")


def build(studio, out, variant="full", source=None, start=0.0, crop=None):
    if variant == "full":
        frames, size = full_frames(studio, source, start, crop)
    elif variant == "bumper":
        size = (1920, 1080)
        frames = logo_only_frames(studio, size, 60, assemble=(0, 15), move=(15, 27), sun_at=(10, 15), stacked=False)
    else:
        size = (1080, 1920)
        frames = logo_only_frames(studio, size, 30, assemble=(0, 9), move=(9, 19), sun_at=(9, 19), stacked=True)
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "intro.wav")
        write_wav(wav, sound(variant))
        encode(frames, size, wav, out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--studio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", default="full", choices=["full", "bumper", "short"])
    ap.add_argument("--source")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--crop", help="x0,y0,x1,y1 in 0-1 of the source frame")
    a = ap.parse_args()
    crop = [float(v) for v in a.crop.split(",")] if a.crop else None
    print(build(a.studio, a.out, a.variant, a.source, a.start, crop))
