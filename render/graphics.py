"""Brand graphics rendered with headless Chromium (Playwright).

Every function returns a path to a PNG at the requested size. Text is set in Jost
(assets/brand/fonts). Colors come from the locked brand palette.
"""
import hashlib
import html
import json
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FONTS = os.path.join(ROOT, "assets", "brand", "fonts")
EXPORT = os.path.join(ROOT, "assets", "brand", "export")
sys.path.insert(0, os.path.join(ROOT, "assets", "brand"))

NAVY, CREAM, RED, MUST, DEEP, OVER = "#1B2340", "#F3EAD7", "#E4572E", "#F2B134", "#121933", "#CC2F1F"

BASE_CSS = f"""
@font-face {{ font-family: Jost; font-weight: 400; src: url('file://{FONTS}/Jost-400.ttf'); }}
@font-face {{ font-family: Jost; font-weight: 500; src: url('file://{FONTS}/Jost-500.ttf'); }}
@font-face {{ font-family: Jost; font-weight: 600; src: url('file://{FONTS}/Jost-600.ttf'); }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 100%; height: 100%; }}
body {{ font-family: Jost, sans-serif; color: {CREAM}; background: {NAVY}; overflow: hidden;
        -webkit-font-smoothing: antialiased; }}
.grain {{ position: absolute; inset: 0; pointer-events: none; opacity: .06;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/></filter><rect width='200' height='200' filter='url(%23n)'/></svg>"); }}
.horizon {{ position: absolute; left: 0; right: 0; height: 3px; background: #303C6E; border-radius: 50%; }}
"""

_browser = None
_pw = None


def _page(w, h):
    global _browser, _pw
    if _browser is None:
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch()
    return _browser.new_page(viewport={"width": w, "height": h})


def close():
    global _browser, _pw
    if _browser:
        _browser.close()
        _pw.stop()
        _browser = _pw = None


def esc(s):
    return html.escape(str(s or ""))


def mark_svg(variant="dark", width=200):
    import mark as m  # assets/brand/mark.py
    vx, vy, vw, vh = 140, 185, 776, 667
    h = width * vh / vw
    if variant == "dark":
        s = m.svg(view=f"{vx} {vy} {vw} {vh}", transparent=True, ground=DEEP, cut_color=CREAM)
    else:
        s = m.svg(view=f"{vx} {vy} {vw} {vh}", transparent=True)
    return s.replace('width="1074" height="1065"', f'width="{width:.0f}" height="{h:.0f}"')


def render(body_html, out, w=1920, h=1080, css="", transparent=False):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    doc = f"<!doctype html><html><head><meta charset='utf-8'><style>{BASE_CSS}{css}</style></head><body>{body_html}</body></html>"
    tmp = out + ".html"
    with open(tmp, "w") as fh:
        fh.write(doc)
    page = _page(w, h)
    page.goto("file://" + tmp)
    page.evaluate("document.fonts.ready")
    page.screenshot(path=out, omit_background=transparent)
    page.close()
    os.remove(tmp)
    return out


def cache_name(kind, spec, w, h, outdir):
    key = hashlib.sha1(json.dumps([kind, spec, w, h], sort_keys=True).encode()).hexdigest()[:12]
    return os.path.join(outdir, f"{kind}-{key}.png")


# ---------------------------------------------------------------- cards
def chapter_card(spec, outdir, w=1920, h=1080):
    if spec.get("time"):
        return time_card(spec, outdir, w, h)
    out = cache_name("chapter", spec, w, h, outdir)
    num = spec.get("num")
    body = f"""
<div class=grain></div>
<div class=horizon style="top:{h*0.62:.0f}px"></div>
<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:28px">
  {f'<div style="font-size:30px;letter-spacing:.5em;color:{MUST};font-weight:500">CHAPTER {esc(num)}</div>' if num else ''}
  <div style="font-size:96px;font-weight:600;letter-spacing:.04em;text-align:center;max-width:1500px;line-height:1.05">{esc(spec['text'])}</div>
  {f'<div style="font-size:38px;color:#C9C0AE;max-width:1300px;text-align:center">{esc(spec.get("sub"))}</div>' if spec.get('sub') else ''}
</div>"""
    return render(body, out, w, h)


