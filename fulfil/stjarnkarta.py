"""Minimal generator: personlig stjärnkarta som PDF (vektor), en fil per språk.

Deterministisk: samma indata -> samma PDF (reportlab invariant=1). Ingen AI i produkten.
Data: Yale Bright Star Catalogue 5 (Hoffleit & Warren 1991, via Harvard TDC-spegel),
JPL DE421 (Skyfield), stjärnbildslinjer från d3-celestial (BSD-3, (c) Olaf Frohn).

Körning:  python stjarnkarta.py order.json  -> ut/<id>_<språk>.pdf + ut/<id>_meta.json
"""
import gzip, json, math, os, sys, time, hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from skyfield.api import Loader, Star, wgs84
from skyfield import almanac
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

GENERATOR_VERSION = "stjarnkarta/0.1.1"  # 0.1.1: långa namn krymps (lärdom från grind "layout")
ROOT = Path(__file__).parent
DATA = ROOT / "data"
FONTS = ROOT / "fonts"
MAG_LIMIT = 5.5
PAGE_W, PAGE_H = 297 * mm, 420 * mm  # A3 stående

pdfmetrics.registerFont(TTFont("Serif", str(FONTS / "NotoSerif-Regular.ttf")))
pdfmetrics.registerFont(TTFont("SerifIt", str(FONTS / "NotoSerif-Italic.ttf")))
pdfmetrics.registerFont(TTFont("Sans", str(FONTS / "NotoSans-Regular.ttf")))

# ---------- Texter (fasta mallar, granskade en gång; inga AI-texter i produkten) ----------
MONTHS = {
    "sv": ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
           "september", "oktober", "november", "december"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"],
}
T = {
    "sv": {"title": "Stjärnhimlen över {place}",
           "date": "{d} {m} {y} kl. {hm}",
           "moon": "Månen: {pct} % belyst",
           "credit": "Stjärnor: Yale Bright Star Catalogue (Hoffleit & Warren 1991). "
                     "Positioner: JPL DE421 via Skyfield. Stjärnbildslinjer: d3-celestial © Olaf Frohn (BSD)."},
    "en": {"title": "The night sky over {place}",
           "date": "{m} {d}, {y} at {hm}",
           "moon": "Moon: {pct}% illuminated",
           "credit": "Stars: Yale Bright Star Catalogue (Hoffleit & Warren 1991). "
                     "Positions: JPL DE421 via Skyfield. Constellation lines: d3-celestial © Olaf Frohn (BSD)."},
    "de": {"title": "Der Sternenhimmel über {place}",
           "date": "{d}. {m} {y} um {hm} Uhr",
           "moon": "Mond: {pct} % beleuchtet",
           "credit": "Sterne: Yale Bright Star Catalogue (Hoffleit & Warren 1991). "
                     "Positionen: JPL DE421 via Skyfield. Sternbildlinien: d3-celestial © Olaf Frohn (BSD)."},
}
COMPASS = {"sv": "NÖSV", "en": "NESW", "de": "NOSW"}


def load_bsc():
    rows = []
    for line in gzip.open(DATA / "bsc5.dat.gz", "rt", encoding="latin-1"):
        if len(line) < 114 or not line[75:77].strip() or not line[102:107].strip():
            continue  # 14 poster saknar position (novor/icke-stjärnor)
        ra_h = int(line[75:77]) + int(line[77:79]) / 60 + float(line[79:83]) / 3600
        sign = -1 if line[83] == "-" else 1
        dec = sign * (int(line[84:86]) + int(line[86:88]) / 60 + int(line[88:90]) / 3600)
        vmag = float(line[102:107])
        bv = float(line[109:114]) if line[109:114].strip() else 0.6
        rows.append((int(line[0:4]), ra_h, dec, vmag, bv))
    return np.array(rows)


def load_lines():
    gj = json.load(open(DATA / "constellations.lines.json", encoding="utf-8"))
    segs = []
    for f in gj["features"]:
        for poly in f["geometry"]["coordinates"]:
            for a, b in zip(poly[:-1], poly[1:]):
                segs.append((a[0] % 360, a[1], b[0] % 360, b[1]))
    return np.array(segs)


def project(alt, az, R):
    """Stereografisk projektion, zenit i mitten, horisont på radie R. Norr upp, öster vänster."""
    r = R * np.tan(np.radians(90 - alt) / 2) / math.tan(math.radians(45))
    return -r * np.sin(np.radians(az)), r * np.cos(np.radians(az))


