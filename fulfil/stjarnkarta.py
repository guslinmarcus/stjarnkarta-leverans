"""Generator: personlig stjärnkarta som PDF (vektor), en fil per språk, i flera STILAR.

Deterministisk: samma indata -> samma PDF (reportlab invariant=1, akvarellen seedas från order-id).
Ingen AI i produkten: allt är beräknat eller procedurellt ritat.
Data: Yale Bright Star Catalogue 5 (Hoffleit & Warren 1991, via Harvard TDC-spegel),
JPL DE421 (Skyfield), stjärnbildslinjer från d3-celestial (BSD-3, (c) Olaf Frohn).
Typsnitt: Noto Serif/Sans, Great Vibes, Varela Round (alla SIL OFL 1.1).

Orderfält (utöver id/name/place/lat/lon/timezone/datetime_local/languages), alla valfria:
  style   : midnatt | minimal | akvarell | hjarta | manfas | barnrum   (standard midnatt)
  frame   : ingen | linje | dubbel | horn | rundad                     (standard: stilens)
  font    : serif | sans | skrivstil | rund                            (standard: stilens)
  palette : stilens paletter, se STYLES[...]["palettes"]               (standard: stilens första)

Körning:  python stjarnkarta.py order.json  -> ut/<id>_<språk>.pdf + ut/<id>_meta.json
"""
import gzip, io, json, math, os, sys, time, hashlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from skyfield.api import Loader, Star, wgs84
from skyfield import almanac
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

GENERATOR_VERSION = "stjarnkarta/0.2.0"  # 0.2.0: stilar, ramar, typsnitt, månfas-rad, akvarell, hjärta
ROOT = Path(__file__).parent
DATA = ROOT / "data"
FONTS = ROOT / "fonts"
MAG_LIMIT = 5.5
PAGE_W, PAGE_H = 297 * mm, 420 * mm  # A3 stående

FONT_FILES = {"Serif": "NotoSerif-Regular.ttf", "SerifIt": "NotoSerif-Italic.ttf", "Sans": "NotoSans-Regular.ttf",
              "Script": "GreatVibes-Regular.ttf", "Round": "VarelaRound-Regular.ttf"}
for _n, _f in FONT_FILES.items():
    pdfmetrics.registerFont(TTFont(_n, str(FONTS / _f)))