def _clock(hh, mm, size=300):
    """A plain analog clock face as inline SVG."""
    import math
    r = size / 2
    ha = math.radians((hh % 12) * 30 + mm * 0.5 - 90)
    ma = math.radians(mm * 6 - 90)
    ticks = "".join(
        f"<line x1='{r + math.cos(math.radians(k*30)) * r*0.80:.1f}' y1='{r + math.sin(math.radians(k*30)) * r*0.80:.1f}' "
        f"x2='{r + math.cos(math.radians(k*30)) * r*0.90:.1f}' y2='{r + math.sin(math.radians(k*30)) * r*0.90:.1f}' "
        f"stroke='{CREAM}' stroke-width='{6 if k % 3 == 0 else 3}' stroke-linecap='round'/>" for k in range(12))
    return (f"<svg width='{size}' height='{size}' viewBox='0 0 {size} {size}'>"
            f"<circle cx='{r}' cy='{r}' r='{r*0.96:.1f}' fill='none' stroke='{CREAM}' stroke-width='5'/>{ticks}"
            f"<line x1='{r}' y1='{r}' x2='{r + math.cos(ha) * r*0.48:.1f}' y2='{r + math.sin(ha) * r*0.48:.1f}' stroke='{MUST}' stroke-width='12' stroke-linecap='round'/>"
            f"<line x1='{r}' y1='{r}' x2='{r + math.cos(ma) * r*0.74:.1f}' y2='{r + math.sin(ma) * r*0.74:.1f}' stroke='{CREAM}' stroke-width='7' stroke-linecap='round'/>"
            f"<circle cx='{r}' cy='{r}' r='10' fill='{RED}'/></svg>")


def time_card(spec, outdir, w=1920, h=1080):
    """Chapter card for a day-in-the-life story: clock, time of day, chapter title, date line."""
    out = cache_name("timecard", spec, w, h, outdir)
    hh, mm = [int(x) for x in spec["time"].split(":")]
    ampm = "A.M." if hh < 12 else "P.M."
    shown = f"{(hh - 1) % 12 + 1}:{mm:02d} {ampm}"
    body = f"""
<div class=grain></div>
<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:110px">
  {_clock(hh, mm, 330)}
  <div style="display:flex;flex-direction:column;gap:18px;max-width:950px">
    <div style="font-size:30px;letter-spacing:.45em;color:{MUST};font-weight:500">{esc(spec.get('date', ''))}</div>
    <div style="font-size:150px;font-weight:600;line-height:1">{esc(shown)}</div>
    <div style="font-size:64px;font-weight:500;line-height:1.1">{esc(spec['text'])}</div>
  </div>
</div>"""
    return render(body, out, w, h)


def caption_overlay(spec, outdir, w=1920, h=1080):
    """Transparent lower third laid over footage: a short line of text on a soft navy band."""
    out = cache_name("caption", spec, w, h, outdir)
    if isinstance(spec, str):
        spec = {"text": spec}
    kicker = spec.get("kicker")
    body = f"""
<div style="position:absolute;left:0;right:0;bottom:0;height:420px;background:linear-gradient(180deg,rgba(18,25,51,0) 0%,rgba(18,25,51,.82) 70%)"></div>
<div style="position:absolute;left:110px;right:110px;bottom:118px">
  {f'<div style="font-size:28px;letter-spacing:.35em;color:{MUST};font-weight:500;margin-bottom:12px">{esc(kicker)}</div>' if kicker else ''}
  <div style="font-size:{spec.get('size', 62)}px;font-weight:600;line-height:1.12;text-shadow:0 3px 12px rgba(0,0,0,.6)">{esc(spec['text'])}</div>
</div>"""
    return render(body, out, w, h, css="body{background:transparent}", transparent=True)