def compute_sky(order, eph, ts):
    tz = ZoneInfo(order["timezone"])
    local = datetime.fromisoformat(order["datetime_local"]).replace(tzinfo=tz)
    t = ts.from_datetime(local)
    earth = eph["earth"]
    obs = earth + wgs84.latlon(order["lat"], order["lon"])
    here = obs.at(t)

    bsc = load_bsc()
    bsc = bsc[bsc[:, 3] <= MAG_LIMIT]
    stars = Star(ra_hours=bsc[:, 1], dec_degrees=bsc[:, 2])
    alt, az, _ = here.observe(stars).apparent().altaz()
    alt, az = alt.degrees, az.degrees

    segs = load_lines()
    a = Star(ra_hours=segs[:, 0] / 15, dec_degrees=segs[:, 1])
    b = Star(ra_hours=segs[:, 2] / 15, dec_degrees=segs[:, 3])
    a_alt, a_az, _ = here.observe(a).apparent().altaz()
    b_alt, b_az, _ = here.observe(b).apparent().altaz()

    bodies = {}
    for key, name in [("moon", "moon"), ("mercury", "mercury"), ("venus", "venus"),
                      ("mars", "mars"), ("jupiter", "jupiter barycenter"),
                      ("saturn", "saturn barycenter")]:
        ba, bz, _ = here.observe(eph[name]).apparent().altaz()
        bodies[key] = (ba.degrees, bz.degrees)
    moon_frac = float(almanac.fraction_illuminated(eph, "moon", t))

    return {
        "t": t, "local": local, "bsc": bsc, "alt": alt, "az": az,
        "seg": (a_alt.degrees, a_az.degrees, b_alt.degrees, b_az.degrees),
        "bodies": bodies, "moon_frac": moon_frac,
    }


def star_radius(vmag):
    return max(0.35, 2.6 - 0.42 * vmag) * mm / 2.2


def render(order, sky, lang, out_path):
    c = canvas.Canvas(str(out_path), pagesize=(PAGE_W, PAGE_H), invariant=1,
                      initialFontName="Sans", initialFontSize=10)  # annars bäddas Helvetica in ej
    c.setTitle(f"{order['name']} – {order['place']}")
    c.setAuthor(GENERATOR_VERSION)
    bg = (0.035, 0.055, 0.11)
    c.setFillColorRGB(*bg); c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    R = 118 * mm
    cx, cy = PAGE_W / 2, PAGE_H - 40 * mm - R
    # himmelsskiva
    c.setFillColorRGB(0.02, 0.03, 0.07); c.setStrokeColorRGB(0.85, 0.8, 0.65); c.setLineWidth(0.8)
    c.circle(cx, cy, R, stroke=1, fill=1)
    c.saveState()
    p = c.beginPath(); p.circle(cx, cy, R); c.clipPath(p, stroke=0, fill=0)

    # stjärnbildslinjer (bara segment där båda ändar är ovan horisonten)
    a_alt, a_az, b_alt, b_az = sky["seg"]
    vis = (a_alt > 0) & (b_alt > 0)
    ax, ay = project(a_alt[vis], a_az[vis], R); bx, by = project(b_alt[vis], b_az[vis], R)
    c.setStrokeColorRGB(0.55, 0.62, 0.78); c.setLineWidth(0.35)
    for x1, y1, x2, y2 in zip(ax, ay, bx, by):
        c.line(cx + x1, cy + y1, cx + x2, cy + y2)

    # stjärnor
    up = sky["alt"] > 0
    x, y = project(sky["alt"][up], sky["az"][up], R)
    mags = sky["bsc"][up, 3]
    c.setFillColorRGB(1, 0.98, 0.93)
    for xi, yi, m in sorted(zip(x, y, mags), key=lambda z: -z[2]):
        c.circle(cx + xi, cy + yi, star_radius(m), stroke=0, fill=1)

    # planeter och måne
    for key, (balt, baz) in sky["bodies"].items():
        if balt <= 0:
            continue
        bx1, by1 = project(np.array([balt]), np.array([baz]), R)
        if key == "moon":
            c.setFillColorRGB(0.95, 0.93, 0.8); c.circle(cx + bx1[0], cy + by1[0], 3.2 * mm, stroke=0, fill=1)
        else:
            c.setFillColorRGB(1, 0.85, 0.55); c.circle(cx + bx1[0], cy + by1[0], 1.1 * mm, stroke=0, fill=1)
    c.restoreState()  # släpp klippningen mot cirkeln
    return c, (cx, cy, R)


