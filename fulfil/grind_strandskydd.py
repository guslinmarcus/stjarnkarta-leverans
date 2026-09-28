"""Kvalitetsgrind för kartunderlaget till strandskyddsdispens (strandskydd.py). Hård grind: faller en kontroll
levereras ingen fil (PRODUKTSKYDD K3).

Oberoende av generatorn: grinden importerar INTE strandskydd.py. Den läser samma rådata (OSM-cachen och
Länsstyrelsens cache via strandskydd_data, geodesi via kartgeo) men gör allt annat själv:
  * egen tolkning av strandlinjen ur OSM-JSON (kust, vattenytor > 1 ha, älvar/kanaler; havssidan avgörs med
    kustlinjens riktning – land till vänster – i stället för generatorns polygonbygge),
  * exakta avstånd punkt–segment och segment–segment (generatorn använder KD-träd och förtätning),
  * åtgärdernas lägen räknas om ur orderns koordinater och mått,
  * PDF:en mäts: text (PyMuPDF), vektorbanor per färg (get_drawings) och raster (get_pixmap).
Varje kontroll hör till ett krav-id i strandskydd_regler.json.

Körning: python grind_strandskydd.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = alla kontroller gröna
"""
import json
import math
import re
import sys
import time
import unicodedata
from pathlib import Path

import fitz
import numpy as np

import kartgeo as K
import strandskydd_data as D

ROOT = Path(__file__).parent
PT_PER_MM = 72 / 25.4
COL = {"shore": (0.05, 0.35, 0.85), "buf": (0.85, 0.00, 0.55), "tomt": (0.80, 0.00, 0.00), "atg": (0.90, 0.20, 0.10),
       "br_plan": (0.42, 0.26, 0.10), "br_sek": (0.30, 0.18, 0.06), "wl": (0.00, 0.45, 0.90), "bottom": (0.45, 0.35, 0.20),
       "lst": (0.95, 0.55, 0.00)}
FORBJUDNA = [r"godk[aä]n\w*", r"garant\w*", r"certifi\w*", r"auktoriserad\w*", r"ackrediterad\w*",
             r"behörig\w*", r"kvalificerad\w*", r"sakkunnig\w*", r"expert\w*", r"konsult\w*", r"ritare", r"projektör\w*",
             r"färdig ansökan", r"klar att skicka in", r"redo för ansökan", r"följer reglerna", r"uppfyller kraven",
             r"regelrätt\w*", r"lagenlig\w*", r"korrekt\w*", r"säker\b", r"säkerställ\w*", r"riskfri\w*", r"felfri\w*",
             r"100 ?%", r"vi ansvarar", r"vi tar ansvar", r"kvalitetssäkrad\w*", r"verifierad\w*", r"officiell\w*",
             r"beviljas", r"beviljad\w*", r"dispens ges", r"får dispens", r"kommunens mall", r"länsstyrelsens mall",
             r"i samarbete med", r"ai-\w+"]
TILLATEN_SIDFOT = r"Granskat och godtaget av [^.]*\."


def fnum(v, d=1):
    return f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


def near_col(c, ref, tol=0.03):
    return c is not None and len(c) >= 3 and all(abs(a - b) <= tol for a, b in zip(c[:3], ref))


# ------------------------------------------------------------------ egen strandlinje
def _xy(pts, E0, N0):
    a = np.asarray(pts, float)
    e, n = K.to_sweref(a[:, 1], a[:, 0])
    return np.column_stack([np.asarray(e) - E0, np.asarray(n) - N0])


def _ring_area(r):
    x, y = r[:, 0], r[:, 1]
    return 0.5 * (np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1]))


def _chain(parts):
    """Slå ihop vägbitar till slutna ringar (egen enkel implementation)."""
    parts = [list(map(tuple, p)) for p in parts if len(p) >= 2]
    rings = []
    key = lambda p: (round(p[0], 7), round(p[1], 7))
    while parts:
        cur = parts.pop()
        grown = True
        while grown and key(cur[0]) != key(cur[-1]):
            grown = False
            for i, p in enumerate(parts):
                if key(p[0]) == key(cur[-1]):
                    cur += p[1:]
                elif key(p[-1]) == key(cur[-1]):
                    cur += p[::-1][1:]
                elif key(p[-1]) == key(cur[0]):
                    cur = p[:-1] + cur
                elif key(p[0]) == key(cur[0]):
                    cur = p[::-1][:-1] + cur
                else:
                    continue
                parts.pop(i); grown = True
                break
        if key(cur[0]) == key(cur[-1]) and len(cur) >= 4:
            rings.append(cur)
    return rings


