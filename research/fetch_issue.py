"""Fetch every page of one Chronicling America newspaper issue: OCR text, IIIF address, size, small preview.

Usage: python scripts/fetch_issue.py <lccn> <YYYY-MM-DD> <out_dir> [edition=1] [max_pages=140]
Writes out_dir/pages.json ([{seq, iiif, w, h, words}]), out_dir/text/p###.txt, out_dir/thumbs/p###.jpg (~600px wide).
"""
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

lccn, date, out = sys.argv[1], sys.argv[2], sys.argv[3]
ed = sys.argv[4] if len(sys.argv) > 4 else "1"
maxp = int(sys.argv[5]) if len(sys.argv) > 5 else 140
UA = {"User-Agent": "Mozilla/5.0 (channel-studio research; github.com/mainstreetrewindchannel)"}
os.makedirs(os.path.join(out, "text"), exist_ok=True)
os.makedirs(os.path.join(out, "thumbs"), exist_ok=True)


def get(url, binary=False, tries=6):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                b = r.read()
                return b if binary else b.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(6 * (a + 1))
        except Exception:
            time.sleep(6 * (a + 1))
    return None


def alto_text(xml):
    # ALTO: TextLine > String CONTENT; keep line breaks, blank line between blocks
    xml = re.sub(r'\sxmlns="[^"]+"', "", xml, count=1)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return re.sub(r"<[^>]+>", " ", xml)
    blocks = []
    for tb in root.iter("TextBlock"):
        lines = []
        for tl in tb.iter("TextLine"):
            lines.append(" ".join(s.get("CONTENT", "") for s in tl.iter("String")))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


pages = []
for sp in range(1, maxp + 1):
    meta = get(f"https://www.loc.gov/resource/{lccn}/{date}/ed-{ed}/?sp={sp}&fo=json")
    if not meta:
        print(f"stop at sp={sp}")
        break
    try:
        m = json.loads(meta)
    except ValueError:
        print(f"bad json at sp={sp}")
        break
    fts = m.get("fulltext_service") or ""
    seg = re.search(r"segment=(/service/ndnp/[^&]+)\.xml", fts)
    if not seg:
        print(f"no segment at sp={sp}")
        break
    iiif = "https://tile.loc.gov/image-services/iiif/" + seg.group(1).lstrip("/").replace("/", ":")
    info = get(iiif + "/info.json")
    w = h = None
    if info:
        try:
            j = json.loads(info)
            w, h = j.get("width"), j.get("height")
        except ValueError:
            pass
    xml = get(fts)
    txt = alto_text(xml) if xml else ""
    open(os.path.join(out, "text", f"p{sp:03d}.txt"), "w").write(txt)
    th = get(iiif + "/full/600,/0/default.jpg", binary=True)
    if th:
        open(os.path.join(out, "thumbs", f"p{sp:03d}.jpg"), "wb").write(th)
    pages.append({"seq": sp, "iiif": iiif, "w": w, "h": h, "words": len(txt.split())})
    print(f"p{sp:03d} {w}x{h} {len(txt.split())} words", flush=True)
    time.sleep(2)
json.dump({"lccn": lccn, "date": date, "pages": pages}, open(os.path.join(out, "pages.json"), "w"), indent=1)
print(f"DONE {len(pages)} pages")
