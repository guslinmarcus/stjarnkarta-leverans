"""Kvalitetsgrind för "hitta hit"-kartan (brollopskarta.py). Oberoende av generatorns rit- och ruttkod:
  * eget vägnät byggs ur SAMMA OSM-svar (osmdata.fetch, cachad – ingen ritkod delas) och en egen Dijkstra
    körs mellan varje par av platser (egen kod, se _graph/_dijkstra nedan) – jämförs mot det tryckta avståndet
    och mot det utritade ruttstrecket (måste följa vägnätet, inte en rak linje),
  * avstånden kontrolleras dessutom mot haversine (raka linjen kan aldrig vara längre än vägen, och vägen ska
    inte vara orimligt mycket längre än den raka linjen),
  * bil- och gångtid kontrolleras mot avstånd och en rimlig hastighetsgräns,
  * markörerna (numrerade cirklar) hittas i PDF:ens ritoperationer via fyllfärgen och projiceras tillbaka med
    grindens egen lokala Mercator – ska ligga nära ortens/adressens koordinat,
  * legendens ordning (vilken ort som skrivs var) kontrolleras textuellt mot platslistan i orderns ordning,
  * källhänvisning, teckenglapp, upplösning, sidformat (A5 + A4) och att kartan inte har tomma vägrutor,
  * determinism: att köra generatorn igen på samma order ger exakt samma beräknade avstånd/tider.

Körning: python grind_brollopskarta.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
"""
import heapq
import json
import math
import re
import sys
import time
import unicodedata
from pathlib import Path

import fitz
import numpy as np

ROOT = Path(__file__).parent
MARK = (0.831, 0.180, 0.310)
TOL_MARKER_MM = 1.5
DIST_TOL_FRAC = 0.03  # tryckt avstånd mot egen Dijkstra
TIME_TOL_FRAC = 0.20  # avrundning till hela minuter ger relativt stor procentfelmarginal på korta sträckor
MUST = ["© OpenStreetMap contributors", "Open Database License (ODbL)"]
# Egen kopia av rolletiketterna (ordförråd, ingen beräkning) och det tryckta radmönstret – för att kontrollera
# legendens ordning och de tryckta avstånden/tiderna oberoende av generatorns egna variabler.
ROLE_LABEL = {
    "sv": {"vigsel": "Vigsel", "mottagning": "Mottagning", "hotell": "Hotell", "parkering": "Parkering", "fest": "Fest", "annat": "Plats"},
    "en": {"vigsel": "Ceremony", "mottagning": "Reception", "hotell": "Hotel", "parkering": "Parking", "fest": "Party", "annat": "Place"},
    "de": {"vigsel": "Trauung", "mottagning": "Feier", "hotell": "Hotel", "parkering": "Parken", "fest": "Party", "annat": "Ort"},
}
LEG_RE = {
    "sv": re.compile(r"([\d,]+) km.*?ca (\d+) min bil.*?ca (\d+) min gång"),
    "en": re.compile(r"([\d.]+) km.*?approx\. (\d+) min by car.*?(\d+) min on foot"),
    "de": re.compile(r"([\d,]+) km.*?ca\. (\d+) Min\. Auto.*?(\d+) Min\. zu Fuß"),
}
WALK_KMH = 4.8
SPEED_KMH = {"motorway": 100, "trunk": 90, "primary": 70, "secondary": 60, "tertiary": 50,
             "unclassified": 40, "residential": 30, "living_street": 15, "pedestrian": 5, "road": 40}
R_EARTH_M = 6371008.8


def _n(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(ch)).strip()


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH_M * math.asin(math.sqrt(min(1.0, h)))


def merc_xy(lat, lon, lat0, lon0):
    R = 6378137.0
    k = math.cos(math.radians(lat0))
    x = R * math.radians(lon - lon0) * k
    y = R * (math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) - math.log(math.tan(math.pi / 4 + math.radians(lat0) / 2))) * k
    return x, y


def _key(lo, la):
    return (round(lo, 6), round(la, 6))