def _pip(P, ring):
    step = max(1, int(2_000_000 // max(1, len(ring))))
    if len(P) > step:
        return np.concatenate([_pip(P[i:i + step], ring) for i in range(0, len(P), step)])
    x, y = P[:, 0][:, None], P[:, 1][:, None]
    xi, yi, xj, yj = ring[:-1, 0][None], ring[:-1, 1][None], ring[1:, 0][None], ring[1:, 1][None]
    with np.errstate(divide="ignore", invalid="ignore"):
        cross = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
    return cross.sum(1) % 2 == 1


def _clip_segs(s, box):
    x0, y0, x1, y1 = box
    return s[~((np.maximum(s[:, 0], s[:, 2]) < x0) | (np.minimum(s[:, 0], s[:, 2]) > x1) |
               (np.maximum(s[:, 1], s[:, 3]) < y0) | (np.minimum(s[:, 1], s[:, 3]) > y1))]


def own_shoreline(osm, E0, N0, box):
    """box = område (lokala meter) där strandlinjen behövs; segment utanför tas bort före alla tester."""
    coast, areas, lines = [], [], []
    for el in osm.get("elements", []):
        t = el.get("tags", {})
        wat = t.get("natural") == "water" or t.get("waterway") == "riverbank" or t.get("landuse") == "reservoir"
        if el["type"] == "way" and el.get("geometry"):
            ll = [(g["lon"], g["lat"]) for g in el["geometry"] if g]
            if len(ll) < 2:
                continue
            if t.get("natural") == "coastline":
                coast.append(_xy(ll, E0, N0))
            elif wat and len(ll) >= 4 and ll[0] == ll[-1]:
                r = _xy(ll, E0, N0)
                areas.append(([r], abs(_ring_area(r))))
            elif t.get("waterway") in ("river", "canal"):
                lines.append(_xy(ll, E0, N0))
        elif el["type"] == "relation" and wat:
            outer = [[(g["lon"], g["lat"]) for g in m["geometry"] if g] for m in el.get("members", []) if m.get("geometry") and m.get("role") != "inner"]
            inner = [[(g["lon"], g["lat"]) for g in m["geometry"] if g] for m in el.get("members", []) if m.get("geometry") and m.get("role") == "inner"]
            ro = [_xy(r, E0, N0) for r in _chain(outer)]
            ri = [_xy(r, E0, N0) for r in _chain(inner)]
            if ro:
                areas.append((ro + ri, sum(abs(_ring_area(r)) for r in ro) - sum(abs(_ring_area(r)) for r in ri)))
    areas_big = [(rings, a) for rings, a in areas if a > 10_000.0]   # MB 7:13 a

    def in_area(P):
        res = np.zeros(len(P), bool)
        for rings, _ in areas:
            par = np.zeros(len(P), int)
            for r in rings:
                bb0, bb1 = r.min(0), r.max(0)
                s = (P[:, 0] >= bb0[0]) & (P[:, 0] <= bb1[0]) & (P[:, 1] >= bb0[1]) & (P[:, 1] <= bb1[1])
                if s.any():
                    par[s] += _pip(P[s], r)
            res |= par % 2 == 1
        return res
    cseg = np.vstack([np.column_stack([l[:-1], l[1:]]) for l in coast]) if coast else np.zeros((0, 4))
    cseg = _clip_segs(cseg, box)

    def in_sea(P):
        if len(cseg) == 0:
            return np.zeros(len(P), bool)
        d, k, t = _nearest(P, cseg)
        a = cseg[k]
        cr = (a[:, 2] - a[:, 0]) * (P[:, 1] - a[:, 1]) - (a[:, 3] - a[:, 1]) * (P[:, 0] - a[:, 0])
        return cr < 0   # land till vänster om kustlinjen
    segs = [cseg]
    for rings, _ in areas_big:
        for r in rings:
            s = _clip_segs(np.column_stack([r[:-1], r[1:]]), box)
            if not len(s):
                continue
            mid = (s[:, :2] + s[:, 2:]) / 2
            d = s[:, 2:] - s[:, :2]
            L = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)
            nv = np.column_stack([-d[:, 1] / L, d[:, 0] / L])
            keep = ~(in_area(mid + 0.5 * nv) & in_area(mid - 0.5 * nv))
            segs.append(s[keep])
    for l in lines:
        s = _clip_segs(np.column_stack([l[:-1], l[1:]]), box)
        if not len(s):
            continue
        mid = (s[:, :2] + s[:, 2:]) / 2
        keep = ~(in_area(mid) | in_sea(mid))
        segs.append(s[keep])
    S = np.vstack([s for s in segs if len(s)]) if any(len(s) for s in segs) else np.zeros((0, 4))
    return S


def _nearest(P, S, chunk=300):
    d = np.full(len(P), np.inf); k = np.zeros(len(P), int); tt = np.zeros(len(P))
    ax, ay, bx, by = S[:, 0], S[:, 1], S[:, 2], S[:, 3]
    dx, dy = bx - ax, by - ay
    L2 = np.maximum(dx * dx + dy * dy, 1e-12)
    for i in range(0, len(P), chunk):
        p = P[i:i + chunk]
        t = np.clip(((p[:, :1] - ax) * dx + (p[:, 1:2] - ay) * dy) / L2, 0, 1)
        dd = np.hypot(p[:, :1] - ax - t * dx, p[:, 1:2] - ay - t * dy)
        kk = np.argmin(dd, 1); r = np.arange(len(p))
        d[i:i + chunk] = dd[r, kk]; k[i:i + chunk] = kk; tt[i:i + chunk] = t[r, kk]
    return d, k, tt


def _seg_seg(p1, p2, S):
    """Exakt minsta avstånd mellan segmentet p1-p2 och alla segment i S."""
    def pt_seg(P, A, B):
        d = B - A
        t = np.clip(((P - A) * d).sum(-1) / np.maximum((d * d).sum(-1), 1e-12), 0, 1)
        return np.hypot(*(P - A - t[..., None] * d).T)
    A, B = S[:, :2], S[:, 2:]
    P1 = np.broadcast_to(p1, A.shape); P2 = np.broadcast_to(p2, A.shape)
    d = np.minimum.reduce([pt_seg(P1, A, B), pt_seg(P2, A, B), pt_seg(A, P1, P2), pt_seg(B, P1, P2)])

    def orient(a, b, c):
        return (b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (c[..., 0] - a[..., 0])
    inter = (np.sign(orient(P1, P2, A)) != np.sign(orient(P1, P2, B))) & (np.sign(orient(A, B, P1)) != np.sign(orient(A, B, P2)))
    d[inter] = 0.0
    return d.min() if len(d) else np.inf


def poly_dist(poly, S):
    poly = np.asarray(poly, float)
    return min(_seg_seg(a, b, S) for a, b in zip(poly[:-1], poly[1:]))


def own_rect(cx, cy, L, W, th):
    t = math.radians(th)
    u = np.array([math.sin(t), math.cos(t)]); v = np.array([math.cos(t), -math.sin(t)])
    c = np.array([cx, cy])
    P = [c - u * L / 2 - v * W / 2, c + u * L / 2 - v * W / 2, c + u * L / 2 + v * W / 2, c - u * L / 2 + v * W / 2]
    return np.array(P + [P[0]])


# ------------------------------------------------------------------ PDF-hjälp
def page_text(p):
    return " ".join(p.get_text().split())


def paths_of(page, col, fill=False):
    out = []
    for d in page.get_drawings():
        c = d.get("fill") if fill else d.get("color")
        if near_col(c, col):
            pts = []
            for it in d["items"]:
                if it[0] == "l":
                    pts.append([(it[1].x, it[1].y), (it[2].x, it[2].y)])
                elif it[0] == "re":
                    r = it[1]
                    pts.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)])
                elif it[0] == "c":
                    pts.append([(it[1].x, it[1].y), (it[4].x, it[4].y)])
                elif it[0] == "qu":
                    q = it[1]
                    pts.append([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y), (q.ul.x, q.ul.y)])
            out.append({"d": d, "pts": pts})
    return out