def finish_text(c, geom, order, sky, lang):
    cx, cy, R = geom
    tx = T[lang]
    c.setFillColorRGB(0.85, 0.8, 0.65); c.setFont("Sans", 11)
    for i, ch in enumerate(COMPASS[lang]):
        ang = math.radians([0, 90, 180, 270][i])
        # norr upp, öster vänster
        dx, dy = -math.sin(ang) * (R + 6 * mm), math.cos(ang) * (R + 6 * mm)
        c.drawCentredString(cx + dx, cy + dy - 4, ch)

    loc = sky["local"]
    y0 = cy - R - 30 * mm
    c.setFillColorRGB(0.96, 0.93, 0.85)
    size = 30  # krymp tills namnet ryms inom 25 mm marginal
    while size > 12 and pdfmetrics.stringWidth(order["name"], "Serif", size) > PAGE_W - 50 * mm:
        size -= 1
    c.setFont("Serif", size); c.drawCentredString(PAGE_W / 2, y0, order["name"])
    c.setFont("SerifIt", 15)
    c.drawCentredString(PAGE_W / 2, y0 - 13 * mm, tx["title"].format(place=order["place"]))
    date = tx["date"].format(d=loc.day, m=MONTHS[lang][loc.month - 1], y=loc.year, hm=loc.strftime("%H:%M"))
    c.setFont("Sans", 11)
    lat, lon = order["lat"], order["lon"]
    coord = f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}  ·  {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}"
    c.drawCentredString(PAGE_W / 2, y0 - 23 * mm, date)
    c.drawCentredString(PAGE_W / 2, y0 - 30 * mm, coord)
    c.drawCentredString(PAGE_W / 2, y0 - 37 * mm, tx["moon"].format(pct=round(sky["moon_frac"] * 100)))
    c.setFont("Sans", 6.5); c.setFillColorRGB(0.6, 0.6, 0.65)
    c.drawCentredString(PAGE_W / 2, 12 * mm, tx["credit"])
    c.showPage(); c.save()
    return {"title": tx["title"].format(place=order["place"]), "date": date, "coord": coord}


def generate(order_path):
    timings = {}
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    load = Loader(str(DATA), verbose=False)
    ts = load.timescale(builtin=True)
    eph = load("de421.bsp")
    timings["ladda_data_s"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    sky = compute_sky(order, eph, ts)
    timings["berakna_s"] = time.perf_counter() - t1

    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, texts = {}, {}
    t2 = time.perf_counter()
    for lang in order["languages"]:
        path = out / f"{order['id']}_{lang}.pdf"
        c, geom = render(order, sky, lang, path)
        texts[lang] = finish_text(c, geom, order, sky, lang)
        files[lang] = str(path)
    timings["rendera_s"] = time.perf_counter() - t2

    # facit för kvalitetsgrinden: de 15 ljusaste stjärnorna ovan 10° höjd, med sidkoordinater
    cx, cy, R = geom
    idx = [i for i in np.argsort(sky["bsc"][:, 3]) if sky["alt"][i] > 10][:15]
    check = []
    for i in idx:
        px, py = project(np.array([sky["alt"][i]]), np.array([sky["az"][i]]), R)
        check.append({"hr": int(sky["bsc"][i, 0]), "ra_h": float(sky["bsc"][i, 1]),
                      "dec": float(sky["bsc"][i, 2]), "vmag": float(sky["bsc"][i, 3]),
                      "alt": float(sky["alt"][i]), "az": float(sky["az"][i]),
                      "page_x_pt": float(cx + px[0]), "page_y_pt": float(cy + py[0])})
    meta = {
        "generator": GENERATOR_VERSION, "geometry_pt": [cx, cy, R], "check_stars": check, "order": order, "files": files, "texts": texts,
        "utc": sky["t"].utc_iso(), "stars_plotted": int((sky["alt"] > 0).sum()),
        "moon_frac": sky["moon_frac"],
        "bodies": {k: [round(v[0], 3), round(v[1], 3)] for k, v in sky["bodies"].items()},
        "timings": timings,
        "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()},
    }
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"], "stars": m["stars_plotted"]}, indent=1))
