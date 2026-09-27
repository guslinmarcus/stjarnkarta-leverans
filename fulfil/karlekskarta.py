"""Generator: kärlekskarta – 2–5 platser med datum på en karta, förbundna med en linje i datumordning.

Karta: Natural Earth 1:50m länder (public domain), Mercatorprojektion. Linjen följer storcirkeln mellan
platserna. Orterna slås upp i GeoNames (CC BY 4.0) av fulfil.py. Avstånd: storcirkel (haversine, R = 6371,0088 km).
Affisch A3 stående (vektor – skalbar).

Körning: python karlekskarta.py order.json -> <ut>/<id>_<språk>.pdf + <ut>/<id>_meta.json
Felinjektion (bara tester): FELINJEKTION=fel_koordinat|fel_ordning|fel_avstand|saknad_kalla
"""
import gzip
import hashlib
import json
import math
import os
import sys
import time
from datetime import date
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

import kartlayout as KL

GENERATOR_VERSION = "karlekskarta/0.1.0"
FEL = os.environ.get("FELINJEKTION", "")
ROOT = Path(__file__).parent
A3 = (297 * mm, 420 * mm)
FRAME = (24 * mm, 150 * mm, 249 * mm, 249 * mm)
R_EARTH = 6371.0088
MARK = (0.831, 0.180, 0.310)  # hjärtmarkörens färg – används bara för markörerna (grinden letar efter den)
STYLES = {
    "ljus": dict(paper=(0.985, 0.972, 0.955), ocean=(0.93, 0.91, 0.88), land=(0.975, 0.955, 0.93),
                 border=(0.80, 0.74, 0.68), line=(0.45, 0.30, 0.28), ink=(0.25, 0.17, 0.15), mute=(0.50, 0.42, 0.38)),
    "natt": dict(paper=(0.07, 0.09, 0.16), ocean=(0.05, 0.07, 0.13), land=(0.12, 0.15, 0.24),
                 border=(0.25, 0.29, 0.40), line=(0.93, 0.83, 0.62), ink=(0.95, 0.90, 0.80), mute=(0.70, 0.66, 0.60)),
}
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "sv": ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
}
TXT = {"en": {"total": "{n} places · {km} km together", "credit": "Map: Natural Earth (public domain). Places: GeoNames (CC BY 4.0). Distances along the great circle."},
       "sv": {"total": "{n} platser · {km} km tillsammans", "credit": "Karta: Natural Earth (public domain). Orter: GeoNames (CC BY 4.0). Avstånd längs storcirkeln."},
       "de": {"total": "{n} Orte · {km} km gemeinsam", "credit": "Karte: Natural Earth (gemeinfrei). Orte: GeoNames (CC BY 4.0). Entfernungen entlang des Großkreises."}}


def fmt_date(d, lang):
    y, m, dd = map(int, d.split("-"))
    if lang == "en":
        return f"{dd} {MONTHS['en'][m - 1]} {y}"
    if lang == "de":
        return f"{dd}. {MONTHS['de'][m - 1]} {y}"
    return f"{dd} {MONTHS['sv'][m - 1]} {y}"


def fmt_km(x, lang):
    s = f"{round(x):,}"
    return s.replace(",", " " if lang != "en" else ",")


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(min(1.0, h)))


def merc(lon, lat):
    lat = max(-80.0, min(80.0, lat))
    return math.radians(lon), math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def gc_points(a, b, n=80):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    v1 = (math.cos(la1) * math.cos(lo1), math.cos(la1) * math.sin(lo1), math.sin(la1))
    v2 = (math.cos(la2) * math.cos(lo2), math.cos(la2) * math.sin(lo2), math.sin(la2))
    om = math.acos(max(-1, min(1, sum(p * q for p, q in zip(v1, v2)))))
    out = []
    for i in range(n + 1):
        t = i / n
        if om < 1e-9:
            v = v1
        else:
            s1, s2 = math.sin((1 - t) * om) / math.sin(om), math.sin(t * om) / math.sin(om)
            v = tuple(s1 * p + s2 * q for p, q in zip(v1, v2))
        out.append((math.degrees(math.atan2(v[2], math.hypot(v[0], v[1]))), math.degrees(math.atan2(v[1], v[0]))))
    return out


def heart_path(c, cx, cy, s):
    p = c.beginPath()
    for i in range(0, 101):
        t = 2 * math.pi * i / 100
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        (p.moveTo if i == 0 else p.lineTo)(cx + x * s / 32, cy + (y + 2.5) * s / 32)
    p.close()
    return p


