"""Generator: "Hitta hit"-karta för bröllop och andra tillfällen (fest, dop, kalas). 1–3 platser (t.ex. vigsel,
mottagning, hotell/parkering) på en lokal karta ur OpenStreetMap, numrerade i den ordning kunden angav dem,
med vägen mellan dem beräknad på OSM:s eget vägnät (Dijkstra på verkliga vägsegment, se vagnat.py) – inte en
rak linje. Under kartan skrivs avstånd och ungefärlig bil- och gångtid per sträcka.

Karta: samma ritmotor och stilar som stadskarta.py (osmdata.py: klassisk, natt, sepia, blueprint), OSM-data ur
Geofabrik-extrakt via osmextract.py (ODbL, "© OpenStreetMap contributors" trycks på kartan). Adresser slås upp
i samma extrakts addr:*-taggar (som historisk.py) när kunden anger en gatuadress; utan adress används ortens
koordinat (GeoNames, satt av fulfil.py).

Utskrift: en PDF med två sidor – sida 1 = kort (A5, 148×210 mm), sida 2 = affisch (A4, 210×297 mm). Samma
geografiska utsnitt på båda sidorna, bara sidstorlek och textstorlek skiljer.

Körning: python brollopskarta.py order.json -> <ut>/<id>_<språk>.pdf + <ut>/<id>_meta.json
Felinjektion (bara tester): FELINJEKTION=saknad_attribution|fel_avstand|fel_tid|fel_koordinat|fel_ordning|
                            rutt_pa_fel_vag|saknad_markor|tomt_omrade|lag_upplosning|text_kapad
"""
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import kartgeo as K
import kartlayout as KL
import osmdata as O
import vagnat as VN

GENERATOR_VERSION = "brollopskarta/0.1.0"
FEL = os.environ.get("FELINJEKTION", "")
ROOT = Path(__file__).parent
MARK = (0.831, 0.180, 0.310)  # markörens fyllfärg (samma som karlekskartans hjärtan) – grinden letar efter den
MAX_LEG_M = 15000.0  # om två platser ligger längre isär än så är det troligen fel ort/adress, inte ett bröllop
ATTRIB = "© OpenStreetMap contributors"
ODBL = "Open Database License (ODbL) 1.0 · openstreetmap.org/copyright"
ROLE_LABEL = {
    "sv": {"vigsel": "Vigsel", "mottagning": "Mottagning", "hotell": "Hotell", "parkering": "Parkering", "fest": "Fest", "annat": "Plats"},
    "en": {"vigsel": "Ceremony", "mottagning": "Reception", "hotell": "Hotel", "parkering": "Parking", "fest": "Party", "annat": "Place"},
    "de": {"vigsel": "Trauung", "mottagning": "Feier", "hotell": "Hotel", "parkering": "Parken", "fest": "Party", "annat": "Ort"},
}
MONTHS = {
    "sv": ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
}
TXT = {
    "sv": dict(leg="{a} -> {b}   {km} km · ca {drive} min bil · ca {walk} min gång",
               credit="Karta: OpenStreetMap-bidragsgivare. Avstånd och restider är beräknade på OpenStreetMap-vägnätet och är ungefärliga.",
               directions="Vägbeskrivning", places="Platser"),
    "en": dict(leg="{a} -> {b}   {km} km · approx. {drive} min by car · {walk} min on foot",
               credit="Map: OpenStreetMap contributors. Distances and times are calculated from the OpenStreetMap road network and are approximate.",
               directions="Directions", places="Places"),
    "de": dict(leg="{a} -> {b}   {km} km · ca. {drive} Min. Auto · {walk} Min. zu Fuß",
               credit="Karte: OpenStreetMap-Mitwirkende. Entfernungen und Zeiten sind aus dem OpenStreetMap-Straßennetz berechnet und ungefähr.",
               directions="Wegbeschreibung", places="Orte"),
}
FORMATS = {
    "kort": dict(size=(148 * mm, 210 * mm), frame=(10 * mm, 66 * mm, 128 * mm, 114 * mm),
                 title_y=210 * mm - 13 * mm, sub_y=210 * mm - 19.5 * mm, legend_y=59 * mm, line_h=5.6 * mm,
                 title_max=22, sub_size=9, legend_size=8.2, dir_size=7.4, attrib_y=(9.5 * mm, 6 * mm), attrib_size=(5.6, 5.2),
                 marker_r=3.0 * mm, marker_font=6.5),
    "affisch": dict(size=(210 * mm, 297 * mm), frame=(15 * mm, 100 * mm, 180 * mm, 154 * mm),
                    title_y=297 * mm - 20 * mm, sub_y=297 * mm - 29 * mm, legend_y=93 * mm, line_h=7.2 * mm,
                    title_max=34, sub_size=11.5, legend_size=10.5, dir_size=9.5, attrib_y=(13 * mm, 8.5 * mm), attrib_size=(7, 6.5),
                    marker_r=4.0 * mm, marker_font=8.5),
}