def scale_from_bar(page, anchor_word="Skalstock"):
    """Läs skalstocken: etiketterna (tal) under ordet 'Skalstock' och deras x-lägen -> punkter per meter."""
    words = page.get_text("words")
    res = []
    for w in words:
        if w[4] != anchor_word:
            continue
        x0, y0 = w[0], w[3]
        nums = [(float(v[4].replace(",", ".")), (v[0] + v[2]) / 2) for v in words
                if re.fullmatch(r"\d+(,\d+)?", v[4]) and y0 + 3 < v[1] < y0 + 30 and x0 - 5 < v[0] < x0 + 260]
        if len(nums) >= 3:
            nums.sort(key=lambda t: t[1])
            m = np.array([t[0] for t in nums]); x = np.array([t[1] for t in nums])
            if m.max() > 0:
                # sista etiketten är vänsterjusterad vid sin tick: använd dess vänsterkant
                last = [v for v in words if re.fullmatch(r"\d+(,\d+)?", v[4]) and float(v[4].replace(",", ".")) == m.max()
                        and y0 + 3 < v[1] < y0 + 30 and x0 - 5 < v[0] < x0 + 260]
                x[-1] = last[0][0] + (last[0][2] - last[0][0]) / 2 if last else x[-1]
                k = np.polyfit(m, x, 1)[0]
                res.append(k)
    return res


