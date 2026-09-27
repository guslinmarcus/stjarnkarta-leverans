"""Generator: minimalistisk stadskarta (affisch A3 stående, vektor – skalbar till A2/A1) ur OpenStreetMap.

Fyra stilar: klassisk (vit), natt (marinblå/guld), sepia, blueprint. Kartan ritas av oss ur OSM-data
(vägar, järnväg, vatten, kust, grönområden) – inga kartplattor används. ODbL: en tryckt karta är ett
"Produced Work" och kräver källnotis; "© OpenStreetMap contributors" och "ODbL" trycks på affischen.
Data hämtas via Overpass (Private.coffee, se osmdata.py för villkoren), mitten = ortens koordinat (GeoNames).

Körning: python stadskarta.py order.json -> <ut>/<id>_<språk>.pdf + <ut>/<id>_meta.json
Felinjektion (bara tester): FELINJEKTION=saknad_attribution|tomt_omrade|fel_centrum|lag_upplosning
"""
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import kartlayout as KL
import osmdata as O

GENERATOR_VERSION = "stadskarta/0.1.0"
FEL = os.environ.get("FELINJEKTION", "")
ROOT = Path(__file__).parent
A3 = (297 * mm, 420 * mm)
FRAME = (20 * mm, 118 * mm, 257 * mm, 282 * mm)  # x, y, b, h
RADIUS_DEFAULT_KM = 3.0
ATTRIB = "© OpenStreetMap contributors · Map data: Open Database License (ODbL) 1.0 · openstreetmap.org/copyright"
LANG = {
    "en": {"data": "Map data as of {d}"},
    "sv": {"data": "Kartdata per {d}"},
    "de": {"data": "Kartendaten Stand {d}"},
}


def fmt_coord(lat, lon):
    return f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}   {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}"


def spaced(s):
    return " ".join(s.upper())


def compute(order):
    t = {}
    lat, lon = order["lat"], order["lon"]
    if FEL == "fel_centrum":
        lat_c, lon_c = lat, lon + 3000 / (111320 * max(0.2, __import__("math").cos(__import__("math").radians(lat))))
    else:
        lat_c, lon_c = lat, lon
    r_km = float(order.get("radius_km") or RADIUS_DEFAULT_KM)
    r_km = min(max(r_km, 1.0), 8.0)
    fw, fh = FRAME[2], FRAME[3]
    half_w = r_km * 1000
    half_h = half_w * fh / fw
    proj, _ = O.mercator_proj(lat_c, lon_c)
    _, inv = O.mercator_proj(lat, lon)  # frågans ruta räknas alltid kring orten
    lo0, la0 = inv(-half_w * 1.03, -half_h * 1.03)
    lo1, la1 = inv(half_w * 1.03, half_h * 1.03)
    t0 = time.perf_counter()
    q = O.query_map(float(la0), float(lo0), float(la1), float(lo1), minor=True)
    osm, ep = O.fetch(q)
    t["osm_s"] = time.perf_counter() - t0
    F = O.parse_features(osm)
    return {"center": [lat_c, lon_c], "geocoded": [lat, lon], "half": [half_w, half_h], "proj": proj,
            "bbox_ll": [float(la0), float(lo0), float(la1), float(lo1)], "F": F, "query": q,
            "osm_ts": osm.get("osm3s", {}).get("timestamp_osm_base", ""), "endpoint": ep, "timings": t}


def render(order, R, lang, path):
    style = order.get("style") if order.get("style") in O.STYLES else "klassisk"
    S = O.STYLES[style]
    W, H = A3
    c = canvas.Canvas(str(path), pagesize=A3, pageCompression=1)
    c.setTitle(f"{order['place']} – city map"); c.setAuthor("Moodly Sverige"); c.setCreator(GENERATOR_VERSION)
    c.setFillColorRGB(*S["paper"]); c.rect(0, 0, W, H, stroke=0, fill=1)
    x, y, fw, fh = FRAME
    hw, hh = R["half"]
    bbox = (-hw, -hh, hw, hh)
    skip = {"roads_quadrant"} if FEL == "tomt_omrade" else set()
    t0 = time.perf_counter()
    if FEL == "lag_upplosning":
        import fitz
        tmp = io.BytesIO()
        c2 = canvas.Canvas(tmp, pagesize=(fw, fh))
        info = O.draw_features(c2, R["F"], R["proj"], (0, 0, fw, fh), bbox, style, fw / (2 * hw))
        c2.showPage(); c2.save()
        pix = fitz.open("pdf", tmp.getvalue())[0].get_pixmap(dpi=60)
        c.drawImage(ImageReader(io.BytesIO(pix.tobytes("png"))), x, y, fw, fh)
    else:
        info = O.draw_features(c, R["F"], R["proj"], (x, y, fw, fh), bbox, style, fw / (2 * hw),
                               skip={"quadrant"} if skip else None)
    render_s = time.perf_counter() - t0
    c.setStrokeColorRGB(*S["ink"]); c.setLineWidth(0.8); c.rect(x, y, fw, fh, stroke=1, fill=0)
    title = (order.get("text") or "").strip() or order["place"]
    t_s = spaced(title) if len(title) <= 18 else title.upper()
    font = "KSans" if KL.covers_glyphs(t_s, "KSans") else "KSerif"
    size = KL.fit_size(t_s, font, 46, fw)
    c.setFillColorRGB(*S["ink"]); c.setFont(font, size); c.drawCentredString(W / 2, 86 * mm, t_s)
    sub = f"{order['place']}, {order.get('country', '')}".strip(", ") if order.get("text") else (order.get("country") or "")
    c.setLineWidth(0.6)
    c.line(W / 2 - 30 * mm, 78 * mm, W / 2 + 30 * mm, 78 * mm)
    c.setFont("KSans", 13); c.drawCentredString(W / 2, 68 * mm, spaced(sub) if len(sub) <= 28 else sub.upper())
    c.setFont("KSans", 10.5); c.drawCentredString(W / 2, 56 * mm, fmt_coord(*R["geocoded"]))
    if FEL != "saknad_attribution":
        c.setFont("KSans", 7); c.drawCentredString(W / 2, 14 * mm, ATTRIB)
        c.setFont("KSans", 6.5)
        c.drawCentredString(W / 2, 10 * mm, LANG.get(lang, LANG["en"])["data"].format(d=R["osm_ts"][:10]) + " · Moodly Sverige")
    c.showPage(); c.save()
    return {"frame_pt": [x, y, fw, fh], "bbox_m": list(bbox), "page_pt": [W, H], "style": style, "ritade": info,
            "rendera_karta_s": round(render_s, 2)}


def generate(order_path):
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    R = compute(order)
    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, geos = {}, {}
    t1 = time.perf_counter()
    for lang in order["languages"]:
        p = out / f"{order['id']}_{lang}.pdf"
        geos[lang] = render(order, R, lang, p)
        files[lang] = str(p)
    timings = dict(R["timings"], rendera_s=time.perf_counter() - t1, totalt_s=time.perf_counter() - t0)
    meta = {"generator": GENERATOR_VERSION, "product": "stadskarta", "order": order, "files": files,
            "center": R["center"], "geocoded": R["geocoded"], "half_m": R["half"], "bbox_ll": R["bbox_ll"],
            "projektion": {"typ": "Mercator, skala sann vid mittens latitud, origo i mitten", "lat0": R["center"][0], "lon0": R["center"][1]},
            "osm": {"tidsstampel": R["osm_ts"], "instans": R["endpoint"], "fraga": R["query"]},
            "geometry": geos, "timings": {k: round(v, 2) for k, v in timings.items()},
            "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"]}, indent=1))