def _norm(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(ch)).strip()


def split_address(addr):
    m = re.match(r"^(.*?)[\s,]+(\d+\s*[a-zA-Z]?)\s*$", addr.strip())
    if m:
        return _norm(m.group(1)), m.group(2).replace(" ", "").lower()
    return _norm(addr), ""


def resolve_place(p):
    """(lat, lon, precision). Utan adress: ortens koordinat (satt av fulfil.py, GeoNames). Med adress: slå upp
    i OSM-adresserna kring orten (samma extrakt som vägarna, ingen extern tjänst). Adress som inte hittas ->
    SystemExit('address_not_found') (samma felkod som historisk.py, redan känd av leveransportalen)."""
    lat0, lon0 = p["lat"], p["lon"]
    addr = (p.get("address") or "").strip()
    if not addr:
        return lat0, lon0, "ort"
    data, _ = O.addresses(lat0, lon0, radius_km=6.0)
    street, nr = split_address(addr)
    R = 4000.0
    hits = [a for a in data["adresser"] if _norm(a["gata"]) == street and (not nr or a["nr"].replace(" ", "").lower() == nr)
            and K.haversine_m(lat0, lon0, a["lat"], a["lon"]) <= R]
    if hits:
        best = min(hits, key=lambda a: K.haversine_m(lat0, lon0, a["lat"], a["lon"]))
        return best["lat"], best["lon"], "hus"
    street_hits = [dict(g, gata=name) for name, lst in data["gator"].items() if _norm(name) == street for g in lst
                   if K.haversine_m(lat0, lon0, g["lat"], g["lon"]) <= R]
    if street_hits:
        best0 = min(street_hits, key=lambda a: K.haversine_m(lat0, lon0, a["lat"], a["lon"]))
        near = [a for a in street_hits if K.haversine_m(best0["lat"], best0["lon"], a["lat"], a["lon"]) < 3000]
        return sum(a["lat"] for a in near) / len(near), sum(a["lon"] for a in near) / len(near), "gata"
    raise SystemExit("address_not_found")


def fmt_km(m, lang):
    s = f"{m / 1000.0:.1f}"
    return s.replace(".", ",") if lang != "en" else s


def fmt_min(s):
    return str(max(1, round(s / 60.0)))


def fmt_date(d, lang):
    if not d:
        return ""
    y, mo, dd = map(int, d.split("-"))
    if lang == "de":
        return f"{dd}. {MONTHS['de'][mo - 1]} {y}"
    if lang == "en":
        return f"{dd} {MONTHS['en'][mo - 1]} {y}"
    return f"{dd} {MONTHS['sv'][mo - 1]} {y}"


def role_text(p, lang):
    lab = (p.get("label") or "").strip()
    if lab:
        return lab
    return ROLE_LABEL.get(lang, ROLE_LABEL["en"]).get(p.get("role") or "annat", ROLE_LABEL["en"]["annat"])


def compute(order):
    places_in = order["places"]
    if not 1 <= len(places_in) <= 3:
        raise SystemExit("places_count")
    resolved = []
    for p in places_in:
        lat, lon, prec = resolve_place(p)
        resolved.append(dict(p, lat=lat, lon=lon, precision=prec))
    for a, b in zip(resolved, resolved[1:]):
        if K.haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) > MAX_LEG_M:
            raise SystemExit("platser_for_langt_isar")
    lat_c = sum(p["lat"] for p in resolved) / len(resolved)
    lon_c = sum(p["lon"] for p in resolved) / len(resolved)
    proj, inv = O.mercator_proj(lat_c, lon_c)
    xs, ys = proj([p["lon"] for p in resolved], [p["lat"] for p in resolved])
    half_w_raw = max(1.0, float(max(abs(x) for x in xs)))
    half_h_raw = max(1.0, float(max(abs(y) for y in ys)))
    pad = max(300.0, 0.4 * max(half_w_raw, half_h_raw))
    half_w = max(350.0, half_w_raw + pad)
    half_h = max(350.0, half_h_raw + pad)
    lo0, la0 = inv(-half_w * 1.05, -half_h * 1.05)
    lo1, la1 = inv(half_w * 1.05, half_h * 1.05)
    t0 = time.perf_counter()
    q = O.query_map(float(la0), float(lo0), float(la1), float(lo1), minor=True)
    osm, ep = O.fetch(q)
    t_osm = time.perf_counter() - t0
    F = O.parse_features(osm)
    legs = []
    for i in range(len(resolved) - 1):
        a, b = resolved[i], resolved[i + 1]
        r = VN.route(F["roads"], (a["lat"], a["lon"]), (b["lat"], b["lon"]))
        if r is None:
            raise SystemExit("ingen_vag_hittad")
        legs.append({"from": i, "to": i + 1, "dist_m": round(r["dist_m"], 1), "drive_s": round(r["drive_s"], 1),
                     "walk_s": round(r["walk_s"], 1), "path": [[float(lo), float(la)] for lo, la in r["path"]],
                     "snap_a_m": r["snap_a_m"], "snap_b_m": r["snap_b_m"]})
    return {"resolved": resolved, "center": [lat_c, lon_c], "half_content_m": [half_w, half_h],
            "bbox_ll": [float(la0), float(lo0), float(la1), float(lo1)], "F": F, "query": q,
            "osm_ts": osm.get("osm3s", {}).get("timestamp_osm_base", ""), "endpoint": ep, "legs": legs,
            "timings": {"osm_s": round(t_osm, 2)}}