def extent(places):
    xs, ys = zip(*(merc(p["lon"], p["lat"]) for p in places))
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0, math.radians(6))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = span / 2 * 1.35
    return cx - half, cy - half, cx + half, cy + half


def load_land():
    return json.load(gzip.open(ROOT / "data" / "ne_50m_lander_varld.json.gz", "rt", encoding="utf-8"))["features"]


def render(order, places, lang, path, style):
    S = STYLES[style]
    W, H = A3
    c = canvas.Canvas(str(path), pagesize=A3, pageCompression=1)
    c.setTitle(order.get("text") or "Our love map"); c.setAuthor("Moodly Sverige"); c.setCreator(GENERATOR_VERSION)
    c.setFillColorRGB(*S["paper"]); c.rect(0, 0, W, H, stroke=0, fill=1)
    fx, fy, fw, fh = FRAME
    X0, Y0, X1, Y1 = extent(places)
    sx, sy = fw / (X1 - X0), fh / (Y1 - Y0)

    def tp(lon, lat):
        x, y = merc(lon, lat)
        return fx + (x - X0) * sx, fy + (y - Y0) * sy
    c.saveState()
    cl = c.beginPath(); cl.rect(fx, fy, fw, fh); c.clipPath(cl, stroke=0, fill=0)
    c.setFillColorRGB(*S["ocean"]); c.rect(fx, fy, fw, fh, stroke=0, fill=1)
    lon_min, lon_max = math.degrees(X0) - 5, math.degrees(X1) + 5
    c.setFillColorRGB(*S["land"]); c.setStrokeColorRGB(*S["border"]); c.setLineWidth(0.35); c.setLineJoin(1)
    for f in load_land():
        for ring in f["rings"]:
            lons = [p[0] for p in ring]
            if max(lons) < lon_min or min(lons) > lon_max:
                continue
            p = c.beginPath()
            for i, (lo, la) in enumerate(ring):
                (p.moveTo if i == 0 else p.lineTo)(*tp(lo, la))
            p.close()
            c.drawPath(p, stroke=1, fill=1)
    # linjen i datumordning
    seq = list(places)
    if FEL == "fel_ordning" and len(seq) >= 3:
        seq[0], seq[-1] = seq[-1], seq[0]
    c.setStrokeColorRGB(*S["line"]); c.setLineWidth(1.3); c.setDash(4, 3); c.setLineCap(1)
    segs = []
    for a, b in zip(seq, seq[1:]):
        pts = [tp(lo, la) for la, lo in gc_points((a["lat"], a["lon"]), (b["lat"], b["lon"]))]
        p = c.beginPath(); p.moveTo(*pts[0])
        for q in pts[1:]:
            p.lineTo(*q)
        c.drawPath(p, stroke=1, fill=0)
        segs.append([pts[0], pts[-1]])
    c.setDash()
    c.restoreState()
    # markörer + etiketter (etiketterna placeras girigt så att de inte krockar med varandra eller hjärtan)
    from reportlab.pdfbase.pdfmetrics import stringWidth
    marks, pos = [], []
    for i, pl in enumerate(places, 1):
        lat, lon = pl["lat"], pl["lon"]
        if FEL == "fel_koordinat" and i == 2:
            lat, lon = lat + 2.5, lon + 2.5
        pos.append(tp(lon, lat))
    boxes = [(x - 3.5 * mm, y - 3.5 * mm, x + 3.5 * mm, y + 3.5 * mm) for x, y in pos]
    labels = []
    for i, (pl, (x, y)) in enumerate(zip(places, pos), 1):
        w = max(stringWidth(pl["place"], "KSerif", 10), stringWidth(fmt_date(pl["date"], lang), "KSans", 7.5))
        cand = []
        for dy in (0, 9, -9, 18, -18, 27, -27):
            for right in ((True, False) if x < fx + fw * 0.7 else (False, True)):
                tx = x + 5.5 * mm if right else x - 5.5 * mm - w
                cand.append((tx, y + dy * mm - 4.6 * mm, tx + w, y + dy * mm + 4.2 * mm, right, dy))
        def free(b):
            if b[0] < fx + 1 or b[2] > fx + fw - 1 or b[1] < fy + 1 or b[3] > fy + fh - 1:
                return False
            return all(b[2] < o[0] or b[0] > o[2] or b[3] < o[1] or b[1] > o[3] for o in boxes + labels)
        b = next((cb for cb in cand if free(cb)), cand[0])
        labels.append(b[:4])
        tx, ty, right, dy = b[0], y + b[5] * mm, b[4], b[5]
        if dy:
            c.setStrokeColorRGB(*S["mute"]); c.setLineWidth(0.4)
            c.line(x, y, (b[0] if right else b[2]), ty + 1 * mm)
        c.setFillColorRGB(*S["ink"]); c.setFont("KSerif", 10); c.drawString(tx, ty + 0.5 * mm, pl["place"])
        c.setFillColorRGB(*S["mute"]); c.setFont("KSans", 7.5); c.drawString(tx, ty - 3.6 * mm, fmt_date(pl["date"], lang))
    for i, (pl, (x, y)) in enumerate(zip(places, pos), 1):
        c.setFillColorRGB(*MARK); c.setStrokeColorRGB(*S["paper"]); c.setLineWidth(0.8)
        c.drawPath(heart_path(c, x, y, 6.5 * mm), stroke=1, fill=1)
        c.setFillColorRGB(1, 1, 1); c.setFont("KSans", 6.5); c.drawCentredString(x, y - 0.6 * mm, str(i))
        marks.append({"i": i, "x": x, "y": y, "lat": pl["lat"], "lon": pl["lon"]})
    c.setStrokeColorRGB(*S["ink"]); c.setLineWidth(0.7); c.rect(fx, fy, fw, fh, stroke=1, fill=0)
    # rubrik och lista
    title = (order.get("text") or "").strip() or "Our story"
    font = "KScript" if KL.covers_glyphs(title, "KScript") else "KSerifIt"
    size = KL.fit_size(title, font, 54, fw)
    c.setFillColorRGB(*S["ink"]); c.setFont(font, size); c.drawCentredString(W / 2, 124 * mm, title)
    y = 106 * mm
    dists = []
    for i, pl in enumerate(places, 1):
        c.setFillColorRGB(*MARK); c.setFont("KSans", 10); c.drawString(40 * mm, y, f"{i}")
        c.setFillColorRGB(*S["ink"]); c.setFont("KSerif", 11)
        lab = pl.get("label", "").strip()
        c.drawString(48 * mm, y, (lab + " · " if lab else "") + f"{pl['place']}, {pl.get('country_name') or pl.get('country', '')}".strip(", "))
        c.setFillColorRGB(*S["mute"]); c.setFont("KSans", 9.5)
        c.drawRightString(W - 40 * mm, y, fmt_date(pl["date"], lang))
        if i > 1:
            d = haversine(places[i - 2]["lat"], places[i - 2]["lon"], pl["lat"], pl["lon"])
            if FEL == "fel_avstand":
                d *= 1.1
            dists.append(d)
            c.setStrokeColorRGB(*S["mute"]); c.setLineWidth(0.5)  # liten pil (vektor – typsnittet saknar pilglyf)
            c.line(46 * mm, y + 7.4 * mm, 46 * mm, y + 3.9 * mm)
            c.line(45.2 * mm, y + 4.8 * mm, 46 * mm, y + 3.9 * mm); c.line(46.8 * mm, y + 4.8 * mm, 46 * mm, y + 3.9 * mm)
            c.setFont("KSans", 7.5); c.drawString(48 * mm, y + 4.2 * mm, f"{fmt_km(d, lang)} km")
        y -= 11 * mm
    total = sum(dists)
    c.setFillColorRGB(*S["ink"]); c.setFont("KSans", 10.5)
    c.drawCentredString(W / 2, y - 2 * mm, TXT[lang]["total"].format(n=len(places), km=fmt_km(total, lang)))
    if FEL != "saknad_kalla":
        c.setFillColorRGB(*S["mute"]); c.setFont("KSans", 6.5)
        c.drawCentredString(W / 2, 12 * mm, TXT[lang]["credit"] + " · Moodly Sverige")
    c.showPage(); c.save()
    return {"frame_pt": list(FRAME), "extent_merc": [X0, Y0, X1, Y1], "markers": marks, "segments": segs,
            "distances_km": dists, "total_km": total, "style": style}


def generate(order_path):
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    places = sorted(order["places"], key=lambda p: p["date"])
    for p in places:
        date.fromisoformat(p["date"])
    if not 2 <= len(places) <= 5:
        raise SystemExit("places_count")
    style = order.get("style") if order.get("style") in STYLES else "ljus"
    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, geos = {}, {}
    for lang in order["languages"]:
        p = out / f"{order['id']}_{lang}.pdf"
        geos[lang] = render(order, places, lang, p, style)
        files[lang] = str(p)
    meta = {"generator": GENERATOR_VERSION, "product": "karlekskarta", "order": order, "places_sorted": places,
            "files": files, "projektion": "Mercator (x = λ, y = ln tan(π/4 + φ/2)), latitud klämd till ±80°",
            "geometry": geos, "timings": {"totalt_s": round(time.perf_counter() - t0, 2)},
            "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"]}, indent=1))