# ---------- Stilar ----------
# Färger 0..1. "star" används också av kvalitetsgrinden för att hitta stjärnorna i rastret.
STYLES = {
    "midnatt": {"label": "Midnight & gold", "shape": "circle", "sky_fill": "flat", "frame": "ingen", "font": "serif",
                "palettes": {"guld": {"page": (0.035, 0.055, 0.11), "sky": (0.02, 0.03, 0.07), "ring": (0.85, 0.8, 0.65),
                                      "lines": (0.55, 0.62, 0.78), "star": (1, 0.98, 0.93), "planet": (1, 0.85, 0.55),
                                      "moon": (0.95, 0.93, 0.8), "text": (0.96, 0.93, 0.85), "accent": (0.85, 0.8, 0.65),
                                      "mute": (0.6, 0.6, 0.65)}}},
    "minimal": {"label": "Minimal white", "shape": "circle", "sky_fill": "flat", "frame": "linje", "font": "sans",
                "palettes": {"svart": {"page": (1, 1, 1), "sky": (1, 1, 1), "ring": (0.1, 0.1, 0.1),
                                       "lines": (0.62, 0.62, 0.62), "star": (0.05, 0.05, 0.05), "planet": (0.35, 0.35, 0.35),
                                       "moon": (0.45, 0.45, 0.45), "text": (0.08, 0.08, 0.08), "accent": (0.1, 0.1, 0.1),
                                       "mute": (0.4, 0.4, 0.4)},
                             "sand": {"page": (0.97, 0.955, 0.93), "sky": (0.97, 0.955, 0.93), "ring": (0.22, 0.2, 0.18),
                                      "lines": (0.66, 0.63, 0.58), "star": (0.14, 0.12, 0.1), "planet": (0.45, 0.4, 0.35),
                                      "moon": (0.5, 0.46, 0.42), "text": (0.14, 0.12, 0.1), "accent": (0.22, 0.2, 0.18),
                                      "mute": (0.42, 0.4, 0.37)}}},
    "akvarell": {"label": "Watercolour sky", "shape": "circle", "sky_fill": "akvarell", "frame": "ingen", "font": "skrivstil",
                 "palettes": {"natt": {"page": (0.985, 0.97, 0.94), "wash": [(18, 28, 72), (46, 36, 98), (16, 70, 96), (70, 38, 92)],
                                       "ring": (0.2, 0.22, 0.38), "lines": (0.8, 0.84, 0.95), "star": (1, 1, 1),
                                       "planet": (1, 0.9, 0.7), "moon": (1, 0.98, 0.9), "text": (0.12, 0.14, 0.3),
                                       "accent": (0.2, 0.22, 0.38), "mute": (0.42, 0.42, 0.5)},
                              "skymning": {"page": (0.99, 0.965, 0.95), "wash": [(40, 28, 82), (110, 50, 104), (30, 44, 96), (132, 60, 88)],
                                           "ring": (0.35, 0.2, 0.35), "lines": (0.95, 0.85, 0.92), "star": (1, 1, 1),
                                           "planet": (1, 0.9, 0.7), "moon": (1, 0.98, 0.9), "text": (0.26, 0.13, 0.26),
                                           "accent": (0.35, 0.2, 0.35), "mute": (0.47, 0.4, 0.47)},
                              "hav": {"page": (0.97, 0.975, 0.97), "wash": [(10, 50, 70), (14, 82, 96), (26, 40, 84), (8, 64, 64)],
                                      "ring": (0.08, 0.26, 0.3), "lines": (0.8, 0.94, 0.95), "star": (1, 1, 1),
                                      "planet": (1, 0.9, 0.7), "moon": (1, 0.98, 0.9), "text": (0.06, 0.22, 0.26),
                                      "accent": (0.08, 0.26, 0.3), "mute": (0.38, 0.45, 0.46)}}},
    "hjarta": {"label": "Heart of stars", "shape": "heart", "sky_fill": "flat", "frame": "dubbel", "font": "skrivstil",
               "palettes": {"marin": {"page": (0.975, 0.955, 0.94), "sky": (0.07, 0.1, 0.2), "ring": (0.72, 0.58, 0.34),
                                      "lines": (0.55, 0.58, 0.72), "star": (0.95, 0.83, 0.55), "planet": (1, 0.93, 0.8),
                                      "moon": (1, 0.96, 0.86), "text": (0.1, 0.13, 0.25), "accent": (0.72, 0.58, 0.34),
                                      "mute": (0.45, 0.45, 0.5)},
                            "vinrod": {"page": (0.985, 0.955, 0.95), "sky": (0.3, 0.06, 0.12), "ring": (0.75, 0.6, 0.38),
                                       "lines": (0.8, 0.55, 0.6), "star": (0.98, 0.88, 0.66), "planet": (1, 0.95, 0.85),
                                       "moon": (1, 0.96, 0.9), "text": (0.3, 0.06, 0.12), "accent": (0.75, 0.6, 0.38),
                                       "mute": (0.5, 0.42, 0.44)}}},
    "manfas": {"label": "Moon phase", "shape": "circle", "sky_fill": "flat", "frame": "linje", "font": "sans", "moon_row": True,
               "palettes": {"kol": {"page": (0.075, 0.075, 0.085), "sky": (0.03, 0.03, 0.035), "ring": (0.8, 0.8, 0.82),
                                    "lines": (0.5, 0.5, 0.55), "star": (1, 1, 1), "planet": (0.95, 0.85, 0.6),
                                    "moon": (0.93, 0.93, 0.9), "moon_dark": (0.2, 0.2, 0.22), "text": (0.94, 0.94, 0.94),
                                    "accent": (0.8, 0.8, 0.82), "mute": (0.58, 0.58, 0.6)},
                            "skiffer": {"page": (0.13, 0.16, 0.2), "sky": (0.06, 0.08, 0.11), "ring": (0.75, 0.8, 0.86),
                                        "lines": (0.48, 0.55, 0.62), "star": (1, 1, 1), "planet": (0.95, 0.85, 0.6),
                                        "moon": (0.92, 0.94, 0.96), "moon_dark": (0.24, 0.28, 0.33), "text": (0.93, 0.95, 0.97),
                                        "accent": (0.75, 0.8, 0.86), "mute": (0.6, 0.64, 0.68)}}},
    "barnrum": {"label": "Nursery pastel", "shape": "circle", "sky_fill": "flat", "frame": "rundad", "font": "rund", "clouds": True,
                "palettes": {"rosa": {"page": (0.985, 0.9, 0.89), "sky": (0.33, 0.4, 0.58), "ring": (0.93, 0.7, 0.68),
                                      "lines": (0.75, 0.8, 0.9), "star": (1, 0.99, 0.95), "planet": (1, 0.88, 0.7),
                                      "moon": (1, 0.96, 0.85), "text": (0.3, 0.3, 0.4), "accent": (0.9, 0.62, 0.6),
                                      "mute": (0.45, 0.43, 0.5), "cloud": (1, 1, 1)},
                             "mint": {"page": (0.87, 0.95, 0.91), "sky": (0.26, 0.42, 0.5), "ring": (0.55, 0.78, 0.7),
                                      "lines": (0.75, 0.88, 0.9), "star": (1, 0.99, 0.95), "planet": (1, 0.88, 0.7),
                                      "moon": (1, 0.96, 0.85), "text": (0.2, 0.32, 0.34), "accent": (0.45, 0.7, 0.62),
                                      "mute": (0.38, 0.47, 0.47), "cloud": (1, 1, 1)},
                             "himmel": {"page": (0.88, 0.93, 0.985), "sky": (0.3, 0.38, 0.6), "ring": (0.62, 0.72, 0.9),
                                        "lines": (0.78, 0.83, 0.95), "star": (1, 0.99, 0.95), "planet": (1, 0.88, 0.7),
                                        "moon": (1, 0.96, 0.85), "text": (0.22, 0.28, 0.42), "accent": (0.5, 0.62, 0.86),
                                        "mute": (0.4, 0.45, 0.55), "cloud": (1, 1, 1)}}},
}
FRAMES = ("ingen", "linje", "dubbel", "horn", "rundad")
FONT_SETS = {  # namn, titel, brödtext, namnets startstorlek, versaler?
    "serif": ("Serif", "SerifIt", "Sans", 30, False),
    "sans": ("Sans", "Sans", "Sans", 26, True),
    "skrivstil": ("Script", "SerifIt", "Sans", 46, False),
    "rund": ("Round", "Round", "Round", 30, False),
}
FRAME_INSET = 12 * mm  # yttersta ramlinjen; text måste ligga innanför INNER_MARGIN
INNER_MARGIN = 17 * mm

