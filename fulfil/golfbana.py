"""Generator: golfbanekarta – köparens golfbana ur OpenStreetMap som affisch (A3 stående, vektor – skalbar till A2/A1).

Köparen anger banans namn + ort + land, valfritt ett hål att markera (hole-in-one, favorithål), spelarens namn,
ett datum och en rad text. Banan slås upp i golfregistret (golfdata.py: Geofabrik-extrakt + pyosmium, ingen extern
OSM-API i drift). Allt ritas av oss ur OSM-data (golf=hole/fairway/green/tee/bunker/rough/water_hazard/cartpath,
leisure=golf_course, skog och vatten kring banan) – inga kartplattor. ODbL: en tryckt karta är ett "Produced Work";
"© OpenStreetMap contributors" och licensnamnet trycks på affischen.

Fyra stilar: klassisk (grön), vintage (ritning på gulnat papper), minimal (linje), mork (mörk/guld).
Banan vrids (hel grad, deterministiskt) om den då fyller ramen klart bättre; norrpilen visar vridningen.

Vägrar (SystemExit med felkod, fulfil.py visar köparen ett begripligt meddelande; detaljer i <id>_fel.json):
  course_not_found        ingen bana med det namnet inom 40 km från orten
  course_ambiguous        flera banor passar (alternativen skrivs ut så att köparen kan välja)
  course_holes_incomplete banan finns men hålen i OSM är färre än 9, saknar nummer, har luckor/dubbletter eller saknar green
  hole_not_found          det valda hålet finns inte på banan

Körning: python golfbana.py order.json -> <ut>/<id>_<språk>.pdf + <ut>/<id>_meta.json
Felinjektion (bara tester): FELINJEKTION=saknad_attribution|tomt_hal|saknat_nummer|fel_rotation|utanfor_marginal|
                            fel_markering|fel_par|lag_upplosning
"""
import hashlib
import io
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

import golfdata as G
import kartlayout as KL