def title_card(spec, outdir, w=1920, h=1080):
    out = cache_name("title", spec, w, h, outdir)
    body = f"""
<div class=grain></div>
<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:34px">
  {mark_svg('dark', 300)}
  <div style="font-size:84px;font-weight:600;text-align:center;max-width:1600px;line-height:1.08">{esc(spec['text'])}</div>
  {f'<div style="font-size:34px;letter-spacing:.3em;color:{MUST}">{esc(spec.get("sub"))}</div>' if spec.get('sub') else ''}
</div>"""
    return render(body, out, w, h)


def statement(spec, outdir, w=1920, h=1080):
    """A big sentence on navy, optional small line under it."""
    out = cache_name("statement", spec, w, h, outdir)
    accent = spec.get("accent")
    text = esc(spec["text"])
    if accent:
        text = text.replace(esc(accent), f"<span style='color:{RED}'>{esc(accent)}</span>")
    body = f"""
<div class=grain></div>
<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:30px;padding:0 160px">
  <div style="font-size:{spec.get('size', 104)}px;font-weight:600;text-align:center;line-height:1.12">{text}</div>
  {f'<div style="font-size:50px;color:#C9C0AE;text-align:center;line-height:1.3">{esc(spec.get("sub"))}</div>' if spec.get('sub') else ''}
</div>"""
    return render(body, out, w, h)


def price_card(spec, outdir, w=1920, h=1080):
    """Then vs now for one item, in dollars and in minutes of work."""
    out = cache_name("price", spec, w, h, outdir)
    verdict = spec.get("verdict", "")
    vcolor = {"cheaper": "#7FC8A9", "pricier": RED, "same": MUST}.get(spec.get("direction"), MUST)

    def col(label, price, minutes, sub, color):
        return f"""
<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:14px">
  <div style="font-size:40px;letter-spacing:.35em;color:{color};font-weight:500">{esc(label)}</div>
  <div style="font-size:150px;font-weight:600;line-height:1">{esc(price)}</div>
  <div style="font-size:30px;color:#C9C0AE">{esc(sub)}</div>
  {f'<div style="margin-top:26px;font-size:64px;font-weight:600;color:{color}">{esc(minutes)}</div><div style="font-size:28px;letter-spacing:.2em;color:#C9C0AE">OF WORK</div>' if minutes else ''}
</div>"""

    body = f"""
<div class=grain></div>
<div style="position:absolute;top:90px;left:0;right:0;text-align:center">
  <div style="font-size:30px;letter-spacing:.4em;color:{MUST}">{esc(spec.get('kicker', 'THEN AND NOW'))}</div>
  <div style="font-size:84px;font-weight:600;margin-top:12px">{esc(spec['item'])}</div>
  {f'<div style="font-size:30px;color:#C9C0AE;margin-top:6px">{esc(spec.get("unit"))}</div>' if spec.get('unit') else ''}
</div>
<div style="position:absolute;top:360px;left:140px;right:140px;display:flex;align-items:flex-start">
  {col(spec.get('then_label', '1958'), spec['then'], spec['then_min'], spec.get('then_sub', ''), MUST)}
  <div style="width:3px;align-self:stretch;background:#303C6E;margin:0 40px"></div>
  {col(spec.get('now_label', 'TODAY'), spec['now'], spec['now_min'], spec.get('now_sub', ''), RED)}
</div>
{f'<div style="position:absolute;bottom:70px;left:0;right:0;text-align:center;font-size:44px;font-weight:600;color:{vcolor}">{esc(verdict)}</div>' if verdict else ''}
"""
    return render(body, out, w, h)


