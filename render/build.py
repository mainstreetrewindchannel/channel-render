"""Render a finished long video, Shorts, captions and thumbnails from a job's timeline.json.

Usage: python scripts/render/build.py jobs/<slug> [--stage all|tts|visuals|assemble|shorts|thumbs] [--draft]

timeline.json shape:
{
  "title": "...",
  "intro": {"mood": "upbeat"},
  "chapters": [
    {"title": "...", "num": 1, "mood": "calm", "card": true,
     "beats": [{"say": "narration text", "show": {...visual...}, "pause": 0.4}, ...]}
  ],
  "end": {"text": "...", "sub": "..."},
  "shorts": [{"id": "s1", "hook": "...", "say": ["line", ...], "show": [{...}, ...]}],
  "thumbnails": [{"big": "...", "accent": "...", "tag": "...", "image": "<visual src>"}]
}
Visual ("show") types:
  image     {"src": url|path, "credit": "...", "motion": "in|out|left|right|up|down", "focus": [x, y]}
  newspaper {"src": image url, "words": url to LoC word coordinates (optional), "find": ["EGGS"], "box": [x0,y0,x1,y1] (0-1),
             "credit": "..."}  -> starts on the full page, pushes into the ad
  chapter|title|statement|price|receipt|bars  -> brand graphics (see graphics.py)
"""
import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import graphics as G  # noqa: E402

FPS = 30
W, H = 1920, 1080
VOICE = "am_eric"
SPEED = 0.95
SR = 24000
UA = {"User-Agent": "Mozilla/5.0 (channel-studio; mainstreetrewindchannel@gmail.com)"}


def sh(*cmd, quiet=True):
    r = subprocess.run(list(map(str, cmd)), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(map(str, cmd))[:400]}\n{r.stderr[-2000:]}")
    return r.stdout


def duration(path):
    return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path).strip())


