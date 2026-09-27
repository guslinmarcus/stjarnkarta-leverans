"""Kvalitetsgrind för stadskartan. Oberoende av generatorns ritkod:
  * källhänvisningen läses ur PDF-texten ("© OpenStreetMap contributors" + "Open Database License (ODbL)"),
  * vägarna i OSM-svaret (egen tolkning av JSON) projiceras med grindens egen Mercator runt ORTENS koordinat
    (egen GeoNames-uppslagning) och jämförs med rastret av den tryckta sidan: varje provpunkt på en väg ska
    ha väg-bläck. Det fäller fel centrum, tomma områden och saknade lager,
  * inga tomma rutor: rutnät 6×8 över kartan – varje ruta med vägdata måste ha vägbläck,
  * upplösning: kartan ska vara vektor, eller rasterbild ≥ 300 dpi,
  * utskrivna koordinater = ortens koordinat, inga saknade tecken, A3.

Körning: python grind_stadskarta.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
"""
import gzip
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
MUST = ["© OpenStreetMap contributors", "Open Database License (ODbL)"]
RENDER_DPI = 100
MIN_RASTER_DPI = 300


def _n(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(ch)).strip()


def geonames_point(place, cc=None):
    q = _n(place); best = None
    with gzip.open(ROOT / "data" / "orter.tsv.gz", "rt", encoding="utf-8") as f:
        for line in f:
            name, lat, lon, c, pop, tz, names = line.rstrip("\n").split("\t")
            if q in names.split("|") and (not cc or c == cc):
                r = (int(pop or 0), float(lat), float(lon))
                if best is None or r > best:
                    best = r
    return best


def merc_xy(lat, lon, lat0, lon0):
    R = 6378137.0
    k = math.cos(math.radians(lat0))
    x = R * math.radians(lon - lon0) * k
    y = R * (math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) - math.log(math.tan(math.pi / 4 + math.radians(lat0) / 2))) * k
    return x, y


