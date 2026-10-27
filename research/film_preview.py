"""Make timestamped contact sheets and a shot list for archive.org films, so clips can be picked by eye.

Usage: python scripts/film_preview.py <out_dir> <identifier> [<identifier> ...]
For each film writes out_dir/<id>/: info.json (title, year, license, duration, best mp4 url for rendering,
shots = scene-cut times), sheet-NN.jpg (frames every few seconds, each labelled with its time).
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

OUT = sys.argv[1]
UA = {"User-Agent": "Mozilla/5.0 (channel-studio film preview)"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read()


def run(*cmd):
    return subprocess.run(list(map(str, cmd)), capture_output=True, text=True)


for ident in sys.argv[2:]:
    d = os.path.join(OUT, ident)
    os.makedirs(d, exist_ok=True)
    try:
        meta = json.loads(get(f"https://archive.org/metadata/{ident}"))
        md = meta.get("metadata", {})
        files = [f for f in meta.get("files", []) if f.get("name", "").lower().endswith((".mp4", ".mpeg4", ".mov", ".avi", ".mpeg", ".mpg", ".ogv"))]
        def size(f):
            return int(f.get("size", 0) or 0)
        mp4s = [f for f in files if f["name"].lower().endswith((".mp4", ".mpeg4"))]
        if not mp4s:
            raise ValueError("no mp4 in item")
        best = max([f for f in mp4s if size(f) < 4_000_000_000] or mp4s, key=size)   # for rendering
        small = min(mp4s, key=size)                                                   # for preview
        url = lambda f: f"https://archive.org/download/{ident}/{urllib.parse.quote(f['name'])}"
        src = os.path.join(d, "preview.mp4")
        with open(src, "wb") as fh:
            fh.write(get(url(small)))
        dur = float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src).stdout.strip() or 0)
        step = max(3, round(dur / 120))
        # frames every `step` seconds, labelled with mm:ss
        os.makedirs(os.path.join(d, "f"), exist_ok=True)
        run("ffmpeg", "-loglevel", "error", "-i", src, "-vf",
            f"fps=1/{step}:start_time=0,scale=320:-2",
            os.path.join(d, "f", "%04d.jpg"))
        # frame n is at (n-1)*step; relabel not needed since fps filter pts gives time
        frames = sorted(os.listdir(os.path.join(d, "f")))
        per = 30
        for k in range(0, len(frames), per):
            args = []
            for j, x in enumerate(frames[k:k + per]):
                t = (k + j) * step
                args += ["-label", f"{int(t // 60)}:{int(t % 60):02d}", os.path.join(d, "f", x)]
            run("montage", *args, "-tile", "6x5", "-geometry", "+3+3", "-background", "black", "-fill", "yellow", "-pointsize", "18",
                os.path.join(d, f"sheet-{k // per + 1:02d}.jpg"))
        # shot boundaries
        sc = run("ffmpeg", "-i", src, "-vf", "select='gt(scene,0.32)',showinfo", "-f", "null", "-")
        shots = []
        for line in sc.stderr.splitlines():
            if "pts_time:" in line:
                try:
                    shots.append(round(float(line.split("pts_time:")[1].split()[0]), 2))
                except ValueError:
                    pass
        info = {"id": ident, "title": md.get("title"), "year": md.get("year") or str(md.get("date", ""))[:4],
                "licenseurl": md.get("licenseurl"), "creator": md.get("creator"), "sponsor": md.get("sponsor"),
                "description": str(md.get("description", ""))[:600], "duration": dur, "step": step,
                "render_url": url(best), "render_bytes": size(best), "preview_url": url(small), "shots": shots}
        json.dump(info, open(os.path.join(d, "info.json"), "w"), indent=1)
        os.remove(src)
        import shutil
        shutil.rmtree(os.path.join(d, "f"))
        print(f"FILM {ident}: {dur/60:.1f} min, {len(shots)} cuts, {len(frames)} frames, license={md.get('licenseurl')}")
    except Exception as e:
        print(f"FAIL {ident}: {str(e)[:200]}")
