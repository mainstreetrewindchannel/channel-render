"""Download research sources (PDFs, data files, pages) and convert to text.
Usage: python scripts/fetch_research.py <list-file> <out-dir>
List file lines: <name> <url>   (blank lines and # comments ignored)
"""
import os, subprocess, sys, time, urllib.request
lst, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36", "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
for line in open(lst):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    name, url = line.split(None, 1)
    raw = os.path.join(out, name)
    if url.startswith("BLS "):
        # BLS API v1 POST: "BLS series1,series2 startyear endyear"
        import json
        _, ids, y0, y1 = url.split()
        payload = {"seriesid": ids.split(","), "startyear": y0, "endyear": y1}
        ver = "v1"
        if os.environ.get("BLS_API_KEY"):
            payload["registrationkey"], ver = os.environ["BLS_API_KEY"], "v2"
        body = json.dumps(payload).encode()
        req = urllib.request.Request(f"https://api.bls.gov/publicAPI/{ver}/timeseries/data/", data=body,
                                     headers={"Content-Type": "application/json", **UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(raw, "wb") as fh:
            fh.write(r.read())
        print(f"ok   {name} ({os.path.getsize(raw)} bytes)", flush=True)
        continue
    for k in ("DATA_GOV_API_KEY", "FRED_API_KEY", "BLS_API_KEY", "EIA_API_KEY"):
        url = url.replace(k, os.environ.get(k, k))
    err = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r, open(raw, "wb") as fh:
                fh.write(r.read())
            err = None
            break
        except Exception as e:
            err = e
            code = getattr(e, "code", 0)
            if code in (429, 500, 502, 503, 504, 520, 522, 524) or "IncompleteRead" in repr(e) or "timed out" in str(e):
                time.sleep(8 * (attempt + 1))
                continue
            break
    if "loc.gov" in url:
        time.sleep(3)  # be gentle with the Library of Congress
    if err:
        print(f"FAIL {name}: {err}", flush=True)
        continue
    final = raw
    if name.endswith(".pdf"):
        final = raw[:-4] + ".txt"
        subprocess.run(["pdftotext", "-layout", raw, final], check=False)
        if os.path.exists(final):
            os.remove(raw)
        else:
            final = raw
    print(f"ok   {name} ({os.path.getsize(final)} bytes)", flush=True)