# ------------------------------------------------------------------ grinden
def run(meta_path):
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]
    checks = []

    def chk(krav, name, ok, detail):
        checks.append({"krav": krav, "grind": name, "ok": bool(ok), "detalj": detail})
    if "avvisad" in meta:
        chk("K-AVGRANSNING", "avvisad_utan_fil", not Path(meta.get("file", "") or "x").exists(),
            f"ordern avvisades ({meta['avvisad']['kod']}), ingen fil ska finnas")
        return _finish(meta_path, o, checks)
    doc = fitz.open(meta["file"])
    E0, N0 = meta["center_sweref"]
    osm, _ = D.fetch(meta["osm"]["spec"])
    bx = meta["box_m"]
    S = own_shoreline(osm, E0, N0, (bx[0] - 250, bx[1] - 250, bx[2] + 250, bx[3] + 250))
    geo = meta["geometry"]
    has_br = any(a["typ"] == "brygga" for a in o["atgarder"])
    n_exp = 3 + (1 if has_br else 0)
    chk("K-FORMAT", "sidantal", len(doc) == n_exp, f"{len(doc)} sidor, väntat {n_exp}")
    chk("K-FORMAT", "a3_liggande", all(abs(p.rect.width - 1190.6) < 2 and abs(p.rect.height - 841.9) < 2 for p in doc),
        "alla sidor A3 liggande")
    T = [page_text(p) for p in doc]
    full = " ".join(T)
    iO, iD, iR = 0, 1, len(doc) - 1
    iB = 2 if has_br else None
    # ---- rubriker
    chk("K-OVERSIKTSKARTA", "rubrik_oversikt", "Översiktskarta" in T[iO], "sida 1 har översiktskarta")
    chk("K-DETALJKARTA", "rubrik_detalj", "Detaljkarta" in T[iD], "sida 2 har detaljkarta")
    # ---- skala detaljkarta
    m = re.search(r"Skala 1:(\d+) vid utskrift i A3", T[iD])
    sc = int(m.group(1)) if m else None
    chk("K-SKALA", "skala_detalj_1000_2000", sc in (1000, 2000) and sc == meta["skala"], f"tryckt skala 1:{sc}, meta 1:{meta['skala']}")
    sc = sc or meta["skala"]
    k_exp = PT_PER_MM * 1000 / sc
    bars = scale_from_bar(doc[iD])
    chk("K-SKALSTOCK", "skalstock_detalj", bars and all(abs(b / k_exp - 1) < 0.01 for b in bars),
        f"skalstock {', '.join(f'{b:.3f}' for b in bars)} pt/m, väntat {k_exp:.3f}")
    mo = re.search(r"Skala 1:([\d ]+) vid utskrift i A3", T[iO])
    sco = int(mo.group(1).replace(" ", "")) if mo else None
    barso = scale_from_bar(doc[iO])
    ko = PT_PER_MM * 1000 / (sco or 1)
    chk("K-SKALSTOCK", "skalstock_oversikt", sco and barso and all(abs(b / ko - 1) < 0.01 for b in barso),
        f"översikt 1:{sco}, skalstock {barso}")
    # ---- norrpil
    for i, nm in ((iO, "oversikt"), (iD, "detalj")):
        words = doc[i].get_text("words")
        Nw = [w for w in words if w[4] == "N"]
        tri = []
        for d in doc[i].get_drawings():
            if d.get("fill") and near_col(d["fill"], (0.08, 0.08, 0.08)) and len(d["items"]) >= 2 and d["rect"].height > 20:
                tri.append(d["rect"])
        ok = any(any(abs((r.x0 + r.x1) / 2 - (w[0] + w[2]) / 2) < 6 and -4 < r.y0 - w[3] < 12 for r in tri) for w in Nw)
        chk("K-NORRPIL", f"norrpil_{nm}", ok, "N-bokstav med pil under" if ok else "norrpil saknas")
    # ---- kartramen detalj: transformation meter -> sida
    fx, fy, fw, fh = geo["detalj"]["frame_pt"]
    H = doc[iD].rect.height
    bx0, by0, bx1, by1 = meta["box_m"]
    # rutans storlek ska stämma med skalan
    chk("K-SKALA", "utsnitt_mot_skala", abs((bx1 - bx0) * k_exp - fw) < 1 and abs((by1 - by0) * k_exp - fh) < 1,
        f"utsnitt {bx1 - bx0:.1f}×{by1 - by0:.1f} m i ram {fw:.0f}×{fh:.0f} pt")

    def to_pg(P):
        P = np.atleast_2d(P)
        return np.column_stack([fx + (P[:, 0] - bx0) * k_exp, H - (fy + (P[:, 1] - by0) * k_exp)])

    def to_m(Q):
        Q = np.atleast_2d(Q)
        return np.column_stack([bx0 + (Q[:, 0] - fx) / k_exp, by0 + (H - Q[:, 1] - fy) / k_exp])
    frame = fitz.Rect(fx, H - fy - fh, fx + fw, H - fy)
    dpi = 150
    pix = doc[iD].get_pixmap(dpi=dpi, clip=frame)
    A = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3].astype(int)
    pxs = dpi / 72

    def ink(col, tol=70):
        return np.abs(A - np.array(col) * 255).sum(-1) < tol

    def px_of(P):
        Q = to_pg(P)
        return ((Q[:, 0] - frame.x0) * pxs).astype(int), ((Q[:, 1] - frame.y0) * pxs).astype(int)
    from scipy.ndimage import maximum_filter
    # ---- strandlinje: provpunkter längs grindens egen strandlinje ska ha strandbläck (±1,5 m)
    inside = lambda P, m=2: (P[:, 0] > bx0 + m) & (P[:, 0] < bx1 - m) & (P[:, 1] > by0 + m) & (P[:, 1] < by1 - m)
    smp = []
    for s in S:
        L = math.hypot(s[2] - s[0], s[3] - s[1]); n = max(1, int(L / 3))
        for t in (np.arange(n) + 0.5) / n:
            smp.append((s[0] + t * (s[2] - s[0]), s[1] + t * (s[3] - s[1])))
    smp = np.array(smp) if smp else np.zeros((0, 2))
    smp = smp[inside(smp, 3)] if len(smp) else smp
    shore_ink = maximum_filter(ink(COL["shore"], 90), size=2 * int(1.5 * k_exp * pxs) + 1)
    if len(smp) >= 20:
        X, Y = px_of(smp)
        ok = (X >= 0) & (X < pix.width) & (Y >= 0) & (Y < pix.height)
        hit = shore_ink[Y[ok], X[ok]].mean()
        chk("K-STRANDLINJE", "strandlinje_pa_ratt_plats", hit >= 0.85, f"{ok.sum()} provpunkter, {hit * 100:.1f} % har strandbläck (≥ 85 %)")
    else:
        chk("K-STRANDLINJE", "strandlinje_pa_ratt_plats", False, f"för få strandpunkter i utsnittet ({len(smp)})")
    # ---- 100 m-linjen: varje ritad punkt 100 ± tol m från strandlinjen, och täckning av grindens egna 100 m-punkter
    tol = 0.8 if sc <= 1000 else 1.5
    bp = paths_of(doc[iD], COL["buf"])
    verts = np.array([p for d in bp for seg in d["pts"] for p in seg]) if bp else np.zeros((0, 2))
    if len(verts):
        Vm = to_m(verts)
        Vm = Vm[inside(Vm, 1.0)]
        dv, _, _ = _nearest(Vm, S) if len(Vm) else (np.zeros(0), 0, 0)
        bad = np.abs(dv - 100) > tol
        chk("K-100M", "hundrameterslinje_avstand", len(Vm) > 10 and not bad.any(),
            f"{len(Vm)} ritade punkter, avstånd {dv.min():.2f}–{dv.max():.2f} m (100 ± {tol})" if len(Vm) else "inga punkter")
        g = 4.0 if sc <= 1000 else 8.0
        gx, gy = np.meshgrid(np.arange(bx0 + 3, bx1 - 3, g), np.arange(by0 + 3, by1 - 3, g))
        G = np.column_stack([gx.ravel(), gy.ravel()])
        dg, _, _ = _nearest(G, S)
        cand = G[np.abs(dg - 100) < g * 0.35]
        if len(cand):
            from scipy.spatial import cKDTree
            dens = []
            for d in bp:
                for seg in d["pts"]:
                    sm = to_m(np.array(seg))
                    for a, b in zip(sm[:-1], sm[1:]):
                        n = max(1, int(np.hypot(*(b - a)) / 0.5))
                        dens += [a + (b - a) * t for t in np.linspace(0, 1, n + 1)]
            dcov, _ = cKDTree(np.array(dens)).query(cand)
            cov = (dcov < g * 0.6 + tol).mean()
            chk("K-100M", "hundrameterslinje_tackning", cov >= 0.95, f"{len(cand)} kontrollpunkter på 100 m, {cov * 100:.1f} % täcks av linjen")
        else:
            chk("K-100M", "hundrameterslinje_tackning", False, "inga punkter 100 m från strandlinjen i utsnittet")
    else:
        chk("K-100M", "hundrameterslinje_avstand", False, "ingen 100 m-linje ritad")
    # ---- åtgärder: läge, mått, area, avstånd
    red = ink(COL["atg"], 60)
    for i, (a_in, a_m) in enumerate(zip(o["atgarder"], meta["atgarder"]), 1):
        if "e" in a_in:
            e, n = float(a_in["e"]), float(a_in["n"])
        else:
            e, n = (float(v) for v in K.to_sweref(float(a_in["lat"]), float(a_in["lon"])))
        p = np.array([e - E0, n - N0])
        L, B = float(a_in["langd"]), float(a_in["bredd"])
        if a_in["typ"] == "brygga":
            d, kk, tt = _nearest(p[None], S)
            s = S[kk[0]]
            q = s[:2] + tt[0] * (s[2:] - s[:2])
            th = float(a_in["riktning"]) if a_in.get("riktning") is not None else float(a_m["riktning"])
            u = np.array([math.sin(math.radians(th)), math.cos(math.radians(th))])
            poly = own_rect(*(q + u * L / 2), L, B, th)
            # automatisk riktning: ska peka från land ut i vatten (grindens egen havs-/sjötest via avstånd):
            # yttre änden längre från strandlinjen än landfästet och på andra sidan
            chk("K-ATGARD", f"brygga{i}_landfaste", np.hypot(*(q - np.array(a_m["landfaste"]))) < 0.3,
                f"grindens landfäste {q.round(2).tolist()}, generatorns {a_m['landfaste']}")
        else:
            poly = own_rect(p[0], p[1], L, B, float(a_in.get("riktning") or 0.0))
        # vektorkontroll: en röd fylld bana vars hörn (omräknade till meter med den tryckta skalan) ligger på
        # grindens egen polygon (±0,3 m)
        best = None
        for d in paths_of(doc[iD], COL["atg"], fill=True):
            pv = np.array([p for seg in d["pts"] for p in seg])
            if len(pv) < 4:
                continue
            vm = to_m(pv)
            err = max(float(np.min(np.hypot(*(vm - c_).T))) for c_ in poly[:-1])
            best = err if best is None else min(best, err)
        chk("K-ATGARD", f"atgard{i}_lage", best is not None and best < 0.3,
            f"närmaste ritade åtgärd avviker {best:.2f} m från grindens läge" if best is not None else "ingen åtgärd ritad")
        gpoly = np.array(a_m["poly"])
        chk("K-ATGARD", f"atgard{i}_geometri_meta", np.abs(gpoly - poly).max() < 0.3, f"max avvikelse {np.abs(gpoly - poly).max():.2f} m")
        area = L * B
        chk("K-MATT-AREA", f"atgard{i}_mattext", f"{fnum(L)} m" in T[iD] and f"{fnum(B)} m" in T[iD], f"{fnum(L)} m och {fnum(B)} m på detaljkartan")
        chk("K-MATT-AREA", f"atgard{i}_area", T[iD].count(f"{fnum(area)} m²") >= 2 and f"{fnum(area)} m²" in T[iO],
            f"area {fnum(area)} m² på översikt och detaljkarta")
        dd = poly_dist(poly, S)
        mt = re.search(rf"{i}\. {a_m['namn']}: [^.]*?avstånd till strandlinjen ([\d ]+(?:,\d)?) m", T[iD])
        printed = float(mt.group(1).replace(" ", "").replace(",", ".")) if mt else None
        chk("K-AVSTAND", f"atgard{i}_avstand", printed is not None and abs(printed - dd) <= 0.15,
            f"tryckt {printed} m, grindens exakta avstånd {dd:.2f} m")
    # ---- tomtplats
    Tm = meta.get("tomtplats")
    if Tm:
        tp = np.array(Tm["poly"])
        area = abs(_ring_area(tp))
        chk("K-TOMTPLATS", "tomtplats_area", f"area {fnum(area)} m²" in T[iD] and abs(area - Tm["area"]) < 0.2, f"area {fnum(area)} m²")
        sides = [math.hypot(*(b - a)) for a, b in zip(tp[:-1], tp[1:])]
        chk("K-TOMTPLATS", "tomtplats_sidmatt", all(T[iD].count(f"{fnum(s)} m") >= 1 for s in sides), f"sidor {[fnum(s) for s in sides]}")
        tpaths = paths_of(doc[iD], COL["tomt"])
        tv = np.array([p for d in tpaths for seg in d["pts"] for p in seg]) if tpaths else np.zeros((0, 2))
        ok = False
        if len(tv):
            tvm = to_m(tv)
            dmin = np.array([np.min(np.hypot(*(tvm - c).T)) for c in tp[:-1]])
            ok = (dmin < 0.3).all()
        chk("K-TOMTPLATS", "tomtplats_ritad", ok, "tomtplatsens hörn finns i den ritade röda streckade linjen" if ok else "tomtplatsen saknas eller ligger fel")
        fp = poly_dist(tp, S)
        chk("K-FRI-PASSAGE", "fri_passage_tryckt", f"strandlinjen {fnum(fp)} m" in T[iD] and abs(fp - Tm["fri_passage"]) <= 0.15,
            f"grindens avstånd tomtplats–strandlinje {fp:.2f} m")
    else:
        chk("K-TOMTPLATS", "tomtplats_uppgift", "Ingen tomtplats föreslagen" in T[iO], "ingen tomtplats: texten säger det")
    # ---- koordinatrutnät
    words = doc[iD].get_text("words")
    errs = []
    nE = nN = 0
    for w in words:
        if re.fullmatch(r"\d{6}", w[4]) and w[1] > frame.y1:
            X = to_pg(np.array([[float(w[4]) - E0, 0]]))[0, 0]
            errs.append(abs((w[0] + w[2]) / 2 - X)); nE += 1
        if re.fullmatch(r"\d{7}", w[4]) and w[2] < frame.x0:
            Y = to_pg(np.array([[0, float(w[4]) - N0]]))[0, 1]
            errs.append(abs((w[1] + w[3]) / 2 - Y)); nN += 1
    chk("K-KOORDINATER", "koordinatrutnat", nE >= 2 and nN >= 2 and max(errs) < 1.5,
        f"{nE} östliga och {nN} nordliga etiketter, största fel {max(errs) if errs else 'n/a'} pt")
    chk("K-KOORDINATER", "koordinatsystem_angivet", "SWEREF 99 TM" in T[iD] and "SWEREF 99 TM" in T[iO], "SWEREF 99 TM anges")
    # ---- översiktskartan: röd ring på platsen
    ofx, ofy, ofw, ofh = geo["oversikt"]["frame_pt"]
    Ho = doc[iO].rect.height
    X = ofx + ofw / 2; Y = Ho - (ofy + ofh / 2)   # platsen = kartans mitt (origo)
    rings = [d for d in doc[iO].get_drawings() if near_col(d.get("color"), (0.85, 0, 0))
             and abs((d["rect"].x0 + d["rect"].x1) / 2 - X) < 1 and abs((d["rect"].y0 + d["rect"].y1) / 2 - Y) < 1]
    chk("K-OVERSIKTSKARTA", "oversikt_markering", bool(rings), "röd ring i översiktskartans mitt = platsens koordinat")
    chk("K-OVERSIKTSKARTA", "oversikt_skala", sco == 10000, f"översiktens skala 1:{sco}")
    # ---- reservationer och texter
    chk("K-RESERVATION", "reservation_utvidgat",
        all(("utvidgat strandskyddet" in T[j] and "300 m" in T[j] and "detaljplan" in T[j]) for j in (iD, iR)),
        "reservation om utvidgat/upphävt strandskydd på detaljkartan och sista sidan")
    chk("K-RESERVATION", "reservation_tomtplats", "Tomtplatsavgränsningen är ett förslag" in T[iR], "tomtplatsen är ett förslag")
    chk("K-RESERVATION", "reservation_proving", "Kommunen eller länsstyrelsen prövar ansökan och beslutar" in T[iR], "inget påstående om utfall")
    chk("K-RESERVATION", "reservation_13a_c", "7 kap. 13 a–13 c" in T[iR], "undantagen för små sjöar och smala vattendrag nämns")
    chk("K-REGELKONTROLL", "regelkontroll_text", "Regelkontrollen jämför med tabellvärden. Den ersätter inte kontroll på plats." in T[iR],
        "obligatorisk text (PRODUKTSKYDD 2.2)")
    # ---- Länsstyrelsens ytor: egen tolkning av samma cache
    L = D.lst_fetch(meta["lst"]["spec"])
    if L["fel"]:
        chk("K-LST", "lst_text", "kunde inte hämtas" in T[iD], "hämtning misslyckades och texten säger det")
    else:
        hits = []
        for y in L["ytor"]:
            rr = [np.array(r, float) - np.array([E0, N0]) for r in y["ringar"] if len(r) >= 4]
            for i, a_m in enumerate(meta["atgarder"], 1):
                P = np.array(a_m["poly"])[:-1]
                par = sum(_pip(P, r).astype(int) for r in rr) if rr else np.zeros(len(P), int)
                if (par % 2 == 1).any():
                    hits.append((i, y["typ"]))
        ok = all(f"åtgärd {i} ligger inom en yta av typen \"{t}\"" in T[iD] for i, t in hits) if hits else ("ligger inom en yta" not in T[iD])
        chk("K-LST", "lst_text", ok, f"egen kontroll: {hits or 'ingen åtgärd inom en länsstyrelseyta'}")
    # ---- källor och licenser
    for j, nm in ((iO, "oversikt"), (iD, "detalj"), (iR, "sista")):
        chk("K-KALLOR", f"attribution_{nm}", "© OpenStreetMap contributors" in T[j] and "Open Database License (ODbL)" in T[j],
            "© OpenStreetMap contributors + ODbL")
    chk("K-KALLOR", "osm_url_tryckt", "openstreetmap.org/copyright" in T[iR], "URL till upphovsrättssidan (OSMF riktlinje för tryck)")
    chk("K-KALLOR", "lst_licens", "CC0" in T[iR] and "Länsstyrelsernas strandskyddsytor" in T[iR], "Länsstyrelsens data och licens")
    chk("K-KALLOR", "datum_for_data", (meta["osm"]["tidsstampel"] or "x")[:10] in T[iD], f"uttagsdatum {meta['osm']['tidsstampel'][:10]}")
    # ---- bryggritning
    if has_br:
        a_in = next(a for a in o["atgarder"] if a["typ"] == "brygga")
        pb = doc[iB]; tb = T[iB]
        chk("K-BRYGGRITNING", "rubriker_plan_sektion", re.search(r"Plan 1:\d+", tb) and re.search(r"Sektion 1:\d+", tb), "plan och sektion finns")
        ms = re.search(r"Plan 1:(\d+)", tb)
        bs = int(ms.group(1)) if ms else 100
        kb = PT_PER_MM * 1000 / bs
        barsb = scale_from_bar(pb)
        chk("K-SKALSTOCK", "skalstock_brygga", barsb and all(abs(b / kb - 1) < 0.01 for b in barsb), f"{barsb} pt/m, väntat {kb:.3f}")
        L, B = float(a_in["langd"]), float(a_in["bredd"])
        hv, dy = float(a_in["hojd_over_vatten"]), float(a_in["vattendjup_yttre"])
        plan = [d["d"]["rect"] for d in paths_of(pb, COL["br_plan"])]
        okp = any(abs(r.width / kb - L) < 0.01 * L + 0.02 and abs(r.height / kb - B) < 0.02 for r in plan)
        chk("K-BRYGGRITNING", "plan_matt", okp, f"plan: {[(round(r.width / kb, 2), round(r.height / kb, 2)) for r in plan]} m, väntat {L} × {B}")
        sek = [d["d"]["rect"] for d in paths_of(pb, COL["br_sek"])]
        wl = [d["d"]["rect"] for d in paths_of(pb, COL["wl"])]
        bot = paths_of(pb, COL["bottom"])
        oks = False; det = "saknas"
        if sek and wl:
            r = sek[0]; yw = wl[0].y0
            oks = abs(r.width / kb - L) < 0.01 * L + 0.02 and abs((yw - r.y1) / kb - hv) < 0.03
            det = f"sektion längd {r.width / kb:.2f} m, höjd över vatten {(yw - r.y1) / kb:.2f} m"
            if bot:
                ys_end = max(p[1] for d in bot for seg in d["pts"] for p in seg)
                okd = abs((ys_end - yw) / kb - dy) < 0.03
                oks = oks and okd
                det += f", största djup {(ys_end - yw) / kb:.2f} m"
        chk("K-BRYGGRITNING", "sektion_matt", oks, det + f"; väntat {L} m, {hv} m, {dy} m")
        need = [f"{fnum(L)} m", f"{fnum(B)} m", f"{fnum(hv)} m", f"{fnum(dy)} m", "Förankring"]
        chk("K-BRYGGRITNING", "uppgifter_text", all(n in tb for n in need), f"{need}")
    # ---- sidfot, vattenmärke, textgrind
    fin = o.get("version") == "slutversion"
    for j, t in enumerate(T):
        if fin:
            b = o.get("bekraftelse") or {}
            ok = f"Granskat och godtaget av {b.get('namn', '')} {b.get('datum', '')}" in t and "är inte sökande eller upprättare" in t and "UTKAST" not in t
        else:
            ok = "Förhandsvisning framtagen med" in t and "är inte sökande eller upprättare" in t and "UTKAST – förhandsvisning" in t
        chk("K-SIDFOT", f"sidfot_sida{j + 1}", ok, "slutversionens sidfot utan vattenmärke" if fin else "förhandsvisningens sidfot + vattenmärke")
    if fin:
        b = o.get("bekraftelse") or {}
        chk("K-SIDFOT", "bekraftelse_ifylld", bool(b.get("namn")) and bool(b.get("datum")), "namn och datum i bekräftelsen")
    clean = re.sub(TILLATEN_SIDFOT, " ", full)
    hits = sorted({m.group(0) for f in FORBJUDNA for m in re.finditer(rf"\b{f}", clean, flags=re.IGNORECASE)})
    chk("K-TEXTGRIND", "forbjudna_ord", not hits, f"träffar: {hits}" if hits else "inga förbjudna ord (PRODUKTSKYDD 2.1)")
    bad = full.count(chr(0xFFFD)) + full.count(chr(0))
    chk("K-FORMAT", "inga_saknade_tecken", bad == 0, f"{bad} tecken utan glyf")
    imgs = sum(len(doc[i].get_images()) for i in range(len(doc)))
    chk("K-FORMAT", "vektor", imgs == 0, f"{imgs} rasterbilder")
    return _finish(meta_path, o, checks)


def _finish(meta_path, o, checks):
    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "product": "strandskydd", "godkand_av_grind": passed, "antal_kontroller": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "gron": r["godkand_av_grind"], "kontroller": r["antal_kontroller"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)}, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand_av_grind"] else 1)
