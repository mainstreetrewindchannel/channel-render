"""Vertical Shorts (1080x1920) built from a job's timeline.json "shorts" list.

Each Short: {"id": "s1", "hook": "top text", "mood": "upbeat",
             "lines": [{"say": "...", "show": {...same visual types as the long video...}}, ...]}
Narration is re-voiced per line (same voice), visuals sit in the middle band over a blurred fill,
captions are burned in, large, two to four words at a time.
"""
import json
import os
import random
import re

import graphics as G

FPS = 30
SW, SH = 1080, 1920
MUSIC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "music"))
FONTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "brand", "fonts"))


def _ass_time(x):
    h, r = divmod(x, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass(events, out):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {SW}
PlayResY: {SH}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Jost,84,&H00D7EAF3,&H0034B1F2,&H00331912,&H80000000,-1,0,0,0,100,100,0,0,1,7,2,2,70,70,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(out, "w") as fh:
        fh.write(head)
        for a, b, txt in events:
            fh.write(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Cap,,0,0,0,,{txt.upper()}\n")
    return out


def _vertical_still(job, show, key):
    """A picture for the Short's middle band (1080x960): ad crops, photos, or graphics drawn for that band."""
    t = show["type"]
    if t in G.RENDERERS:
        return G.RENDERERS[t](show, job.p("gfx"), w=1080, h=960)
    if t == "film":
        import subprocess
        vid = job.fetch_file(show["src"])
        out = job.p("media", f"frame-{key(show)}.png")
        if not os.path.exists(out):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(float(show.get("start", 0)) + 1), "-i", vid,
                            "-frames:v", "1", out], check=True)
        return out
    src = job.fetch(show["src"])
    if t == "newspaper" and show.get("box"):
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        out = job.p("media", f"crop-{key(show, 'v2')}.png")
        if not os.path.exists(out):
            im = Image.open(src)
            w, h = im.size
            x0, y0, x1, y1 = show["box"]
            cx, cy = (x0 + x1) / 2 * w, (y0 + y1) / 2 * h
            bw = (x1 - x0) * w * 1.08
            bh = (y1 - y0) * h * 1.12
            box = (max(0, cx - bw / 2), max(0, cy - bh / 2), min(w, cx + bw / 2), min(h, cy + bh / 2))
            im.crop(tuple(int(v) for v in box)).save(out)
        return out
    return src


def build(job):
    import __main__ as _b  # the running build.py (a different "build" module exists in assets/brand)
    sh, duration, key, VOICE, SPEED, SR = _b.sh, _b.duration, _b.key, _b.VOICE, _b.SPEED, _b.SR
    shorts = job.tl.get("shorts", [])
    if not shorts:
        return []
    from kokoro import KPipeline
    import numpy as np
    import soundfile as sf
    pipe = KPipeline(lang_code="a")
    cat = json.load(open(os.path.join(MUSIC, "catalog.json")))["tracks"]
    outs = []
    for s in shorts:
        sdir = job.p("shorts", s["id"])
        os.makedirs(sdir, exist_ok=True)
        clips, events, t = [], [], 0.0
        auds = []
        for i, ln in enumerate(s["lines"]):
            wav = job.p("audio", f"short-{key(ln['say'], VOICE, SPEED)}.wav")
            if not os.path.exists(wav):
                chunks = []
                for _g, _p, audio in pipe(ln["say"], voice=VOICE, speed=SPEED):
                    if audio is not None:
                        chunks.append((audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)).astype("float32"))
                chunks.append(np.zeros(int(SR * 0.12), dtype="float32"))
                sf.write(wav, np.concatenate(chunks), SR)
            d = duration(wav)
            auds.append(wav)
            # visual
            show = ln["show"][0] if isinstance(ln["show"], list) else ln["show"]
            still = _vertical_still(job, show, key)
            frame = G.shorts_frame({"hook": s.get("hook", ""), "flat": show["type"] in G.RENDERERS}, job.p("gfx"), image_path=still)
            frames = max(1, round(d * FPS))
            clip = os.path.join(sdir, f"l{i:02d}-{key(show, round(d, 2))}.mp4")
            if not os.path.exists(clip):
                sh("ffmpeg", "-y", "-loglevel", "error", "-i", frame, "-filter_complex",
                   f"[0:v]scale={SW*2}:{SH*2},setsar=1,zoompan=z='1+0.05*on/{frames}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
                   f":d={frames}:s={SW}x{SH}:fps={FPS},format=yuv420p[v]",
                   "-map", "[v]", "-frames:v", frames, "-c:v", "libx264", "-preset", "medium", "-crf", "19", clip)
            clips.append(clip)
            # captions: 2-4 words per card, timed by character share
            words = ln["say"].split()
            groups, cur = [], []
            for w_ in words:
                cur.append(w_)
                if len(cur) >= 4 or (len(cur) >= 2 and re.search(r"[.,!?;:]$", w_)):
                    groups.append(" ".join(cur))
                    cur = []
            if cur:
                groups.append(" ".join(cur))
            speak = d - 0.12
            total_chars = sum(len(g) for g in groups) or 1
            tt = t
            for g in groups:
                dd = speak * len(g) / total_chars
                events.append((tt, tt + dd, g))
                tt += dd
            t += d
        total = t
        # video body
        lst = os.path.join(sdir, "list.txt")
        open(lst, "w").write("".join(f"file '{c}'\n" for c in clips))
        body = os.path.join(sdir, "body.mp4")
        sh("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", body)
        # narration
        lst2 = os.path.join(sdir, "alist.txt")
        open(lst2, "w").write("".join(f"file '{a}'\n" for a in auds))
        narr = os.path.join(sdir, "narr.wav")
        sh("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst2, "-ar", "48000", "-ac", "1", narr)
        # music
        pool = [x for x in cat if x["mood"] == s.get("mood", "upbeat")] or cat
        tr = random.Random(s["id"] + job.tl.get("title", "")).choice(pool)
        if tr.get("attribution"):
            job.credits.append("Music: " + tr["attribution"])
        music = os.path.join(MUSIC, tr["file"])
        ass = _ass(events, os.path.join(sdir, "caps.ass"))
        out = job.p("final", f"short-{s['id']}.mp4")
        fc = (f"[2:a]aloop=loop=-1:size=2e9,atrim=0:{total:.3f},loudnorm=I=-28:TP=-6,afade=t=out:st={max(0,total-1.2):.2f}:d=1.2[m];"
              "[1:a]aformat=channel_layouts=stereo,asplit=2[n][sc];"
              "[m][sc]sidechaincompress=threshold=0.03:ratio=6:attack=30:release=500[md];"
              "[n][md]amix=inputs=2:normalize=0,loudnorm=I=-14:TP=-1.5[a];"
              f"[0:v]subtitles={ass}:fontsdir={FONTS}[v]")
        sh("ffmpeg", "-y", "-loglevel", "error", "-i", body, "-i", narr, "-i", music, "-filter_complex", fc,
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", out)
        print(f"short {s['id']} {total:.1f}s -> {out}")
        outs.append(out)
    return outs