# ---------- Texter (fasta mallar, granskade en gång; inga AI-texter i produkten) ----------
MONTHS = {
    "sv": ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
           "september", "oktober", "november", "december"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"],
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
    # franska: "1er" för månadens första dag, "de"/"d’" före ortnamnet (se fr_title), 24-timmarsklocka
    "fr": {"title": "Le ciel étoilé au-dessus {de_place}",
           "date": "{d} {m} {y} à {hm}",
           "moon": "Lune : {pct} % éclairée",
           "credit": "Étoiles : Yale Bright Star Catalogue (Hoffleit & Warren 1991). "
                     "Positions : JPL DE421 via Skyfield. Lignes des constellations : d3-celestial © Olaf Frohn (BSD)."},
}
COMPASS = {"sv": "NÖSV", "en": "NESW", "de": "NOSW", "fr": "NESO"}


def fr_de(place):
    """Fransk elision: "de Paris" men "d’Orléans" (vokal eller stumt h räknas inte – h-ord får "de")."""
    return ("d’" if place[:1].lower() in "aeiouyàâäéèêëîïôöùûü" else "de ") + place


def fr_day(d):
    return "1er" if d == 1 else str(d)


def font_for(text, preferred, fallback="Serif"):
    """Byter till reservtypsnitt om något tecken saknas i det valda (t.ex. skrivstil + ovanliga bokstäver)."""
    cmap = pdfmetrics.getFont(preferred).face.charToGlyph
    return preferred if all(ord(ch) in cmap for ch in text if not ch.isspace()) else fallback


