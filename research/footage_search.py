"""Search free archival and stock footage sources and keep only what we may legally use.

Run by .github/workflows/footage-search.yml (the Claude workspace can't reach these sites).
Usage: python scripts/footage_search.py out_dir "query one" "query two" ...
Writes out_dir/<slug>.json per query: candidates with title, year, source, license verdict,
page url and a direct video file url where one exists. Prints one summary line per query.

License rules (see docs/sources.md):
  ok      public domain / CC0, or a US federal government work, or a stock license that allows
          monetized YouTube use with no attribution (Pixabay, Coverr)
  credit  allowed but must be credited in the description (CC BY)
  skip    anything else (editorial-only, non-commercial, no-derivatives, unknown)
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (channel-studio footage search; contact via github.com/mainstreetrewindchannel)"
OUT = sys.argv[1]
QUERIES = sys.argv[2:]
os.makedirs(OUT, exist_ok=True)

# archive.org collections worth searching for 1940s-1990s American life
IA_COLLECTIONS = {
    "FedFlix": "US government films (public domain as federal works)",
    "prelinger": "Prelinger Archives: educational, industrial and home movies",
    "universal_newsreels": "Universal Newsreels 1929-1967 (donated to the public domain)",
    "classic_tv_commercials": "Old TV commercials (check each item)",
    "home_movies": "Home movies (check each item)",
}
FED_PD = {"FedFlix", "universal_newsreels"}


def get(url, headers=None, timeout=60):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))


def verdict(licenseurl, rights, collections):
    lic = (licenseurl or "").lower()
    r = (rights or "").lower() if isinstance(rights, str) else " ".join(rights or []).lower()
    if "publicdomain" in lic or "/zero/" in lic or "public domain" in r:
        return "ok", "public domain / CC0"
    if set(collections) & FED_PD:
        return "ok", "US government work / donated to public domain"
    if re.search(r"/by/|/by-sa/", lic):
        return "credit", "CC BY: credit the creator"
    if re.search(r"-nc|-nd", lic):
        return "skip", "non-commercial or no-derivatives license"
    return "skip", "no clear license on the item"


def ia_files(ident):
    meta = json.loads(get(f"https://archive.org/metadata/{ident}"))
    best = None
    for f in meta.get("files", []):
        n = f.get("name", "")
        if n.lower().endswith((".mp4", ".mpeg4")):
            size = int(f.get("size", 0) or 0)
            if best is None or (size > best[1] and size < 2_000_000_000):
                best = (n, size)
    return (f"https://archive.org/download/{ident}/{urllib.parse.quote(best[0])}", best[1]) if best else (None, None)


def search_ia(q):
    coll = " OR ".join(f"collection:{c}" for c in IA_COLLECTIONS)
    params = {"q": f"({q}) AND ({coll}) AND mediatype:movies",
              "fl[]": ["identifier", "title", "year", "date", "licenseurl", "rights", "collection", "runtime", "downloads"],
              "rows": 40, "sort[]": "downloads desc", "output": "json"}
    url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(params, doseq=True)
    docs = json.loads(get(url))["response"]["docs"]
    out = []
    for d in docs:
        colls = d.get("collection") or []
        colls = [colls] if isinstance(colls, str) else colls
        v, why = verdict(d.get("licenseurl"), d.get("rights"), colls)
        item = {"source": "archive.org", "id": d["identifier"], "title": d.get("title"),
                "year": d.get("year") or str(d.get("date", ""))[:4], "collections": [c for c in colls if c in IA_COLLECTIONS],
                "runtime": d.get("runtime"), "license": v, "why": why, "licenseurl": d.get("licenseurl"),
                "page": f"https://archive.org/details/{d['identifier']}", "file": None}
        if v != "skip":
            try:
                item["file"], item["bytes"] = ia_files(d["identifier"])
            except Exception as e:
                item["file_error"] = str(e)[:100]
        out.append(item)
    return out


def search_pixabay(q):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return []
    d = json.loads(get("https://pixabay.com/api/videos/?" + urllib.parse.urlencode({"key": key, "q": q[:100], "per_page": 20})))
    return [{"source": "pixabay", "id": str(h["id"]), "title": h.get("tags"), "year": None, "license": "ok",
             "why": "Pixabay Content License (no credit needed; no trademarks for commercial use)",
             "page": h["pageURL"], "file": (h["videos"].get("large") or h["videos"].get("medium") or {}).get("url"),
             "by": h.get("user")} for h in d.get("hits", [])]


def search_coverr(q):
    key = os.environ.get("COVERR_API_KEY")
    if not key:
        return []
    d = json.loads(get("https://api.coverr.co/videos?" + urllib.parse.urlencode({"query": q, "page_size": 20, "urls": "true"}),
                       headers={"Authorization": f"Bearer {key}"}))
    hits = d.get("hits") or d.get("videos") or []
    return [{"source": "coverr", "id": str(h.get("id")), "title": h.get("title"), "year": None, "license": "ok",
             "why": "Coverr license (no credit needed)", "page": f"https://coverr.co/videos/{h.get('slug', '')}",
             "file": (h.get("urls") or {}).get("mp4")} for h in hits]


for q in QUERIES:
    res, errs = [], []
    for fn in (search_ia, search_pixabay, search_coverr):
        try:
            res += fn(q)
        except Exception as e:
            errs.append(f"{fn.__name__}: {str(e)[:120]}")
    slug = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")[:60]
    json.dump({"query": q, "results": res, "errors": errs}, open(os.path.join(OUT, f"{slug}.json"), "w"), indent=1)
    ok = [r for r in res if r["license"] == "ok"]
    print(f"QUERY '{q}': {len(res)} found, {len(ok)} usable, {sum(1 for r in res if r['license']=='credit')} need credit"
          + (f"; errors: {'; '.join(errs)}" if errs else ""))
    for r in ok[:5]:
        print(f"  {r['source']} {r['year'] or ''} {str(r['title'])[:70]} {r['page']}")