def build_graph(roads):
    """Egen, från generatorns vagnat.py oberoende, uppbyggnad av samma slags graf ur F['roads']."""
    g = {}
    for cls, pts in roads:
        for (lo1, la1), (lo2, la2) in zip(pts, pts[1:]):
            if lo1 == lo2 and la1 == la2:
                continue
            a, b = _key(lo1, la1), _key(lo2, la2)
            d = haversine_m(la1, lo1, la2, lo2)
            if d <= 0:
                continue
            g.setdefault(a, []).append((b, d, cls))
            g.setdefault(b, []).append((a, d, cls))
    return g


def nearest_node(g, lat, lon):
    best, bd = None, None
    for lo, la in g:
        d = haversine_m(lat, lon, la, lo)
        if bd is None or d < bd:
            best, bd = (lo, la), d
    return best, bd


def dijkstra(g, start, end):
    if start not in g or end not in g:
        return None
    dist = {start: 0.0}; prev = {}
    pq = [(0.0, start)]; seen = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in seen:
            continue
        seen.add(u)
        if u == end:
            break
        for v, w, cls in g.get(u, []):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd; prev[v] = (u, w, cls)
                heapq.heappush(pq, (nd, v))
    if end not in dist:
        return None
    path = [end]; drive_s = 0.0; cur = end
    while cur != start:
        u, w, cls = prev[cur]
        drive_s += w / (SPEED_KMH.get(cls, 40) * 1000 / 3600)
        path.append(u); cur = u
    path.reverse()
    return {"dist_m": dist[end], "path": path, "drive_s": drive_s, "walk_s": dist[end] / (WALK_KMH * 1000 / 3600)}


def route_own(roads, a, b):
    g = build_graph(roads)
    if not g:
        return None
    na, da = nearest_node(g, *a); nb, db = nearest_node(g, *b)
    if na is None or nb is None or da > 400.0 or db > 400.0:
        return None
    return dijkstra(g, na, nb)


def point_seg_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def polyline_dist(pt, poly):
    return min(point_seg_dist(pt, poly[i], poly[i + 1]) for i in range(len(poly) - 1))