def resolve_style(order):
    """Returnerar (stilnamn, stil, palett, ram, typsnitt). Okända värden -> ValueError (grinden stoppar)."""
    s = order.get("style") or "midnatt"
    if s not in STYLES:
        raise ValueError(f"okänd stil {s!r}")
    st = STYLES[s]
    pal_name = order.get("palette") or next(iter(st["palettes"]))
    if pal_name not in st["palettes"]:
        raise ValueError(f"okänd palett {pal_name!r} för {s}")
    frame = order.get("frame") or st["frame"]
    font = order.get("font") or st["font"]
    if frame not in FRAMES or font not in FONT_SETS:
        raise ValueError(f"okänd ram/typsnitt {frame!r}/{font!r}")
    return s, st, pal_name, st["palettes"][pal_name], frame, font


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


# ---------- Hjärtform (samma formel återimplementeras oberoende i kvalitetsgrinden) ----------
def heart_points(n=400):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    x = 16 * np.sin(t) ** 3
    y = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
    return x, y


def heart_params(R):
    """Skala + förskjutning så att hjärtat ryms helt i himmelscirkeln och zenit ligger i hjärtat."""
    x, y = heart_points(2000)
    oy = -(y.max() + y.min()) / 2  # centrera höjdled
    s = 0.985 * R / np.sqrt(x ** 2 + (y + oy) ** 2).max()
    return {"type": "heart", "scale": float(s), "oy": float(oy * s)}


def in_shape(px, py, shape, R):
    """px,py relativt cirkelns mitt (pt). Punkt-i-polygon för hjärtat."""
    if shape is None or shape["type"] == "circle":
        return math.hypot(px, py) < R
    x, y = heart_points(400)
    xs, ys = x * shape["scale"], y * shape["scale"] + shape["oy"]
    inside = False
    j = len(xs) - 1
    for i in range(len(xs)):
        if (ys[i] > py) != (ys[j] > py) and px < (xs[j] - xs[i]) * (py - ys[i]) / (ys[j] - ys[i]) + xs[i]:
            inside = not inside
        j = i
    return inside


# ---------- Astronomi ----------
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


def moon_row_data(order, eph, ts, days=range(-3, 4)):
    """Månens belysning samma klockslag dagarna runt datumet (7 månar, mitten = datumet)."""
    tz = ZoneInfo(order["timezone"])
    local = datetime.fromisoformat(order["datetime_local"]).replace(tzinfo=tz)
    out = []
    for dd in days:
        lt = local + timedelta(days=dd)
        t = ts.from_datetime(lt)
        frac = float(almanac.fraction_illuminated(eph, "moon", t))
        elong = float(almanac.moon_phase(eph, t).degrees)
        out.append({"offset": dd, "day": lt.day, "utc": t.utc_iso(), "frac": frac, "waxing": elong < 180})
    return out


def star_radius(vmag):
    return max(0.35, 2.6 - 0.42 * vmag) * mm / 2.2


# ---------- Procedurell akvarell (numpy/PIL, seedad) ----------
def _fbm(rng, px, octaves=6, base=3):
    from PIL import Image
    acc = np.zeros((px, px), np.float32); amp = 1.0; tot = 0
    for o in range(octaves):
        n = base * 2 ** o
        a = (rng.random((n, n)) * 255).astype(np.uint8)
        acc += amp * np.asarray(Image.fromarray(a).resize((px, px), Image.BICUBIC), np.float32) / 255
        tot += amp; amp *= 0.55
    acc /= tot
    return (acc - acc.min()) / (acc.max() - acc.min() + 1e-9)