GENERATOR_VERSION = "golfbana/0.1.0"
FEL = os.environ.get("FELINJEKTION", "")
ROOT = Path(__file__).parent
A3 = (297 * mm, 420 * mm)
FRAME = (20 * mm, 96 * mm, 257 * mm, 306 * mm)  # x, y, b, h
MARGIN = 8 * mm  # allt tryck ska ligga innanför (grinden kontrollerar)
PAD = 0.035  # luft runt banan i ramen
GREEN_MAX_M = 60.0  # hålets slutpunkt ska ligga högst så här nära en green
ATTRIB = "© OpenStreetMap contributors · Map data: Open Database License (ODbL) 1.0 · openstreetmap.org/copyright"
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "sv": ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
}
TXT = {
    "en": {"hole": "Hole", "par": "Par", "total": "Total", "holes": "{n} holes", "data": "Map data as of {d}", "north": "N"},
    "sv": {"hole": "Hål", "par": "Par", "total": "Totalt", "holes": "{n} hål", "data": "Kartdata per {d}", "north": "N"},
    "de": {"hole": "Loch", "par": "Par", "total": "Gesamt", "holes": "{n} Löcher", "data": "Kartendaten Stand {d}", "north": "N"},
}
# färger 0..1. hole = hållinjens färg, accent = det markerade hålet (grinden letar efter båda)
STYLES = {
    "klassisk": dict(label="Classic green", paper=(0.965, 0.955, 0.925), ink=(0.10, 0.23, 0.16), mute=(0.33, 0.42, 0.35),
                     course=(0.80, 0.87, 0.69), rough=(0.72, 0.81, 0.60), wood=(0.55, 0.69, 0.48), grass=(0.84, 0.89, 0.75),
                     water=(0.60, 0.77, 0.87), fairway=(0.55, 0.75, 0.41), green=(0.36, 0.63, 0.30), tee=(0.45, 0.69, 0.36),
                     bunker=(0.96, 0.91, 0.76), bunker_edge=(0.78, 0.70, 0.50), outline=None, hole=(1.0, 1.0, 1.0),
                     num_fill=(0.10, 0.23, 0.16), num_text=(0.99, 0.99, 0.96), accent=(0.80, 0.14, 0.12),
                     cart=(0.93, 0.91, 0.84), title_font="KSerif", sans="KSans", frame_line=0.0),
    "vintage": dict(label="Vintage drawing", paper=(0.935, 0.885, 0.785), ink=(0.32, 0.21, 0.12), mute=(0.45, 0.34, 0.23),
                    course=(0.905, 0.85, 0.73), rough=(0.87, 0.81, 0.68), wood=(0.80, 0.74, 0.60), grass=(0.90, 0.855, 0.745),
                    water=(0.72, 0.77, 0.75), fairway=(0.85, 0.79, 0.63), green=(0.69, 0.63, 0.45), tee=(0.76, 0.70, 0.53),
                    bunker=(0.97, 0.94, 0.86), bunker_edge=(0.32, 0.21, 0.12), outline=(0.32, 0.21, 0.12), hole=(0.32, 0.21, 0.12),
                    num_fill=(0.935, 0.885, 0.785), num_text=(0.32, 0.21, 0.12), accent=(0.63, 0.12, 0.08),
                    cart=(0.80, 0.73, 0.60), title_font="KSerifIt", sans="KSerif", frame_line=0.9),
    "minimal": dict(label="Minimal line", paper=(1.0, 1.0, 1.0), ink=(0.08, 0.08, 0.08), mute=(0.40, 0.40, 0.40),
                    course=None, rough=None, wood=None, grass=None, water=(0.90, 0.90, 0.90), fairway=None, green=(0.16, 0.16, 0.16),
                    tee=None, bunker=(0.94, 0.94, 0.94), bunker_edge=(0.08, 0.08, 0.08), outline=(0.08, 0.08, 0.08),
                    course_edge=(0.62, 0.62, 0.62), hole=(0.08, 0.08, 0.08), num_fill=(1.0, 1.0, 1.0), num_text=(0.08, 0.08, 0.08),
                    accent=(0.86, 0.16, 0.13), cart=(0.70, 0.70, 0.70), title_font="KSans", sans="KSans", frame_line=0.0),
    "mork": dict(label="Dark & gold", paper=(0.055, 0.075, 0.10), ink=(0.87, 0.75, 0.48), mute=(0.66, 0.58, 0.42),
                 course=(0.085, 0.115, 0.135), rough=(0.10, 0.135, 0.15), wood=(0.07, 0.095, 0.11), grass=(0.075, 0.10, 0.12),
                 water=(0.06, 0.13, 0.21), fairway=(0.14, 0.20, 0.19), green=(0.82, 0.70, 0.44), tee=(0.47, 0.42, 0.29),
                 bunker=(0.63, 0.56, 0.39), bunker_edge=None, outline=(0.55, 0.48, 0.33), hole=(0.97, 0.91, 0.72),
                 num_fill=(0.87, 0.75, 0.48), num_text=(0.055, 0.075, 0.10), accent=(0.96, 0.42, 0.32),
                 cart=(0.16, 0.19, 0.20), title_font="KSerif", sans="KSans", frame_line=0.5),
}
LABEL_R = 2.9 * mm


def fmt_date(d, lang):
    y, m, dd = map(int, d.split("-"))
    if lang == "en":
        return f"{dd} {MONTHS['en'][m - 1]} {y}"
    if lang == "de":
        return f"{dd}. {MONTHS['de'][m - 1]} {y}"
    return f"{dd} {MONTHS['sv'][m - 1]} {y}"


def fmt_coord(lat, lon):
    return f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}   {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}"


def spaced(s):
    return " ".join(s.upper())


class Proj:
    """Lokal ekvirektangulär projektion i meter kring (lat0, lon0), vriden rot grader moturs, skalad till punkter."""

    def __init__(self, lat0, lon0, rot_deg=0.0):
        self.lat0, self.lon0, self.rot = lat0, lon0, rot_deg
        self.kx = 6371008.8 * math.pi / 180 * math.cos(math.radians(lat0))
        self.ky = 6371008.8 * math.pi / 180
        self.ca, self.sa = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
        self.s = 1.0; self.ox = 0.0; self.oy = 0.0; self.cx = 0.0; self.cy = 0.0

    def m(self, lon, lat):
        x = (lon - self.lon0) * self.kx; y = (lat - self.lat0) * self.ky
        return x * self.ca - y * self.sa, x * self.sa + y * self.ca

    def p(self, lon, lat):
        x, y = self.m(lon, lat)
        return self.ox + (x - self.cx) * self.s, self.oy + (y - self.cy) * self.s