def receipt(spec, outdir, w=1920, h=1080):
    """A clearly labeled recreation of a 1958 grocery receipt built from real advertised prices."""
    out = cache_name("receipt", spec, w, h, outdir)
    rows = "".join(
        f"<div style='display:flex;justify-content:space-between'><span>{esc(n)}</span><span>{esc(p)}</span></div>"
        for n, p in spec["items"])
    body = f"""
<div class=grain></div>
<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:110px">
  <div style="width:620px;background:#FBF6EA;color:#2b2622;padding:46px 48px 56px;font-family:'DejaVu Sans Mono',monospace;font-size:30px;line-height:1.55;
              box-shadow:0 30px 60px rgba(0,0,0,.45);transform:rotate(-1.6deg);
              -webkit-mask: linear-gradient(#000 0 0) top/100% calc(100% - 18px) no-repeat, radial-gradient(circle 12px at 12px 100%, transparent 98%, #000) bottom/24px 18px repeat-x">
    <div style="text-align:center;font-weight:bold;font-size:34px">{esc(spec.get('store', 'NEIGHBORHOOD MARKET'))}</div>
    <div style="text-align:center;font-size:24px;margin-bottom:22px">{esc(spec.get('date', '1958'))}</div>
    {rows}
    <div style="border-top:2px dashed #2b2622;margin:18px 0 8px"></div>
    <div style="display:flex;justify-content:space-between;font-weight:bold"><span>TOTAL</span><span>{esc(spec['total'])}</span></div>
  </div>
  <div style="max-width:640px">
    <div style="font-size:72px;font-weight:600;line-height:1.1">{esc(spec.get('headline', ''))}</div>
    <div style="font-size:34px;color:#C9C0AE;margin-top:24px">{esc(spec.get('sub', ''))}</div>
    <div style="font-size:24px;color:{MUST};margin-top:40px;letter-spacing:.08em">{esc(spec.get('note', 'RECREATION. Prices from real 1958 newspaper ads.'))}</div>
  </div>
</div>"""
    return render(body, out, w, h)


def bars(spec, outdir, w=1920, h=1080):
    """Horizontal bar chart: minutes of work, then vs now, per item."""
    out = cache_name("bars", spec, w, h, outdir)
    rows = spec["rows"]
    single = spec.get("single", False)
    unit = spec.get("unit", " min")
    unit = (" " + unit.strip()) if unit.strip() else ""
    mx = max(max(r[1], r[2]) for r in rows)
    lines = ""
    for label, a, b in rows:
        lines += f"""
<div style="display:grid;grid-template-columns:330px 1fr;align-items:center;gap:22px;margin:16px 0">
  <div style="font-size:34px;text-align:right">{esc(label)}</div>
  <div>
    <div style="display:flex;align-items:center;gap:14px"><div style="height:24px;width:{a/mx*1100:.0f}px;background:{MUST}"></div><span style="font-size:{34 if single else 24}px;color:{MUST}">{a:g}{unit}</span></div>
    {'' if single else f'<div style="display:flex;align-items:center;gap:14px;margin-top:6px"><div style="height:24px;width:{b/mx*1100:.0f}px;background:{RED}"></div><span style="font-size:24px;color:{RED}">{b:g}{unit}</span></div>'}
  </div>
</div>"""
    body = f"""
<div class=grain></div>
<div style="position:absolute;top:70px;left:140px;right:140px">
  <div style="font-size:64px;font-weight:600">{esc(spec['title'])}</div>
  <div style="font-size:28px;color:#C9C0AE;margin:10px 0 26px">
    <span style="color:{MUST}">&#9632; {esc(spec.get('a_label', '1958'))}</span> &nbsp;&nbsp;
    {'' if spec.get('single') else '<span style="color:' + RED + '">&#9632; ' + esc(spec.get('b_label', 'Today')) + '</span> &nbsp;&nbsp;'} {esc(spec.get('note', ''))}
  </div>
  {lines}
</div>"""
    return render(body, out, w, h)


def credit_overlay(text, outdir, w=1920, h=1080):
    """Transparent PNG with a small source credit in the lower left."""
    out = cache_name("credit", text, w, h, outdir)
    body = f"""<div style="position:absolute;left:48px;bottom:40px;font-size:27px;color:rgba(243,234,215,.85);
      text-shadow:0 1px 3px rgba(0,0,0,.8);letter-spacing:.04em">{esc(text)}</div>"""
    return render(body, out, w, h, css="body{background:transparent}", transparent=True)