def download(src, out, timeout):
    """Download with retries; archives like the Library of Congress throttle with 5xx errors."""
    import time
    last = None
    for attempt in range(8):
        try:
            req = urllib.request.Request(src, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r, open(out + ".part", "wb") as fh:
                shutil.copyfileobj(r, fh)
            os.replace(out + ".part", out)
            return out
        except Exception as e:
            last = e
            print(f"download retry {attempt+1}: {src[:90]} ({e})", flush=True)
            time.sleep(min(90, 10 * (attempt + 1)))
    raise RuntimeError(f"download failed after retries: {src} ({last})")


def key(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:12]


class Job:
    def __init__(self, path, draft=False):
        self.dir = os.path.abspath(path)
        self.tl = json.load(open(os.path.join(self.dir, "timeline.json")))
        self.work = os.path.join(ROOT, "out", os.path.basename(self.dir))
        self.draft = draft
        for d in ("audio", "media", "gfx", "clips", "shorts", "final"):
            os.makedirs(os.path.join(self.work, d), exist_ok=True)
        self.credits = []

    def p(self, *a):
        return os.path.join(self.work, *a)

    # ------------------------------------------------------------ sequence
    def sequence(self):
        """Flatten chapters into an ordered list of beats, inserting chapter cards."""
        seq = []
        for ci, ch in enumerate(self.tl["chapters"]):
            if ch.get("card", True) and ch.get("title"):
                card = ch.get("card_show") or {"type": "chapter", "text": ch["title"], "num": ch.get("num"), "sub": ch.get("sub")}
                seq.append({"show": card, "say": "", "hold": 3.6 if card.get("time") else 3.2, "chapter": ci, "card": True})
            for b in ch["beats"]:
                seq.append({**b, "chapter": ci})
        return seq

    # ------------------------------------------------------------ narration
    def tts(self):
        from kokoro import KPipeline
        import numpy as np
        import soundfile as sf
        pipe = KPipeline(lang_code="a")
        seq = self.sequence()
        for i, b in enumerate(seq):
            if not b.get("say"):
                continue
            out = self.p("audio", f"beat-{key(b.get('tts') or b['say'], VOICE, SPEED)}.wav")
            if os.path.exists(out):
                continue
            chunks = []
            for _g, _p, audio in pipe(b.get("tts") or b["say"], voice=VOICE, speed=SPEED):
                if audio is None:
                    continue
                a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
                chunks.append(a.astype("float32"))
                chunks.append(np.zeros(int(SR * 0.18), dtype="float32"))
            sf.write(out, np.concatenate(chunks) if chunks else np.zeros(SR, dtype="float32"), SR)
            print(f"tts {i+1}/{len(seq)}", flush=True)

    def beat_audio(self, b):
        if not b.get("say"):
            return None
        return self.p("audio", f"beat-{key(b.get('tts') or b['say'], VOICE, SPEED)}.wav")

    def timing(self):
        """Duration of every beat: narration length + pause, or a fixed hold for silent beats."""
        seq = self.sequence()
        for b in seq:
            a = self.beat_audio(b)
            if a and os.path.exists(a):
                b["dur"] = duration(a) + b.get("pause", 0.35)
            elif a:
                b["dur"] = max(2.5, len(b["say"].split()) / 2.5) + b.get("pause", 0.35)  # draft estimate
            else:
                b["dur"] = b.get("hold", 3.0)
        return seq

    # ------------------------------------------------------------ media
    def fetch(self, src):
        if not src.startswith("http"):
            return os.path.join(ROOT, src) if not os.path.isabs(src) else src
        ext = os.path.splitext(src.split("?")[0])[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp"):
            ext = ".jpg"
        out = self.p("media", key(src) + ext)
        if os.path.exists(out):
            try:
                from PIL import Image
                Image.MAX_IMAGE_PIXELS = None
                with Image.open(out) as im:
                    im.load()
                    lo, hi = im.convert("L").getextrema()
                if hi - lo < 10:
                    raise ValueError("blank image")
            except Exception:
                os.remove(out)  # cached file is broken or blank; fetch it again
        if not os.path.exists(out):
            for attempt in range(3):   # LoC sometimes ends a big image early; fetch again if it doesn't decode fully
                download(src, out, 180)
                try:
                    from PIL import Image
                    Image.MAX_IMAGE_PIXELS = None
                    with Image.open(out) as im:
                        im.load()
                    break
                except Exception as e:
                    print(f"image incomplete, refetching ({e}): {src[:90]}", flush=True)
        if ext in (".gif", ".tif", ".tiff", ".webp"):
            png = out.rsplit(".", 1)[0] + ".png"
            if not os.path.exists(png):
                sh("convert", out + "[0]", png)
            return png
        return out

    def fetch_file(self, src):
        """Download a non-image file (e.g. archival film) once and cache it."""
        if not src.startswith("http"):
            return os.path.join(ROOT, src) if not os.path.isabs(src) else src
        ext = os.path.splitext(src.split("?")[0])[1].lower() or ".mp4"
        out = self.p("media", key(src) + ext)
        if not os.path.exists(out):
            download(src, out, 600)
        return out

    def film_clip(self, show, dur, out):
        """Archival film: a muted segment from show['start'] seconds.
        crop: [x0,y0,x1,y1] fractions to reframe; fit: "fill" crops to 16:9, default keeps the whole frame
        over a blurred, darkened copy of itself. Light grain unifies sources. caption: lower-third text."""
        src = self.fetch_file(show["src"])
        start = float(show.get("start", 0))
        speed = float(show.get("speed", 1.0))
        frames = max(1, round(dur * FPS))
        pre = f"setpts=PTS/{speed},fps={FPS}"
        if show.get("crop"):
            x0, y0, x1, y1 = show["crop"]
            pre += f",crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f}"
        grade = f"eq=saturation={show.get('saturation', 1.0)}:contrast=1.04,noise=alls=7:allf=t"
        if show.get("fit") == "fill":
            vf = f"[0:v]{pre},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1,{grade}[v]"
        else:
            vf = (f"[0:v]{pre},split=2[a][b];"
                  f"[a]scale={W//8}:{H//8}:force_original_aspect_ratio=increase,crop={W//8}:{H//8},boxblur=6:2,"
                  f"scale={W}:{H},eq=brightness=-0.22:saturation=0.6,setsar=1[bg];"
                  f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1[fg];"
                  f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{grade}[v]")
        fd = 0.25 if show.get("dissolve") else 0.3
        vf += f";[v]fade=t=in:st=0:d={fd}:color=black,fade=t=out:st={max(0, dur - fd):.2f}:d={fd}:color=black[v]" if not show.get("nofade") else ""
        inputs = ["-ss", f"{start:.2f}", "-i", src]
        n = 1
        for ov in self.overlay_layers(show):
            inputs += ["-loop", "1", "-t", f"{dur:.2f}", "-i", ov]
            fc_ov = f"[{n}:v]format=rgba,fade=t=in:st=0.4:d=0.4:alpha=1,fade=t=out:st={max(0.5, dur - 0.5):.2f}:d=0.4:alpha=1[o{n}]"
            vf += f";{fc_ov};[v][o{n}]overlay=0:0:format=auto[v]"
            n += 1
        vf += ";[v]format=yuv420p[v]"
        if show.get("credit"):
            self.credits.append(show["credit"])
        sh("ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", vf, "-map", "[v]", "-an",
           "-frames:v", frames, "-r", FPS, "-c:v", "libx264", "-preset", "veryfast" if self.draft else "medium",
           "-crf", "26" if self.draft else "18", out)
        return out

    def overlay_layers(self, show):
        """Transparent PNG layers for a shot: lower-third caption, then source credit."""
        layers = []
        if show.get("caption"):
            layers.append(G.caption_overlay(show["caption"], self.p("gfx")))
        if show.get("credit"):
            layers.append(G.credit_overlay(show["credit"], self.p("gfx")))
        return layers

    def newspaper_box(self, show, img_w, img_h):
        """Find the ad region: explicit box, or the bounding box of searched words from LoC word coordinates."""
        if show.get("box"):
            x0, y0, x1, y1 = show["box"]
            return x0 * img_w, y0 * img_h, x1 * img_w, y1 * img_h
        if show.get("words") and show.get("find"):
            req = urllib.request.Request(show["words"], headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            coords = []
            for word, boxes in data.get("coords", data).items() if isinstance(data, dict) else []:
                if any(word.lower() == f.lower() for f in show["find"]):
                    coords.extend(boxes)
            if coords:
                # LoC coords are [x, y, w, h] in the page image's native pixels
                xs = [c[0] for c in coords] + [c[0] + c[2] for c in coords]
                ys = [c[1] for c in coords] + [c[1] + c[3] for c in coords]
                cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                bw = max(max(xs) - min(xs), img_w * 0.22)
                bh = bw * H / W
                return cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2
        return 0, 0, img_w, img_h

    # ------------------------------------------------------------ visuals
    def still_for(self, show):
        t = show["type"]
        if t in G.RENDERERS:
            return G.RENDERERS[t](show, self.p("gfx"))
        if t in ("image", "newspaper"):
            return self.fetch(show["src"])
        raise ValueError(f"unknown visual type {t}")

    def clip(self, b, idx):
        """Render one beat to a silent H.264 clip of exactly b['dur'] seconds."""
        show = b["show"]
        dur = b["dur"]
        frames = max(1, round(dur * FPS))
        out = self.p("clips", f"c{idx:04d}-{key(show, round(dur, 2), self.draft, 'v2')}.mp4")
        if os.path.exists(out):
            return out
        if isinstance(show, list):
            return self.multi_shot(show, dur, out, idx)
        return self.shot(show, dur, out)

    def multi_shot(self, shots, dur, out, idx):
        """Several shots inside one narration beat, joined with short dissolves.
        Each shot may carry "w" (share of the beat, default equal)."""
        xf = 0.4
        weights = [float(s_.get("w", 1)) for s_ in shots]
        tot = sum(weights)
        n = len(shots)
        usable = dur + xf * (n - 1)
        durs = [usable * w_ / tot for w_ in weights]
        shots = [dict(x) for x in shots]
        for j in range(1, n):   # never replay the same moment of a film twice in one beat
            p_, c_ = shots[j - 1], shots[j]
            if p_["type"] == c_["type"] == "film" and p_["src"] == c_["src"]:
                p_end = float(p_.get("start", 0)) + durs[j - 1] * float(p_.get("speed", 1.0))
                if float(p_.get("start", 0)) <= float(c_.get("start", 0)) < p_end:
                    c_["start"] = round(p_end + 0.2, 2)
                    print(f"WARNING overlapping shots in one beat; moved second to {c_['start']}s", flush=True)
        parts = []
        for j, (s_, d_) in enumerate(zip(shots, durs)):
            s2 = {**s_, "dissolve": True, "nofade": True}
            part = self.p("clips", f"s{idx:04d}-{j}-{key(s2, round(d_, 2), self.draft, 'v2')}.mp4")
            if not os.path.exists(part):
                self.shot(s2, d_, part)
            parts.append((part, d_))
        inputs, fc, prev, off = [], [], "0:v", 0.0
        for j, (part, d_) in enumerate(parts):
            inputs += ["-i", part]
        for j in range(1, n):
            off += parts[j - 1][1] - xf
            lab = f"x{j}"
            fc.append(f"[{prev}][{j}:v]xfade=transition=fade:duration={xf}:offset={off:.3f}[{lab}]")
            prev = lab
        if n == 1:
            sh("cp", parts[0][0], out)
            return out
        fades = []   # no dip to black where the camera is carrying on across the beat boundary
        if not shots[0].get("continue_from"):
            fades.append("fade=t=in:st=0:d=0.3")
        if not shots[-1].get("hold_out"):
            fades.append(f"fade=t=out:st={max(0, dur - 0.3):.2f}:d=0.3")
        fc.append(f"[{prev}]{','.join(fades) or 'null'}[out]")
        prev = "out"
        sh("ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(fc), "-map", f"[{prev}]",
           "-frames:v", max(1, round(dur * FPS)), "-r", FPS, "-c:v", "libx264", "-preset", "veryfast" if self.draft else "medium",
           "-crf", "26" if self.draft else "18", "-pix_fmt", "yuv420p", out)
        for part, _ in parts:
            os.remove(part)   # sub-shots are only needed until they're joined; runner disk is limited
        return out

    def shot(self, show, dur, out):
        frames = max(1, round(dur * FPS))
        if show["type"] == "film":
            return self.film_clip(show, dur, out)
        t = show["type"]
        if t in ("newspaper", "image") and not show.get("legacy"):
            import motion
            path = self.fetch(show["src"])
            ovs = [(motion.load_rgba(o), 0.0, dur) for o in self.overlay_layers(show)]
            if show.get("credit"):
                self.credits.append(show["credit"])
            motion.render(t, path, out, dur, show, w=W, h=H, fps=FPS, draft=self.draft, overlays=ovs)
            return out
        still = self.still_for(show)
        scale = 1 if self.draft else 2
        # deep zooms into newspaper pages need a big canvas; photos and graphics don't
        zw, zh = (W * scale * 2, H * scale * 2) if t == "newspaper" else (W * (scale + 1), H * (scale + 1))

        if t == "newspaper":
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = None
            iw, ih = Image.open(still).size
            x0, y0, x1, y1 = self.newspaper_box(show, iw, ih)
            # work canvas: page fitted to 16:9 with padding, then zoom from full page into the box
            vf = (f"scale=-2:{zh}:flags=lanczos,pad=max(iw\\,{zw}):{zh}:(ow-iw)/2:0:color=0x121933,"
                  f"setsar=1")
            # compute box in padded canvas coordinates
            sc = zh / ih
            pw = max(iw * sc, zw)
            ox = (pw - iw * sc) / 2
            bx0, by0, bx1, by1 = x0 * sc + ox, y0 * sc, x1 * sc + ox, y1 * sc
            z_end = min(8.0, max(1.0, pw / max(bx1 - bx0, 1)))
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            ease = f"(1-cos(PI*min(on/{frames*0.8:.1f}\\,1)))/2"
            z = f"1+({z_end:.4f}-1)*{ease}"
            x = f"max(0\\,min(iw-iw/zoom\\,({pw/2:.1f}+({cx:.1f}-{pw/2:.1f})*{ease})-iw/zoom/2))"
            y = f"max(0\\,min(ih-ih/zoom\\,({zh/2:.1f}+({cy:.1f}-{zh/2:.1f})*{ease})-ih/zoom/2))"
            vf += f",zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS}"
        elif t == "image":
            motion = show.get("motion") or random.Random(show["src"]).choice(["in", "out", "left", "right"])
            fx, fy = show.get("focus", [0.5, 0.45])
            vf = (f"scale={zw}:{zh}:force_original_aspect_ratio=increase:flags=lanczos,crop={zw}:{zh},setsar=1")
            p = f"(on/{frames})"
            zmax = 1.18
            if motion == "in":
                z, x, y = f"1+{zmax-1}*{p}", f"(iw-iw/zoom)*{fx}", f"(ih-ih/zoom)*{fy}"
            elif motion == "out":
                z, x, y = f"{zmax}-{zmax-1}*{p}", f"(iw-iw/zoom)*{fx}", f"(ih-ih/zoom)*{fy}"
            elif motion == "left":
                z, x, y = f"{zmax}", f"(iw-iw/zoom)*(1-{p})", f"(ih-ih/zoom)*{fy}"
            elif motion == "right":
                z, x, y = f"{zmax}", f"(iw-iw/zoom)*{p}", f"(ih-ih/zoom)*{fy}"
            elif motion == "up":
                z, x, y = f"{zmax}", f"(iw-iw/zoom)*{fx}", f"(ih-ih/zoom)*(1-{p})"
            else:
                z, x, y = f"{zmax}", f"(iw-iw/zoom)*{fx}", f"(ih-ih/zoom)*{p}"
            vf += f",zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS}"
        else:
            # brand graphic: held perfectly still (slow pushes on sharp text shimmer), soft fade in and out
            fd = min(0.35, dur / 4)
            vf = (f"scale={W}:{H},setsar=1,loop=loop={frames}:size=1:start=0,fps={FPS},"
                  f"fade=t=in:st=0:d={fd:.2f}:c=0x1B2340,fade=t=out:st={max(0, dur - fd):.2f}:d={fd:.2f}:c=0x1B2340")

        vf += ",format=yuv420p"
        inputs = ["-i", still]
        fc = f"[0:v]{vf}[v]"
        if show.get("credit"):
            cr = G.credit_overlay(show["credit"], self.p("gfx"))
            inputs += ["-i", cr]
            fc += ";[v][1:v]overlay=0:0:format=auto[v]"
        # gentle fade from/to black on chapter cards only
        # graphics (including chapter cards) already fade in and out of navy above
        sh("ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fc, "-map", "[v]",
           "-frames:v", frames, "-r", FPS, "-c:v", "libx264", "-preset", "veryfast" if self.draft else "medium",
           "-crf", "26" if self.draft else "18", out)
        if show.get("credit"):
            self.credits.append(show["credit"])
        return out

    def link_shots(self, seq):
        """Consecutive beats on the same newspaper page become one continuous camera move:
        the next beat starts exactly where the last one ended (no fade out to the full page and back in)."""
        # flatten: shots inside a multi-shot beat link too (Montgomery -> Springfield on one page = one move)
        flat = []
        for b_ in seq:
            flat += b_["show"] if isinstance(b_["show"], list) else [b_["show"]]
        for sa, sb in zip(flat, flat[1:]):
            if isinstance(sa, dict) and isinstance(sb, dict) and sa.get("type") == "newspaper" and sb.get("type") == "newspaper" and sa["src"] == sb["src"]:
                la = sa.get("_levels", 1)   # how many slow settles that shot ended after
                sa["hold_out"] = True
                sb["continue_from"] = {"box": sa.get("box", [0, 0, 1, 1]), "pad": sa.get("pad", 0.08), "levels": la}
                same = list(sa.get("box", [0, 0, 1, 1])) == list(sb.get("box", [0, 0, 1, 1]))
                sb["_levels"] = la + 1 if same else 1
        return seq

    def visuals(self):
        seq = self.link_shots(self.timing())
        clips = []
        for i, b in enumerate(seq):
            c = self.clip(b, i)
            # a clip cut short (e.g. cached from a cancelled run) silently truncates the whole join: check every one
            ok = False
            for attempt in range(2):
                try:
                    got = duration(c)
                    ok = abs(got - b["dur"]) < 0.25
                except Exception:
                    got, ok = -1, False
                if ok:
                    break
                print(f"WARNING clip {i+1} is {got:.2f}s, expected {b['dur']:.2f}s; re-rendering", flush=True)
                os.remove(c)
                c = self.clip(b, i)
            if not ok:
                raise RuntimeError(f"clip {i+1} keeps coming out the wrong length: {c}")
            clips.append(c)
            kind = "+".join(x["type"] for x in b["show"]) if isinstance(b["show"], list) else b["show"]["type"]
            print(f"clip {i+1}/{len(seq)} ({kind}, {b['dur']:.1f}s)", flush=True)
        return seq, clips

    # ------------------------------------------------------------ audio
    def narration_track(self, seq):
        """Concatenate beat audio with silences so it lines up with the clips exactly."""
        parts = []
        for i, b in enumerate(seq):
            a = self.beat_audio(b)
            seg = self.p("audio", f"seg-{i:04d}.wav")
            dur = b["dur"]
            if a and os.path.exists(a):
                sh("ffmpeg", "-y", "-loglevel", "error", "-i", a, "-af", f"apad=whole_dur={dur:.3f}",
                   "-t", f"{dur:.3f}", "-ar", "48000", "-ac", "1", seg)
            else:
                sh("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                   "-t", f"{dur:.3f}", seg)
            parts.append(seg)
        lst = self.p("audio", "narr.txt")
        open(lst, "w").write("".join(f"file '{x}'\n" for x in parts))
        out = self.p("audio", "narration.wav")
        sh("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out)
        return out

    def music_track(self, seq, total):
        """Pick one track per chapter by mood, loop to length, crossfade between chapters, level to -30 LUFS."""
        cat = json.load(open(os.path.join(ROOT, "assets", "music", "catalog.json")))["tracks"]
        by_mood = {}
        for t in cat:
            by_mood.setdefault(t["mood"], []).append(t)
        rng = random.Random(self.tl.get("title", "x"))
        used = []
        # chapter spans
        spans, start = [], 0.0
        cur = seq[0]["chapter"]
        for b in seq:
            if b["chapter"] != cur:
                spans.append((cur, start))
                cur = b["chapter"]
                start = sum(x["dur"] for x in seq[:seq.index(b)])
        spans.append((cur, start))
        pieces = []
        for k, (ci, s0) in enumerate(spans):
            s1 = spans[k + 1][1] if k + 1 < len(spans) else total
            mood = self.tl["chapters"][ci].get("mood", "calm")
            pool = [t for t in by_mood.get(mood, by_mood["calm"]) if t["file"] not in used] or by_mood.get(mood, by_mood["calm"])
            tr = rng.choice(pool)
            used.append(tr["file"])
            if tr.get("attribution"):
                self.credits.append("Music: " + tr["attribution"])
            seg = self.p("audio", f"music-{k:02d}.wav")
            length = s1 - s0 + 2.0
            sh("ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", os.path.join(ROOT, "assets", "music", tr["file"]),
               "-t", f"{length:.3f}", "-af", "loudnorm=I=-30:TP=-6:LRA=11,afade=t=in:d=1.5,afade=t=out:st=%.3f:d=2" % (length - 2),
               "-ar", "48000", "-ac", "2", seg)
            pieces.append((seg, s0))
        # lay pieces on the timeline
        inputs, fc = [], []
        for j, (seg, s0) in enumerate(pieces):
            inputs += ["-i", seg]
            fc.append(f"[{j}:a]adelay={int(s0*1000)}|{int(s0*1000)}[m{j}]")
        fc.append("".join(f"[m{j}]" for j in range(len(pieces))) + f"amix=inputs={len(pieces)}:normalize=0,atrim=0:{total:.3f}[out]")
        out = self.p("audio", "music.wav")
        sh("ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(fc), "-map", "[out]", out)
        return out

    def mix(self, narration, music, total):
        out = self.p("audio", "mix.wav")
        fc = ("[0:a]aformat=channel_layouts=stereo,asplit=2[nv][sc];"
              "[1:a][sc]sidechaincompress=threshold=0.03:ratio=6:attack=30:release=600[mus];"
              "[nv][mus]amix=inputs=2:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[out]")
        sh("ffmpeg", "-y", "-loglevel", "error", "-i", narration, "-i", music, "-filter_complex", fc,
           "-map", "[out]", "-t", f"{total:.3f}", "-ar", "48000", out)
        return out

    # ------------------------------------------------------------ captions
    def captions(self, seq):
        """SRT built from the known script text, split into short lines and timed within each beat."""
        lines, t = [], 0.0
        for b in seq:
            if b.get("say"):
                a = self.beat_audio(b)
                speak = duration(a) if a and os.path.exists(a) else b["dur"] - b.get("pause", 0.35)
                words = b["say"].split()
                chunks, cur = [], []
                for w_ in words:
                    cur.append(w_)
                    if len(" ".join(cur)) > 38 or re.search(r"[.!?;:]$", w_):
                        chunks.append(cur)
                        cur = []
                if cur:
                    chunks.append(cur)
                total_chars = sum(len(" ".join(c)) for c in chunks) or 1
                tt = t
                for c in chunks:
                    d = speak * len(" ".join(c)) / total_chars
                    lines.append((tt, tt + d, " ".join(c)))
                    tt += d
            t += b["dur"]

        def ts(x):
            h, r = divmod(x, 3600)
            m, s = divmod(r, 60)
            return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")
        out = os.path.join(self.p("final"), "captions.srt")
        with open(out, "w") as fh:
            for i, (a, b2, txt) in enumerate(lines, 1):
                fh.write(f"{i}\n{ts(a)} --> {ts(b2)}\n{txt}\n\n")
        return out

    # ------------------------------------------------------------ assemble
    def assemble(self):
        seq, clips = self.visuals()
        total = sum(b["dur"] for b in seq)
        lst = self.p("clips", "list.txt")
        open(lst, "w").write("".join(f"file '{c}'\n" for c in clips))
        body = self.p("clips", "body.mp4")
        # re-encode while joining: clips come from different encoders (and from cache), and a stream-copy join
        # can silently stop at the first mismatch. One pass also gives a sensible delivery bitrate.
        sh("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-r", FPS,
           "-c:v", "libx264", "-preset", "veryfast" if self.draft else "medium", "-crf", "19",
           "-maxrate", "14M", "-bufsize", "28M", "-pix_fmt", "yuv420p", body)
        got = duration(body)
        if abs(got - total) > 2.0:
            raise RuntimeError(f"joined video is {got:.1f}s but the timeline is {total:.1f}s")
        narration = self.narration_track(seq)
        music = self.music_track(seq, total)
        mix = self.mix(narration, music, total)
        final = self.p("final", "video.mp4")
        sh("ffmpeg", "-y", "-loglevel", "error", "-i", body, "-i", mix, "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", final)
        self.captions(seq)
        # chapter timestamps for the description
        chapters, t = [], 0.0
        for b in seq:
            if b.get("card"):
                chapters.append((t, b["show"]["text"]))
            t += b["dur"]
        if chapters and chapters[0][0] > 0:
            chapters.insert(0, (0.0, "Intro"))
        with open(self.p("final", "chapters.txt"), "w") as fh:
            for t0, name in chapters:
                fh.write(f"{int(t0//60)}:{int(t0%60):02d} {name}\n")
        with open(self.p("final", "credits.json"), "w") as fh:
            json.dump(sorted(set(self.credits)), fh, indent=2)
        print(f"final video {total/60:.1f} min -> {final}")
        return final

    # ------------------------------------------------------------ thumbnails
    def thumbs(self):
        outs = []
        for i, t in enumerate(self.tl.get("thumbnails", [])):
            img = self.fetch(t["image"]) if t.get("image") else None
            png = G.thumbnail(t, self.p("gfx"), image_path=img)
            dst = self.p("final", f"thumb-{i+1}.png")
            sh("convert", png, "-quality", "92", dst.replace(".png", ".jpg"))
            outs.append(dst.replace(".png", ".jpg"))
        return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--stage", default="all")
    ap.add_argument("--draft", action="store_true")
    a = ap.parse_args()
    job = Job(a.job, draft=a.draft)
    try:
        if a.stage in ("all", "tts"):
            job.tts()
        if a.stage in ("all", "assemble", "visuals"):
            job.assemble()
        if a.stage in ("all", "thumbs"):
            job.thumbs()
        if a.stage in ("all", "shorts"):
            import shorts
            shorts.build(job)
    finally:
        G.close()


if __name__ == "__main__":
    main()