def fit(pts, lat0, lon0, rot, frame):
    P = Proj(lat0, lon0, rot)
    xy = [P.m(*q) for q in pts]
    xs = [a for a, _ in xy]; ys = [b for _, b in xy]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    fx, fy, fw, fh = frame
    s = min(fw * (1 - 2 * PAD) / max(w, 1e-6), fh * (1 - 2 * PAD) / max(h, 1e-6))
    P.s = s; P.cx = (max(xs) + min(xs)) / 2; P.cy = (max(ys) + min(ys)) / 2
    P.ox = fx + fw / 2; P.oy = fy + fh / 2
    return P, s, (w * s / fw, h * s / fh)


def choose_rotation(pts, lat0, lon0, frame):
    """Norr uppåt, om inte en vridning ger minst 18 % större skala (hela grader −90…90, lägsta |vinkel| vid lika)."""
    s0 = fit(pts, lat0, lon0, 0, frame)[1]
    best = (s0, 0)
    for r in range(-90, 91):
        s = fit(pts, lat0, lon0, r, frame)[1]
        if s > best[0] * 1.0001 or (abs(s - best[0]) <= best[0] * 1e-4 and abs(r) < abs(best[1])):
            best = (s, r)
    return best[1] if best[0] >= s0 * 1.18 else 0


