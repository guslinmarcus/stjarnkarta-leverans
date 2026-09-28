# -*- coding: utf-8 -*-
"""flygresekarta.py - FLYGRESEKARTAN. 1-10 flygningar (varje flygning: från/till flygplats, valfritt datum och
flygnummer) på en världskarta, med RIKTIGA storcirkelbågar (inte raka linjer - se flygdata.storcirkelbage) mellan
flygplatserna. Karta: Natural Earth 1:50m länder (public domain), Mercatorprojektion. Flygplatsdata: OpenFlights
(ODbL, källangivelse obligatorisk - se flygdata.py:s dokstycke). 4 stilar (klassisk/natt/sepia/blueprint),
5 språk (en/sv/de/fr/es), 3 format (A4/A3/50x70 cm) - vektor-PDF, skalbar utan kvalitetsförlust.

Differentiering mot Etsy-konkurrenterna (se etsy/granskning/flygresekarta_konkurrensanalys.json): riktiga
storcirkelbågar med datumlinjes-hantering (de flesta konkurrenter ritar raka linjer på en plattkarta - fel för
långa rutter), korrekta IATA-koder och avstånd ur en verklig flygplatsdatabas (inte handritade uppskattningar),
upp till 10 flygningar i EN karta (konkurrenterna klarar oftast bara 1-2), 5 språk (konkurrenterna är nästan
alltid bara engelska), 3 pappersformat inklusive 50x70.

Körning: python flygresekarta.py order.json -> <ut>/<id>_<språk>.pdf + <ut>/<id>_meta.json
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
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import flygdata as FD
import kartlayout as KL

GENERATOR_VERSION = "flygresekarta/1.0.0"
FEL = os.environ.get("FELINJEKTION", "")
ROOT = Path(__file__).parent

# mm, stående. 50x70 är samma pappersformat som de fysiska affischerna (tryck/sku.py FORMAT).
SIDOR_MM = {"A4": (210.0, 297.0), "A3": (297.0, 420.0), "50x70": (500.0, 700.0)}
# ram/layout som andel av A3-förlagan (24/150/249/249 mm på 297x420) - skalas till valt format
FRAME_FRAC = (24 / 297, 150 / 420, 249 / 297, 249 / 420)  # fx, fy, fw, fh (andel av W, H, W, H)
TITLE_Y_FRAC = 124 / 420
LIST_TOP_FRAC = 106 / 420
CREDIT_Y_FRAC = 12 / 420
MARK = None  # sätts per stil (routefärgen - grinden letar efter den)

STYLES = {
    "klassisk": dict(paper=(1.0, 1.0, 1.0), ocean=(0.73, 0.82, 0.88), land=(0.975, 0.965, 0.945),
                      border=(0.80, 0.80, 0.80), line=(0.85, 0.35, 0.10), ink=(0.10, 0.10, 0.10), mute=(0.45, 0.42, 0.38)),
    "natt": dict(paper=(0.06, 0.08, 0.14), ocean=(0.02, 0.03, 0.07), land=(0.10, 0.13, 0.20),
                 border=(0.25, 0.29, 0.40), line=(0.93, 0.80, 0.52), ink=(0.93, 0.85, 0.66), mute=(0.70, 0.66, 0.60)),
    "sepia": dict(paper=(0.97, 0.94, 0.88), ocean=(0.80, 0.74, 0.63), land=(0.95, 0.91, 0.83),
                  border=(0.70, 0.60, 0.45), line=(0.60, 0.25, 0.12), ink=(0.30, 0.20, 0.12), mute=(0.55, 0.45, 0.35)),
    "blueprint": dict(paper=(0.08, 0.24, 0.45), ocean=(0.05, 0.17, 0.33), land=(0.08, 0.24, 0.45),
                       border=(0.86, 0.91, 0.97), line=(0.97, 0.98, 1.0), ink=(0.97, 0.98, 1.0), mute=(0.75, 0.82, 0.90)),
}
MONTHS = {
    "en": "January February March April May June July August September October November December".split(),
    "sv": "januari februari mars april maj juni juli augusti september oktober november december".split(),
    "de": "Januar Februar März April Mai Juni Juli August September Oktober November Dezember".split(),
    "fr": "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split(),
    "es": "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(),
}
TXT = {
    "en": {"total": "{n} flights · {km} km together", "unknown_date": "date not given",
           "credit": "Airports & routes: © OpenFlights (openflights.org), ODbL 1.0. Map: Natural Earth (public domain). "
                     "Great-circle distances."},
    "sv": {"total": "{n} flygningar · {km} km tillsammans", "unknown_date": "datum ej angivet",
           "credit": "Flygplatser & rutter: © OpenFlights (openflights.org), ODbL 1.0. Karta: Natural Earth (public domain). "
                     "Storcirkelavstånd."},
    "de": {"total": "{n} Flüge · {km} km zusammen", "unknown_date": "Datum nicht angegeben",
           "credit": "Flughäfen & Routen: © OpenFlights (openflights.org), ODbL 1.0. Karte: Natural Earth (gemeinfrei). "
                     "Großkreisentfernungen."},
    "fr": {"total": "{n} vols · {km} km ensemble", "unknown_date": "date non indiquée",
           "credit": "Aéroports et itinéraires : © OpenFlights (openflights.org), ODbL 1.0. Carte : Natural Earth (domaine public). "
                     "Distances orthodromiques."},
    "es": {"total": "{n} vuelos · {km} km juntos", "unknown_date": "fecha no indicada",
           "credit": "Aeropuertos y rutas: © OpenFlights (openflights.org), ODbL 1.0. Mapa: Natural Earth (dominio público). "
                     "Distancias ortodrómicas."},
}


def fmt_date(d, lang):
    if not d:
        return TXT[lang]["unknown_date"]
    y, m, dd = map(int, d.split("-"))
    if lang == "en":
        return f"{dd} {MONTHS['en'][m - 1]} {y}"
    if lang == "de":
        return f"{dd}. {MONTHS['de'][m - 1]} {y}"
    if lang in ("fr",):
        return f"{dd} {MONTHS['fr'][m - 1]} {y}"
    if lang == "es":
        return f"{dd} de {MONTHS['es'][m - 1]} de {y}"
    return f"{dd} {MONTHS['sv'][m - 1]} {y}"


def fmt_km(x, lang):
    s = f"{round(x):,}"
    return s.replace(",", " " if lang != "en" else ",")


def merc(lon, lat):
    lat = max(-80.0, min(80.0, lat))
    return math.radians(lon), math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def dela_vid_antimeridian(punkter):
    if not punkter:
        return []
    seg = [punkter[0]]
    ut = [seg]
    for prev, cur in zip(punkter, punkter[1:]):
        if abs(cur[1] - prev[1]) > 180:
            seg = [cur]
            ut.append(seg)
        else:
            seg.append(cur)
    return ut


def plane_path(c, cx, cy, ang_deg, s):
    """Enkel vektor-flygplansform (pappersflygplan/dart), pekandes i rörelseriktningen ang_deg (0 = öster)."""
    pts_local = [(1.0, 0), (-0.6, 0.45), (-0.25, 0), (-0.6, -0.45)]
    a = math.radians(ang_deg)
    ca, sa = math.cos(a), math.sin(a)
    p = c.beginPath()
    for i, (lx, ly) in enumerate(pts_local):
        x, y = cx + (lx * ca - ly * sa) * s, cy + (lx * sa + ly * ca) * s
        (p.moveTo if i == 0 else p.lineTo)(x, y)
    p.close()
    return p


def unwrap_legs(leg_points):
    """leg_points: lista av (lat,lon)-listor, en per flygning (rå, ur storcirkelbage). Returnerar samma struktur
    men med longitud "upplindad" (ingen konstgjord ±360-språng) inom varje flygning OCH mellan flygningar, så att
    kartans utsnitt (extent) blir rätt även när en resa passerar nära ±180°. Ritningen splittas ändå separat vid
    datumlinjen (dela_vid_antimeridian) - detta är bara för att räkna ut vilket kartutsnitt som behövs."""
    out = []
    ref = leg_points[0][0][1] if leg_points and leg_points[0] else 0.0
    for leg in leg_points:
        ny = []
        for lat, lon in leg:
            while lon - ref > 180:
                lon -= 360
            while lon - ref < -180:
                lon += 360
            ny.append((lat, lon))
            ref = lon
        out.append(ny)
    return out


def extent(unwrapped_legs):
    xs, ys = [], []
    for leg in unwrapped_legs:
        for lat, lon in leg:
            x, y = merc(lon, lat)
            xs.append(x); ys.append(y)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    spanx, spany = x1 - x0, y1 - y0
    span = max(spanx, spany, math.radians(8))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = span / 2 * 1.22
    return cx - half, cy - half, cx + half, cy + half


def load_land():
    return json.load(gzip.open(ROOT / "data" / "ne_50m_lander_varld.json.gz", "rt", encoding="utf-8"))["features"]


def render(order, flights, airports, lang, path, style, fmt):
    S = STYLES[style]
    W, H = SIDOR_MM[fmt][0] * mm, SIDOR_MM[fmt][1] * mm
    c = canvas.Canvas(str(path), pagesize=(W, H), pageCompression=1, initialFontName="KSans", initialFontSize=10)
    c.setTitle(order.get("text") or "Flight map"); c.setAuthor("Moodly Sverige"); c.setCreator(GENERATOR_VERSION)
    c.setFillColorRGB(*S["paper"]); c.rect(0, 0, W, H, stroke=0, fill=1)
    fxf, fyf, fwf, fhf = FRAME_FRAC
    fx, fy, fw, fh = fxf * W, fyf * H, fwf * W, fhf * H

    raw_legs = [FD.storcirkelbage(a["lat"], a["lon"], b["lat"], b["lon"], n=96) for a, b in flights]
    unwrapped = unwrap_legs(raw_legs)
    X0, Y0, X1, Y1 = extent(unwrapped)
    sx, sy = fw / (X1 - X0), fh / (Y1 - Y0)

    def tp(lon, lat):
        x, y = merc(lon, lat)
        return fx + (x - X0) * sx, fy + (y - Y0) * sy

    c.saveState()
    cl = c.beginPath(); cl.rect(fx, fy, fw, fh); c.clipPath(cl, stroke=0, fill=0)
    c.setFillColorRGB(*S["ocean"]); c.rect(fx, fy, fw, fh, stroke=0, fill=1)
    lon_min, lon_max = math.degrees(X0) - 8, math.degrees(X1) + 8
    c.setFillColorRGB(*S["land"]); c.setStrokeColorRGB(*S["border"]); c.setLineWidth(0.35 * (W / (297 * mm))); c.setLineJoin(1)
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
    # storcirkelbågarna, en flygning i taget, delade vid datumlinjen
    linewidth = 1.5 * (W / (297 * mm))
    c.setStrokeColorRGB(*S["line"]); c.setLineWidth(linewidth); c.setLineCap(1)
    leg_ends = []
    for i, raw in enumerate(raw_legs):
        pts_ll = raw
        if FEL == "fel_ordning" and i == 0 and len(raw_legs) > 1:
            pts_ll = list(reversed(pts_ll))
        for seg in dela_vid_antimeridian(pts_ll):
            pts = [tp(lo, la) for la, lo in seg]
            if len(pts) < 2:
                continue
            p = c.beginPath(); p.moveTo(*pts[0])
            for q in pts[1:]:
                p.lineTo(*q)
            c.drawPath(p, stroke=1, fill=0)
        a_pt, b_pt = tp(pts_ll[0][1], pts_ll[0][0]), tp(pts_ll[-1][1], pts_ll[-1][0])
        leg_ends.append((a_pt, b_pt))
        # riktningspil (litet flygplan) vid bågens mittpunkt, i rörelseriktningen
        mid = pts_ll[len(pts_ll) // 2]
        nxt = pts_ll[min(len(pts_ll) // 2 + 1, len(pts_ll) - 1)]
        mx, my = tp(mid[1], mid[0]); nx2, ny2 = tp(nxt[1], nxt[0])
        ang = math.degrees(math.atan2(ny2 - my, nx2 - mx))
        c.setFillColorRGB(*S["line"]); c.setStrokeColorRGB(*S["paper"]); c.setLineWidth(0.5)
        c.drawPath(plane_path(c, mx, my, ang, 3.2 * mm * (W / (297 * mm))), stroke=1, fill=1)
    c.restoreState()

    # unika flygplatser, markörer + etiketter (greedig placering, krockar inte med varandra eller markörerna)
    seen, uniq = set(), []
    for a, b in flights:
        for ap in (a, b):
            k = (round(ap["lat"], 4), round(ap["lon"], 4))
            if k not in seen:
                seen.add(k); uniq.append(ap)
    pos = []
    for i, ap in enumerate(uniq):
        lat, lon = ap["lat"], ap["lon"]
        if FEL == "fel_koordinat" and i == 0 and len(uniq) > 1:
            lat, lon = lat + 3.0, lon + 3.0
        pos.append(tp(lon, lat))
    R = 3.0 * mm * (W / (297 * mm))
    boxes = [(x - R, y - R, x + R, y + R) for x, y in pos]
    labels = []
    for ap, (x, y) in zip(uniq, pos):
        label = f"{ap['iata'] or ap['icao'] or ''} {ap['stad']}".strip()
        w = stringWidth(label, "KSans", 8.5)
        cand = []
        for dy in (0, 8, -8, 16, -16, 24, -24):
            for right in ((True, False) if x < fx + fw * 0.7 else (False, True)):
                tx = x + (R + 2 * mm) if right else x - (R + 2 * mm) - w
                cand.append((tx, y + dy * mm * 0.7 - 3.2 * mm, tx + w, y + dy * mm * 0.7 + 3.0 * mm, right))

        def free(bx):
            if bx[0] < fx + 1 or bx[2] > fx + fw - 1 or bx[1] < fy + 1 or bx[3] > fy + fh - 1:
                return False
            return all(bx[2] < o[0] or bx[0] > o[2] or bx[3] < o[1] or bx[1] > o[3] for o in boxes + labels)
        b = next((cb for cb in cand if free(cb)), cand[0])
        labels.append(b[:4])
        c.setFillColorRGB(*S["ink"]); c.setFont("KSans", 8.5); c.drawString(b[0], b[1] + 0.4 * mm, label)
    for ap, (x, y) in zip(uniq, pos):
        c.setFillColorRGB(*S["line"]); c.setStrokeColorRGB(*S["paper"]); c.setLineWidth(0.8)
        c.circle(x, y, R, stroke=1, fill=1)
    c.setStrokeColorRGB(*S["ink"]); c.setLineWidth(0.7); c.rect(fx, fy, fw, fh, stroke=1, fill=0)

    # rubrik
    title = (order.get("text") or "").strip() or "Our flights"
    font = "KScript" if KL.covers_glyphs(title, "KScript") else "KSerifIt"
    size = KL.fit_size(title, font, 42 * (W / (297 * mm)), fw)
    c.setFillColorRGB(*S["ink"]); c.setFont(font, size); c.drawCentredString(W / 2, TITLE_Y_FRAC * H, title)

    # flygningslistan
    n = len(flights)
    y0 = LIST_TOP_FRAC * H
    bottom_stop = CREDIT_Y_FRAC * H + 12 * mm
    row_h = min(11 * mm, max(6.5 * mm, (y0 - bottom_stop) / max(n, 1)))
    fsz_main = max(7.0, min(11.0, row_h / mm * 0.85))
    fsz_sub = max(6.0, fsz_main - 1.5)
    y = y0
    dists = []
    left_x, right_x = fx, fx + fw
    for i, (a, b) in enumerate(flights, 1):
        d = FD.haversine(a["lat"], a["lon"], b["lat"], b["lon"])
        if FEL == "fel_avstand":
            d *= 1.1
        dists.append(d)
        route = f"{a['iata'] or a['icao'] or a['stad']} - {b['iata'] or b['icao'] or b['stad']}"
        sub = f"{a['stad']} – {b['stad']}"
        if order["flights"][i - 1].get("flight_no"):
            sub += f" · {order['flights'][i - 1]['flight_no']}"
        c.setFillColorRGB(*S["line"]); c.setFont("KSans", fsz_main); c.drawString(left_x, y, f"{i}")
        c.setFillColorRGB(*S["ink"]); c.setFont("KSerif", fsz_main); c.drawString(left_x + 7 * mm, y, route)
        c.setFillColorRGB(*S["mute"]); c.setFont("KSans", fsz_sub)
        c.drawString(left_x + 7 * mm, y - row_h * 0.42, sub)
        dtxt = fmt_date(order["flights"][i - 1].get("date"), lang)
        c.drawRightString(right_x, y, dtxt)
        c.setFont("KSans", fsz_sub); c.drawRightString(right_x, y - row_h * 0.42, f"{fmt_km(d, lang)} km")
        y -= row_h
    total = sum(dists)
    c.setFillColorRGB(*S["ink"]); c.setFont("KSans", max(8.5, fsz_main))
    c.drawCentredString(W / 2, y - 3 * mm, TXT[lang]["total"].format(n=n, km=fmt_km(total, lang)))
    if FEL != "saknad_kalla":
        c.setFillColorRGB(*S["mute"]); c.setFont("KSans", 6.2 * (W / (297 * mm)) if fmt != "A4" else 5.6)
        c.drawCentredString(W / 2, CREDIT_Y_FRAC * H, TXT[lang]["credit"] + " · Moodly Sverige")
    c.showPage(); c.save()
    return {"frame_pt": [fx, fy, fw, fh], "extent_merc": [X0, Y0, X1, Y1], "page_mm": list(SIDOR_MM[fmt]),
            "airports": [{"iata": a["iata"], "icao": a["icao"], "lat": a["lat"], "lon": a["lon"]} for a in uniq],
            "leg_ends_pt": leg_ends, "distances_km": dists, "total_km": total, "style": style, "format": fmt}


def generate(order_path):
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    flyglist = order["flights"]
    if not 1 <= len(flyglist) <= 10:
        raise SystemExit("flights_count")
    flights = []
    for leg in flyglist:
        a = leg["from"]; b = leg["to"]
        flights.append((a, b))
    style = order.get("style") if order.get("style") in STYLES else "klassisk"
    fmt = order.get("format") if order.get("format") in SIDOR_MM else "A3"
    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, geos = {}, {}
    for lang in order["languages"]:
        p = out / f"{order['id']}_{lang}.pdf"
        geos[lang] = render(order, flights, None, lang, p, style, fmt)
        files[lang] = str(p)
    meta = {"generator": GENERATOR_VERSION, "product": "flygresekarta", "order": order,
            "files": files, "projektion": "Mercator (x = λ, y = ln tan(π/4 + φ/2)), latitud klämd till ±80°",
            "geometry": geos, "timings": {"totalt_s": round(time.perf_counter() - t0, 2)},
            "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"]}, indent=1))