def run(meta_path):
    import osmdata as O  # bara hämtning/tolkning av samma OSM-svar (cachat), ingen ritkod eller ruttkod delas
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]
    places = meta["places_resolved"]
    lat0, lon0 = meta["center"]
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})

    chk("antal_platser", 1 <= len(places) <= 3, f"{len(places)} platser (tillåtet 1-3)")
    osm, _ = O.fetch(meta["osm"]["fraga"])
    F = O.parse_features(osm)

    # --- eget vägnät + Dijkstra per sträcka, jämfört med tryckt avstånd/tid och med haversine
    own_legs = []
    for i, leg in enumerate(meta["legs"]):
        a, b = places[leg["from"]], places[leg["to"]]
        r = route_own(F["roads"], (a["lat"], a["lon"]), (b["lat"], b["lon"]))
        hs = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        own_legs.append((r, hs))
        chk(f"vag_hittad_strecka_{i + 1}", r is not None, "egen Dijkstra hittade en väg" if r else "ingen väg hittad i det egna vägnätet")
        if r is None:
            continue
        chk(f"avstand_ej_kortare_an_haversine_strecka_{i + 1}", r["dist_m"] >= hs - 5.0,
            f"egen vägdistans {r['dist_m']:.0f} m, raka linjen (haversine) {hs:.0f} m")
        chk(f"avstand_ej_orimligt_strecka_{i + 1}", r["dist_m"] <= max(hs * 4.0, hs + 1500.0),
            f"egen vägdistans {r['dist_m']:.0f} m mot raka linjen {hs:.0f} m (gräns {max(hs * 4.0, hs + 1500.0):.0f} m)")
        chk(f"tryckt_avstand_strecka_{i + 1}", abs(leg["dist_m"] - r["dist_m"]) <= max(30.0, DIST_TOL_FRAC * r["dist_m"]),
            f"metadatans avstånd {leg['dist_m']:.0f} m, egen Dijkstra {r['dist_m']:.0f} m")
        chk(f"tid_rimlig_strecka_{i + 1}",
            abs(leg["drive_s"] - r["drive_s"]) <= max(60.0, TIME_TOL_FRAC * r["drive_s"]) and abs(leg["walk_s"] - r["walk_s"]) <= max(60.0, TIME_TOL_FRAC * r["walk_s"]),
            f"metadata bil {leg['drive_s']:.0f} s / gång {leg['walk_s']:.0f} s, egen {r['drive_s']:.0f} s / {r['walk_s']:.0f} s")

    for lang, pdf in meta["files"].items():
        doc = fitz.open(pdf)
        chk(f"{lang}_antal_sidor", len(doc) == 2, f"{len(doc)} sidor (förväntat 2: kort + affisch)")
        for pi, fmt, wantwh in ((0, "kort", (419.5, 595.3)), (1, "affisch", (595.3, 841.9))):
            if pi >= len(doc):
                continue
            page = doc[pi]
            geo = meta["geometry"][lang][fmt]
            chk(f"{lang}_{fmt}_sidformat", abs(page.rect.width - wantwh[0]) < 2 and abs(page.rect.height - wantwh[1]) < 2,
                f"{page.rect.width:.0f}×{page.rect.height:.0f} pt")
            text = page.get_text()
            miss = [m for m in MUST if m not in text]
            chk(f"{lang}_{fmt}_attribution", not miss, "källhänvisning och ODbL-licens finns" if not miss else f"saknas: {miss}")
            bad = text.count(chr(0)) + text.count(chr(0xFFFD))
            chk(f"{lang}_{fmt}_inga_saknade_tecken", bad == 0, f"{bad} tecken utan glyf")
            # legendens ordning: "roll · ort" (egen rolletikett, samma radmönster som generatorn skriver) ska
            # förekomma i texten i orderns ordning (sekventiell sökning, tål att flera platser delar ort)
            cursor, bad_order = 0, []
            for pl in places:
                role = (pl.get("label") or "").strip() or ROLE_LABEL.get(lang, ROLE_LABEL["en"]).get(pl.get("role") or "annat", "?")
                town = pl.get("place", "") + (f", {pl.get('country', '')}" if pl.get("country") else "")
                needle = f"{role} · {town}"
                idx = text.find(needle, cursor)
                if idx < 0:
                    bad_order.append(needle)
                else:
                    cursor = idx + 1
            chk(f"{lang}_{fmt}_legend_ordning", not bad_order, "alla platser finns i texten i rätt ordning" if not bad_order else f"fel ordning/saknas: {bad_order}")
            # tryckta avstånd/tider per sträcka mot egen Dijkstra (fångar avstånd/tid som skrivs fel utan att
            # metadatan ändras)
            m = LEG_RE.get(lang, LEG_RE["en"]).findall(text)
            for i, (r, hs) in enumerate(own_legs):
                if r is None or i >= len(m):
                    continue
                km_s, drive_s, walk_s = m[i]
                km_val = float(km_s.replace(",", "."))
                chk(f"{lang}_{fmt}_tryckt_km_strecka_{i + 1}", abs(km_val * 1000 - r["dist_m"]) <= max(60.0, 0.06 * r["dist_m"]),
                    f"tryckt {km_val} km, egen Dijkstra {r['dist_m'] / 1000:.3f} km")
                chk(f"{lang}_{fmt}_tryckt_tid_strecka_{i + 1}",
                    abs(int(drive_s) - r["drive_s"] / 60.0) <= 1.0 and abs(int(walk_s) - r["walk_s"] / 60.0) <= 1.0,
                    f"tryckt bil {drive_s} min / gång {walk_s} min, egen {r['drive_s'] / 60:.1f} / {r['walk_s'] / 60:.1f} min")
            chk(f"{lang}_{fmt}_vagbeskrivning_rader", len(m) == len(meta["legs"]), f"{len(m)} rader hittade, {len(meta['legs'])} sträckor")
            # markörer: hitta fyllda cirklar i MARK-färgen, oberoende av generatorns egen markörlista
            fx, fy, fw, fh = geo["frame_pt"]
            x0, y0, x1, y1 = geo["bbox_m"]
            scale = geo["scale_pt_per_m"]
            H = page.rect.height
            found = []
            for d in page.get_drawings():
                col = d.get("fill")
                r = d.get("rect")
                if col and r and all(abs(a - b) < 0.02 for a, b in zip(col, MARK)) and 8 <= r.width <= 32 and abs(r.width - r.height) < 2.5:
                    found.append(((r.x0 + r.x1) / 2, H - (r.y0 + r.y1) / 2))
            chk(f"{lang}_{fmt}_antal_markorer", len(found) == len(places), f"{len(found)} markörer hittade i PDF:en, {len(places)} platser")
            errs, used = [], set()
            for pl in places:
                mx, my = merc_xy(pl["lat"], pl["lon"], lat0, lon0)
                ex, ey = fx + (mx - x0) * scale, fy + (my - y0) * scale
                best = min(((math.hypot(ex - fxp, ey - fyp), k) for k, (fxp, fyp) in enumerate(found)), default=(9e9, -1))
                errs.append(round(best[0] / 72 * 25.4, 3))
                if best[1] >= 0:
                    used.add(best[1])
            worst = max(errs) if errs else 99
            chk(f"{lang}_{fmt}_markorer_pa_ratt_koordinat", worst <= TOL_MARKER_MM and len(used) == len(places),
                f"största avvikelse {worst:.2f} mm på papperet (gräns {TOL_MARKER_MM} mm), {len(used)}/{len(places)} markörer unikt tillordnade")
            # ruttlinjen: hitta streckade, ofyllda linjer (bred nog för att inte vara järnväg) och jämför mot egen Dijkstra
            lines = []
            for d in page.get_drawings():
                dashes = d.get("dashes")
                if dashes and dashes not in (None, "[] 0") and not d.get("fill") and d.get("width", 0) >= 1.0:
                    pts = []
                    for it in d.get("items", []):
                        if it[0] == "l":
                            if not pts:
                                pts.append((it[1].x, H - it[1].y))
                            pts.append((it[2].x, H - it[2].y))
                    if len(pts) >= 2:
                        lines.append(pts)
            chk(f"{lang}_{fmt}_antal_ruttlinjer", len(lines) == len(meta["legs"]), f"{len(lines)} ruttlinjer ritade, {len(meta['legs'])} sträckor")
            for i, (r, hs) in enumerate(own_legs):
                if r is None or i >= len(lines):
                    continue
                own_px = [(fx + (merc_xy(la, lo, lat0, lon0)[0] - x0) * scale, fy + (merc_xy(la, lo, lat0, lon0)[1] - y0) * scale) for lo, la in r["path"]]
                dev = max(polyline_dist(p, lines[i]) for p in own_px) if len(lines[i]) >= 2 else 9e9
                dev_mm = dev / 72 * 25.4
                chk(f"{lang}_{fmt}_ruttlinje_pa_vagnatet_strecka_{i + 1}", dev_mm <= 3.0,
                    f"största avvikelse mellan ritad linje och egen Dijkstra-väg: {dev_mm:.2f} mm (gräns 3 mm)")
            # upplösning: kartrutan ska vara vektor, eller raster ≥ 300 dpi
            frame = fitz.Rect(fx, H - fy - fh, fx + fw, H - fy)
            lowres = []
            for im in page.get_images(full=True):
                for rr in page.get_image_rects(im[0]):
                    if rr.intersects(frame):
                        dpi = im[2] / (rr.width / 72)
                        if dpi < 300:
                            lowres.append(round(dpi))
            chk(f"{lang}_{fmt}_upplosning", not lowres, "vektor" if not page.get_images(full=True) else (f"raster {lowres} dpi < 300" if lowres else "raster ≥ 300 dpi"))
            # ingen text får sticka ut utanför sidan (fångar text_kapad: text som ritas utanför marginalerna)
            overflow = []
            for b in page.get_text("blocks"):
                if b[6] == 0 and b[4].strip() and (b[0] < -0.5 or b[1] < -0.5 or b[2] > page.rect.width + 0.5 or b[3] > page.rect.height + 0.5):
                    overflow.append(b[4].strip()[:30])
            chk(f"{lang}_{fmt}_text_innanfor_sidan", not overflow, "all text ligger inom sidan" if not overflow else f"text utanför sidan: {overflow}")
            # vägtäckning: OSM-vägarna (egen tolkning av samma svar) ska synas som bläck på kartan, i alla fyra
            # kvadranter kring centrum – fångar en kvadrant som tappats vid ritningen (tomt_omrade)
            pix = page.get_pixmap(dpi=100, clip=frame)
            a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3].astype(int)
            q = (a // 8).reshape(-1, 3)
            keys, counts = np.unique(q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2], return_counts=True)
            top = keys[np.argsort(-counts)[:2]]
            bg = [np.array([k // 1024, (k // 32) % 32, k % 32]) * 8 + 4 for k in top]
            dist_bg = np.min([np.abs(a - b).sum(-1) for b in bg], axis=0)
            ink = dist_bg > 60
            from scipy.ndimage import maximum_filter
            ink3 = maximum_filter(ink, size=3)
            samples = []
            for cls, pts in F["roads"]:
                for (lo1, la1), (lo2, la2) in zip(pts, pts[1:]):
                    sx1, sy1 = merc_xy(la1, lo1, lat0, lon0); sx2, sy2 = merc_xy(la2, lo2, lat0, lon0)
                    L = math.hypot(sx2 - sx1, sy2 - sy1)
                    n = max(1, int(L / 12.0))
                    for k in range(n):
                        t = (k + 0.5) / n
                        samples.append((sx1 + t * (sx2 - sx1), sy1 + t * (sy2 - sy1)))
            inside = [(mx, my) for mx, my in samples if x0 <= mx <= x1 and y0 <= my <= y1]
            if len(inside) < 150:
                chk(f"{lang}_{fmt}_vagtackning", True, f"för få vägpunkter i utsnittet ({len(inside)}) för att pröva – hoppar över")
            else:
                cols_px = np.clip(((np.array([p[0] for p in inside]) - x0) / (x1 - x0) * pix.width).astype(int), 0, pix.width - 1)
                rows_px = np.clip(((y1 - np.array([p[1] for p in inside])) / (y1 - y0) * pix.height).astype(int), 0, pix.height - 1)
                hit = ink3[rows_px, cols_px]
                tot = hit.mean()
                mxs, mys = np.array([p[0] for p in inside]), np.array([p[1] for p in inside])
                quad = {}
                for name, sel in (("NV", (mxs < 0) & (mys > 0)), ("NO", (mxs >= 0) & (mys > 0)),
                                  ("SV", (mxs < 0) & (mys <= 0)), ("SO", (mxs >= 0) & (mys <= 0))):
                    if sel.sum() >= 15:
                        quad[name] = round(float(hit[sel].mean()), 3)
                chk(f"{lang}_{fmt}_vagtackning", tot >= 0.80 and all(v >= 0.55 for v in quad.values()),
                    f"{len(inside)} vägpunkter, {tot * 100:.1f} % har vägbläck (gräns 80 %, per kvadrant ≥ 55 %): {quad}")

    # --- determinism: kör generatorn igen på samma order, jämför beräknade avstånd/tider
    try:
        import importlib
        import os as _os
        import tempfile
        gen = importlib.import_module("brollopskarta")
        with tempfile.TemporaryDirectory() as wd:
            old = _os.environ.get("STJARN_OUT")
            _os.environ["STJARN_OUT"] = wd
            try:
                op = Path(wd) / f"{o['id']}_det.json"
                op.write_text(json.dumps(dict(o, id=f"{o['id']}_det"), ensure_ascii=False), encoding="utf-8")
                m2 = gen.generate(op)
            finally:
                if old is None:
                    _os.environ.pop("STJARN_OUT", None)
                else:
                    _os.environ["STJARN_OUT"] = old
        same = len(m2["legs"]) == len(meta["legs"]) and all(
            abs(a["dist_m"] - b["dist_m"]) < 0.5 and abs(a["drive_s"] - b["drive_s"]) < 0.5 and abs(a["walk_s"] - b["walk_s"]) < 0.5
            for a, b in zip(m2["legs"], meta["legs"]))
        chk("determinism", same, "andra körningen gav identiska avstånd/tider" if same else f"skiljer sig: {m2['legs']} vs {meta['legs']}")
    except Exception as e:
        chk("determinism", False, f"kunde inte köras om: {e!r}")

    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "product": "brollopskarta", "godkand": passed, "antal_grindar": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)}, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