def feature_point(f):
    r = f["rings"][0]
    return r[len(r) // 2]


def closed(r):
    return len(r) >= 4 and abs(r[0][0] - r[-1][0]) < 1e-9 and abs(r[0][1] - r[-1][1]) < 1e-9


def compute(order):
    t = {}
    t0 = time.perf_counter()
    reg, regdir = G.register_for_point(order["lat"], order["lon"])
    t["register_s"] = time.perf_counter() - t0
    c, err, det = G.resolve_with_facility(order["course"], order["lat"], order["lon"], reg)
    if err:
        return None, err, det
    base, err, det2 = course_check(reg, regdir, c, det)
    if err:
        return base, err, det2
    if order.get("hole") and order["hole"] not in [h["ref"] for h in base["holes"]]:
        return base, "hole_not_found", {"valt": order["hole"], "antal_hal": len(base["holes"])}
    lat0, lon0 = c["center"]
    outline = [p for r in c["outer"] for p in r] + [p for h in base["holes"] for p in h["pts"]]
    rot = choose_rotation(outline, lat0, lon0, FRAME)
    P, s, fill = fit(outline, lat0, lon0, rot, FRAME)
    t["berakning_s"] = time.perf_counter() - t0 - t["register_s"]
    base.update(proj=P, rot=rot, fill=fill, timings=t)
    return base, None, None


def course_check(reg, regdir, c, det=None):
    """Grinden för en bana ur registret (utan köparens val): kompletta numrerade hål och en green vid varje håls slut.
    Används av compute (order) och av verktyg/golfindex.py (förhandskollen i portalen) – samma kod, samma svar."""
    hs = [reg["hal"][i] for i in c["holes"]]
    summ = G.hole_summary(reg, c)
    feats = G.course_features(regdir, c["osm"])
    rings = c["outer"] + c["inner"]
    golf, ctx = [], []
    for f in feats:
        tg = f["tags"]
        if "golf" in tg:
            if any(G.point_in_rings(rings, x, y) for x, y in f["rings"][0][:: max(1, len(f["rings"][0]) // 6)]):
                golf.append(f)
        else:
            ctx.append(f)
    greens = [f for f in golf if f["tags"].get("golf") == "green" and closed(f["rings"][0])]
    lat0, lon0 = c["center"]
    P0 = Proj(lat0, lon0)

    def dist_to_green(pt):
        x, y = P0.m(*pt)
        best = 1e9
        for g in greens:
            if G.point_in_rings(g["rings"], *pt):
                return 0.0
            for a, b in zip(g["rings"][0], g["rings"][0][1:]):
                ax, ay = P0.m(*a); bx, by = P0.m(*b)
                dx, dy = bx - ax, by - ay
                L = dx * dx + dy * dy
                u = 0 if L == 0 else max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / L))
                best = min(best, math.hypot(ax + u * dx - x, ay + u * dy - y))
        return best
    holes = []
    for h in hs:
        ref = int(h["ref"]) if re.fullmatch(r"\d{1,2}", h["ref"] or "") else None
        par = int(h["par"]) if re.fullmatch(r"[3-6]", (h["par"] or "").strip()) else None
        holes.append({"ref": ref, "par": par, "osm": h["osm"], "pts": h["pts"], "green_m": round(dist_to_green(h["pts"][-1]), 1)})
    holes.sort(key=lambda h: (h["ref"] is None, h["ref"] or 0, h["osm"]))
    no_green = [h["ref"] for h in holes if h["green_m"] > GREEN_MAX_M]
    base = {"course": c, "summary": summ, "resolve": det, "holes": holes, "register": str(regdir), "osm_datum": reg["osm_datum"],
            "region": reg["region"], "region_url": reg.get("url", ""), "golf": golf, "ctx": ctx}
    if not summ["komplett"] or no_green:
        return base, "course_holes_incomplete", {"antal_hal": summ["antal"], "numrerade": summ["numrerade"], "utan_green": no_green}
    return base, None, None


# ------------------------------------------------------------------ ritning
def _path(c, P, rings, rot_extra=0.0):
    p = c.beginPath()
    for r in rings:
        pts = [P.p(*q) for q in r]
        p.moveTo(*pts[0])
        for q in pts[1:]:
            p.lineTo(*q)
        if closed(r):
            p.close()
    return p


def _fill(c, P, rings, fill, stroke=None, lw=0.3, dash=None):
    if fill is None and stroke is None:
        return
    if fill is not None:
        c.setFillColorRGB(*fill)
    if stroke is not None:
        c.setStrokeColorRGB(*stroke); c.setLineWidth(lw)
        c.setDash(*dash) if dash else c.setDash()
    c.drawPath(_path(c, P, rings), stroke=1 if stroke is not None else 0, fill=1 if fill is not None else 0, fillMode=0)
    c.setDash()


def _line(c, P, pts, color, lw, dash=None):
    c.setStrokeColorRGB(*color); c.setLineWidth(lw); c.setLineCap(1); c.setLineJoin(1)
    c.setDash(*dash) if dash else c.setDash()
    xy = [P.p(*q) for q in pts]
    p = c.beginPath(); p.moveTo(*xy[0])
    for q in xy[1:]:
        p.lineTo(*q)
    c.drawPath(p, stroke=1, fill=0)
    c.setDash()


def golf_kind(tg):
    g = tg.get("golf")
    if g in ("lateral_water_hazard", "water_hazard") or tg.get("natural") == "water":
        return "water"
    if g in ("fairway", "green", "tee", "bunker", "rough", "hole", "cartpath", "path", "driving_range", "clubhouse"):
        return g
    if tg.get("natural") == "sand":
        return "bunker"
    return None


def ctx_kind(tg):
    if tg.get("natural") == "water" or tg.get("waterway") in ("riverbank",) or tg.get("landuse") in ("reservoir", "basin") or tg.get("water"):
        return "water"
    if tg.get("waterway") in ("river", "stream", "canal"):
        return "stream"
    if tg.get("natural") in ("wood", "scrub", "heath") or tg.get("landuse") == "forest":
        return "wood"
    if tg.get("natural") in ("grassland", "wetland") or tg.get("landuse") in ("grass", "meadow") or tg.get("leisure") == "park":
        return "grass"
    if tg.get("natural") in ("sand", "beach"):
        return "sand"
    return None


def inside_frame(P, rings, margin=1.5):
    """Hela ytan ryms i kartramen. Kontextytor som skulle klippas av ramen ritas inte – raka klippkanter i mark och
    vatten ser ut som ett fel på affischen (bildgranskningen 2026-09-27)."""
    fx, fy, fw, fh = FRAME
    for q in rings[0]:
        x, y = P.p(*q)
        if not (fx + margin <= x <= fx + fw - margin and fy + margin <= y <= fy + fh - margin):
            return False
    return True


def draw_map(c, R, S, order):
    P = R["proj"]
    fx, fy, fw, fh = FRAME
    c.saveState()
    cl = c.beginPath(); cl.rect(fx, fy, fw, fh); c.clipPath(cl, stroke=0, fill=0)
    c.setFillColorRGB(*S["paper"]); c.rect(fx, fy, fw, fh, stroke=0, fill=1)
    k = P.s  # punkter per meter
    lw = max(0.25, min(0.6, 0.35 * (k / 0.25) ** 0.3))
    # kontext kring banan
    if S["style"] != "minimal":
        for kind in ("grass", "wood", "sand", "water"):
            col = {"grass": S["grass"], "wood": S["wood"], "sand": S["bunker"], "water": S["water"]}[kind]
            w_ = 0.22 if kind == "sand" else 0.45
            col = tuple(a * w_ + b * (1 - w_) for a, b in zip(col, S["paper"]))  # kontexten dämpad mot papperet – banan i fokus
            for f in R["ctx"]:
                if ctx_kind(f["tags"]) == kind and closed(f["rings"][0]) and inside_frame(P, f["rings"]):
                    _fill(c, P, f["rings"], col)
    for f in R["ctx"]:
        if ctx_kind(f["tags"]) == "water" and closed(f["rings"][0]) and S["style"] == "minimal" and inside_frame(P, f["rings"]):
            _fill(c, P, f["rings"], S["water"])
        if ctx_kind(f["tags"]) == "stream":
            _line(c, P, f["rings"][0], S["water"], max(0.6, 4 * k))
    # banan
    crs = R["course"]
    _fill(c, P, crs["outer"] + crs["inner"], S["course"], S.get("course_edge") or (S["outline"] if S["style"] == "vintage" else None), lw * 0.8,
          (2, 1.5) if S["style"] == "minimal" else None)
    order_k = ["rough", "driving_range", "wood_in", "water", "fairway", "tee", "green", "bunker"]
    col = {"rough": S["rough"], "driving_range": S["rough"], "water": S["water"], "fairway": S["fairway"], "tee": S["tee"],
           "green": S["green"], "bunker": S["bunker"]}
    for kind in order_k:
        if kind == "wood_in":
            continue
        for f in R["golf"]:
            if golf_kind(f["tags"]) != kind or not closed(f["rings"][0]):
                continue
            edge = S["outline"]
            if kind == "bunker":
                edge = S["bunker_edge"]
            if kind in ("rough", "driving_range") and S["style"] == "minimal":
                _fill(c, P, f["rings"], None, S["course_edge"], lw * 0.6)
                continue
            if kind == "water" and S["style"] == "minimal":
                edge = (0.55, 0.55, 0.55)
            _fill(c, P, f["rings"], col[kind], edge, lw * (0.7 if kind == "bunker" else 0.8))
    if S["cart"] is not None:
        for f in R["golf"]:
            if golf_kind(f["tags"]) in ("cartpath", "path") and not closed(f["rings"][0]):
                _line(c, P, f["rings"][0], S["cart"], max(0.5, 2.5 * k))
    # hållinjer
    mark = order.get("hole")
    if FEL == "fel_markering" and mark:
        mark = mark % len(R["holes"]) + 1
    holes_drawn = []
    for h in R["holes"]:
        if FEL == "tomt_hal" and h["ref"] == 5:
            continue
        if h["ref"] == mark:
            continue
        _line(c, P, h["pts"], S["hole"], max(0.55, lw * 1.5), (2.4, 1.4))
        holes_drawn.append(h["ref"])
    if mark:
        h = next(x for x in R["holes"] if x["ref"] == mark)
        _line(c, P, h["pts"], S["accent"], lw * 3.2)
        gx, gy = P.p(*h["pts"][-1])
        # flagga på greenen
        c.setStrokeColorRGB(*S["ink"]); c.setLineWidth(0.7); c.line(gx, gy, gx, gy + 7 * mm)
        c.setFillColorRGB(*S["accent"])
        p = c.beginPath(); p.moveTo(gx, gy + 7 * mm); p.lineTo(gx + 4.2 * mm, gy + 5.8 * mm); p.lineTo(gx, gy + 4.6 * mm); p.close()
        c.drawPath(p, stroke=0, fill=1)
        c.setFillColorRGB(*S["ink"]); c.circle(gx, gy, 0.8, stroke=0, fill=1)
    # hålnummer vid utslaget, förskjutet bakåt längs hålet; undviker krockar med andra nummer och ramkanten
    labels = []
    placed = []
    for h in R["holes"]:
        if FEL == "saknat_nummer" and h["ref"] == 7:
            continue
        x0, y0 = P.p(*h["pts"][0]); x1, y1 = P.p(*h["pts"][1])
        L = math.hypot(x1 - x0, y1 - y0) or 1
        ux, uy = (x0 - x1) / L, (y0 - y1) / L
        best = None
        for d_ in (4.2 * mm, 6.5 * mm, 2.5 * mm, 9 * mm):
            for ang in (0, 35, -35, 70, -70, 110, -110, 150, -150, 180):
                a = math.radians(ang)
                vx, vy = ux * math.cos(a) - uy * math.sin(a), ux * math.sin(a) + uy * math.cos(a)
                px, py = x0 + vx * d_, y0 + vy * d_
                inside = fx + LABEL_R + 1 < px < fx + fw - LABEL_R - 1 and fy + LABEL_R + 1 < py < fy + fh - LABEL_R - 1
                clash = min([math.hypot(px - qx, py - qy) for qx, qy in placed] or [99])
                score = (inside, clash >= 2 * LABEL_R + 0.8, -abs(ang), -d_)
                if best is None or score > best[0]:
                    best = (score, px, py)
                if inside and clash >= 2 * LABEL_R + 0.8:
                    break
            if best[0][0] and best[0][1]:
                break
        _, px, py = best
        placed.append((px, py))
        is_mark = h["ref"] == mark
        c.setFillColorRGB(*(S["accent"] if is_mark else S["num_fill"]))
        c.setStrokeColorRGB(*S["num_text"] if S["style"] != "minimal" else S["ink"]); c.setLineWidth(0.5)
        c.circle(px, py, LABEL_R, stroke=1 if S["style"] in ("vintage", "minimal") else 0, fill=1)
        c.setFillColorRGB(*((1, 1, 1) if is_mark and S["style"] != "mork" else S["paper"] if is_mark else S["num_text"]))
        c.setFont(S["sans"], 7.6 if h["ref"] < 10 else 6.8)
        c.drawCentredString(px, py - 2.6, str(h["ref"]))
        labels.append({"ref": h["ref"], "x": round(px, 2), "y": round(py, 2)})
    c.restoreState()
    if S["frame_line"]:
        c.setStrokeColorRGB(*S["ink"]); c.setLineWidth(S["frame_line"]); c.rect(fx, fy, fw, fh, stroke=1, fill=0)
        if S["style"] == "vintage":
            c.setLineWidth(0.4); c.rect(fx - 2.2 * mm, fy - 2.2 * mm, fw + 4.4 * mm, fh + 4.4 * mm, stroke=1, fill=0)
    return {"labels": labels, "holes_drawn": holes_drawn, "marked": mark}


def north_arrow(c, x, y, rot, S, lang):
    """Norrpil: pekar mot kartans norr (banan vriden rot grader moturs => norr vriden lika mycket)."""
    a = math.radians(rot)
    ux, uy = -math.sin(a), math.cos(a)
    L = 7 * mm
    c.setFillColorRGB(*S["ink"]); c.setStrokeColorRGB(*S["ink"]); c.setLineWidth(0.6)
    tip = (x + ux * L / 2, y + uy * L / 2); tail = (x - ux * L / 2, y - uy * L / 2)
    px, py = -uy, ux
    p = c.beginPath(); p.moveTo(*tip); p.lineTo(tail[0] + px * 2.2 * mm, tail[1] + py * 2.2 * mm); p.lineTo(x - ux * L * 0.2, y - uy * L * 0.2)
    p.lineTo(tail[0] - px * 2.2 * mm, tail[1] - py * 2.2 * mm); p.close()
    c.drawPath(p, stroke=1, fill=1)
    c.setFont(S["sans"], 8); c.drawCentredString(tip[0] + ux * 3.2 * mm, tip[1] + uy * 3.2 * mm - 2.8, TXT[lang]["north"])


def scale_bar(c, x, y, s_pt_per_m, S):
    for m_ in (50, 100, 200, 250, 500):
        if m_ * s_pt_per_m >= 22 * mm:
            break
    L = m_ * s_pt_per_m
    c.setStrokeColorRGB(*S["ink"]); c.setLineWidth(0.7)
    c.line(x - L, y, x, y); c.line(x - L, y - 1.2 * mm, x - L, y + 1.2 * mm); c.line(x, y - 1.2 * mm, x, y + 1.2 * mm)
    c.line(x - L / 2, y - 0.8 * mm, x - L / 2, y + 0.8 * mm)
    c.setFillColorRGB(*S["ink"]); c.setFont(S["sans"], 7.5); c.drawCentredString(x - L / 2, y + 2.2 * mm, f"{m_} m")
    return m_


def scorecard(c, R, S, lang, y_top):
    """Scorekort: hålnummer och par (om taggat i OSM). Upp till 18 hål per rad."""
    holes = R["holes"]
    have = sum(1 for h in holes if h["par"])
    if have < len(holes) / 2:
        return None
    pars = [h["par"] for h in holes]
    if FEL == "fel_par":
        pars = pars[1:] + pars[:1]
    chunks = [holes[i:i + 18] for i in range(0, len(holes), 18)]
    total = sum(p for p in pars if p) if all(pars) else None
    cw = 11.2 * mm; rh = 5.6 * mm if len(holes) <= 18 else 4.6 * mm
    y = y_top
    tf = S["sans"]
    rows = []
    for ci, ch in enumerate(chunks):
        n = len(ch) + (1 if ci == len(chunks) - 1 else 0)
        lab_w = 13 * mm
        W = lab_w + n * cw
        x0 = (A3[0] - W) / 2
        c.setStrokeColorRGB(*S["mute"]); c.setLineWidth(0.4)
        c.line(x0, y, x0 + W, y); c.line(x0, y - rh, x0 + W, y - rh); c.line(x0, y - 2 * rh, x0 + W, y - 2 * rh)
        c.setFillColorRGB(*S["ink"]); c.setFont(tf, 7.2)
        c.drawString(x0 + 1 * mm, y - rh + 1.8 * mm, TXT[lang]["hole"]); c.drawString(x0 + 1 * mm, y - 2 * rh + 1.8 * mm, TXT[lang]["par"])
        for j, h in enumerate(ch):
            cx = x0 + lab_w + (j + 0.5) * cw
            c.setFont(tf, 7.6)
            c.drawCentredString(cx, y - rh + 1.8 * mm, str(h["ref"]))
            p = pars[holes.index(h)]
            c.drawCentredString(cx, y - 2 * rh + 1.8 * mm, str(p) if p else "–")
            rows.append({"ref": h["ref"], "par": p, "x": round(cx, 2)})
        if ci == len(chunks) - 1:
            cx = x0 + lab_w + (n - 0.5) * cw
            c.setFont(tf, 7.2); c.drawCentredString(cx, y - rh + 1.8 * mm, TXT[lang]["total"])
            c.setFont(tf, 7.6); c.drawCentredString(cx, y - 2 * rh + 1.8 * mm, str(total) if total else "–")
        y -= 2 * rh + 2.5 * mm
    return {"rader": rows, "total": total, "y_top": y_top, "y_bottom": y}


def render(order, R, lang, path):
    style = order.get("style") if order.get("style") in STYLES else "klassisk"
    S = dict(STYLES[style], style=style)
    W, H = A3
    c = canvas.Canvas(str(path), pagesize=A3, invariant=1, pageCompression=1, initialFontName="KSans", initialFontSize=10)
    crs = R["course"]
    cname = (order.get("title") or "").strip() or crs["tags"].get("name", order["course"])
    c.setTitle(f"{cname} – golf course map"); c.setAuthor("Moodly Sverige"); c.setCreator(GENERATOR_VERSION)
    c.setFillColorRGB(*S["paper"]); c.rect(0, 0, W, H, stroke=0, fill=1)
    t0 = time.perf_counter()
    P = R["proj"]
    if FEL == "fel_rotation":
        lat0, lon0 = crs["center"]
        P2 = Proj(lat0, lon0, R["rot"] + 25)
        P2.s, P2.cx, P2.cy, P2.ox, P2.oy = P.s, P.cx, P.cy, P.ox, P.oy
        R = dict(R, proj=P2)
    if FEL == "lag_upplosning":
        import fitz
        tmp = io.BytesIO()
        c2 = canvas.Canvas(tmp, pagesize=A3)
        c2.setFillColorRGB(*S["paper"]); c2.rect(0, 0, W, H, stroke=0, fill=1)
        info = draw_map(c2, R, S, order)
        c2.showPage(); c2.save()
        fx, fy, fw, fh = FRAME
        pg = fitz.open("pdf", tmp.getvalue())[0]
        pix = pg.get_pixmap(dpi=60, clip=fitz.Rect(fx, H - fy - fh, fx + fw, H - fy))
        c.drawImage(ImageReader(io.BytesIO(pix.tobytes("png"))), fx, fy, fw, fh)
    else:
        info = draw_map(c, R, S, order)
    render_s = time.perf_counter() - t0
    # text
    title = cname
    t_s = spaced(title) if len(title) <= 20 else title.upper()
    font = S["title_font"] if KL.covers_glyphs(t_s, S["title_font"]) else "KSans"
    size = KL.fit_size(t_s, font, 40, W - 2 * 30 * mm)
    tx = W / 2 if FEL != "utanfor_marginal" else W / 2 + 150 * mm
    c.setFillColorRGB(*S["ink"]); c.setFont(font, size); c.drawCentredString(tx, 72 * mm, t_s)
    sub = ", ".join(x for x in (order.get("place", ""), order.get("country", "")) if x)
    c.setFont(S["sans"], 11.5)
    c.drawCentredString(W / 2, 63.5 * mm, (spaced(sub) if len(sub) <= 30 else sub.upper()))
    lat0, lon0 = crs["center"]
    c.setFont(S["sans"], 9); c.setFillColorRGB(*S["mute"])
    n = len(R["holes"])
    c.drawCentredString(W / 2, 57 * mm, f"{fmt_coord(lat0, lon0)}   ·   {TXT[lang]['holes'].format(n=n)}")
    parts = [x for x in ((order.get("text") or "").strip(), (f"{TXT[lang]['hole']} {order['hole']}" if order.get("hole") else ""),
                         (order.get("player") or "").strip(), fmt_date(order["date"], lang) if order.get("date") else "") if x]
    personal = "  ·  ".join(parts)
    if personal:
        c.setFillColorRGB(*S["accent"] if style != "mork" else S["ink"])
        pf = S["title_font"] if KL.covers_glyphs(personal, S["title_font"]) else "KSans"
        c.setFont(pf, KL.fit_size(personal, pf, 15, W - 2 * 30 * mm)); c.drawCentredString(W / 2, 48 * mm, personal)
    # norrpil + skala på raden under ramen
    north_arrow(c, FRAME[0] + 6 * mm, 86 * mm, R["rot"] if FEL != "fel_rotation" else R["rot"], S, lang)
    sb = scale_bar(c, FRAME[0] + FRAME[2] - 2 * mm, 84.5 * mm, R["proj"].s, S)
    sc = scorecard(c, R, S, lang, 42 * mm if len(R["holes"]) > 18 else 40 * mm)
    if FEL != "saknad_attribution":
        c.setFillColorRGB(*S["mute"]); c.setFont("KSans", 6.8); c.drawCentredString(W / 2, 13.5 * mm, ATTRIB)
        c.setFont("KSans", 6.4)
        c.drawCentredString(W / 2, 10.5 * mm, TXT[lang]["data"].format(d=R["osm_datum"]) + " · Moodly Sverige")
    c.showPage(); c.save()
    P = R["proj"]
    return {"frame_pt": list(FRAME), "page_pt": [W, H], "style": style, "labels": info["labels"], "markerat": info["marked"],
            "scorecard": sc, "skala_m": sb, "rendera_karta_s": round(render_s, 2), "titel": title, "personlig_rad": personal}


def generate(order_path):
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    R, err, det = compute(order)
    if err:
        info = {"order": order["id"], "fel": err, "detalj": det}
        if R:
            info["bana"] = {"osm": R["course"]["osm"], "namn": R["course"]["tags"].get("name", "")}
        json.dump(info, open(out / f"{order['id']}_fel.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        raise SystemExit(err)
    files, geos = {}, {}
    t1 = time.perf_counter()
    for lang in order["languages"]:
        p = out / f"{order['id']}_{lang}.pdf"
        geos[lang] = render(order, R, lang, p)
        files[lang] = str(p)
    P = R["proj"]
    crs = R["course"]
    timings = dict(R["timings"], rendera_s=time.perf_counter() - t1, totalt_s=time.perf_counter() - t0)
    meta = {"generator": GENERATOR_VERSION, "product": "golfbana", "order": order, "files": files,
            "bana": {"osm": crs["osm"], "namn": crs["tags"].get("name", ""), "center": crs["center"], "bbox": crs["bbox"],
                     "parent": crs.get("parent"), "uppslag": R["resolve"]},
            "hal": [{k: h[k] for k in ("ref", "par", "osm", "green_m")} for h in R["holes"]],
            "register": R["register"], "region": R["region"], "osm": {"datum": R["osm_datum"], "kalla": R["region_url"]},
            "projektion": {"typ": "ekvirektangulär i meter kring banans mitt (R = 6 371 008,8 m), vriden moturs, skalad",
                           "lat0": P.lat0, "lon0": P.lon0, "rot_grader": R["rot"], "skala_pt_per_m": P.s,
                           "cx_m": P.cx, "cy_m": P.cy, "ox_pt": P.ox, "oy_pt": P.oy, "fyllnad": [round(x, 3) for x in R["fill"]]},
            "geometry": geos, "timings": {k: round(v, 2) for k, v in timings.items()},
            "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"], "rot": m["projektion"]["rot_grader"]}, indent=1))