def watercolor_png(seed, wash, px=1500):
    """Procedurell akvarell: brusfält (fBm) trösklas till pigmentpölar med oregelbundna kanter,
    mörkare torkkanter och papperskorn. Seedad -> deterministisk."""
    from PIL import Image, ImageFilter
    rng = np.random.default_rng(seed)
    base = np.empty((px, px, 3), np.float32); base[:] = np.array(wash[0], np.float32)
    # bakgrundstvätt: långsam gradient mellan två kulörer
    g = _fbm(rng, px, 3, 2)[..., None]
    base = base * (1 - g) + np.array(wash[1], np.float32) * g
    for k in range(5):
        col = np.array(wash[(k + 1) % len(wash)], np.float32) * rng.uniform(0.85, 1.25)
        n = _fbm(rng, px, 5, 2)
        t = rng.uniform(0.5, 0.66)
        m = np.clip((n - t) / 0.035, 0, 1)
        m = m * m * (3 - 2 * m)
        soft = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(px / 180)),
                          np.float32) / 255
        edge = np.clip(m - soft, 0, 1) * 2.2  # pigment samlas vid kanten när det torkar
        tex = 0.8 + 0.4 * _fbm(rng, px, 5, 12)
        a = (m * rng.uniform(0.3, 0.6) * tex)[..., None]
        base = base * (1 - a) + col * a
        base *= (1 - np.clip(edge, 0, 1)[..., None] * 0.22)
    # granulering + papperskorn
    fine = _fbm(rng, px, 3, 200) - 0.5
    base *= (1 + fine[..., None] * 0.18)
    grain = rng.normal(0, 1, (px, px)).astype(np.float32)
    base += grain[..., None] * 4
    base = np.minimum(base, 150)  # tak: stjärnorna (vita) måste ha kontrast mot himlen
    img = Image.fromarray(base.clip(0, 255).astype(np.uint8), "RGB")
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ---------- Rendering ----------
def draw_frame(c, frame, pal):
    c.setStrokeColorRGB(*pal["accent"])
    i = FRAME_INSET
    if frame == "linje":
        c.setLineWidth(0.7); c.rect(i, i, PAGE_W - 2 * i, PAGE_H - 2 * i, stroke=1, fill=0)
    elif frame == "dubbel":
        c.setLineWidth(1.2); c.rect(i, i, PAGE_W - 2 * i, PAGE_H - 2 * i, stroke=1, fill=0)
        c.setLineWidth(0.45); j = i + 2.2 * mm; c.rect(j, j, PAGE_W - 2 * j, PAGE_H - 2 * j, stroke=1, fill=0)
    elif frame == "rundad":
        c.setLineWidth(1.4); c.roundRect(i, i, PAGE_W - 2 * i, PAGE_H - 2 * i, 9 * mm, stroke=1, fill=0)
    elif frame == "horn":
        c.setLineWidth(0.9); L = 22 * mm
        for (x, y, sx, sy) in [(i, i, 1, 1), (PAGE_W - i, i, -1, 1), (i, PAGE_H - i, 1, -1), (PAGE_W - i, PAGE_H - i, -1, -1)]:
            c.line(x, y, x + sx * L, y); c.line(x, y, x, y + sy * L)
            c.line(x + sx * 3 * mm, y + sy * 3 * mm, x + sx * (L - 6 * mm), y + sy * 3 * mm)
            c.line(x + sx * 3 * mm, y + sy * 3 * mm, x + sx * 3 * mm, y + sy * (L - 6 * mm))


def draw_clouds(c, pal, cx, cy, R, seed):
    rng = np.random.default_rng(seed)
    c.setFillColorRGB(*pal["cloud"])
    for side in (-1, 1):
        bx = cx + side * (R * 0.92); by = cy - R * rng.uniform(0.55, 0.8)
        for k in range(5):
            r = (7 + 4 * rng.random()) * mm
            c.circle(bx + (k - 2) * 7 * mm * side * 0.9, by + (4 * mm if k in (1, 2) else 0), r, stroke=0, fill=1)