def road_samples(osm, lat0, lon0, step_m=40):
    pts = []
    for el in osm.get("elements", []):
        if el.get("type") != "way" or "highway" not in el.get("tags", {}) or "geometry" not in el:
            continue
        g = [merc_xy(p["lat"], p["lon"], lat0, lon0) for p in el["geometry"] if p]
        for (x1, y1), (x2, y2) in zip(g, g[1:]):
            L = math.hypot(x2 - x1, y2 - y1)
            n = max(1, int(L / step_m))
            for k in range(n):
                t = (k + 0.5) / n
                pts.append((x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
    return np.array(pts) if pts else np.zeros((0, 2))


def run(meta_path):
    import osmdata as O  # bara hämtning/cache av samma Overpass-svar, ingen ritkod
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})
    g = geonames_point(o["place"], o.get("cc"))
    chk("ort_mot_geonames", g is not None and math.dist((g[1], g[2]), (o["lat"], o["lon"])) < 0.0005,
        f"egen uppslagning {g[1:] if g else None} mot orderns {o['lat']}, {o['lon']}")
    # kartans mitt ska vara orderns koordinat (fulfil.py: GeoNames avrundat till 4 decimaler, kontrollerad ovan ≤ 50 m)
    lat0, lon0 = o["lat"], o["lon"]
    osm, _ = O.fetch(meta["osm"]["fraga"])
    for lang, pdf in meta["files"].items():
        doc = fitz.open(pdf); page = doc[0]
        geo = meta["geometry"][lang]
        text = page.get_text()
        miss = [m for m in MUST if m not in text]
        chk(f"{lang}_attribution", not miss, "källhänvisning och licens finns i texten" if not miss else f"saknas: {miss}")
        bad = text.count(chr(0)) + text.count(chr(0xFFFD))
        chk(f"{lang}_inga_saknade_tecken", bad == 0, f"{bad} tecken utan glyf")
        m = re.search(r"(\d+\.\d+)° ([NS])\s+(\d+\.\d+)° ([EW])", text)
        if m:
            la = float(m.group(1)) * (1 if m.group(2) == "N" else -1); lo = float(m.group(3)) * (1 if m.group(4) == "E" else -1)
            chk(f"{lang}_koordinater_tryckta", abs(la - lat0) < 0.002 and abs(lo - lon0) < 0.002, f"tryckt {la}, {lo}; ort {lat0:.4f}, {lon0:.4f}")
        else:
            chk(f"{lang}_koordinater_tryckta", False, "inga koordinater hittades i texten")
        chk(f"{lang}_titel", _n(o.get("text") or o["place"]).replace(" ", "")[:6] in _n(text).replace(" ", ""), "titeln finns i texten")
        chk(f"{lang}_sidformat", abs(page.rect.width - 841.9) < 2 and abs(page.rect.height - 1190.6) < 2, f"{page.rect.width:.0f}×{page.rect.height:.0f} pt")
        fx, fy, fw, fh = geo["frame_pt"]
        H = page.rect.height
        frame = fitz.Rect(fx, H - fy - fh, fx + fw, H - fy)
        imgs = page.get_images(full=True)
        lowres = []
        for im in imgs:
            for r in page.get_image_rects(im[0]):
                if r.intersects(frame):
                    dpi = im[2] / (r.width / 72)
                    if dpi < MIN_RASTER_DPI:
                        lowres.append(round(dpi))
        chk(f"{lang}_upplosning", not lowres, "kartan är vektor" if not imgs else (f"rasterbilder {lowres} dpi < {MIN_RASTER_DPI}" if lowres else "rasterbilder ≥ 300 dpi"))
        # rastrera kartytan
        pix = page.get_pixmap(dpi=RENDER_DPI, clip=frame)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3].astype(int)
        q = (a // 8).reshape(-1, 3)
        keys, counts = np.unique(q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2], return_counts=True)
        top = keys[np.argsort(-counts)[:2]]
        bg = [np.array([k // 1024, (k // 32) % 32, k % 32]) * 8 + 4 for k in top]
        dist = np.min([np.abs(a - b).sum(-1) for b in bg], axis=0)
        ink = dist > 60
        # bläck i närheten (3×3)
        from scipy.ndimage import maximum_filter
        m_px = 2 * meta["half_m"][0] / pix.width
        ink3 = maximum_filter(ink, size=2 * max(1, int(math.ceil(8 / m_px))) + 1)  # ±8 m eller minst ±1 px
        hw, hh = meta["half_m"]
        P = road_samples(osm, lat0, lon0)
        inside = (np.abs(P[:, 0]) < hw * 0.985) & (np.abs(P[:, 1]) < hh * 0.985) if len(P) else np.zeros(0, bool)
        P = P[inside]
        if len(P) < 200:
            chk(f"{lang}_vagar_pa_ratt_plats", False, f"för få vägpunkter i OSM-data ({len(P)})"); continue
        px = ((P[:, 0] + hw) / (2 * hw) * (pix.width - 1)).round().astype(int)
        py = ((hh - P[:, 1]) / (2 * hh) * (pix.height - 1)).round().astype(int)
        hit = ink3[py, px]
        tot = hit.mean()
        quad = {}
        for name, sel in (("NV", (P[:, 0] < 0) & (P[:, 1] > 0)), ("NO", (P[:, 0] >= 0) & (P[:, 1] > 0)),
                          ("SV", (P[:, 0] < 0) & (P[:, 1] <= 0)), ("SO", (P[:, 0] >= 0) & (P[:, 1] <= 0))):
            if sel.sum() > 50:
                quad[name] = round(float(hit[sel].mean()), 3)
        chk(f"{lang}_vagar_pa_ratt_plats", tot >= 0.85 and all(v >= 0.75 for v in quad.values()),
            f"{len(P)} provpunkter på OSM-vägar, {tot * 100:.1f} % har vägbläck (gräns 85 %, per kvadrant ≥ 75 %): {quad}")
        # rutnät
        empty = []
        cols, rows = 6, 8
        ci = np.minimum((px * cols) // pix.width, cols - 1); ri = np.minimum((py * rows) // pix.height, rows - 1)
        for r_ in range(rows):
            for c_ in range(cols):
                sel = (ci == c_) & (ri == r_)
                if sel.sum() >= 20 and hit[sel].mean() < 0.5:
                    empty.append(f"r{r_}k{c_}:{hit[sel].mean() * 100:.0f}%")
        cell_ink = [ink[r_ * pix.height // rows:(r_ + 1) * pix.height // rows, c_ * pix.width // cols:(c_ + 1) * pix.width // cols].mean()
                    for r_ in range(rows) for c_ in range(cols)]
        chk(f"{lang}_inga_tomma_rutor", not empty, f"{rows * cols} rutor; minst bläck i en ruta {min(cell_ink) * 100:.1f} %" + (f"; tomma trots vägdata: {empty}" if empty else ""))
    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "product": "stadskarta", "godkand": passed, "antal_grindar": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)}, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