def end_screen(spec, outdir, w=1920, h=1080):
    """Leaves room for two video tiles (left) and a subscribe circle (right), per YouTube end screen layout."""
    out = cache_name("end", spec, w, h, outdir)
    body = f"""
<div class=grain></div>
<div class=horizon style="top:{h*0.80:.0f}px"></div>
<div style="position:absolute;top:120px;left:0;right:0;text-align:center">
  <div style="font-size:64px;font-weight:600">{esc(spec.get('text', 'Keep rewinding'))}</div>
  <div style="font-size:30px;color:#C9C0AE;margin-top:10px">{esc(spec.get('sub', 'More stories of everyday American life'))}</div>
</div>
<div style="position:absolute;left:150px;top:330px;width:760px;height:427px;border:4px dashed #8C93B5;border-radius:10px"></div>
<div style="position:absolute;left:150px;top:790px;width:760px;text-align:center;font-size:24px;color:#8f8a80">&nbsp;</div>
<div style="position:absolute;right:250px;top:400px;width:300px;height:300px;border-radius:50%;border:4px dashed #8C93B5"></div>
<div style="position:absolute;right:120px;bottom:60px">{mark_svg('dark', 150)}</div>
"""
    return render(body, out, w, h)


def thumbnail(spec, outdir, image_path=None, w=1280, h=720):
    out = cache_name("thumb", [spec, image_path], w, h, outdir)
    bg = f"background:url('file://{image_path}') center/cover;" if image_path else f"background:{NAVY};"
    big = esc(spec["big"])
    if spec.get("accent"):
        big = big.replace(esc(spec["accent"]), f"<span style='color:{MUST}'>{esc(spec['accent'])}</span>")
    body = f"""
<div style="position:absolute;inset:0;{bg}filter:saturate(.9) contrast(1.05)"></div>
<div style="position:absolute;inset:0;background:linear-gradient(90deg,rgba(18,25,51,.92) 0%,rgba(18,25,51,.75) 45%,rgba(18,25,51,0) 75%)"></div>
<div style="position:absolute;left:56px;top:0;bottom:0;display:flex;flex-direction:column;justify-content:center;width:720px">
  <div style="font-size:{spec.get('size', 112)}px;font-weight:600;line-height:.98;text-transform:uppercase;text-shadow:0 4px 18px rgba(0,0,0,.5)">{big}</div>
  {f'<div style="margin-top:22px;display:inline-block;background:{RED};color:{CREAM};font-size:44px;font-weight:600;padding:6px 22px;align-self:flex-start">{esc(spec.get("tag"))}</div>' if spec.get('tag') else ''}
</div>
<div style="position:absolute;right:28px;bottom:22px">{mark_svg('dark', 110)}</div>
"""
    return render(body, out, w, h)


def shorts_frame(spec, outdir, image_path=None, w=1080, h=1920):
    """Static layer for a Short: blurred fill, centered image, top hook text, bottom brand."""
    out = cache_name("shortbg", [spec, image_path], w, h, outdir)
    fill = (f"<div style=\"position:absolute;inset:0;background:{NAVY}\"></div>" if spec.get("flat") else
            f"<div style=\"position:absolute;inset:0;background:url('file://{image_path}') center/cover;filter:blur(40px) brightness(.45)\"></div>")
    img = (fill +
           f"<div style=\"position:absolute;left:0;right:0;top:480px;height:960px;background:url('file://{image_path}') center/contain no-repeat\"></div>") if image_path else ""
    body = f"""
{img}
<div style="position:absolute;top:150px;left:60px;right:60px;text-align:center;font-size:68px;font-weight:600;line-height:1.08;text-shadow:0 4px 16px rgba(0,0,0,.6)">{esc(spec.get('hook', ''))}</div>
<div style="position:absolute;bottom:120px;left:0;right:0;display:flex;justify-content:center;align-items:center;gap:18px">
  {mark_svg('dark', 90)}<span style="font-size:34px;letter-spacing:.2em">MAIN STREET <b style="color:{RED}">REWIND</b></span>
</div>"""
    return render(body, out, w, h)


RENDERERS = {
    "chapter": chapter_card, "title": title_card, "statement": statement,
    "price": price_card, "receipt": receipt, "bars": bars, "end": end_screen,
}