def moon_poly(k, waxing, r):
    """Polygon för månens belysta del. k = belyst andel. Belyst höger sida vid tilltagande (norra halvklotet)."""
    ph = np.linspace(-math.pi / 2, math.pi / 2, 48)
    limb = [(r * math.cos(p), r * math.sin(p)) for p in ph]
    term = [((1 - 2 * k) * r * math.cos(p), r * math.sin(p)) for p in ph[::-1]]
    pts = limb + term
    if not waxing:
        pts = [(-x, y) for x, y in pts]
    return pts


def render(order, sky, lang, out_path, style_bundle, extras):
    sname, st, pal_name, pal, frame, font = style_bundle
    c = canvas.Canvas(str(out_path), pagesize=(PAGE_W, PAGE_H), invariant=1,
                      initialFontName="Sans", initialFontSize=10)  # annars bäddas Helvetica in ej
    c.setTitle(f"{order['name']} – {order['place']}")
    c.setAuthor(GENERATOR_VERSION)
    c.setFillColorRGB(*pal["page"]); c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    draw_frame(c, frame, pal)

    R = 105 * mm if st.get("moon_row") else 118 * mm
    top = 40 * mm if frame == "ingen" else 44 * mm
    cx, cy = PAGE_W / 2, PAGE_H - top - R
    shape = heart_params(R) if st["shape"] == "heart" else {"type": "circle"}

    if st.get("clouds"):
        draw_clouds(c, pal, cx, cy, R, int(hashlib.sha256(order["id"].encode()).hexdigest()[:8], 16))

    # himmelsyta (cirkel eller hjärta) som klippbana
    clip = c.beginPath()
    if shape["type"] == "heart":
        x, y = heart_points(400)
        xs, ys = x * shape["scale"] + cx, y * shape["scale"] + shape["oy"] + cy
        clip.moveTo(xs[0], ys[0])
        for xi, yi in zip(xs[1:], ys[1:]):
            clip.lineTo(xi, yi)
        clip.close()
    else:
        clip.circle(cx, cy, R)
    if st["sky_fill"] == "akvarell":
        seed = int(hashlib.sha256((order["id"] + pal_name).encode()).hexdigest()[:12], 16)
        c.saveState(); c.clipPath(clip, stroke=0, fill=0)
        c.drawImage(ImageReader(io.BytesIO(watercolor_png(seed, pal["wash"]))), cx - R, cy - R, 2 * R, 2 * R)
        c.restoreState()
        c.setStrokeColorRGB(*pal["ring"]); c.setLineWidth(0.6); c.drawPath(clip, stroke=1, fill=0)
    else:
        c.setFillColorRGB(*pal["sky"]); c.setStrokeColorRGB(*pal["ring"]); c.setLineWidth(0.8)
        c.drawPath(clip, stroke=1, fill=1)
    c.saveState()
    c.clipPath(clip, stroke=0, fill=0)

    # stjärnbildslinjer (bara segment där båda ändar är ovan horisonten)
    a_alt, a_az, b_alt, b_az = sky["seg"]
    vis = (a_alt > 0) & (b_alt > 0)
    ax, ay = project(a_alt[vis], a_az[vis], R); bx, by = project(b_alt[vis], b_az[vis], R)
    c.setStrokeColorRGB(*pal["lines"]); c.setLineWidth(0.35)
    for x1, y1, x2, y2 in zip(ax, ay, bx, by):
        c.line(cx + x1, cy + y1, cx + x2, cy + y2)

    # stjärnor
    up = sky["alt"] > 0
    x, y = project(sky["alt"][up], sky["az"][up], R)
    mags = sky["bsc"][up, 3]
    c.setFillColorRGB(*pal["star"])
    drawn = 0
    for xi, yi, m in sorted(zip(x, y, mags), key=lambda z: -z[2]):
        c.circle(cx + xi, cy + yi, star_radius(m), stroke=0, fill=1)
        drawn += 1

    # planeter och måne
    for key, (balt, baz) in sky["bodies"].items():
        if balt <= 0:
            continue
        bx1, by1 = project(np.array([balt]), np.array([baz]), R)
        if key == "moon":
            c.setFillColorRGB(*pal["moon"]); c.circle(cx + bx1[0], cy + by1[0], 3.2 * mm, stroke=0, fill=1)
        else:
            c.setFillColorRGB(*pal["planet"]); c.circle(cx + bx1[0], cy + by1[0], 1.1 * mm, stroke=0, fill=1)
    c.restoreState()  # släpp klippningen

    sky_bottom = cy - R
    moon_row = None
    if st.get("moon_row"):
        mr = extras["moon_row"]; rr = 5.2 * mm; gap = 25 * mm; my = sky_bottom - 17 * mm
        mirror = order["lat"] < 0  # södra halvklotet ser månen spegelvänd
        moon_row = []
        for i, md in enumerate(mr):
            mx = cx + (i - (len(mr) - 1) / 2) * gap
            r = rr * (1.25 if md["offset"] == 0 else 1)
            c.setFillColorRGB(*pal["moon_dark"]); c.circle(mx, my, r, stroke=0, fill=1)
            pts = moon_poly(md["frac"], md["waxing"] != mirror, r)
            p = c.beginPath(); p.moveTo(mx + pts[0][0], my + pts[0][1])
            for px_, py_ in pts[1:]:
                p.lineTo(mx + px_, my + py_)
            p.close(); c.setFillColorRGB(*pal["moon"]); c.drawPath(p, stroke=0, fill=1)
            if md["offset"] == 0:
                c.setStrokeColorRGB(*pal["accent"]); c.setLineWidth(0.6); c.circle(mx, my, r + 1.6 * mm, stroke=1, fill=0)
            moon_row.append({**md, "x_pt": mx, "y_pt": my, "r_pt": r, "mirror": mirror})
        sky_bottom = my - rr - 2 * mm
    return c, (cx, cy, R), shape, sky_bottom, moon_row, drawn


