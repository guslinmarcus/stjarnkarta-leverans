"""Kvalitetsgrind för golfbanekartan. Oberoende av generatorns ritkod och uppslag:
  * data: grinden läser banans OSM-objekt ur registrets cache (samma Geofabrik-data) och tolkar dem själv:
    hålen = golf=hole-vägar vars mittpunkt ligger i banans polygon (egen punkt-i-polygon), numren 1…n utan
    luckor/dubbletter, n ≥ 9, varje håls slutpunkt ≤ 60 m från en green, och samma hål som generatorn ritade,
  * entydig: ingen annan bana inom 40 km har samma namn (egen normalisering), och den valda banan finns i registret,
  * ritning: hållinjerna projiceras med grindens egen projektion (parametrarna ur meta) och jämförs med rastret av den
    tryckta sidan – varje hål ska ha sin hållinjefärg (eller accentfärgen om det är det markerade hålet) längs linjen,
    det markerade hålet ska ha accentfärgen och inget annat hål får ha den,
  * hålnummer: varje nummer 1…n finns som ord på kartan, högst 12 mm från hålets utslag,
  * par: scorekortets par-rad = OSM:s par-taggar i hålordning (om taggat), totalen = summan,
  * inga tomma rutor: rutnät 6×8 över kartan – varje ruta med golfdata (provpunkter på banans objekt) måste ha tryck där,
    och banan ska fylla ramen (≥ 70 % av bredd eller höjd),
  * allt inom marginal: inga tecken och inget tryck (annat än papperets färg) närmare kanten än 8 mm,
  * källhänvisning ("© OpenStreetMap contributors" + "Open Database License (ODbL)"), vektor eller ≥ 300 dpi,
    utskrivna koordinater = banans mitt, inga saknade tecken, A3.

Körning: python grind_golfbana.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
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

MUST = ["© OpenStreetMap contributors", "Open Database License (ODbL)"]
DPI = 200
MIN_RASTER_DPI = 300
MARGIN_MM = 8.0
R_M = 6371008.8
# golfobjekt som varje stil ska rita (ytor eller linjer) – provpunkterna för "inga tomma rutor" tas bara från dem
RITAS = {"fairway", "green", "tee", "bunker", "rough", "water_hazard", "lateral_water_hazard", "driving_range", "cartpath", "path"}


def _n(s):
    s = "".join(ch for ch in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def pip(rings, x, y):
    ins = False
    for ring in rings:
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]; x2, y2 = ring[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if x < xc:
                    ins = not ins
    return ins


def hav_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class Proj:
    def __init__(self, pr):
        self.__dict__.update(pr)
        self.a = math.radians(pr["rot_grader"])

    def __call__(self, lon, lat):
        x = math.radians(lon - self.lon0) * R_M * math.cos(math.radians(self.lat0))
        y = math.radians(lat - self.lat0) * R_M
        xr = x * math.cos(self.a) - y * math.sin(self.a)
        yr = x * math.sin(self.a) + y * math.cos(self.a)
        return self.ox_pt + (xr - self.cx_m) * self.skala_pt_per_m, self.oy_pt + (yr - self.cy_m) * self.skala_pt_per_m


def samples(pts, P, step_pt=2.0):
    xy = [P(*q) for q in pts]
    out = []
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        n = max(1, int(L / step_pt))
        for k in range(n):
            t = (k + 0.5) / n
            out.append((x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
    return out


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    u = 0 if L == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(a[0] + u * dx - p[0], a[1] + u * dy - p[1])


def run(meta_path):
    import golfbana as GB  # bara stilarnas färgtabell (vad som SKA synas) – ingen rit- eller uppslagskod
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})
    regdir = Path(meta["register"])
    reg = json.load(gzip.open(regdir / "register.json.gz", "rt", encoding="utf-8"))
    cid = meta["bana"]["osm"]
    crs = next((c for c in reg["banor"] if c["osm"] == cid), None)
    chk("bana_finns", crs is not None, f"{cid} i registret {regdir.name}")
    if crs is None:
        return finish(meta_path, o, checks)
    name = crs["tags"].get("name", "")
    # entydig: ingen annan bana med samma namn som ligger ungefär lika nära köparens ort (inom 3 × avståndet, eller
    # inom 40 km om den valda banan ligger mer än 10 km bort). Samma bana karterad två gånger (< 300 m) räknas inte.
    town = (o["lat"], o["lon"])
    d0 = hav_km(town, crs["center"])
    lim = 3 * d0 if d0 <= 10 else 40
    same = [(c["osm"], round(hav_km(town, c["center"]), 1)) for c in reg["banor"] if c["osm"] != cid and _n(c["tags"].get("name")) == _n(name)
            and hav_km(c["center"], crs["center"]) > 0.3 and hav_km(town, c["center"]) < lim]
    chk("bana_entydig", not same, f"'{name}' {d0:.1f} km från orten; andra banor med samma namn inom {lim:.0f} km: {same or 'inga'}")
    feats = json.load(gzip.open(regdir / (cid.replace("/", "_") + ".json.gz"), "rt", encoding="utf-8"))["objekt"]
    rings = crs["outer"] + crs["inner"]
    holes = []
    for f in feats:
        if f["tags"].get("golf") == "hole" and not f.get("multi"):
            p = f["rings"][0]
            mid = ((p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2) if len(p) == 2 else p[len(p) // 2]
            if pip(rings, *mid):
                holes.append(f)
    refs = sorted(int(h["tags"]["ref"]) for h in holes if re.fullmatch(r"\d{1,2}", h["tags"].get("ref", "")))
    n = len(holes)
    chk("minst_9_hal_numrerade", n >= 9 and refs == list(range(1, n + 1)), f"{n} hål i polygonen, nummer {refs}")
    chk("samma_hal_som_generatorn", sorted(h["osm"] for h in holes) == sorted(h["osm"] for h in meta["hal"]),
        f"grinden {n}, generatorn {len(meta['hal'])}")
    hole_by_ref = {int(h["tags"]["ref"]): h for h in holes if re.fullmatch(r"\d{1,2}", h["tags"].get("ref", ""))}
    # green vid varje håls slut (egen avståndsberäkning i meter)
    lat0 = crs["center"][0]
    kx = math.radians(1) * R_M * math.cos(math.radians(lat0)); ky = math.radians(1) * R_M
    greens = [f["rings"][0] for f in feats if f["tags"].get("golf") == "green" and len(f["rings"][0]) >= 4]
    far = []
    for r, h in sorted(hole_by_ref.items()):
        e = h["rings"][0][-1]
        pe = (e[0] * kx, e[1] * ky)
        d = min([0.0 if pip([g], *e) else min(seg_dist(pe, (a[0] * kx, a[1] * ky), (b[0] * kx, b[1] * ky)) for a, b in zip(g, g[1:]))
                 for g in greens] or [1e9])
        if d > 60:
            far.append((r, round(d)))
    chk("green_vid_varje_hal", not far, f"{len(greens)} greener; hål utan green inom 60 m: {far or 'inga'}")
    S = GB.STYLES[meta["geometry"][next(iter(meta["geometry"]))]["style"]]
    P = Proj(meta["projektion"])
    for lang, pdf in meta["files"].items():
        doc = fitz.open(pdf); page = doc[0]
        geo = meta["geometry"][lang]
        W, H = page.rect.width, page.rect.height
        text = page.get_text()
        miss = [m for m in MUST if m not in text]
        chk(f"{lang}_attribution", not miss, "källhänvisning och licens finns i texten" if not miss else f"saknas: {miss}")
        bad = text.count(chr(0)) + text.count(chr(0xFFFD))
        chk(f"{lang}_inga_saknade_tecken", bad == 0, f"{bad} tecken utan glyf")
        chk(f"{lang}_sidformat", abs(W - 841.9) < 2 and abs(H - 1190.6) < 2, f"{W:.0f}×{H:.0f} pt")
        title = (o.get("title") or "").strip() or name
        chk(f"{lang}_titel", _n(title).replace(" ", "")[:8] in _n(text).replace(" ", ""), f"'{title}' i texten")
        m = re.search(r"(\d+\.\d+)° ([NS])\s+(\d+\.\d+)° ([EW])", text)
        cla, clo = crs["center"]
        if m:
            la = float(m.group(1)) * (1 if m.group(2) == "N" else -1); lo = float(m.group(3)) * (1 if m.group(4) == "E" else -1)
            chk(f"{lang}_koordinater", abs(la - cla) < 0.001 and abs(lo - clo) < 0.001, f"tryckt {la}, {lo}; banans mitt {cla:.4f}, {clo:.4f}")
        else:
            chk(f"{lang}_koordinater", False, "inga koordinater i texten")
        # marginal: ord och tryck
        mpt = MARGIN_MM / 25.4 * 72
        words = page.get_text("words")
        out_w = [w[4] for w in words if w[0] < mpt or w[1] < mpt or w[2] > W - mpt or w[3] > H - mpt]
        pix = page.get_pixmap(dpi=40)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3].astype(int)
        paper = np.array([round(x * 255) for x in S["paper"]])
        mp = int(math.ceil(MARGIN_MM / 25.4 * 40))
        band = np.ones(a.shape[:2], bool); band[mp:-mp, mp:-mp] = False
        band[:1, :] = band[-1:, :] = band[:, :1] = band[:, -1:] = False  # sidans yttersta pixel (avrundning av sidkanten)
        ink_band = (np.abs(a - paper).sum(-1) > 40) & band
        chk(f"{lang}_inom_marginal", not out_w and ink_band.sum() == 0,
            f"ord utanför {MARGIN_MM:.0f} mm: {out_w[:5] or 'inga'}; tryckta px i kantbandet: {int(ink_band.sum())}")
        # upplösning
        fx, fy, fw, fh = geo["frame_pt"]
        frame = fitz.Rect(fx, H - fy - fh, fx + fw, H - fy)
        imgs = page.get_images(full=True)
        low = []
        for im in imgs:
            for r in page.get_image_rects(im[0]):
                if r.intersects(frame) and im[2] / (r.width / 72) < MIN_RASTER_DPI:
                    low.append(round(im[2] / (r.width / 72)))
        chk(f"{lang}_upplosning", not low, "kartan är vektor" if not imgs else (f"raster {low} dpi < {MIN_RASTER_DPI}" if low else "raster ≥ 300 dpi"))
        # raster av kartytan
        pix = page.get_pixmap(dpi=DPI, clip=frame)
        A = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3].astype(np.int16)
        sc = pix.width / fw

        def to_px(x, y):
            return int(round((x - fx) * sc)), int(round((fy + fh - y) * sc))

        paper_c = np.array([round(v * 255) for v in S["paper"]])

        def near_color(pts, col, rad=2, tol=70):
            c = np.array([round(v * 255) for v in col])
            hit = 0; tot = 0
            for x, y in pts:
                px, py = to_px(x, y)
                if not (rad <= px < pix.width - rad and rad <= py < pix.height - rad):
                    continue
                tot += 1
                win = A[py - rad:py + rad + 1, px - rad:px + rad + 1]
                dc = np.abs(win - c).sum(-1); dp = np.abs(win - paper_c).sum(-1)
                if ((dc <= tol) & (dc < dp)).any():  # närmare linjens färg än papperets
                    hit += 1
            return hit / max(1, tot), tot
        mark = o.get("hole")
        per = {}
        for r, h in sorted(hole_by_ref.items()):
            pts = samples(h["rings"][0], P)
            L = len(pts)
            pts = pts[int(L * 0.12):max(int(L * 0.12) + 1, int(L * 0.85))]  # utan utslagets nummer och greenens flagga
            if r == mark:
                per[r] = round(near_color(pts, S["accent"])[0], 2)
            else:
                per[r] = round(near_color(pts, S["hole"])[0], 2)
        weak = {r: v for r, v in per.items() if v < 0.6}
        chk(f"{lang}_hal_ritade_pa_ratt_plats", not weak and np.mean(list(per.values()) or [0]) >= 0.8,
            f"andel provpunkter med hållinjens färg per hål (gräns 60 %, snitt 80 %): {per}")
        if mark:
            acc_other = {r: round(near_color(samples(h["rings"][0], P)[2:-2], S["accent"], rad=1, tol=45)[0], 2)
                         for r, h in hole_by_ref.items() if r != mark}
            bad_o = {r: v for r, v in acc_other.items() if v > 0.25}
            chk(f"{lang}_markerat_hal", per.get(mark, 0) >= 0.6 and not bad_o and geo.get("markerat") == mark,
                f"hål {mark}: {per.get(mark, 0) * 100:.0f} % accentfärg; andra hål med accent: {bad_o or 'inga'}")
        # hålnummer som ord nära utslaget
        fr_words = [w for w in words if frame.contains(fitz.Rect(w[:4]))]
        miss_n, far_n = [], []
        for r, h in sorted(hole_by_ref.items()):
            tx, ty = P(*h["rings"][0][0])
            cand = [w for w in fr_words if w[4] == str(r)]
            if not cand:
                miss_n.append(r); continue
            d = min(math.hypot((w[0] + w[2]) / 2 - tx, H - (w[1] + w[3]) / 2 - ty) for w in cand) / 72 * 25.4
            if d > 12:
                far_n.append((r, round(d, 1)))
        chk(f"{lang}_halnummer", not miss_n and not far_n, f"saknas: {miss_n or 'inga'}; > 12 mm från utslaget: {far_n or 'inga'}")
        # par
        pars = [int(hole_by_ref[r]["tags"]["par"]) if re.fullmatch(r"[3-6]", hole_by_ref[r]["tags"].get("par", "").strip()) else None
                for r in sorted(hole_by_ref)]
        if sum(p is not None for p in pars) >= len(pars) / 2 and pars:
            sc_ = geo.get("scorecard") or {}
            # par-raden läses ur sidtexten: ord under scorekortets "Par"-etikett, i x-ordning, per rad
            labs = [w for w in words if w[4] in ("Par",) and H - w[3] < 50 * 72 / 25.4]
            got = []
            for lab in sorted(labs, key=lambda w: w[1]):
                row = sorted([w for w in words if abs((w[1] + w[3]) / 2 - (lab[1] + lab[3]) / 2) < 3 and w[0] > lab[2]], key=lambda w: w[0])
                got += [w[4] for w in row]
            want = [str(p) if p else "–" for p in pars]
            tot = str(sum(pars)) if all(pars) else "–"
            chk(f"{lang}_par", got == want + [tot], f"tryckt {got}; OSM {want} + total {tot}")
        # tomma rutor + fyllnad
        paper_px = np.array([round(v * 255) for v in S["paper"]])
        ink = np.abs(A - paper_px).sum(-1) > 30
        from scipy.ndimage import maximum_filter
        ink3 = maximum_filter(ink, size=9)
        pts = []
        for f in feats:
            if f["tags"].get("golf") in RITAS:
                r0 = f["rings"][0]
                if pip(rings, *r0[len(r0) // 2]):
                    pts += samples(r0, P, 6.0)
        for r, h in hole_by_ref.items():
            pts += samples(h["rings"][0], P, 6.0)
        cols, rows = 6, 8
        cells = {}
        for x, y in pts:
            px, py = to_px(x, y)
            if 0 <= px < pix.width and 0 <= py < pix.height:
                k = (min(rows - 1, py * rows // pix.height), min(cols - 1, px * cols // pix.width))
                cells.setdefault(k, []).append(bool(ink3[py, px]))
        empty = {f"r{k[0]}k{k[1]}": round(float(np.mean(v)), 2) for k, v in cells.items() if len(v) >= 10 and np.mean(v) < 0.6}
        fill = meta["projektion"]["fyllnad"]
        xy = [P(*q) for rr in crs["outer"] for q in rr]
        bw = (max(p[0] for p in xy) - min(p[0] for p in xy)) / fw; bh = (max(p[1] for p in xy) - min(p[1] for p in xy)) / fh
        inside = all(fx - 1 <= p[0] <= fx + fw + 1 and fy - 1 <= p[1] <= fy + fh + 1 for p in xy)
        chk(f"{lang}_inga_tomma_rutor", not empty and max(bw, bh) >= 0.7 and inside,
            f"{len(cells)} rutor med golfdata, tomma: {empty or 'inga'}; banan fyller {bw * 100:.0f} % × {bh * 100:.0f} % av ramen, helt inne: {inside}")
    return finish(meta_path, o, checks)


def finish(meta_path, o, checks):
    res = {"order": o["id"], "product": "golfbana", "godkand": all(c["ok"] for c in checks), "antal_grindar": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)}, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