def _mk_tp(proj, cx0, cy0, cx1, cy1, frame):
    fx, fy, fw, fh = frame
    scale = min(fw / (cx1 - cx0), fh / (cy1 - cy0))
    shown_w, shown_h = fw / scale, fh / scale
    x0, y0 = -shown_w / 2, -shown_h / 2
    x1, y1 = shown_w / 2, shown_h / 2

    def tp(lon, lat):
        x, y = proj(lon, lat)
        return fx + (float(x) - x0) * scale, fy + (float(y) - y0) * scale
    return tp, (x0, y0, x1, y1), scale


def draw_page(c, order, R, lang, fmt, resolved):
    G = FORMATS[fmt]
    W, H = G["size"]
    style = order.get("style") if order.get("style") in O.STYLES else "klassisk"
    S = O.STYLES[style]
    c.setPageSize(G["size"])
    c.setFillColorRGB(*S["paper"]); c.rect(0, 0, W, H, stroke=0, fill=1)
    frame = G["frame"]
    proj = O.mercator_proj(*R["center"])[0]
    half_w, half_h = R["half_content_m"]
    tp, bbox, scale = _mk_tp(proj, -half_w, -half_h, half_w, half_h, frame)
    fx, fy, fw, fh = frame
    if FEL == "lag_upplosning":
        import fitz
        tmp = io.BytesIO()
        c2 = canvas.Canvas(tmp, pagesize=(fw, fh))
        info = O.draw_features(c2, R["F"], proj, (0, 0, fw, fh), bbox, style, scale)
        c2.showPage(); c2.save()
        pix = fitz.open("pdf", tmp.getvalue())[0].get_pixmap(dpi=60)
        c.drawImage(ImageReader(io.BytesIO(pix.tobytes("png"))), fx, fy, fw, fh)
    else:
        skip = {"quadrant"} if FEL == "tomt_omrade" else None
        info = O.draw_features(c, R["F"], proj, frame, bbox, style, scale, skip=skip)
    # rutten: en sammanhängande streckad linje per sträcka, ovanpå kartan
    c.saveState()
    cl = c.beginPath(); cl.rect(fx, fy, fw, fh); c.clipPath(cl, stroke=0, fill=0)
    c.setStrokeColorRGB(*S["ink"]); c.setLineWidth(1.5); c.setLineCap(1); c.setLineJoin(1); c.setDash(5, 3.2)
    for li, leg in enumerate(R["legs"]):
        pts = leg["path"]
        if FEL == "rutt_pa_fel_vag" and li == 0:
            pts = [pts[0], pts[-1]]
        xy = [tp(lo, la) for lo, la in pts]
        p = c.beginPath(); p.moveTo(*xy[0])
        for q in xy[1:]:
            p.lineTo(*q)
        c.drawPath(p, stroke=1, fill=0)
    c.setDash()
    c.restoreState()
    # markörer: numrerad cirkel per plats
    marks = []
    for i, pl in enumerate(resolved, 1):
        lat, lon = pl["lat"], pl["lon"]
        if FEL == "fel_koordinat" and i == 2:
            lat, lon = lat + 0.004, lon + 0.004
        x, y = tp(lon, lat)
        if FEL == "saknad_markor" and i == len(resolved):
            marks.append({"i": i, "x": None, "y": None, "lat": pl["lat"], "lon": pl["lon"]})
            continue
        r = G["marker_r"]
        c.setFillColorRGB(*MARK); c.setStrokeColorRGB(*S["paper"]); c.setLineWidth(0.9)
        c.circle(x, y, r, stroke=1, fill=1)
        c.setFillColorRGB(1, 1, 1); c.setFont("KSans", G["marker_font"]); c.drawCentredString(x, y - G["marker_font"] * 0.34, str(i))
        marks.append({"i": i, "x": x, "y": y, "lat": pl["lat"], "lon": pl["lon"]})
    c.setStrokeColorRGB(*S["ink"]); c.setLineWidth(0.8); c.rect(fx, fy, fw, fh, stroke=1, fill=0)
    # rubrik
    title = (order.get("text") or "").strip() or ROLE_LABEL.get(lang, ROLE_LABEL["en"])["annat"]
    font = "KScript" if KL.covers_glyphs(title, "KScript") else "KSerif"
    size = G["title_max"] * 2.3 if FEL == "text_kapad" else KL.fit_size(title, font, G["title_max"], W - 16 * mm)
    c.setFillColorRGB(*S["ink"]); c.setFont(font, size); c.drawCentredString(W / 2, G["title_y"], title)
    sub = fmt_date(order.get("date"), lang)
    if sub:
        c.setFont("KSans", G["sub_size"]); c.drawCentredString(W / 2, G["sub_y"], sub)
    # legend (nummer -> plats)
    T = TXT.get(lang, TXT["en"])
    legend_lines = [(i, role_text(pl, lang), f"{pl['place']}" + (f", {pl.get('country', '')}" if pl.get("country") else "")) for i, pl in enumerate(resolved, 1)]
    if FEL == "fel_ordning" and len(legend_lines) >= 2:
        legend_lines[0], legend_lines[1] = legend_lines[1], legend_lines[0]
    y = G["legend_y"]
    c.setFont("KSans", G["legend_size"])
    for i, role, town in legend_lines:
        c.setFillColorRGB(*MARK); c.drawString(fx, y, str(i))
        c.setFillColorRGB(*S["ink"]); c.drawString(fx + 5 * mm, y, f"{role} · {town}")
        y -= G["line_h"]
    y -= G["line_h"] * 0.25
    c.setFont("KSans", G["dir_size"])
    for li, leg in enumerate(R["legs"]):
        km, drive, walk = leg["dist_m"], leg["drive_s"], leg["walk_s"]
        if FEL == "fel_avstand":
            km = km * 1.5
        if FEL == "fel_tid":
            drive, walk = drive * 0.5, walk * 0.5
        line = T["leg"].format(a=li + 1, b=li + 2, km=fmt_km(km, lang), drive=fmt_min(drive), walk=fmt_min(walk))
        c.setFillColorRGB(*S["ink"]); c.drawString(fx, y, line)
        y -= G["line_h"]
    if FEL != "saknad_attribution":
        ay1, ay2 = G["attrib_y"]
        s1, s2 = G["attrib_size"]
        c.setFillColorRGB(*S["ink"]); c.setFont("KSans", s1); c.drawCentredString(W / 2, ay1, ATTRIB + " · " + ODBL)
        c.setFont("KSans", s2); c.drawCentredString(W / 2, ay2, T["credit"] + " · Moodly Sverige")
    return {"frame_pt": list(frame), "page_pt": [W, H], "bbox_m": list(bbox), "scale_pt_per_m": scale,
            "markers": marks, "style": style, "ritade": info}


def generate(order_path):
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    R = compute(order)
    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, geos = {}, {}
    for lang in order["languages"]:
        p = out / f"{order['id']}_{lang}.pdf"
        c = canvas.Canvas(str(p), pageCompression=1)
        c.setTitle((order.get("text") or "Find your way map")); c.setAuthor("Moodly Sverige"); c.setCreator(GENERATOR_VERSION)
        g_kort = draw_page(c, order, R, lang, "kort", R["resolved"]); c.showPage()
        g_aff = draw_page(c, order, R, lang, "affisch", R["resolved"]); c.showPage()
        c.save()
        files[lang] = str(p)
        geos[lang] = {"kort": g_kort, "affisch": g_aff}
    meta = {"generator": GENERATOR_VERSION, "product": "brollopskarta", "order": order, "places_resolved": R["resolved"],
            "files": files, "center": R["center"], "half_content_m": R["half_content_m"], "bbox_ll": R["bbox_ll"],
            "legs": R["legs"], "osm": {"tidsstampel": R["osm_ts"], "instans": R["endpoint"], "fraga": R["query"]},
            "geometry": geos, "timings": dict(R["timings"], totalt_s=round(time.perf_counter() - t0, 2)),
            "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"], "legs": m["legs"]}, indent=1))