def finish_text(c, geom, sky_bottom, order, sky, lang, style_bundle):
    sname, st, pal_name, pal, frame, font = style_bundle
    f_name, f_title, f_body, name_size, upper = FONT_SETS[font]
    cx, cy, R = geom
    tx = T[lang]
    c.setFillColorRGB(*pal["accent"]); c.setFont(f_body, 11)
    for i, ch in enumerate(COMPASS[lang]):
        ang = math.radians([0, 90, 180, 270][i])
        dx, dy = -math.sin(ang) * (R + 6 * mm), math.cos(ang) * (R + 6 * mm)
        if st["shape"] == "heart" and i != 0 and i != 2:
            continue  # hjärtat: bara N/S, sidobokstäverna hamnar långt från hjärtkanten
        c.drawCentredString(cx + dx, cy + dy - 4, ch)

    loc = sky["local"]
    name = order["name"].upper() if upper else order["name"]
    cs = 2.2 if upper else 0
    y0 = sky_bottom - (22 * mm if st.get("moon_row") else 30 * mm)
    c.setFillColorRGB(*pal["text"])
    f_name = font_for(name, f_name)
    size = name_size if f_name == FONT_SETS[font][0] else 30  # krymp tills namnet ryms innanför marginalen
    maxw = PAGE_W - 2 * INNER_MARGIN - 16 * mm
    while size > 12 and pdfmetrics.stringWidth(name, f_name, size) + cs * len(name) > maxw:
        size -= 1
    c.setFont(f_name, size); c.drawCentredString(PAGE_W / 2, y0, name, charSpace=cs)
    title = tx["title"].format(place=order["place"], de_place=fr_de(order["place"]))
    c.setFont(font_for(title, f_title, "SerifIt"), 15)
    c.drawCentredString(PAGE_W / 2, y0 - 13 * mm, title)
    date = tx["date"].format(d=fr_day(loc.day) if lang == "fr" else loc.day, m=MONTHS[lang][loc.month - 1], y=loc.year,
                             hm=loc.strftime("%H:%M"))
    c.setFont(f_body, 11)
    lat, lon = order["lat"], order["lon"]
    coord = f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}  ·  {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}"
    c.drawCentredString(PAGE_W / 2, y0 - 23 * mm, date)
    c.drawCentredString(PAGE_W / 2, y0 - 30 * mm, coord)
    c.drawCentredString(PAGE_W / 2, y0 - 37 * mm, tx["moon"].format(pct=round(sky["moon_frac"] * 100)))
    c.setFont(f_body if font != "rund" else "Round", 6.5); c.setFillColorRGB(*pal["mute"])
    c.drawCentredString(PAGE_W / 2, (12 if frame == "ingen" else 19) * mm, tx["credit"])
    c.showPage(); c.save()
    return {"name": name, "title": title, "date": date, "coord": coord}


