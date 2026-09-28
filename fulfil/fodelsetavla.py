"""Generator: "Födelsetavla – natten du föddes". Personlig affisch (A3 + A4, en fil per språk) med
den verkliga stjärnhimlen och månfasen över födelseorten vid exakt födelseminuten (samma astronomimotor
som stjarnkarta.py/manfas.py – Skyfield + JPL DE421, Yale Bright Star Catalogue, d3-celestial-linjer),
plus namn, födelsedata, vikt, längd och – bara för orter i Sverige, bara när SMHI har täckning för
datumet – dygnets uppmätta väder (temperatur, nederbörd, ev. soltimmar) från SMHI:s öppna data.

Två varianter:
  variant="barn"     : namn, födelsedatum+tid, ort, vikt, längd, valfri textrad, valfri familjerad (<=4 namn), valfritt väder.
  variant="husdjur"  : "gotcha day" – djurets namn, datumet det kom hem (ingen tid krävs, kl 12 lokal tid
                       används för himlen precis som manfas.py:s "utan tid"-regel), ort, himmel + måne. Inget
                       vikt/längd/väder/familj – bara det stilrena kärnmotivet.

Fyra stilar, byggda på återkommande mönster hos de populäraste befintliga födelsetavlorna på Etsy i DE/US/GB
(se stilprofiler/fodelsetavla_stilbeslut.json – SE gav noll träffar, ingen konkurrens finns där idag):
  natur           : ljus, varm cremefärgad bas, skogs-/safarisiluetter som ram.
  nordisk_minimal : ljus, nordisk minimalism, tunn linje, inga motiv (kravet "ljus nordisk stil").
  nattstjarna     : mörk marinblå/kolgrå bas, guldaccent, större himmel (kravet "mörk stil"; vår tydligaste
                    datafördel – en riktig himmel, inte klipparts).
  ballong         : ljus pastell, luftballongsiluetter, valfri accentfärg (rosa/mint) – inget kön tvingas fram.
Himmelscirkeln (stjärnor + månfasdisk) är identisk i alla fyra stilar – bara palett, ram och dekorativa
accenter skiljer. Vi kopierar ingen konkurrents design, bara vilka MÖNSTER (palett/typografi/motivkategori)
som återkommer över många annonser.

Enheter (obligatoriskt, per språk+land – aldrig blandat): sv/de = alltid metriskt (kg/g, cm). en = metriskt
om ordern gäller en ort i ett land som normalt använder metriskt; imperialt (lb/oz, in) när landet är i
IMPERIAL_LANDER (USA, Storbritannien – de två engelskspråkiga marknader vi annonserar mot).

Körning: python fodelsetavla.py order.json -> <ut>/<id>_<språk>.pdf + <ut>/<id>_meta.json
Felinjektion (bara tester): FELINJEKTION=fel_stjarnposition|fel_manfas|fel_vader|saknad_vader_kalla|
    text_utanfor|text_overlapp|fel_enhet|fel_vikt_langd|fel_familj|fel_tidszon|sprak_fel|saknad_attribution|
    lag_kontrast|fel_avstand_station
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # numpy: annars ≈ 700 MB privat minne i trådbuffertar (minneskrav < 1,5 GB)
import hashlib
import json
import math
import sys
import time
from datetime import date as Date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from skyfield.api import Loader
from skyfield import almanac
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics

import stjarnkarta as SK   # projektion, stjärnradie, halvmåne-polygon, himmelsberäkning, typsnittsregistrering
import manfas as MF        # halvmånedisken (lit/dark-formen med mare-fält)
import smhi_vader as VADER

GENERATOR_VERSION = "fodelsetavla/0.1.0"
FEL = os.environ.get("FELINJEKTION", "")
ROOT = Path(__file__).parent
A3 = (297 * mm, 420 * mm)
SCALE_A4 = 210 / 297
VARIANTER = ("barn", "husdjur")
IMPERIAL_LANDER = {"US", "GB"}  # enheter: se dokstycket. Alla andra länder -> metriskt oavsett språk.
G_PER_OZ = 28.349523125
CM_PER_IN = 2.54
MAX_FAMILJ = 4

STYLES = {
    "natur": {
        "label": "Natur", "page": (0.975, 0.955, 0.925), "ink": (0.24, 0.19, 0.15), "accent": (0.55, 0.42, 0.30),
        "mute": (0.50, 0.44, 0.38), "sky": (0.14, 0.16, 0.22), "ring": (0.55, 0.42, 0.30), "lines": (0.55, 0.58, 0.68),
        "star": (0.98, 0.95, 0.88), "planet": (0.95, 0.80, 0.55), "moon_lit": (0.96, 0.93, 0.85), "moon_dark": (0.20, 0.22, 0.30),
        "maria": (0.62, 0.58, 0.50), "frame": "linje", "motif": "skog", "fonts": ("Script", "SerifIt", "Sans"),
    },
    "nordisk_minimal": {
        "label": "Nordisk minimal", "page": (0.985, 0.985, 0.982), "ink": (0.12, 0.12, 0.12), "accent": (0.12, 0.12, 0.12),
        "mute": (0.42, 0.42, 0.42), "sky": (0.97, 0.97, 0.965), "ring": (0.16, 0.16, 0.16), "lines": (0.55, 0.55, 0.55),
        "star": (0.10, 0.10, 0.10), "planet": (0.35, 0.35, 0.35), "moon_lit": (0.90, 0.90, 0.88), "moon_dark": (0.20, 0.20, 0.20),
        "maria": (0.55, 0.55, 0.55), "frame": "linje_tunn", "motif": "ingen", "fonts": ("Sans", "Sans", "Sans"),
    },
    "nattstjarna": {
        "label": "Nattstjärna", "page": (0.043, 0.058, 0.10), "ink": (0.95, 0.93, 0.85), "accent": (0.85, 0.74, 0.47),
        "mute": (0.62, 0.62, 0.66), "sky": (0.02, 0.03, 0.07), "ring": (0.85, 0.74, 0.47), "lines": (0.50, 0.55, 0.70),
        "star": (1.0, 0.98, 0.92), "planet": (1.0, 0.85, 0.55), "moon_lit": (0.96, 0.93, 0.82), "moon_dark": (0.10, 0.12, 0.20),
        "maria": (0.68, 0.64, 0.54), "frame": "dubbel", "motif": "stjarnor", "fonts": ("SerifIt", "Serif", "Sans"),
    },
    "ballong": {
        "label": "Ballong", "page": (0.985, 0.93, 0.93), "ink": (0.30, 0.24, 0.30), "accent": (0.80, 0.45, 0.46),
        "mute": (0.52, 0.46, 0.50), "sky": (0.28, 0.30, 0.46), "ring": (0.80, 0.45, 0.46), "lines": (0.70, 0.72, 0.85),
        "star": (1.0, 0.98, 0.95), "planet": (1.0, 0.88, 0.7), "moon_lit": (1.0, 0.97, 0.9), "moon_dark": (0.24, 0.26, 0.40),
        "maria": (0.75, 0.60, 0.60), "frame": "rundad", "motif": "ballong", "fonts": ("Script", "SerifIt", "Sans"),
    },
}
ACCENT_ALT = {"rosa": (0.80, 0.45, 0.46), "mint": (0.42, 0.62, 0.55)}  # ballong-stilens valfria accentfärg (kön tvingas inte fram)

HEADING = {
    "barn": {"sv": "Natten du föddes", "en": "The night you were born", "de": "Die Nacht deiner Geburt"},
    "husdjur": {"sv": "Dagen du kom hem", "en": "The day you came home", "de": "Der Tag, an dem du heimkamst"},
}
FAMILJ_RUBRIK = {"sv": "Familjen", "en": "Family", "de": "Familie"}
MOON_TXT = {"sv": "Månen: {p} % belyst", "en": "Moon: {p}% illuminated", "de": "Mond: {p} % beleuchtet"}
PLACE_ONLY = {"sv": "{p}", "en": "{p}", "de": "{p}"}
WEIGHT_LBL = {"sv": "Vikt", "en": "Weight", "de": "Gewicht"}
HEIGHT_LBL = {"sv": "Längd", "en": "Length", "de": "Größe"}
DATE_FMT_TXT = {
    "sv": "{d} {m} {y}" + "{tid}",
    "en": "{m} {d}, {y}" + "{tid}",
    "de": "{d}. {m} {y}" + "{tid}",
}
MONTHS = dict(SK.MONTHS)
CREDIT = {
    "sv": "Stjärnor: Yale Bright Star Catalogue (Hoffleit & Warren 1991). Positioner: JPL DE421 via Skyfield. "
          "Stjärnbildslinjer: d3-celestial © Olaf Frohn (BSD).",
    "en": "Stars: Yale Bright Star Catalogue (Hoffleit & Warren 1991). Positions: JPL DE421 via Skyfield. "
          "Constellation lines: d3-celestial © Olaf Frohn (BSD).",
    "de": "Sterne: Yale Bright Star Catalogue (Hoffleit & Warren 1991). Positionen: JPL DE421 via Skyfield. "
          "Sternbildlinien: d3-celestial © Olaf Frohn (BSD).",
}
WEATHER_CREDIT = {"sv": "Väderdata: SMHI, CC BY 4.0.", "en": "Weather data: SMHI, CC BY 4.0.", "de": "Wetterdaten: SMHI, CC BY 4.0."}
WEATHER_LINE = {
    "sv": "Vädret den dagen: {t} · {n} nederbörd{sol} (närmaste station {station}, {km} km bort)",
    "en": "Weather that day: {t} · {n} precipitation{sol} (nearest station {station}, {km} km away)",
    "de": "Wetter an dem Tag: {t} · {n} Niederschlag{sol} (nächste Station {station}, {km} km entfernt)",
}
SPRAK_FRAMMANDE = {
    "sv": ["the night you were born", "die nacht deiner geburt", "weight", "gewicht", "illuminated", "beleuchtet"],
    "en": ["natten du föddes", "die nacht deiner geburt", "vikt", "gewicht", "belyst", "beleuchtet"],
    "de": ["natten du föddes", "the night you were born", "vikt", "weight", "belyst", "illuminated"],
}
FOREIGN = SPRAK_FRAMMANDE


def norm(s):
    return " ".join(str(s or "").split())


def units_for(lang, country_cc):
    """'metric' eller 'imperial'. sv/de alltid metriskt. en: imperialt bara i IMPERIAL_LANDER (US/GB)."""
    if lang != "en":
        return "metric"
    return "imperial" if (country_cc or "").upper() in IMPERIAL_LANDER else "metric"


def fmt_weight(grams, units, lang):
    if grams is None:
        return None
    if FEL == "fel_enhet":
        units = "imperial" if units == "metric" else "metric"
    if units == "imperial":
        total_oz = round(grams / G_PER_OZ)
        lb, oz = total_oz // 16, total_oz % 16
        return f"{lb} lb {oz} oz"
    kg = grams / 1000.0
    s = f"{kg:.2f}".rstrip("0").rstrip(".")
    return (s.replace(".", ",") if lang != "en" else s) + " kg"


def fmt_height(cm, units, lang):
    if cm is None:
        return None
    if FEL == "fel_enhet":
        units = "imperial" if units == "metric" else "metric"
    if units == "imperial":
        inch = cm / CM_PER_IN
        s = f"{inch:.1f}"
        return f"{s} in"
    s = f"{cm:.0f}"
    return f"{s} cm"


def fmt_date(lang, d: Date, hm):
    mo = MONTHS[lang][d.month - 1]
    tid = ""
    if hm:
        h, mi = hm
        if lang == "en":
            hh = (h % 12) or 12
            tid = f" · {hh}:{mi:02d} {'AM' if h < 12 else 'PM'}"
        elif lang == "de":
            tid = f" · {h:02d}:{mi:02d} Uhr"
        else:
            tid = f" · kl. {h:02d}.{mi:02d}"
    return DATE_FMT_TXT[lang].format(d=d.day, m=mo, y=d.year, tid=tid)


def place_line(order):
    return f"{order['place']}, {order['country']}" if order.get("country") else order["place"]


def compute(order):
    variant = order.get("variant", "barn")
    d = Date.fromisoformat(order["date"])
    hm = None
    if variant == "barn" and order.get("time"):
        h, mi = (int(x) for x in order["time"].split(":")[:2])
        hm = (h, mi)
    h, mi = hm if hm else (12, 0)
    tz = ZoneInfo(order["timezone"])
    local = datetime(d.year, d.month, d.day, h, mi, tzinfo=tz)

    load = Loader(str(SK.DATA), verbose=False)
    ts = load.timescale(builtin=True)
    eph = load("de421.bsp")
    # planterat fel (bara tester): den lokala väggklockans siffror tolkas i FEL tidszon (UTC i stället för
    # ortens egen) – ett klassiskt tidszonsfel som grinden ska fånga via sin oberoende omräkning
    order_sk = {"name": order["name"], "place": order["place"], "lat": order["lat"], "lon": order["lon"],
                "timezone": "UTC" if FEL == "fel_tidszon" else order["timezone"],
                "datetime_local": local.strftime("%Y-%m-%dT%H:%M:%S")}
    sky = SK.compute_sky(order_sk, eph, ts)
    t = sky["t"]
    elong = float(almanac.moon_phase(eph, t).degrees)
    waxing = elong < 180
    south = order["lat"] < 0
    if FEL == "fel_manfas":
        sky = dict(sky, moon_frac=min(1.0, sky["moon_frac"] + 0.12))

    weather = None
    if variant == "barn" and order.get("weather_requested") and (order.get("country_cc") or "").upper() == "SE":
        w = VADER.weather_for(order["lat"], order["lon"], d)
        if w.get("har_data"):
            weather = w

    return {"local": local, "sky": sky, "elong": elong, "waxing": waxing, "south": south, "weather": weather, "hm": hm}


def fit_font(text, font, size, max_w, min_size=8):
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_w:
        size -= 0.5
    return size


def draw_motif(c, st, W, H, cx, cy, R, seed, scale=1.0):
    """Enkel, egen geometrisk dekor (inga bildfiler, ingen konkurrents illustration) – skiljer stilarna åt.
    Allt ritas i marginalbandet OVANFÖR himmelscirkeln (mellan ramen och cirkelns topp), aldrig i textzonen
    under cirkeln – annars riskerar dekoren att krocka med data-raderna (grinden kontrollerar det, se K_text)."""
    rng = np.random.default_rng(seed)
    motif = st["motif"]
    top_y = cy + R  # cirkelns topp
    band_y = (top_y + (H - 12 * mm * scale)) / 2  # mitten av marginalbandet ovanför cirkeln
    if motif == "stjarnor":
        c.setFillColorRGB(*st["accent"])
        for _ in range(22):
            ang = rng.uniform(0, math.pi); rad = R * rng.uniform(1.10, 1.30)  # bara övre halvcirkeln (0..pi = uppåt)
            x, y = cx + rad * math.cos(ang), cy + rad * math.sin(ang)
            if not (top_y - 4 * mm * scale < y < H - 14 * mm * scale):
                continue
            r = rng.uniform(0.5, 1.2) * mm * scale
            p = c.beginPath()
            for k in range(10):
                a = math.pi / 2 + k * math.pi / 5
                rr = r if k % 2 == 0 else r * 0.42
                (p.moveTo if k == 0 else p.lineTo)(x + rr * math.cos(a), y + rr * math.sin(a))
            p.close(); c.drawPath(p, stroke=0, fill=1)
    elif motif == "ballong":
        c.setStrokeColorRGB(*st["accent"]); c.setLineWidth(0.7)
        for dx, s in ((-0.55, 1.0), (0.58, 0.8)):
            bx = cx + dx * R
            by = min(band_y + 6 * mm * scale, H - 26 * mm * scale)
            rr = 8 * mm * s * scale
            c.setFillColorRGB(*st["accent"]); c.ellipse(bx - rr * 0.82, by - rr, bx + rr * 0.82, by + rr, stroke=0, fill=1)
            c.setFillColorRGB(*st["page"]); c.setFillAlpha(0.35)
            c.ellipse(bx - rr * 0.32, by + rr * 0.15, bx + rr * 0.05, by + rr * 0.6, stroke=0, fill=1)
            c.setFillAlpha(1)
            c.setStrokeColorRGB(*st["accent"]); c.setLineWidth(0.5 * scale)
            c.line(bx, by - rr, bx - 1.6 * mm * scale, by - rr - 6 * mm * scale)
            c.line(bx, by - rr, bx + 1.6 * mm * scale, by - rr - 6 * mm * scale)
            c.rect(bx - 1.6 * mm * scale, by - rr - 7.6 * mm * scale, 3.2 * mm * scale, 1.8 * mm * scale, stroke=1, fill=0)
    elif motif == "skog":
        c.setFillColorRGB(*st["accent"])
        for dx in (-0.62, 0.64):
            x0 = cx + dx * R
            y0 = min(band_y - 4 * mm * scale, top_y + 18 * mm * scale)
            for k in range(3):  # tre travade trianglar = enkel, egen "gran"-siluett
                yk = y0 + k * 4.4 * mm * scale
                w = (5.6 - k * 1.1) * mm * scale
                p = c.beginPath(); p.moveTo(x0, yk + 6.2 * mm * scale); p.lineTo(x0 - w, yk); p.lineTo(x0 + w, yk); p.close()
                c.drawPath(p, stroke=0, fill=1)
            c.rect(x0 - 0.9 * mm * scale, y0 - 3.2 * mm * scale, 1.8 * mm * scale, 3.2 * mm * scale, stroke=0, fill=1)
    # "ingen" (nordisk_minimal): inga extra former – tomrummet ÄR stilen


def draw_frame(c, st, W, H):
    i = 12 * mm
    c.setStrokeColorRGB(*st["accent"])
    fr = st["frame"]
    if fr == "linje":
        c.setLineWidth(0.8); c.rect(i, i, W - 2 * i, H - 2 * i, stroke=1, fill=0)
    elif fr == "linje_tunn":
        c.setLineWidth(0.4); c.rect(i, i, W - 2 * i, H - 2 * i, stroke=1, fill=0)
    elif fr == "dubbel":
        c.setLineWidth(1.1); c.rect(i, i, W - 2 * i, H - 2 * i, stroke=1, fill=0)
        j = i + 2.4 * mm; c.setLineWidth(0.4); c.rect(j, j, W - 2 * j, H - 2 * j, stroke=1, fill=0)
    elif fr == "rundad":
        c.setLineWidth(1.2); c.roundRect(i, i, W - 2 * i, H - 2 * i, 9 * mm, stroke=1, fill=0)


def draw_page(c, order, R_, lang, page_wh):
    W, H = page_wh
    style = order["style"]
    st = STYLES[style]
    if style == "ballong" and order.get("accent") in ACCENT_ALT:
        st = dict(st, accent=ACCENT_ALT[order["accent"]])
    variant = order.get("variant", "barn")
    sky = R_["sky"]
    scale = W / A3[0]
    c.setFillColorRGB(*st["page"]); c.rect(0, 0, W, H, stroke=0, fill=1)
    draw_frame(c, st, W, H)

    R = 88 * mm * scale if style != "nattstjarna" else 98 * mm * scale
    cx, cy = W / 2, H - (54 * mm) * scale - R
    seed = int(hashlib.sha256((order["id"] + style).encode()).hexdigest()[:10], 16)
    draw_motif(c, st, W, H, cx, cy, R, seed, scale)

    # himmelscirkel
    clip = c.beginPath(); clip.circle(cx, cy, R)
    c.setFillColorRGB(*st["sky"]); c.setStrokeColorRGB(*st["ring"]); c.setLineWidth(0.9 * scale)
    c.drawPath(clip, stroke=1, fill=1)
    c.saveState(); c.clipPath(clip, stroke=0, fill=0)
    a_alt, a_az, b_alt, b_az = sky["seg"]
    vis = (a_alt > 0) & (b_alt > 0)
    ax, ay = SK.project(a_alt[vis], a_az[vis], R); bx, by = SK.project(b_alt[vis], b_az[vis], R)
    c.setStrokeColorRGB(*st["lines"]); c.setLineWidth(0.32 * scale)
    for x1, y1, x2, y2 in zip(ax, ay, bx, by):
        c.line(cx + x1, cy + y1, cx + x2, cy + y2)
    up = sky["alt"] > 0
    xs, ys = SK.project(sky["alt"][up], sky["az"][up], R)
    mags = sky["bsc"][up, 3]
    c.setFillColorRGB(*st["star"])
    for xi, yi, m in sorted(zip(xs, ys, mags), key=lambda z: -z[2]):
        c.circle(cx + xi, cy + yi, SK.star_radius(m) * scale, stroke=0, fill=1)
    for key, (balt, baz) in sky["bodies"].items():
        if balt <= 0 or key == "moon":
            continue
        bx1, by1 = SK.project(np.array([balt]), np.array([baz]), R)
        c.setFillColorRGB(*st["planet"]); c.circle(cx + bx1[0], cy + by1[0], 1.1 * mm * scale, stroke=0, fill=1)
    c.restoreState()

    # facit åt grinden: 12 ljusaste synliga stjärnor i himmelsytan
    check = []
    order_idx = np.argsort(sky["bsc"][:, 3])
    for i in order_idx:
        if sky["alt"][i] <= 10:
            continue
        px, py = SK.project(np.array([sky["alt"][i]]), np.array([sky["az"][i]]), R)
        rpx, rpy = float(px[0]), float(py[0])
        if math.hypot(rpx, rpy) > R - 1.5 * mm * scale:
            continue
        rec = {"hr": int(sky["bsc"][i, 0]), "ra_h": float(sky["bsc"][i, 1]), "dec": float(sky["bsc"][i, 2]),
               "vmag": float(sky["bsc"][i, 3]), "alt": float(sky["alt"][i]), "az": float(sky["az"][i]),
               "page_x_pt": float(cx + rpx), "page_y_pt": float(cy + rpy)}
        if FEL == "fel_stjarnposition" and len(check) == 0:
            rec["page_x_pt"] += 6.0 * scale
        check.append(rec)
        if len(check) == 12:
            break

    # månfasdisk under himmelscirkeln
    moon_r = 13 * mm * scale
    moon_cy = cy - R - 22 * mm * scale
    st_moon = {"lit": st["moon_lit"], "dark": st["moon_dark"], "maria": st["maria"], "rim": st["accent"]}
    MF.draw_moon(c, W / 2, moon_cy, moon_r, sky["moon_frac"], R_["waxing"], R_["south"], st_moon)
    pct = int(round(sky["moon_frac"] * 100))
    if FEL == "fel_manfas":
        pct = min(100, pct + 12)

    # namnet ritas först (fast ankarpunkt under månskivan) så att skrivstilens höga överhöjd (ascent) kan
    # mätas exakt – annars riskerar procentraden ovanför att hamna INUTI namnets bokstavshöjd (uppmätt fel tidigare)
    f_name, f_title, f_body = st["fonts"]
    name = order["name"]
    fn = f_name if SK.font_for(name, f_name) == f_name else "Sans"
    name_size = fit_font(name, fn, 46 * scale, W - 40 * mm * scale, 16 * scale)
    name_y = moon_cy - moon_r - 30 * mm * scale
    asc = pdfmetrics.getFont(fn).face.ascent / 1000 * name_size
    pct_y = name_y + asc + 5 * mm * scale
    c.setFillColorRGB(*st["mute"]); c.setFont(st["fonts"][2], 9 * scale)
    c.drawCentredString(W / 2, pct_y, MOON_TXT[lang].format(p=pct))
    c.setFillColorRGB(*st["ink"]); c.setFont(fn, name_size); c.drawCentredString(W / 2, name_y, name)
    y = name_y - 12 * mm * scale
    heading = HEADING[variant][lang]
    c.setFont(f_title, 13 * scale); c.setFillColorRGB(*st["accent"])
    c.drawCentredString(W / 2, y, heading)
    y -= 10 * mm * scale
    c.setFont(f_body, 11 * scale); c.setFillColorRGB(*st["ink"])
    c.drawCentredString(W / 2, y, fmt_date(lang, Date.fromisoformat(order["date"]), R_["hm"]))
    y -= 8 * mm * scale
    if FEL == "text_overlapp":
        y += 6.5 * mm * scale  # planterat fel: ortsraden flyttas upp så den krockar med datumraden ovanför
    c.setFont(f_body, 10.5 * scale); c.setFillColorRGB(*st["mute"])
    c.drawCentredString(W / 2, y, place_line(order))
    y -= 9 * mm * scale

    if variant == "barn":
        w_txt = fmt_weight(order.get("weight_g"), order["units"], lang)
        h_txt = fmt_height(order.get("height_cm"), order["units"], lang)
        if FEL == "fel_vikt_langd":
            w_txt = fmt_weight((order.get("weight_g") or 0) + 300, order["units"], lang) if w_txt else w_txt
        parts = []
        if w_txt:
            parts.append(f"{WEIGHT_LBL[lang]} {w_txt}")
        if h_txt:
            parts.append(f"{HEIGHT_LBL[lang]} {h_txt}")
        if parts:
            c.setFont(f_body, 10.5 * scale); c.setFillColorRGB(*st["ink"])
            c.drawCentredString(W / 2, y, "   ·   ".join(parts))
            y -= 8.5 * mm * scale
        if order.get("text"):
            c.setFont(f_title, 10.5 * scale); c.setFillColorRGB(*st["accent"])
            c.drawCentredString(W / 2, y, order["text"])
            y -= 8.5 * mm * scale
        fam = order.get("family") or []
        if FEL == "fel_familj" and fam:
            fam = fam[:-1]
        if fam:
            c.setFont(f_body, 6.8 * scale); c.setFillColorRGB(*st["mute"])
            c.drawCentredString(W / 2, y, FAMILJ_RUBRIK[lang].upper())
            y -= 5.2 * mm * scale
            c.setFont(f_body, 9.5 * scale); c.setFillColorRGB(*st["ink"])
            txt = "  ·  ".join(fam[:MAX_FAMILJ])
            if FEL == "text_utanfor":
                c.drawString(W - 2 * mm, y, txt)
            else:
                c.drawCentredString(W / 2, y, txt)
            y -= 8 * mm * scale
        weather = R_["weather"]
        if weather:
            def wv(key):
                v = weather.get(key)
                return v["varde"] if v else None
            # väder finns bara när country_cc=="SE" (se compute()), vilket aldrig sammanfaller med imperialt
            # (imperialt kräver land US/GB) – temperaturen visas alltså alltid i °C, som SMHI mäter den
            temp_txt = f"{wv('medel'):.0f}°C" if wv("medel") is not None else "–"
            ned = weather.get("nederbord")
            ned_txt = f"{ned['varde']:.1f} mm".replace(".", ",") if ned else "–"
            sol = weather.get("sol")
            sol_txt = f" · {sol['varde']:.1f} h sol".replace(".", ",") if sol else ""
            src = next((weather[k] for k in ("medel", "nederbord", "min", "max", "sol") if weather.get(k)), None)
            station, km = (src["station"], src["avstand_km"]) if src else ("–", 0)
            if FEL == "fel_vader":
                temp_txt = f"{(wv('medel') or 0) + 8:.0f}°C"
            if FEL == "fel_avstand_station":
                km = km + 40
            line = WEATHER_LINE[lang].format(t=temp_txt, n=ned_txt, sol=sol_txt, station=station, km=round(km, 1))
            c.setFont(f_body, 7.6 * scale); c.setFillColorRGB(*st["mute"])
            c.drawCentredString(W / 2, y, line)
            y -= 6.5 * mm * scale
            if FEL != "saknad_vader_kalla":
                c.setFont(f_body, 6.0 * scale)
                c.drawCentredString(W / 2, y, WEATHER_CREDIT[lang])
                y -= 5 * mm * scale

    credit = CREDIT[lang if FEL != "sprak_fel" else ("en" if lang != "en" else "sv")]
    if FEL != "saknad_attribution":
        c.setFont(f_body, 5.6 * scale); c.setFillColorRGB(*st["mute"])
        c.drawCentredString(W / 2, 19.5 * mm * scale, credit)
        c.drawCentredString(W / 2, 15 * mm * scale, "Moodly Sverige")
    return {"cx": cx, "cy": cy, "r": R, "moon_cx": W / 2, "moon_cy": moon_cy, "moon_r": moon_r,
            "check_stars": check, "scale": scale}


def render(order, R_, lang, path):
    c = canvas.Canvas(str(path), pagesize=A3, invariant=1, initialFontName="Sans", initialFontSize=10)
    c.setTitle(f"{order['name']} – {order['place']}"); c.setAuthor("Moodly Sverige"); c.setCreator(GENERATOR_VERSION)
    g_a3 = draw_page(c, order, R_, lang, A3); c.showPage()
    A4 = (A3[0] * SCALE_A4, A3[1] * SCALE_A4)
    c.setPageSize(A4)
    g_a4 = draw_page(c, order, R_, lang, A4); c.showPage()
    c.save()
    return {"a3": g_a3, "a4": g_a4}


def validate(order):
    if order.get("variant", "barn") not in VARIANTER:
        raise SystemExit("internal")
    if order.get("style") not in STYLES:
        raise SystemExit("internal")
    d = Date.fromisoformat(order["date"])
    if not (Date(1900, 1, 1) <= d <= Date(2050, 12, 31)):
        raise SystemExit("date_out_of_range")
    if order.get("variant", "barn") == "barn" and len(order.get("family") or []) > MAX_FAMILJ:
        raise SystemExit("internal")


def generate(order_path):
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    validate(order)
    R_ = compute(order)
    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, geos = {}, {}
    for lang in order["languages"]:
        p = out / f"{order['id']}_{lang}.pdf"
        geos[lang] = render(order, R_, lang, p)
        files[lang] = str(p)
    meta = {"generator": GENERATOR_VERSION, "product": "fodelsetavla", "order": order, "files": files,
            "utc": R_["sky"]["t"].utc_iso(), "moon_frac": R_["sky"]["moon_frac"], "elong": R_["elong"],
            "waxing": R_["waxing"], "south": R_["south"], "weather": R_["weather"],
            "geometry": {l: {"a3": {k: v for k, v in g["a3"].items() if k != "check_stars"},
                             "a4": {k: v for k, v in g["a4"].items() if k != "check_stars"}} for l, g in geos.items()},
            "check_stars": {l: g["a3"]["check_stars"] for l, g in geos.items()},
            "timings": {"totalt_s": round(time.perf_counter() - t0, 2)},
            "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"]}, indent=1))