def generate(order_path):
    timings = {}
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    bundle = resolve_style(order)
    sname, st, pal_name, pal, frame, font = bundle
    load = Loader(str(DATA), verbose=False)
    ts = load.timescale(builtin=True)
    eph = load("de421.bsp")
    timings["ladda_data_s"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    sky = compute_sky(order, eph, ts)
    extras = {"moon_row": moon_row_data(order, eph, ts) if st.get("moon_row") else None}
    timings["berakna_s"] = time.perf_counter() - t1

    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, texts = {}, {}
    t2 = time.perf_counter()
    for lang in order["languages"]:
        path = out / f"{order['id']}_{lang}.pdf"
        c, geom, shape, sky_bottom, moon_row, drawn = render(order, sky, lang, path, bundle, extras)
        texts[lang] = finish_text(c, geom, sky_bottom, order, sky, lang, bundle)
        files[lang] = str(path)
    timings["rendera_s"] = time.perf_counter() - t2

    # facit för kvalitetsgrinden: de 15 ljusaste stjärnorna ovan 10° höjd som syns i himmelsytan
    cx, cy, R = geom
    check = []
    for i in np.argsort(sky["bsc"][:, 3]):
        if sky["alt"][i] <= 10:
            continue
        px, py = project(np.array([sky["alt"][i]]), np.array([sky["az"][i]]), R)
        rpx, rpy = float(px[0]), float(py[0])
        # marginal 1,5 mm mot kanten så att halva stjärnor vid klippkanten inte räknas
        if not in_shape(rpx, rpy, shape, R - 1.5 * mm) or (shape["type"] == "heart" and not all(
                in_shape(rpx + dx, rpy + dy, shape, R) for dx, dy in [(4.3, 0), (-4.3, 0), (0, 4.3), (0, -4.3)])):
            continue
        # hoppa över stjärnor som skyms av månen/planeterna (ritas ovanpå)
        hidden = False
        for key, (balt, baz) in sky["bodies"].items():
            if balt > 0:
                bx1, by1 = project(np.array([balt]), np.array([baz]), R)
                if math.hypot(bx1[0] - rpx, by1[0] - rpy) < (3.2 if key == "moon" else 1.1) * mm + 1.5 * mm:
                    hidden = True
        if hidden:
            continue
        check.append({"hr": int(sky["bsc"][i, 0]), "ra_h": float(sky["bsc"][i, 1]),
                      "dec": float(sky["bsc"][i, 2]), "vmag": float(sky["bsc"][i, 3]),
                      "alt": float(sky["alt"][i]), "az": float(sky["az"][i]),
                      "page_x_pt": float(cx + rpx), "page_y_pt": float(cy + rpy)})
        if len(check) == 15:
            break
    meta = {
        "generator": GENERATOR_VERSION, "geometry_pt": [cx, cy, R], "check_stars": check, "order": order, "files": files,
        "texts": texts,
        "style": {"name": sname, "palette": pal_name, "frame": frame, "font": font, "shape": shape,
                  "sky_fill": st["sky_fill"], "colors": {k: v for k, v in pal.items() if k != "wash"},
                  "frame_inset_pt": FRAME_INSET, "inner_margin_pt": INNER_MARGIN if frame != "ingen" else 10 * mm,
                  "font_files": FONT_FILES},
        "moon_row": moon_row,
        "utc": sky["t"].utc_iso(), "stars_plotted": int((sky["alt"] > 0).sum()), "stars_drawn": drawn,
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
