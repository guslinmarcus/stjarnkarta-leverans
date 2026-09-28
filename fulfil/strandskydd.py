"""Generator (prototyp): kartunderlag till ansökan om strandskyddsdispens och anmälan om vattenverksamhet (Sverige).

Kunden anger koordinat (WGS 84 eller SWEREF 99 TM) för en eller flera åtgärder (brygga, bod, altan) med mått.
Ut kommer EN PDF (A3 liggande, vektor) med:
  1. Sammanställning + översiktskarta 1:10 000
  2. Detaljkarta 1:1000 eller 1:2000: strandlinje, linje 100 m från strandlinjen, åtgärden med mått och area,
     avstånd till strandlinjen, föreslagen tomtplatsavgränsning, norrpil, skalstock, koordinatrutnät, teckenförklaring
  3. Bryggritning i plan och sektion (bara om en brygga ingår)
  4. Regelkontroll, reservationer, källor och licenser
+ <id>_meta.json som grinden (grind_strandskydd.py) läser.

Produkten är ett UNDERLAG som sökanden själv lämnar in (juridik/PRODUKTSKYDD.md). Inga påståenden om utfall.
Data: bara OpenStreetMap via Geofabrik (ODbL, strandskydd_data.py). Koordinatsystem SWEREF 99 TM (kartgeo.py).
Motorn är regelbaserad (PRODUKTSKYDD K11): ingen språkmodell ändrar något värde eller någon ritning.

Körning: python strandskydd.py order.json  -> <ut>/<id>.pdf + <ut>/<id>_meta.json
Felinjektion (bara tester): FELINJEKTION=<namn> (se FELTYPER).
"""
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from scipy.spatial import cKDTree

import kartgeo as K
import kartlayout as KL
import osmdata as O          # bara geometrihjälp (join_lines, coast_land_polygons, _clip_polyline)
import strandskydd_data as D

GENERATOR_VERSION = "strandskydd/0.1.0"
FEL = os.environ.get("FELINJEKTION", "")
FELTYPER = ["saknad_attribution", "fel_skala", "forskjuten_strandlinje", "fel_buffert", "saknad_reservation",
            "fel_area", "fel_avstand", "saknad_norrpil", "forbjudet_ord", "fel_bryggmatt", "saknad_tomtplats",
            "saknad_sidfot", "fel_atgard_lage", "saknad_oversiktsmarkering", "fel_skalstock"]
ROOT = Path(__file__).parent
REGLER = json.load(open(ROOT / "strandskydd_regler.json", encoding="utf-8"))
VARUMARKE = os.environ.get("KART_VARUMARKE", "[Varumärke]")

PAGE = (420 * mm, 297 * mm)                       # A3 liggande
MAPFRAME = (12 * mm, 24 * mm, 290 * mm, 261 * mm)  # x, y, b, h (punkter, origo nere till vänster)
COL_X = 310 * mm                                   # högerspalt (rubrikfält)
COL_W = 98 * mm
OV_FRAME = (12 * mm, 24 * mm, 228 * mm, 261 * mm)  # översiktskartan
OV_COL_X = 248 * mm
OV_COL_W = 160 * mm
OV_SCALE = 10000
DETAIL_SCALES = (1000, 2000)
STRANDSKYDD_M = 100.0
FRI_PASSAGE_M = 25.0
DATA_HALF_M = 1600.0
SNAP_BRYGGA_M = 15.0
MAX_AVSTAND_M = 300.0     # längre från strandlinjen än så: strandskyddet berörs inte (inte ens utvidgat)

# färger (grinden känner igen dem)
C_WATER = (0.80, 0.89, 0.97)
C_SHORE = (0.05, 0.35, 0.85)
C_BUF = (0.85, 0.00, 0.55)
C_TOMT = (0.80, 0.00, 0.00)
C_LST = (0.95, 0.55, 0.00)
C_ATG = (0.90, 0.20, 0.10)
C_BUILD = (0.62, 0.62, 0.62)
C_PIER = (0.55, 0.40, 0.25)
C_ROAD = (0.80, 0.78, 0.74)
C_INK = (0.08, 0.08, 0.08)
C_BR_PLAN = (0.42, 0.26, 0.10)
C_BR_SEK = (0.30, 0.18, 0.06)
C_WL = (0.00, 0.45, 0.90)
C_BOTTOM = (0.45, 0.35, 0.20)

TYPER = {
    "brygga": {"namn": "Brygga", "langd": (2, 30), "bredd": (0.8, 4.0)},
    "bod": {"namn": "Bod", "langd": (1, 8), "bredd": (1, 6), "hojd": (1.5, 4.5)},
    "altan": {"namn": "Altan", "langd": (1, 15), "bredd": (1, 10), "hojd": (0, 3)},
}
FORANKRING = {"stolpar": "Stolpar (pålar) nedslagna i botten", "pontoner": "Flytbrygga på pontoner, förankrad med kätting mot bottenvikter",
              "stenkistor": "Stenkistor på botten"}


class Avvisad(Exception):
    def __init__(self, kod, text):
        super().__init__(text); self.kod = kod; self.text = text


def fnum(v, d=1):
    s = f"{v:,.{d}f}".replace(",", " ").replace(".", ",")
    return s


# ------------------------------------------------------------------ geometri
def ring_xy(pts, E0, N0):
    a = np.asarray(pts, float)
    e, n = K.to_sweref(a[:, 1], a[:, 0])
    return np.column_stack([np.asarray(e) - E0, np.asarray(n) - N0])


def pip(P, ring):
    """Punkter P (N,2) i ring (M,2)? jämn-udda, vektoriserat (i bitar: högst ~2 miljoner par åt gången)."""
    P = np.atleast_2d(P)
    step = max(1, int(2_000_000 // max(1, len(ring))))
    if len(P) > step:
        return np.concatenate([pip(P[i:i + step], ring) for i in range(0, len(P), step)])
    x, y = P[:, 0][:, None], P[:, 1][:, None]
    xi, yi = ring[:-1, 0][None, :], ring[:-1, 1][None, :]
    xj, yj = ring[1:, 0][None, :], ring[1:, 1][None, :]
    cond = (yi > y) != (yj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = (xj - xi) * (y - yi) / (yj - yi) + xi
    return (np.sum(cond & (x < xc), axis=1) % 2) == 1


def seg_dist(P, S, chunk=400):
    """Minsta avstånd från punkter P (N,2) till segment S (M,4) + närmaste punkt."""
    P = np.atleast_2d(np.asarray(P, float))
    out = np.full(len(P), np.inf); near = np.zeros((len(P), 2))
    if len(S) == 0:
        return out, near
    ax, ay, bx, by = S[:, 0], S[:, 1], S[:, 2], S[:, 3]
    dx, dy = bx - ax, by - ay
    L2 = np.maximum(dx * dx + dy * dy, 1e-12)
    for i in range(0, len(P), chunk):
        p = P[i:i + chunk]
        t = np.clip(((p[:, 0:1] - ax) * dx + (p[:, 1:2] - ay) * dy) / L2, 0, 1)
        qx, qy = ax + t * dx, ay + t * dy
        d = np.hypot(p[:, 0:1] - qx, p[:, 1:2] - qy)
        k = np.argmin(d, axis=1); r = np.arange(len(p))
        out[i:i + chunk] = d[r, k]; near[i:i + chunk, 0] = qx[r, k]; near[i:i + chunk, 1] = qy[r, k]
    return out, near


def densify(poly, step):
    pts = []
    for a, b in zip(poly[:-1], poly[1:]):
        n = max(1, int(math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / step)))
        for k in range(n):
            pts.append((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n))
    pts.append(tuple(poly[-1]))
    return np.array(pts)


def shoelace(poly):
    p = np.asarray(poly)
    return 0.5 * abs(np.dot(p[:-1, 0], p[1:, 1]) - np.dot(p[1:, 0], p[:-1, 1]))


def rect(cx, cy, L, W, theta_deg):
    """Rektangel med mitt (cx, cy), längd L längs riktningen theta (grader medurs från rutnätsnorr), bredd W."""
    t = math.radians(theta_deg)
    u = np.array([math.sin(t), math.cos(t)]); v = np.array([u[1], -u[0]])
    c = np.array([cx, cy])
    pts = [c - u * L / 2 - v * W / 2, c + u * L / 2 - v * W / 2, c + u * L / 2 + v * W / 2, c - u * L / 2 + v * W / 2]
    pts.append(pts[0])
    return np.array(pts)


def iso_lines(xs, ys, Dg, level):
    """Marching squares: isolinjer för Dg (ny, nx) på nivån level. Returnerar lista av polylinjer."""
    F = Dg - level
    ny, nx = F.shape
    b = F > 0

    def hp(j, i):  # kant mellan (j,i) och (j,i+1)
        t = F[j, i] / (F[j, i] - F[j, i + 1]); return (xs[i] + t * (xs[i + 1] - xs[i]), ys[j])

    def vp(j, i):  # kant mellan (j,i) och (j+1,i)
        t = F[j, i] / (F[j, i] - F[j + 1, i]); return (xs[i], ys[j] + t * (ys[j + 1] - ys[j]))
    case = (b[:-1, :-1].astype(int) + 2 * b[:-1, 1:] + 4 * b[1:, 1:] + 8 * b[1:, :-1])
    segs = []
    js, is_ = np.nonzero((case != 0) & (case != 15))
    for j, i in zip(js.tolist(), is_.tolist()):
        cs = int(case[j, i])
        E = {"B": lambda: hp(j, i), "R": lambda: vp(j, i + 1), "T": lambda: hp(j + 1, i), "L": lambda: vp(j, i)}
        if cs in (5, 10):
            centre = (F[j, i] + F[j, i + 1] + F[j + 1, i] + F[j + 1, i + 1]) / 4 > 0
            pairs = {5: (("L", "B"), ("R", "T")) if not centre else (("L", "T"), ("R", "B")),
                     10: (("B", "R"), ("T", "L")) if not centre else (("B", "L"), ("T", "R"))}[cs]
        else:
            m = cs if cs < 8 else 15 - cs
            pairs = {1: (("L", "B"),), 2: (("B", "R"),), 3: (("L", "R"),), 4: (("R", "T"),), 6: (("B", "T"),),
                     7: (("L", "T"),)}[m]
        for a, c in pairs:
            segs.append([E[a](), E[c]()])
    return O.join_lines(segs)


# ------------------------------------------------------------------ OSM -> lokala features
def parse(osm, E0, N0):
    F = {"coast": [], "lakes": [], "waterlines": [], "buildings": [], "piers": [], "roads": [], "places": []}
    for el in osm.get("elements", []):
        t = el.get("tags", {})
        if el["type"] == "node":
            if t.get("place") and t.get("name"):
                xy = ring_xy([(el["lon"], el["lat"])], E0, N0)[0]
                F["places"].append((t["place"], t["name"], float(xy[0]), float(xy[1])))
            continue
        if el["type"] == "way" and el.get("geometry"):
            pts = [(g["lon"], g["lat"]) for g in el["geometry"] if g]
            if len(pts) < 2:
                continue
            xy = ring_xy(pts, E0, N0)
            closed = len(pts) >= 4 and O._key(pts[0]) == O._key(pts[-1])
            if t.get("natural") == "coastline":
                F["coast"].append(xy)
            elif closed and (t.get("natural") == "water" or t.get("waterway") == "riverbank" or t.get("landuse") == "reservoir"):
                F["lakes"].append([xy])
            elif t.get("waterway") in ("river", "stream", "canal"):
                F["waterlines"].append((t["waterway"], xy))
            elif "building" in t and closed:
                F["buildings"].append([xy])
            elif t.get("man_made") == "pier":
                F["piers"].append((xy, closed))
            elif "highway" in t:
                F["roads"].append((t["highway"], xy))
        elif el["type"] == "relation":
            outer, inner = [], []
            for m in el.get("members", []):
                if m.get("geometry"):
                    pts = [(g["lon"], g["lat"]) for g in m["geometry"] if g]
                    (inner if m.get("role") == "inner" else outer).append(pts)
            rings = [r for r in O.join_lines(outer) + O.join_lines(inner) if len(r) >= 4 and O._key(r[0]) == O._key(r[-1])]
            if not rings:
                continue
            rx = [ring_xy(r, E0, N0) for r in rings]
            if t.get("natural") == "water" or t.get("waterway") == "riverbank" or t.get("landuse") == "reservoir":
                F["lakes"].append(rx)
            elif "building" in t:
                F["buildings"].append(rx)
            elif t.get("man_made") == "pier":
                for r in rx:
                    F["piers"].append((r, True))
    return F


class Vatten:
    """Vattenmodell i lokala koordinater: sjöar/vattenytor (jämn-udda per feature) + hav ur kustlinjen."""

    def __init__(self, F, half):
        # sjöringarna klipps till området (jämn-udda-testet är oförändrat för punkter inne i området;
        # Mälaren/Vättern har annars hundratusentals hörn per test)
        bx = (-half, -half, half, half)
        self.lakes = [cr for cr in ([c for c in (sh_clip(r, bx) for r in rings) if c is not None] for rings in F["lakes"]) if cr]
        coast = [[tuple(p) for p in l] for l in F["coast"]]
        if coast:
            land, clakes, has = O.coast_land_polygons(coast, -half, -half, half, half)
        else:
            land, clakes, has = [], [], False
        self.land = [np.asarray(p, float) for p in land if len(p) >= 4]
        self.clakes = [np.asarray(p, float) for p in clakes if len(p) >= 4]
        self.has_coast = has
        self.half = half

    def in_lake(self, P):
        P = np.atleast_2d(P); r = np.zeros(len(P), bool)
        for rings in self.lakes:
            par = np.zeros(len(P), int)
            for ring in rings:
                bb = (ring[:, 0].min(), ring[:, 1].min(), ring[:, 0].max(), ring[:, 1].max())
                sel = (P[:, 0] >= bb[0]) & (P[:, 0] <= bb[2]) & (P[:, 1] >= bb[1]) & (P[:, 1] <= bb[3])
                if sel.any():
                    par[sel] += pip(P[sel], ring)
            r |= (par % 2) == 1
        return r

    def in_sea(self, P):
        P = np.atleast_2d(P)
        if not self.has_coast:
            return np.zeros(len(P), bool)
        onland = np.zeros(len(P), bool)
        for ring in self.land:
            onland |= pip(P, ring)
        sea = ~onland
        for ring in self.clakes:
            sea |= pip(P, ring)
        return sea

    def in_water(self, P):
        return self.in_lake(P) | self.in_sea(P)


SMA_SJO_M2 = 10_000.0
VATTENDRAG_MED_SKYDD = ("river", "canal")


def lake_area(rings):
    """Vattenyta: yttre ringar minus inre (jämn-udda antas: största ringen yttre, övriga som öar om de ligger inuti)."""
    a = [shoelace(r) for r in rings]
    big = max(a)
    return big - sum(x for x in a if x < big) if len(rings) > 1 else big


def shoreline(F, W):
    """Strandlinjen: kustlinje + vattenytornas kanter (utom sjöar ≤ 1 ha och kanter mellan två vattenytor)
    + älvar/kanaler som linjer (utom delar i en vattenyta eller i havet).
    Returnerar (segment (M,4), sort per segment, polylinjer för ritning)."""
    segs, kinds, lines = [], [], []
    h = W.half

    def region(pl):  # segment vars ruta skär området runt platsen (stora sjöar: Mälaren har > 100 000 hörn)
        a, b = pl[:-1], pl[1:]
        return ~((np.maximum(a[:, 0], b[:, 0]) < -h) | (np.minimum(a[:, 0], b[:, 0]) > h) |
                 (np.maximum(a[:, 1], b[:, 1]) < -h) | (np.minimum(a[:, 1], b[:, 1]) > h))

    def add_runs(pl, keep, kind):
        run = []
        for a, b, k in zip(pl[:-1], pl[1:], keep):
            if k:
                if not run:
                    run = [a]
                run.append(b)
                segs.append((a[0], a[1], b[0], b[1])); kinds.append(kind)
            elif run:
                lines.append(np.array(run)); run = []
        if run:
            lines.append(np.array(run))
    for l in F["coast"]:
        add_runs(l, region(l).tolist(), "kust")
    for rings in F["lakes"]:
        if lake_area(rings) <= SMA_SJO_M2:   # MB 7 kap. 13 a § (SFS 2025:512): insjöar ≤ 1 ha
            continue
        for r in rings:
            reg = region(r)
            keep = np.zeros(len(r) - 1, bool)
            if reg.any():
                A = np.column_stack([r[:-1], r[1:]])[reg]
                mid = (A[:, :2] + A[:, 2:]) / 2
                d = A[:, 2:] - A[:, :2]
                L = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)
                nrm = np.column_stack([-d[:, 1] / L, d[:, 0] / L])
                keep[reg] = ~(W.in_lake(mid + 0.5 * nrm) & W.in_lake(mid - 0.5 * nrm))
            add_runs(r, keep.tolist(), "sjo")
    for kind, l in F["waterlines"]:
        if kind not in VATTENDRAG_MED_SKYDD:     # MB 7 kap. 13 b §: vattendrag ≤ 2 m breda (OSM stream) räknas inte
            continue
        reg = region(l)
        keep = np.zeros(len(l) - 1, bool)
        if reg.any():
            mid = ((l[:-1] + l[1:]) / 2)[reg]
            keep[reg] = ~(W.in_lake(mid) | W.in_sea(mid))
        add_runs(l, keep.tolist(), "vattendrag")
    return (np.array(segs) if segs else np.zeros((0, 4))), kinds, lines


# ------------------------------------------------------------------ beräkning
def _num(a, key, lo_hi, namn, req=True):
    v = a.get(key)
    if v is None:
        if req:
            raise Avvisad("matt_saknas", f"{namn}: {key} saknas.")
        return None
    v = float(v)
    lo, hi = lo_hi
    if not (lo <= v <= hi):
        raise Avvisad("matt_utanfor_granser", f"{namn}: {key} = {fnum(v)} m ligger utanför verktygets gränser "
                                               f"{fnum(lo)}–{fnum(hi)} m. Det här fallet kan verktyget inte göra ett underlag för.")
    return v


def point_of(a, E0=None, N0=None):
    if "e" in a and "n" in a:
        la, lo = K.from_sweref(float(a["e"]), float(a["n"]))
        return float(la), float(lo)
    if "lat" in a and "lon" in a:
        return float(a["lat"]), float(a["lon"])
    raise Avvisad("koordinat_saknas", "Koordinat saknas för en åtgärd (lat/lon eller e/n i SWEREF 99 TM).")


_LANDER = None


def in_sweden(lat, lon):
    """Natural Earth 1:50m (public domain). Sverige om punkten ligger i Sveriges polygon, eller (skärgård, sjöar,
    öar som saknas i 1:50m) om den inte ligger i något annat land och närmaste land är Sverige inom 40 km (ytterskärgården)."""
    global _LANDER
    if not (55.0 <= lat <= 69.2 and 10.5 <= lon <= 24.3):
        return False
    if _LANDER is None:
        import gzip
        f = json.load(gzip.open(ROOT / "data" / "ne_50m_lander_varld.json.gz", "rt", encoding="utf-8"))["features"]
        _LANDER = []
        for x in f:
            rings = [np.array(r, float) for r in x["rings"]]
            allp = np.vstack(rings)
            if allp[:, 0].max() < 3 or allp[:, 0].min() > 32 or allp[:, 1].max() < 50 or allp[:, 1].min() > 72:
                continue
            _LANDER.append((x["iso"], rings))
    P = np.array([[lon, lat]])
    kx = math.cos(math.radians(lat))
    best = (1e9, None)
    for iso, rings in _LANDER:
        if sum(int(pip(P, r)[0]) for r in rings) % 2 == 1:
            return iso == "SE"
        S = np.vstack([np.column_stack([r[:-1], r[1:]]) for r in rings]) * np.array([kx, 1, kx, 1]) * 111.2
        d, _ = seg_dist(np.array([[lon * kx * 111.2, lat * 111.2]]), S)
        if d[0] < best[0]:
            best = (float(d[0]), iso)
    return best[1] == "SE" and best[0] <= 40.0


def compute(order):
    t0 = time.perf_counter()
    atg_in = order.get("atgarder") or []
    if not 1 <= len(atg_in) <= 3:
        raise Avvisad("antal_atgarder", "Ange en till tre åtgärder.")
    lat0, lon0 = point_of(atg_in[0])
    if not in_sweden(lat0, lon0):
        raise Avvisad("utanfor_sverige", "Koordinaten ligger inte i Sverige.")
    E0, N0 = (float(v) for v in K.to_sweref(lat0, lon0))
    E0, N0 = round(E0, 1), round(N0, 1)
    spec = D.spec_for_point(lat0, lon0)
    osm, ep = D.fetch(spec)
    t_data = time.perf_counter() - t0
    F = parse(osm, E0, N0)
    W = Vatten(F, DATA_HALF_M * 0.94)
    S, kinds, S_lines = shoreline(F, W)
    if len(S) == 0:
        raise Avvisad("ingen_strandlinje", "Ingen strandlinje finns i kartdatan nära platsen.")
    # --- åtgärder
    atg = []
    for i, a in enumerate(atg_in):
        typ = a.get("typ")
        if typ not in TYPER:
            raise Avvisad("okand_atgard", f"Okänd åtgärd '{typ}'. Verktyget hanterar brygga, bod och altan.")
        T = TYPER[typ]
        la, lo = point_of(a)
        e, n = (float(v) for v in K.to_sweref(la, lo))
        p = np.array([e - E0, n - N0])
        L = _num(a, "langd", T["langd"], T["namn"]); B = _num(a, "bredd", T["bredd"], T["namn"])
        rec = {"typ": typ, "namn": T["namn"], "langd": L, "bredd": B, "indata_punkt": [round(float(p[0]), 2), round(float(p[1]), 2)]}
        if typ == "brygga":
            d0, q0 = seg_dist(p, S)
            if d0[0] > SNAP_BRYGGA_M:
                raise Avvisad("brygga_ej_vid_strand", f"Bryggans landfäste ligger {fnum(d0[0])} m från närmaste strandlinje i kartdatan "
                                                       f"(högst {fnum(SNAP_BRYGGA_M, 0)} m). Ange landfästets läge vid stranden.")
            q = q0[0]
            if a.get("riktning") is not None:
                th = float(a["riktning"]) % 360
            else:  # ut från stranden mot vattnet: medelriktningen till vattenpunkter på en cirkel (r = 6 m) runt landfästet
                angs = np.radians(np.arange(0, 360, 5))
                U = np.column_stack([np.sin(angs), np.cos(angs)])
                wat = W.in_water(q + 6 * U)
                if wat.all() or not wat.any():
                    raise Avvisad("riktning_oklar", "Bryggans riktning kan inte bestämmas ur kartdatan. Ange riktning i grader.")
                m = U[wat].sum(0)
                th = math.degrees(math.atan2(m[0], m[1])) % 360
            th = round(th, 1)
            u = np.array([math.sin(math.radians(th)), math.cos(math.radians(th))])
            q = np.round(q, 2)
            c = q + u * L / 2
            poly = rect(c[0], c[1], L, B, th)
            end = q + u * L
            if not W.in_water(end[None, :])[0]:
                raise Avvisad("brygga_nar_ej_vatten", "Bryggans yttre ände hamnar inte i vatten enligt kartdatan. Kontrollera läge och riktning.")
            hv = _num(a, "hojd_over_vatten", (0.1, 2.5), "Brygga")
            dy = _num(a, "vattendjup_yttre", (0.2, 8.0), "Brygga")
            di = _num(a, "vattendjup_inre", (0.0, 8.0), "Brygga", req=False) or 0.0
            fo = a.get("forankring") or "stolpar"
            if fo not in FORANKRING:
                raise Avvisad("forankring_okand", "Förankring ska vara stolpar, pontoner eller stenkistor.")
            rec.update(riktning=th, landfaste=[float(q[0]), float(q[1])], hojd_over_vatten=hv, vattendjup_yttre=dy,
                       vattendjup_inre=di, forankring=fo, material=str(a.get("material") or "Trä")[:60],
                       snap_m=round(float(d0[0]), 2))
        else:
            H = _num(a, "hojd", T["hojd"], T["namn"])
            th = float(a.get("riktning") or 0.0) % 360
            poly = rect(p[0], p[1], L, B, th)
            if W.in_water(densify(poly, 0.5)).any():
                raise Avvisad("atgard_i_vatten", f"{T['namn']} hamnar helt eller delvis i vatten enligt kartdatan. Kontrollera läget.")
            rec.update(riktning=round(th, 1), hojd=H)
        if FEL == "fel_atgard_lage" and i == 0:
            poly = poly + np.array([10.0, 0.0])
        rec["poly"] = np.round(poly, 3).tolist()
        rec["area"] = round(L * B, 2)
        dmin = distance_poly(poly, S)
        rec["avstand_strandlinje"] = round(dmin, 1)
        if typ != "brygga" and dmin > MAX_AVSTAND_M:
            raise Avvisad("utanfor_strandskydd", f"{T['namn']} ligger {fnum(dmin)} m från närmaste strandlinje i kartdatan, alltså "
                                                  f"längre bort än 300 m. Strandskyddet berörs troligen inte. Kontrollera med kommunen.")
        atg.append(rec)
    # --- tomtplats
    tomt, tomt_kalla = None, None
    if order.get("tomtplats"):
        tp = [point_of({"lat": c[0], "lon": c[1]}) for c in order["tomtplats"]]
        tomt = ring_xy([(lo, la) for la, lo in tp], E0, N0)
        if np.hypot(*(tomt[0] - tomt[-1])) > 0.01:
            tomt = np.vstack([tomt, tomt[:1]])
        tomt_kalla = "sökandens uppgift"
    else:
        tomt = propose_tomt(atg, F, W, S)
        tomt_kalla = "förslag från verktyget" if tomt is not None else None
    tomtinfo = None
    if tomt is not None:
        tomt = np.round(tomt, 2)
        sides = [float(np.hypot(*(b - a))) for a, b in zip(tomt[:-1], tomt[1:])]
        tomtinfo = {"poly": tomt.tolist(), "area": round(shoelace(tomt), 1), "sidor": [round(s, 1) for s in sides],
                    "fri_passage": round(distance_poly(tomt, S), 1), "kalla": tomt_kalla}
    # --- skala och utsnitt
    req = [np.array(a["poly"]) for a in atg] + ([tomt] if tomt is not None else [])
    near = []
    for a in atg:
        _, nq = seg_dist(np.array(a["poly"]), S)
        dd, _ = seg_dist(np.array(a["poly"]), S)
        near.append(nq[int(np.argmin(dd))])
    allp = np.vstack(req + [np.array(near)])
    bb = allp.min(0), allp.max(0)
    ctr = np.round((bb[0] + bb[1]) / 2, 0)
    chosen = None
    for sc in DETAIL_SCALES:
        fw_m, fh_m = MAPFRAME[2] / mm * sc / 1000, MAPFRAME[3] / mm * sc / 1000
        if (bb[1] - bb[0])[0] + 30 > fw_m or (bb[1] - bb[0])[1] + 30 > fh_m:
            continue
        box = (ctr[0] - fw_m / 2, ctr[1] - fh_m / 2, ctr[0] + fw_m / 2, ctr[1] + fh_m / 2)
        lines = buffer_lines(S, box, sc)
        pts = np.vstack([np.array(l) for l in lines]) if lines else np.zeros((0, 2))
        dsite = min((float(np.min(np.hypot(pts[:, 0] - a["poly"][0][0], pts[:, 1] - a["poly"][0][1]))) for a in atg), default=1e9) if len(pts) else 1e9
        if len(pts) and dsite <= 0.8 * min(fw_m, fh_m):
            chosen = (sc, box, lines); break
    if chosen is None:
        raise Avvisad("ryms_ej", "Åtgärderna och linjen 100 m från strandlinjen ryms inte på en detaljkarta i skala 1:2000.")
    sc, box, lines = chosen
    osm_ts = osm.get("osm3s", {}).get("timestamp_osm_base", "")
    lspec = D.lst_spec(E0, N0, 1200)
    lst = D.lst_fetch(lspec)
    lst_ytor = []
    for y in lst["ytor"]:
        rings = [np.array(r, float) - np.array([E0, N0]) for r in y["ringar"] if len(r) >= 4]
        if not rings:
            continue
        cover = []
        for a in atg:
            P = np.array(a["poly"])[:-1]
            par = np.zeros(len(P), int)
            for r in rings:
                par += pip(P, r)
            cover.append(bool(((par % 2) == 1).any()))
        lst_ytor.append({"lager": y["lager"], "typ": y["typ"], "kommunkod": y["kommunkod"], "ringar": rings,
                         "traffar_atgard": cover})
    return {"lst": {"spec": lspec, "hamtad": lst["hamtad"], "fel": lst["fel"], "ytor": lst_ytor},
            "streams_utelamnade": sum(1 for k, _ in F["waterlines"] if k not in VATTENDRAG_MED_SKYDD),
            "sma_sjoar_utelamnade": sum(1 for r in F["lakes"] if lake_area(r) <= SMA_SJO_M2),
            "E0": E0, "N0": N0, "lat0": lat0, "lon0": lon0, "F": F, "W": W, "S": S, "kinds": kinds, "S_lines": S_lines, "atg": atg,
            "tomt": tomtinfo, "scale": sc, "box": box, "buf_lines": lines, "spec": spec, "endpoint": ep,
            "osm_ts": osm_ts, "t_data": t_data, "t_compute": time.perf_counter() - t0}


def seg_dist_each(q, S):
    ax, ay, bx, by = S[:, 0], S[:, 1], S[:, 2], S[:, 3]
    dx, dy = bx - ax, by - ay
    t = np.clip(((q[0] - ax) * dx + (q[1] - ay) * dy) / np.maximum(dx * dx + dy * dy, 1e-12), 0, 1)
    return np.hypot(q[0] - (ax + t * dx), q[1] - (ay + t * dy))


def distance_poly(poly, S):
    """Minsta avstånd polygon–strandlinje (0 om strandlinjen korsar eller ligger inne i polygonen)."""
    P = densify(np.asarray(poly), 0.05)
    d, _ = seg_dist(P, S)
    dmin = float(d.min())
    ring = np.asarray(poly)
    ends = np.vstack([S[:, :2], S[:, 2:]])
    bb = ring.min(0), ring.max(0)
    sel = (ends[:, 0] >= bb[0][0]) & (ends[:, 0] <= bb[1][0]) & (ends[:, 1] >= bb[0][1]) & (ends[:, 1] <= bb[1][1])
    if sel.any() and pip(ends[sel], ring).any():
        return 0.0
    return 0.0 if dmin < 0.03 else dmin


def buffer_lines(S, box, sc):
    """Linjen STRANDSKYDD_M från strandlinjen inom rutan (marching squares på exakt avståndsfält via KD-träd)."""
    x0, y0, x1, y1 = box
    g = 1.0 if sc <= 1000 else 2.0
    xs = np.arange(x0, x1 + g / 2, g); ys = np.arange(y0, y1 + g / 2, g)
    m = STRANDSKYDD_M + 20
    sel = ~((np.maximum(S[:, 0], S[:, 2]) < x0 - m) | (np.minimum(S[:, 0], S[:, 2]) > x1 + m) |
            (np.maximum(S[:, 1], S[:, 3]) < y0 - m) | (np.minimum(S[:, 1], S[:, 3]) > y1 + m))
    Ss = S[sel]
    if len(Ss) == 0:
        return []
    pts = np.vstack([densify(np.array([[s[0], s[1]], [s[2], s[3]]]), 0.5) for s in Ss])
    tree = cKDTree(pts)
    GX, GY = np.meshgrid(xs, ys)
    dg, _ = tree.query(np.column_stack([GX.ravel(), GY.ravel()]))
    Dg = dg.reshape(GX.shape)
    level = STRANDSKYDD_M * (0.8 if FEL == "fel_buffert" else 1.0)
    lines = iso_lines(xs, ys, Dg, level)
    return [[(round(float(x), 3), round(float(y), 3)) for x, y in l] for l in lines if len(l) >= 2]


def propose_tomt(atg, F, W, S):
    """Förslag till tomtplats: rektangel runt åtgärderna på land och den befintliga byggnad som ligger närmast
    (inom 40 m), med 5 m marginal. Marginalen dras in där rektangeln hamnar i vatten eller närmare strandlinjen
    än FRI_PASSAGE_M (MB 7 kap. 18 i § om fri passage). Kärnan (åtgärder + byggnad) skärs aldrig av."""
    land = [np.array(a["poly"]).mean(0) for a in atg if a["typ"] != "brygga"]
    anchors = land or [np.array(a["landfaste"]) for a in atg if a["typ"] == "brygga"]
    core = [np.array(a["poly"]) for a in atg if a["typ"] != "brygga"]
    best = None
    for rings in F["buildings"]:
        r = rings[0]
        d = min(float(np.min(np.hypot(*(r - an).T))) for an in anchors)
        if d <= 40 and (best is None or d < best[0]):
            best = (d, r)
    if best is not None:
        core.append(best[1])
    if not core:
        return None
    A = np.vstack(core)
    cx0, cy0 = A.min(0); cx1, cy1 = A.max(0)
    x0, y0, x1, y1 = cx0 - 5, cy0 - 5, cx1 + 5, cy1 + 5
    for _ in range(40):
        R = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]])
        smp = densify(R, 0.5)
        d, _ = seg_dist(smp, S)
        bad = smp[W.in_water(smp) | (d < FRI_PASSAGE_M)]
        if not len(bad):
            break
        moved = False
        if np.any(np.abs(bad[:, 0] - x0) < 0.3) and x0 + 0.5 <= cx0:
            x0 += 0.5; moved = True
        if np.any(np.abs(bad[:, 0] - x1) < 0.3) and x1 - 0.5 >= cx1:
            x1 -= 0.5; moved = True
        if np.any(np.abs(bad[:, 1] - y0) < 0.3) and y0 + 0.5 <= cy0:
            y0 += 0.5; moved = True
        if np.any(np.abs(bad[:, 1] - y1) < 0.3) and y1 - 0.5 >= cy1:
            y1 -= 0.5; moved = True
        if not moved:
            break
    R = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]])
    if W.in_water(densify(R, 0.5)).any():
        return None
    return R


# ------------------------------------------------------------------ ritning
class Map:
    def __init__(self, frame, box, E0, N0):
        self.frame, self.box, self.E0, self.N0 = frame, box, E0, N0
        px, py, pw, ph = frame
        x0, y0, x1, y1 = box
        self.sx = pw / (x1 - x0); self.sy = ph / (y1 - y0)

    def tp(self, x, y):
        px, py, pw, ph = self.frame
        return px + (x - self.box[0]) * self.sx, py + (y - self.box[1]) * self.sy

    def path(self, c, pts, close=False):
        p = c.beginPath()
        xy = [self.tp(*q) for q in pts]
        p.moveTo(*xy[0])
        for q in xy[1:]:
            p.lineTo(*q)
        if close:
            p.close()
        return p

    def rings_path(self, c, rings):
        p = c.beginPath()
        for r in rings:
            xy = [self.tp(*q) for q in r]
            p.moveTo(*xy[0])
            for q in xy[1:]:
                p.lineTo(*q)
            p.close()
        return p

    def clip(self, c):
        px, py, pw, ph = self.frame
        cl = c.beginPath(); cl.rect(px, py, pw, ph); c.clipPath(cl, stroke=0, fill=0)

    def clipped(self, pts, margin=0.0):
        x0, y0, x1, y1 = self.box
        return O._clip_polyline([tuple(p) for p in pts], x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def sh_clip(ring, box):
    """Sutherland–Hodgman: klipp en ring mot rutan (för ritning; jämn-udda-fyllningen inne i rutan bevaras)."""
    x0, y0, x1, y1 = box
    P = np.asarray(ring, float)
    bb0, bb1 = P.min(0), P.max(0)
    if bb0[0] >= x0 and bb0[1] >= y0 and bb1[0] <= x1 and bb1[1] <= y1:
        return P
    if bb1[0] < x0 or bb0[0] > x1 or bb1[1] < y0 or bb0[1] > y1:
        return None
    for axis, lim, keep_ge in ((0, x0, True), (0, x1, False), (1, y0, True), (1, y1, False)):
        if len(P) < 3:
            return None
        Q = np.roll(P, -1, axis=0)
        ins_p = P[:, axis] >= lim if keep_ge else P[:, axis] <= lim
        ins_q = Q[:, axis] >= lim if keep_ge else Q[:, axis] <= lim
        out = []
        for p, q, ip, iq in zip(P, Q, ins_p, ins_q):
            if ip:
                out.append(p)
            if ip != iq:
                t = (lim - p[axis]) / (q[axis] - p[axis])
                out.append(p + t * (q - p))
        P = np.array(out) if out else np.zeros((0, 2))
    if len(P) < 3:
        return None
    return np.vstack([P, P[:1]])


def draw_base(c, M, R, detail):
    F, W = R["F"], R["W"]
    x0, y0, x1, y1 = M.box
    c.saveState(); M.clip(c)
    px, py, pw, ph = M.frame
    coast = [[tuple(p) for p in l] for l in F["coast"]]
    mx = (x1 - x0) * 0.03
    land, clakes, has = O.coast_land_polygons(coast, x0 - mx, y0 - mx, x1 + mx, y1 + mx) if coast else ([], [], False)
    c.setFillColorRGB(*(C_WATER if has else (1, 1, 1))); c.rect(px, py, pw, ph, stroke=0, fill=1)
    if has:
        c.setFillColorRGB(1, 1, 1)
        for poly in land:
            c.drawPath(M.path(c, poly, close=True), stroke=0, fill=1)
        c.setFillColorRGB(*C_WATER)
        for poly in clakes:
            c.drawPath(M.path(c, poly, close=True), stroke=0, fill=1)
    c.setFillColorRGB(*C_WATER)
    cb = (x0 - mx, y0 - mx, x1 + mx, y1 + mx)
    for rings in F["lakes"]:
        cr = [r for r in (sh_clip(r, cb) for r in rings) if r is not None]
        if cr:
            c.drawPath(M.rings_path(c, cr), stroke=0, fill=1, fillMode=0)
    # vägar
    c.setLineCap(1); c.setLineJoin(1)
    for cls, l in F["roads"]:
        big = cls in ("motorway", "trunk", "primary", "secondary", "tertiary")
        if not detail and cls in ("footway", "path", "cycleway", "steps", "track", "service"):
            continue
        c.setStrokeColorRGB(*C_ROAD)
        c.setLineWidth((3.0 if big else 1.8) if not detail else (5 if big else 3) * M.sx / 2.83)
        for pc in M.clipped(l, 5):
            c.drawPath(M.path(c, pc), stroke=1, fill=0)
    # byggnader
    c.setFillColorRGB(*C_BUILD); c.setStrokeColorRGB(0.3, 0.3, 0.3); c.setLineWidth(0.3 if detail else 0.1)
    for rings in F["buildings"]:
        r0 = rings[0]
        if r0[:, 0].max() < x0 or r0[:, 0].min() > x1 or r0[:, 1].max() < y0 or r0[:, 1].min() > y1:
            continue
        c.drawPath(M.rings_path(c, rings), stroke=1 if detail else 0, fill=1, fillMode=0)
    # befintliga bryggor
    c.setStrokeColorRGB(*C_PIER); c.setFillColorRGB(*C_PIER)
    for l, closed in F["piers"]:
        if closed:
            c.drawPath(M.path(c, l, close=True), stroke=0, fill=1)
        else:
            c.setLineWidth(max(1.0, 2.0 * M.sx))
            for pc in M.clipped(l, 5):
                c.drawPath(M.path(c, pc), stroke=1, fill=0)
    # strandlinje (exakt den som avstånden räknas mot)
    S = R["S"]
    shift = np.array([8.0, 0.0]) if (FEL == "forskjuten_strandlinje" and detail) else np.zeros(2)
    c.setStrokeColorRGB(*C_SHORE); c.setLineWidth(1.3 if detail else 0.6)
    for l in R["S_lines"]:
        for pc in M.clipped(np.array(l) + shift, 0):
            c.drawPath(M.path(c, pc), stroke=1, fill=0)
    c.restoreState()


def scalebar(c, x, y, scale, length_m, label="Skalstock"):
    pt_per_m = 72 / 25.4 * 1000 / scale
    if FEL == "fel_skalstock":
        pt_per_m *= 1.2
    n = 4
    seg = length_m / n
    c.setFont("KSans", 7.5); c.setFillColorRGB(*C_INK); c.drawString(x, y + 14, label)
    for k in range(n):
        c.setFillColorRGB(*((0, 0, 0) if k % 2 == 0 else (1, 1, 1)))
        c.setStrokeColorRGB(0, 0, 0); c.setLineWidth(0.5)
        c.rect(x + k * seg * pt_per_m, y, seg * pt_per_m, 3.5, stroke=1, fill=1)
    c.setFillColorRGB(*C_INK); c.setFont("KSans", 7)
    for k in range(n + 1):
        v = k * seg
        s = f"{v:g}".replace(".", ",") + (" m" if k == n else "")
        xx = x + k * seg * pt_per_m
        if k == n:
            c.drawString(xx - c.stringWidth(f"{v:g}".replace(".", ","), "KSans", 7) / 2, y - 9, s)
        else:
            c.drawCentredString(xx, y - 9, s)


def north_arrow(c, x, y, size=11 * mm):
    if FEL == "saknad_norrpil":
        return
    c.setFillColorRGB(*C_INK); c.setStrokeColorRGB(*C_INK); c.setLineWidth(0.6)
    p = c.beginPath(); p.moveTo(x, y + size); p.lineTo(x - size * 0.3, y); p.lineTo(x, y + size * 0.28); p.close()
    c.drawPath(p, stroke=1, fill=1)
    p = c.beginPath(); p.moveTo(x, y + size); p.lineTo(x + size * 0.3, y); p.lineTo(x, y + size * 0.28); p.close()
    c.setFillColorRGB(1, 1, 1); c.drawPath(p, stroke=1, fill=1)
    c.setFillColorRGB(*C_INK); c.setFont("KSans", 12); c.drawCentredString(x, y + size + 3, "N")


def footer(c, order, page_no, n_pages):
    W, H = PAGE
    c.setFillColorRGB(*C_INK); c.setFont("KSans", 7)
    if FEL != "saknad_sidfot":
        if order.get("version") == "slutversion":
            b = order.get("bekraftelse") or {}
            txt = (f"Underlag framtaget med {VARUMARKE}. Granskat och godtaget av {b.get('namn', '')} {b.get('datum', '')}. "
                   f"{VARUMARKE} är inte sökande eller upprättare.")
        else:
            txt = (f"Förhandsvisning framtagen med {VARUMARKE}. Sökanden ser över underlaget och ansvarar för uppgifterna i ansökan. "
                   f"{VARUMARKE} är inte sökande eller upprättare.")
        c.drawString(12 * mm, 10 * mm, txt)
    c.drawRightString(W - 12 * mm, 10 * mm, f"Sida {page_no} av {n_pages}")
    if order.get("version") != "slutversion":
        c.saveState()
        c.setFillColorRGB(0.6, 0.6, 0.6); c.setFillAlpha(0.35); c.setFont("KSans", 54)
        c.translate(W / 2, H / 2); c.rotate(28)
        c.drawCentredString(0, 0, "UTKAST – förhandsvisning")
        c.restoreState()


def header(c, title, sub):
    c.setFillColorRGB(*C_INK); c.setFont("KSans", 15); c.drawString(12 * mm, PAGE[1] - 10 * mm, title)
    c.setFont("KSans", 8.5); c.drawRightString(PAGE[0] - 12 * mm, PAGE[1] - 10 * mm, sub)


def grid_ticks(c, M, step):
    x0, y0, x1, y1 = M.box
    px, py, pw, ph = M.frame
    c.setFont("KSans", 6); c.setFillColorRGB(*C_INK); c.setStrokeColorRGB(*C_INK); c.setLineWidth(0.4)
    E0, N0 = M.E0, M.N0
    e = math.ceil((x0 + E0) / step) * step
    while e - E0 <= x1:
        X, _ = M.tp(e - E0, 0)
        c.line(X, py, X, py - 2 * mm); c.line(X, py + ph, X, py + ph + 1.5 * mm)
        c.drawCentredString(X, py - 5 * mm, f"{int(e)}")
        e += step
    n = math.ceil((y0 + N0) / step) * step
    while n - N0 <= y1:
        _, Y = M.tp(0, n - N0)
        c.line(px, Y, px - 1.5 * mm, Y); c.line(px + pw, Y, px + pw + 1.5 * mm, Y)
        c.saveState(); c.translate(px - 2.5 * mm, Y); c.rotate(90); c.drawCentredString(0, 0, f"{int(n)}"); c.restoreState()
        n += step


def legend(c, x, y, items):
    c.setFont("KSans", 7.5)
    for kind, col, text in items:
        c.saveState()
        if kind == "line":
            c.setStrokeColorRGB(*col); c.setLineWidth(1.4); c.line(x, y + 2.5, x + 9 * mm, y + 2.5)
        elif kind == "dash":
            c.setStrokeColorRGB(*col); c.setLineWidth(1.4); c.setDash(5, 2.5); c.line(x, y + 2.5, x + 9 * mm, y + 2.5)
        else:
            c.setFillColorRGB(*col); c.setStrokeColorRGB(0.3, 0.3, 0.3); c.setLineWidth(0.3)
            c.rect(x, y, 9 * mm, 5, stroke=1, fill=1)
        c.restoreState()
        c.setFillColorRGB(*C_INK); c.drawString(x + 11 * mm, y + 0.5, text)
        y -= 4.4 * mm
    return y


def label_on_edge(c, M, a, b, text, off=2.2, size=7):
    (X1, Y1), (X2, Y2) = M.tp(*a), M.tp(*b)
    ang = math.degrees(math.atan2(Y2 - Y1, X2 - X1))
    if ang > 90 or ang < -90:
        ang += 180
    mx, my = (X1 + X2) / 2, (Y1 + Y2) / 2
    nx, ny = -(Y2 - Y1), (X2 - X1)
    ln = math.hypot(nx, ny) or 1
    c.saveState(); c.translate(mx + nx / ln * off * mm, my + ny / ln * off * mm); c.rotate(ang)
    c.setFont("KSans", size); c.setFillColorRGB(*C_INK)
    tw = c.stringWidth(text, "KSans", size)
    c.setFillColorRGB(1, 1, 1); c.rect(-tw / 2 - 1, -1.8, tw + 2, size + 1, stroke=0, fill=1)
    c.setFillColorRGB(*C_INK); c.drawCentredString(0, 0, text)
    c.restoreState()


def page_overview(c, order, R, n_pages):
    header(c, "Kartunderlag till ansökan om strandskyddsdispens", f"{order.get('fastighet', '')} · {order.get('kommun', '')} kommun")
    sc = OV_SCALE
    fw_m, fh_m = OV_FRAME[2] / mm * sc / 1000, OV_FRAME[3] / mm * sc / 1000
    box = (-fw_m / 2, -fh_m / 2, fw_m / 2, fh_m / 2)
    M = Map(OV_FRAME, box, R["E0"], R["N0"])
    draw_base(c, M, R, detail=False)
    c.saveState(); M.clip(c)
    # ortnamn
    c.setFillColorRGB(0.25, 0.25, 0.25)
    for kind, name, x, y in R["F"]["places"]:
        if box[0] < x < box[2] and box[1] < y < box[3]:
            c.setFont("KSans", 8 if kind in ("village", "town", "suburb", "island") else 6.5)
            X, Y = M.tp(x, y); c.drawCentredString(X, Y, name)
    if FEL != "saknad_oversiktsmarkering":
        X, Y = M.tp(0, 0)
        c.setStrokeColorRGB(0.85, 0, 0); c.setLineWidth(1.6); c.circle(X, Y, 6 * mm, stroke=1, fill=0)
        c.setFillColorRGB(0.85, 0, 0); c.circle(X, Y, 1.1 * mm, stroke=0, fill=1)
        bx = R["box"]
        c.setLineWidth(0.7); c.setDash(3, 2)
        (a1, b1), (a2, b2) = M.tp(bx[0], bx[1]), M.tp(bx[2], bx[3])
        c.rect(a1, b1, a2 - a1, b2 - b1, stroke=1, fill=0); c.setDash()
    c.restoreState()
    px, py, pw, ph = OV_FRAME
    c.setStrokeColorRGB(*C_INK); c.setLineWidth(0.8); c.rect(px, py, pw, ph, stroke=1, fill=0)
    x = OV_COL_X; y = PAGE[1] - 24 * mm
    c.setFont("KSans", 11); c.setFillColorRGB(*C_INK); c.drawString(x, y, "Översiktskarta"); y -= 5 * mm
    c.setFont("KSans", 8.5); c.drawString(x, y, f"Skala 1:{sc:,} vid utskrift i A3".replace(",", " ")); y -= 4.5 * mm
    c.drawString(x, y, "Röd ring: platsen för åtgärderna. Streckad ruta: detaljkartans utsnitt."); y -= 8 * mm
    c.setFont("KSans", 11); c.drawString(x, y, "Uppgifter från sökanden"); y -= 5.5 * mm
    rows = [("Sökande", (order.get("sokande") or {}).get("namn", "")), ("Fastighet", order.get("fastighet", "")),
            ("Kommun", order.get("kommun", "")),
            ("Läge (SWEREF 99 TM)", f"E {fnum(R['E0'], 0)}  N {fnum(R['N0'], 0)}"),
            ("Läge (WGS 84)", f"{R['lat0']:.5f}° N  {R['lon0']:.5f}° E")]
    c.setFont("KSans", 8.5)
    for k, v in rows:
        c.setFillColorRGB(0.35, 0.35, 0.35); c.drawString(x, y, k); c.setFillColorRGB(*C_INK); c.drawString(x + 38 * mm, y, str(v)); y -= 4.6 * mm
    y -= 4 * mm
    c.setFont("KSans", 11); c.drawString(x, y, "Åtgärder"); y -= 5.5 * mm
    c.setFont("KSans", 8)
    heads = [("Åtgärd", 0), ("Mått (m)", 26), ("Area", 62), ("Avstånd till strandlinjen", 90)]
    for h, dx in heads:
        c.setFillColorRGB(0.35, 0.35, 0.35); c.drawString(x + dx * mm, y, h)
    y -= 4.6 * mm
    for i, a in enumerate(R["atg"], 1):
        c.setFillColorRGB(*C_INK)
        c.drawString(x, y, f"{i}. {a['namn']}")
        c.drawString(x + 26 * mm, y, f"{fnum(a['langd'])} × {fnum(a['bredd'])}")
        c.drawString(x + 62 * mm, y, f"{fnum(area_label(a))} m²")
        c.drawString(x + 90 * mm, y, avst_text(a))
        y -= 4.6 * mm
    y -= 3 * mm
    T = R["tomt"]
    c.setFont("KSans", 11); c.drawString(x, y, "Föreslagen tomtplats"); y -= 5.5 * mm
    c.setFont("KSans", 8.5)
    if T:
        c.drawString(x, y, f"Area {fnum(T['area'])} m² ({T['kalla']}). Kortaste avstånd till strandlinjen {fnum(T['fri_passage'])} m."); y -= 4.6 * mm
    else:
        c.drawString(x, y, "Ingen tomtplats föreslagen. Ange tomtplatsen i beställningen."); y -= 4.6 * mm
    y -= 4 * mm
    c.setFont("KSans", 11); c.drawString(x, y, "Innehåll"); y -= 5.5 * mm
    c.setFont("KSans", 8.5)
    toc = ["1. Sammanställning och översiktskarta", f"2. Detaljkarta 1:{R['scale']} med strandlinje, 100 m-linje, åtgärder och tomtplats"]
    if any(a["typ"] == "brygga" for a in R["atg"]):
        toc.append("3. Bryggritning i plan och sektion")
    toc.append(f"{len(toc) + 1}. Regelkontroll, reservationer, källor och licenser")
    for t in toc:
        c.drawString(x, y, t); y -= 4.6 * mm
    y -= 4 * mm
    y = KL.para(c, "Underlaget är gjort för att bifogas en ansökan som sökanden själv lämnar in. Kommunen eller "
                   "länsstyrelsen prövar ansökan och beslutar. Läs reservationerna på sista sidan.", x, y, OV_COL_W - 4 * mm, size=8.5)
    north_arrow(c, px + pw - 12 * mm, py + ph - 20 * mm)
    c.setFillColorRGB(1, 1, 1); c.rect(px + 4 * mm, py + 4 * mm, 62 * mm, 14 * mm, stroke=0, fill=1)
    scalebar(c, px + 8 * mm, py + 10 * mm, sc, 500)
    c.setFont("KSans", 6.5); c.setFillColorRGB(*C_INK)
    c.drawString(px, py - 5 * mm, "Kartdata © OpenStreetMap contributors, Open Database License (ODbL). Koordinatsystem SWEREF 99 TM.")
    footer(c, order, 1, n_pages)
    c.showPage()


def area_label(a):
    v = a["area"]
    return v * 1.1 if FEL == "fel_area" else v


def avst_text(a):
    d = a["avstand_strandlinje"] + (5.0 if FEL == "fel_avstand" else 0.0)
    if a["typ"] == "brygga" and d == 0:
        return "0 m (går ut i vattnet)"
    return f"{fnum(d)} m"


def page_detail(c, order, R, page_no, n_pages):
    sc = R["scale"]
    header(c, f"Detaljkarta – strandskydd, skala 1:{sc}", f"{order.get('fastighet', '')} · {order.get('kommun', '')} kommun")
    box = R["box"]
    draw_scale = sc * (1.25 if FEL == "fel_skala" else 1.0)
    if draw_scale != sc:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        hw, hh = (box[2] - box[0]) / 2 * 1.25, (box[3] - box[1]) / 2 * 1.25
        dbox = (cx - hw, cy - hh, cx + hw, cy + hh)
    else:
        dbox = box
    M = Map(MAPFRAME, dbox, R["E0"], R["N0"])
    draw_base(c, M, R, detail=True)
    c.saveState(); M.clip(c)
    # 100 m-linjen
    c.setStrokeColorRGB(*C_BUF); c.setLineWidth(1.6); c.setDash(7, 3)
    for l in R["buf_lines"]:
        c.drawPath(M.path(c, l), stroke=1, fill=0)
    c.setDash()
    # Länsstyrelsernas strandskyddsytor (CC0)
    c.setStrokeColorRGB(*C_LST); c.setLineWidth(1.1); c.setDash(2, 2)
    for yy in R["lst"]["ytor"]:
        cr = [r for r in (sh_clip(r, (box[0] - 20, box[1] - 20, box[2] + 20, box[3] + 20)) for r in yy["ringar"]) if r is not None]
        if cr:
            c.drawPath(M.rings_path(c, cr), stroke=1, fill=0)
        r0 = yy["ringar"][0]
        inside = r0[(r0[:, 0] > box[0]) & (r0[:, 0] < box[2]) & (r0[:, 1] > box[1]) & (r0[:, 1] < box[3])]
        if len(inside):
            X, Y = M.tp(*inside[len(inside) // 2])
            c.setFont("KSans", 6.5); c.setFillColorRGB(*C_LST); c.drawString(X + 2, Y + 2, f"Länsstyrelsen: {yy['typ']}")
    c.setDash()
    # tomtplats
    T = R["tomt"]
    if T and FEL != "saknad_tomtplats":
        c.setStrokeColorRGB(*C_TOMT); c.setLineWidth(1.3); c.setDash(4, 2)
        c.drawPath(M.path(c, T["poly"], close=True), stroke=1, fill=0); c.setDash()
        for (a, b), s in zip(zip(T["poly"][:-1], T["poly"][1:]), T["sidor"]):
            label_on_edge(c, M, a, b, f"{fnum(s)} m", off=-2.6, size=6.5)
    # åtgärder
    for i, a in enumerate(R["atg"], 1):
        P = a["poly"]
        c.setFillColorRGB(*C_ATG); c.setStrokeColorRGB(0, 0, 0); c.setLineWidth(0.6)
        c.drawPath(M.path(c, P, close=True), stroke=1, fill=1)
        label_on_edge(c, M, P[0], P[1], f"{fnum(a['langd'])} m", off=2.4)
        label_on_edge(c, M, P[1], P[2], f"{fnum(a['bredd'])} m", off=2.4)
        # avståndsmått
        Pd = densify(np.array(P), 0.05)
        d, nq = seg_dist(Pd, R["S"])
        k = int(np.argmin(d))
        if a["avstand_strandlinje"] > 0.5:
            (X1, Y1), (X2, Y2) = M.tp(*Pd[k]), M.tp(*nq[k])
            c.setStrokeColorRGB(*C_INK); c.setLineWidth(0.5); c.setDash(1.5, 1.2); c.line(X1, Y1, X2, Y2); c.setDash()
            label_on_edge(c, M, Pd[k], nq[k], avst_text(a), off=2.0, size=6.5)
        cx, cy = M.tp(*np.array(P[:-1]).mean(0))
        c.setFont("KSans", 8); c.setFillColorRGB(*C_INK)
        c.drawString(cx + 5 * mm, cy + 4 * mm, f"{i}. {a['namn']} {fnum(area_label(a))} m²")
    c.restoreState()
    px, py, pw, ph = MAPFRAME
    c.setStrokeColorRGB(*C_INK); c.setLineWidth(0.8); c.rect(px, py, pw, ph, stroke=1, fill=0)
    grid_ticks(c, M, 50 if sc <= 1000 else 100)
    # rubrikfält
    x = COL_X; y = PAGE[1] - 24 * mm
    c.setFont("KSans", 11); c.setFillColorRGB(*C_INK); c.drawString(x, y, "Detaljkarta"); y -= 5 * mm
    c.setFont("KSans", 8.5); c.drawString(x, y, f"Skala 1:{sc} vid utskrift i A3"); y -= 4.5 * mm
    c.drawString(x, y, "Koordinatsystem SWEREF 99 TM, rutnät i meter"); y -= 4.5 * mm
    c.drawString(x, y, "Norrpilen visar rutnätsnorr (SWEREF 99 TM)"); y -= 8 * mm
    north_arrow(c, x + 8 * mm, y - 12 * mm)
    scalebar(c, x + 24 * mm, y - 8 * mm, sc, 50 if sc <= 1000 else 100)
    y -= 22 * mm
    c.setFont("KSans", 10); c.drawString(x, y, "Teckenförklaring"); y -= 5 * mm
    y = legend(c, x, y, [("line", C_SHORE, "Strandlinje (OpenStreetMap)"),
                         ("dash", C_BUF, "100 m från strandlinjen (generellt strandskydd)"),
                         ("dash", C_TOMT, "Föreslagen tomtplatsavgränsning"),
                         ("fill", C_ATG, "Planerad åtgärd"),
                         ("dash", C_LST, "Länsstyrelsernas strandskyddsyta (om sådan finns)"),
                         ("fill", C_BUILD, "Befintlig byggnad (OpenStreetMap)"),
                         ("fill", C_PIER, "Befintlig brygga (OpenStreetMap)"),
                         ("fill", C_WATER, "Vatten")])
    y -= 3 * mm
    c.setFont("KSans", 10); c.drawString(x, y, "Åtgärder"); y -= 5 * mm
    for i, a in enumerate(R["atg"], 1):
        c.setFont("KSans", 8)
        y = KL.para(c, f"{i}. {a['namn']}: {fnum(a['langd'])} × {fnum(a['bredd'])} m, area {fnum(area_label(a))} m², "
                       f"avstånd till strandlinjen {avst_text(a)}.", x, y, COL_W - 2 * mm, size=8)
        y -= 1 * mm
    if T:
        y = KL.para(c, f"Tomtplats ({T['kalla']}): area {fnum(T['area'])} m², kortaste avstånd till strandlinjen "
                       f"{fnum(T['fri_passage'])} m.", x, y, COL_W - 2 * mm, size=8)
    y -= 2 * mm
    y = KL.para(c, lst_text(R), x, y, COL_W - 2 * mm, size=7.5) - 1.5 * mm
    if FEL != "saknad_reservation":
        y = KL.para(c, "Reservation: linjen visar 100 m från strandlinjen enligt OpenStreetMap. Länsstyrelsen kan ha "
                       "utvidgat strandskyddet (upp till 300 m) och skyddet kan vara upphävt, till exempel i en detaljplan. "
                       "Kontrollera länsstyrelsens beslut och kommunens planer för platsen.", x, y, COL_W - 2 * mm, size=7.5)
    c.setFont("KSans", 6.5); c.setFillColorRGB(*C_INK)
    if FEL != "saknad_attribution":
        c.drawString(px, py - 9 * mm, f"Kartdata © OpenStreetMap contributors, Open Database License (ODbL), uttag {R['osm_ts'][:10]}. "
                                      "Åtgärder, tomtplats och 100 m-linje: beräknade av verktyget ur sökandens uppgifter.")
    footer(c, order, page_no, n_pages)
    c.showPage()
    return {"frame_pt": list(MAPFRAME), "box_m": list(box), "scale": sc}


def page_brygga(c, order, R, a, page_no, n_pages):
    header(c, "Bryggritning – plan och sektion", f"{order.get('fastighet', '')} · {order.get('kommun', '')} kommun")
    L, B, hv, dy, di = a["langd"], a["bredd"], a["hojd_over_vatten"], a["vattendjup_yttre"], a["vattendjup_inre"]
    sc = next(s for s in (100, 200) if L * 1000 / s <= 220)   # Värmdö: plan- och elevationsritning 1:100
    k = 72 / 25.4 * 1000 / sc   # punkter per meter
    Ld = L * (0.8 if FEL == "fel_bryggmatt" else 1.0)
    x0 = 60 * mm
    # ---- plan
    yp = 200 * mm
    c.setFont("KSans", 11); c.setFillColorRGB(*C_INK); c.drawString(x0 - 10 * mm, yp + B * k + 18 * mm, f"Plan 1:{sc}")
    c.setStrokeColorRGB(*C_SHORE); c.setLineWidth(1.3)
    c.line(x0, yp - 12 * mm, x0, yp + B * k + 12 * mm)
    c.setFont("KSans", 7); c.setFillColorRGB(*C_SHORE); c.drawString(x0 - 26 * mm, yp - 16 * mm, "Strandlinje / landfäste")
    c.setFillColorRGB(0.87, 0.75, 0.55); c.setStrokeColorRGB(*C_BR_PLAN); c.setLineWidth(1.0)
    c.rect(x0, yp, Ld * k, B * k, stroke=1, fill=1)
    c.setStrokeColorRGB(0.6, 0.45, 0.25); c.setLineWidth(0.3)
    step = 0.145
    xx = step
    while xx < Ld:
        c.line(x0 + xx * k, yp, x0 + xx * k, yp + B * k); xx += step
    dim_h(c, x0, x0 + Ld * k, yp - 7 * mm, f"Längd {fnum(L)} m")
    dim_v(c, x0 + Ld * k + 7 * mm, yp, yp + B * k, f"Bredd {fnum(B)} m")
    c.setFont("KSans", 7.5); c.setFillColorRGB(*C_INK)
    c.drawString(x0 + Ld * k + 7 * mm, yp - 4 * mm, f"Area {fnum(L * B)} m²")
    # ---- sektion
    ys = 95 * mm
    c.setFont("KSans", 11); c.drawString(x0 - 10 * mm, ys + (hv + 0.3) * k + 22 * mm, f"Sektion 1:{sc}")
    c.setStrokeColorRGB(*C_WL); c.setLineWidth(1.0)
    c.line(x0 - 15 * mm, ys, x0 + Ld * k + 20 * mm, ys)
    c.setFont("KSans", 7); c.setFillColorRGB(*C_WL); c.drawString(x0 + Ld * k + 21 * mm, ys - 1, "Vattenyta")
    # botten
    c.setStrokeColorRGB(*C_BOTTOM); c.setLineWidth(1.2)
    c.line(x0 - 15 * mm, ys - di * k + 15 * mm * 0.3, x0, ys - di * k)
    c.line(x0, ys - di * k, x0 + Ld * k, ys - dy * k)
    c.line(x0 + Ld * k, ys - dy * k, x0 + Ld * k + 20 * mm, ys - dy * k)
    c.setFont("KSans", 7); c.setFillColorRGB(*C_BOTTOM); c.drawString(x0 + Ld * k + 21 * mm, ys - dy * k - 1, "Botten")
    # däck
    t = 0.2
    c.setFillColorRGB(0.87, 0.75, 0.55); c.setStrokeColorRGB(*C_BR_SEK); c.setLineWidth(1.0)
    c.rect(x0, ys + hv * k, Ld * k, t * k, stroke=1, fill=1)
    fo = a["forankring"]
    c.setStrokeColorRGB(0.35, 0.25, 0.15); c.setLineWidth(1.0)
    if fo == "stolpar":
        n = max(2, int(math.ceil(L / 2.5)) + 1)
        for j in range(n):
            xx = x0 + (Ld * k) * j / (n - 1)
            depth = di + (dy - di) * j / (n - 1)
            c.line(xx, ys + hv * k, xx, ys - depth * k - 0.5 * k)
    elif fo == "pontoner":
        n = max(1, int(round(L / 3)))
        for j in range(n):
            cx = x0 + (Ld * k) * (j + 0.5) / n
            c.setFillColorRGB(0.75, 0.75, 0.75); c.rect(cx - 0.6 * k, ys - 0.3 * k, 1.2 * k, hv * k + 0.3 * k, stroke=1, fill=1)
            depth = di + (dy - di) * (j + 0.5) / n
            c.setDash(2, 1.5); c.line(cx, ys - 0.3 * k, cx + 0.8 * k, ys - depth * k); c.setDash()
            c.setFillColorRGB(0.4, 0.4, 0.4); c.rect(cx + 0.6 * k, ys - depth * k, 0.4 * k, 0.25 * k, stroke=0, fill=1)
    else:
        n = max(2, int(math.ceil(L / 4)) + 1)
        for j in range(n):
            xx = x0 + (Ld * k) * j / (n - 1)
            depth = di + (dy - di) * j / (n - 1)
            c.setFillColorRGB(0.6, 0.6, 0.6); c.rect(xx - 0.5 * k, ys - depth * k, 1.0 * k, (depth + hv) * k, stroke=1, fill=1)
    dim_v(c, x0 - 8 * mm, ys, ys + hv * k, f"{fnum(hv)} m över vattenytan", left=True)
    dim_v(c, x0 + Ld * k + 8 * mm, ys - dy * k, ys, f"Vattendjup {fnum(dy)} m")
    dim_h(c, x0, x0 + Ld * k, ys + (hv + t) * k + 7 * mm, f"Längd {fnum(L)} m")
    # rubrikfält
    x = COL_X; y = PAGE[1] - 24 * mm
    c.setFont("KSans", 11); c.setFillColorRGB(*C_INK); c.drawString(x, y, "Brygga – uppgifter från sökanden"); y -= 6 * mm
    rows = [("Längd", f"{fnum(L)} m"), ("Bredd", f"{fnum(B)} m"), ("Area", f"{fnum(L * B)} m²"),
            ("Höjd över vattenytan", f"{fnum(hv)} m"), ("Vattendjup vid landfästet", f"{fnum(di)} m"),
            ("Vattendjup vid yttre änden", f"{fnum(dy)} m"), ("Förankring", fo), ("Material", a["material"]),
            ("Riktning", f"{fnum(a['riktning'], 0)}° från rutnätsnorr")]
    c.setFont("KSans", 8.5)
    for kk, v in rows:
        c.setFillColorRGB(0.35, 0.35, 0.35); c.drawString(x, y, kk); c.setFillColorRGB(*C_INK); c.drawString(x + 44 * mm, y, v); y -= 4.6 * mm
    y -= 3 * mm
    y = KL.para(c, f"Förankring: {FORANKRING[fo]}.", x, y, COL_W - 2 * mm, size=8)
    y -= 2 * mm
    y = KL.para(c, "Botten är ritad som en rak linje mellan de angivna vattendjupen. Sökanden anger djupen; "
                   "verktyget har inte mätt dem. Samma skala gäller på längden och på höjden.", x, y, COL_W - 2 * mm, size=8)
    scalebar(c, x, y - 14 * mm, sc, 10 if sc <= 100 else 20)
    footer(c, order, page_no, n_pages)
    c.showPage()
    return {"skala": sc, "plan_origo_pt": [x0, yp], "sektion_vattenyta_pt": ys}


def dim_h(c, xa, xb, y, text):
    c.setStrokeColorRGB(*C_INK); c.setLineWidth(0.4)
    c.line(xa, y, xb, y); c.line(xa, y - 1.5 * mm, xa, y + 1.5 * mm); c.line(xb, y - 1.5 * mm, xb, y + 1.5 * mm)
    c.setFont("KSans", 7.5); c.setFillColorRGB(1, 1, 1)
    tw = c.stringWidth(text, "KSans", 7.5)
    c.rect((xa + xb) / 2 - tw / 2 - 1, y - 1.5, tw + 2, 8, stroke=0, fill=1)
    c.setFillColorRGB(*C_INK); c.drawCentredString((xa + xb) / 2, y - 0.5, text)


def dim_v(c, x, ya, yb, text, left=False):
    c.setStrokeColorRGB(*C_INK); c.setLineWidth(0.4)
    c.line(x, ya, x, yb); c.line(x - 1.5 * mm, ya, x + 1.5 * mm, ya); c.line(x - 1.5 * mm, yb, x + 1.5 * mm, yb)
    c.setFont("KSans", 7.5); c.setFillColorRGB(*C_INK)
    if left:
        c.drawRightString(x - 2 * mm, (ya + yb) / 2 - 2, text)
    else:
        c.drawString(x + 2 * mm, (ya + yb) / 2 - 2, text)


def regelkontroll(R, order):
    """Verktygets egen kontroll mot regeltabellen (strandskydd_regler.json). Visas på sista sidan."""
    res = []
    for r in REGLER["krav"]:
        rid = r["id"]
        if rid == "K-BRYGGRITNING" and not any(a["typ"] == "brygga" for a in R["atg"]):
            res.append((r, "ej aktuell", "ingen brygga")); continue
        if rid == "K-FRI-PASSAGE":
            T = R["tomt"]
            if not T:
                res.append((r, "uppgift saknas", "ingen tomtplats")); continue
            ok = T["fri_passage"] >= FRI_PASSAGE_M
            res.append((r, "uppfyllt" if ok else "avvikelse",
                        f"kortaste avstånd {fnum(T['fri_passage'])} m" + ("" if ok else f" (mindre än {fnum(FRI_PASSAGE_M, 0)} m)"))); continue
        if rid == "K-TOMTPLATS" and not R["tomt"]:
            res.append((r, "uppgift saknas", "ingen tomtplats föreslagen")); continue
        if rid == "K-FASTIGHETSGRANS":
            res.append((r, "uppgift saknas", "redovisas inte, se reservation")); continue
        if rid == "K-SKALA":
            ok = R["scale"] <= 1000
            res.append((r, "finns" if ok else "avvikelse", f"1:{R['scale']}" + ("" if ok else " (större utsnitt än 1:1000 behövdes)"))); continue
        if rid == "K-GRUNDKARTA":
            k = (r.get("kommuner") or {}).get(order.get("kommun", ""))
            res.append((r, "avvikelse" if k and k.startswith("avvikelse") else "kontrollera",
                        k or "underlaget bygger på öppna data; fråga kommunen om den kräver sin egen karta")); continue
        if rid == "K-FOTO":
            res.append((r, "sökanden", r.get("var", ""))); continue
        if rid == "K-VATTENVERKSAMHET":
            br = [a for a in R["atg"] if a["typ"] == "brygga"]
            if not br:
                res.append((r, "ej aktuell", "ingen brygga")); continue
            res.append((r, "kontrollera", f"bryggdäck {fnum(br[0]['area'])} m²; länsstyrelsen avgör om anmälan krävs")); continue
        res.append((r, "finns", r.get("var", "")))
    return res


def lst_text(R):
    L = R["lst"]
    if L["fel"]:
        return ("Länsstyrelsernas strandskyddsytor kunde inte hämtas. Uppgift om utvidgat eller upphävt strandskydd "
                "saknas därför i underlaget.")
    hit = [(i + 1, y["typ"]) for y in L["ytor"] for i, h in enumerate(y["traffar_atgard"]) if h]
    if hit:
        t = "; ".join(f"åtgärd {i} ligger inom en yta av typen \"{typ}\"" for i, typ in hit)
        return f"Länsstyrelsernas strandskyddsytor (hämtade {L['hamtad']}): {t}. Ytorna är inte juridiskt bindande."
    if L["ytor"]:
        return (f"Länsstyrelsernas strandskyddsytor (hämtade {L['hamtad']}): det finns ytor i närheten men ingen "
                "berör åtgärderna. Ytorna är inte juridiskt bindande.")
    return (f"Länsstyrelsernas strandskyddsytor (hämtade {L['hamtad']}) visar inga ytor här. Tjänsten täcker i dag "
            "bara vissa län, så det utesluter inte att strandskyddet är utvidgat eller upphävt.")


def page_rules(c, order, R, page_no, n_pages):
    header(c, "Regelkontroll, reservationer, källor och licenser", f"{order.get('fastighet', '')} · {order.get('kommun', '')} kommun")
    x = 12 * mm; y = PAGE[1] - 22 * mm
    c.setFont("KSans", 11); c.setFillColorRGB(*C_INK); c.drawString(x, y, "Regelkontroll"); y -= 5 * mm
    c.setFont("KSans", 7.5)
    c.drawString(x, y, "Verktyget jämför underlaget med kartkrav som kommuner och länsstyrelser anger på sina egna webbsidor och blanketter."); y -= 5 * mm
    cols = [0, 30, 118, 142]
    c.setFillColorRGB(0.35, 0.35, 0.35)
    for h, dx in zip(("Krav", "Beskrivning", "Resultat", "Detalj / källor"), cols):
        c.drawString(x + dx * mm, y, h)
    y -= 4.2 * mm
    c.setFillColorRGB(*C_INK)
    for r, status, det in regelkontroll(R, order):
        c.setFont("KSans", 7)
        c.drawString(x, y, r["id"].replace("K-", ""))
        y2 = KL.para(c, r["text"], x + 30 * mm, y, 86 * mm, size=7, leading=8.2)
        c.drawString(x + 118 * mm, y, status)
        src = ", ".join(r.get("kallor", []))
        y3 = KL.para(c, (det + " · " if det else "") + src, x + 142 * mm, y, 114 * mm, size=6.5, leading=7.6)
        y = min(y2, y3) - 1.2 * mm
    y -= 2 * mm
    c.setFont("KSans", 8); c.drawString(x, y, "Regelkontrollen jämför med tabellvärden. Den ersätter inte kontroll på plats.")
    y -= 7 * mm
    # reservationer
    xr = 272 * mm; yr = PAGE[1] - 22 * mm; wr = 136 * mm
    c.setFont("KSans", 11); c.drawString(xr, yr, "Reservationer"); yr -= 5.5 * mm
    res = []
    if FEL != "saknad_reservation":
        res.append("Strandskyddets utbredning: linjen på detaljkartan visar 100 m från strandlinjen enligt OpenStreetMap, "
                   "alltså det generella strandskyddet. Länsstyrelsen kan ha utvidgat strandskyddet till högst 300 m, och "
                   "strandskyddet kan vara upphävt, till exempel i en detaljplan. Kontrollera länsstyrelsens beslut och "
                   "kommunens planer för platsen innan ansökan lämnas in.")
    res.append(lst_text(R))
    res.append("Strandskydd gäller inte vid insjöar på högst en hektar, vid vattendrag som är högst två meter breda "
               "eller vid sjöar och vattendrag som anlagts efter den 30 juni 1975, om inte länsstyrelsen beslutat "
               "annat (miljöbalken 7 kap. 13 a–13 c §§). Verktyget räknar inte med sjöar på högst en hektar eller "
               "med bäckar (OpenStreetMap: waterway=stream). Bredden på övriga vattendrag har verktyget inte mätt.")
    res += ["Strandlinjen kommer från OpenStreetMap och kan avvika från strandlinjen vid normalt medelvattenstånd. "
            "Kontrollera läget på plats.",
            "Tomtplatsavgränsningen är ett förslag som sökanden ser över. Kommunen eller länsstyrelsen anger i beslutet "
            "hur stor del av marken som får tas i anspråk.",
            "Fastighetsgränser redovisas inte. Kontrollera mot Lantmäteriets fastighetskarta att åtgärderna ligger på "
            "fastigheten.",
            "Mått, höjder, vattendjup och förankring kommer från sökanden. Verktyget har inte mätt dem.",
            "Verktyget bedömer inte om det finns särskilda skäl för dispens. Kommunen eller länsstyrelsen prövar "
            "ansökan och beslutar.",
            "En brygga kan också kräva anmälan om vattenverksamhet till länsstyrelsen. Samma kartor kan bifogas den anmälan.",
            "Sökanden lämnar själv in underlaget och ansvarar för uppgifterna i ansökan."]
    if FEL == "forbjudet_ord":
        res.append("Underlaget är godkänt och dispensen beviljas med stor sannolikhet.")
    c.setFont("KSans", 7.5)
    for t in res:
        yr = KL.para(c, "• " + t, xr, yr, wr, size=7.5, leading=9) - 1.5 * mm
    yr -= 3 * mm
    c.setFont("KSans", 11); c.setFillColorRGB(*C_INK); c.drawString(xr, yr, "Källor och licenser"); yr -= 5.5 * mm
    src = []
    if FEL != "saknad_attribution":
        src.append(f"Kartdata © OpenStreetMap contributors. Licens: Open Database License (ODbL) 1.0, "
                   f"openstreetmap.org/copyright. Uttag ur Geofabriks Sverigeextrakt, tidsstämpel {R['osm_ts'] or 'okänd'}.")
    src.append(f"Länsstyrelsernas strandskyddsytor, nationell visningstjänst (lager 0 och 1), licens CC0 1.0, "
               f"hämtade {R['lst']['hamtad']}. Tjänsten visar inte det generella strandskyddet.")
    src += ["Koordinatsystem: SWEREF 99 TM (EPSG:3006). Omräkning med Lantmäteriets formler för Gauss–Krügers projektion.",
            "Strandlinje, 100 m-linje, avstånd och areor är beräknade av verktyget. Åtgärder och tomtplats bygger på "
            "sökandens uppgifter.",
            f"Regeltabell: {REGLER['version']}, se källorna i tabellen till vänster.",
            f"Generator {GENERATOR_VERSION}."]
    for t in src:
        yr = KL.para(c, "• " + t, xr, yr, wr, size=7.5, leading=9) - 1.5 * mm
    footer(c, order, page_no, n_pages)
    c.showPage()


def render(order, R, path):
    has_br = [a for a in R["atg"] if a["typ"] == "brygga"]
    n_pages = 3 + (1 if has_br else 0)
    c = canvas.Canvas(str(path), pagesize=PAGE, pageCompression=1, invariant=1)
    c.setTitle(f"Kartunderlag strandskydd – {order.get('fastighet', '')}"); c.setAuthor(VARUMARKE); c.setCreator(GENERATOR_VERSION)
    geo = {}
    page_overview(c, order, R, n_pages)
    geo["detalj"] = page_detail(c, order, R, 2, n_pages)
    pn = 3
    if has_br:
        geo["brygga"] = page_brygga(c, order, R, has_br[0], pn, n_pages); pn += 1
    page_rules(c, order, R, pn, n_pages)
    c.save()
    geo["oversikt"] = {"frame_pt": list(OV_FRAME), "scale": OV_SCALE}
    geo["sidor"] = n_pages
    return geo


def generate(order_path_or_dict, out_dir=None):
    t0 = time.perf_counter()
    order = order_path_or_dict if isinstance(order_path_or_dict, dict) else json.load(open(order_path_or_dict, encoding="utf-8"))
    out = Path(out_dir or os.environ.get("STRAND_OUT", ROOT / "ut")).resolve(); out.mkdir(parents=True, exist_ok=True)
    for old in (out / f"{order['id']}.pdf", out / f"{order['id']}_meta.json", out / f"{order['id']}_qc.json"):
        if old.exists():
            old.unlink()   # ingen gammal fil får ligga kvar om ordern avvisas eller kraschar
    try:
        R = compute(order)
    except Avvisad as e:
        res = {"generator": GENERATOR_VERSION, "order": order, "avvisad": {"kod": e.kod, "text": e.text}}
        json.dump(res, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return res
    pdf = out / f"{order['id']}.pdf"
    t1 = time.perf_counter()
    geo = render(order, R, pdf)
    meta = {"generator": GENERATOR_VERSION, "product": "strandskydd", "order": order, "file": str(pdf),
            "center_sweref": [R["E0"], R["N0"]], "center_wgs84": [R["lat0"], R["lon0"]],
            "osm": {"spec": R["spec"], "tidsstampel": R["osm_ts"], "kalla": R["endpoint"]},
            "atgarder": R["atg"], "tomtplats": R["tomt"], "skala": R["scale"], "box_m": list(R["box"]),
            "lst": {"spec": R["lst"]["spec"], "hamtad": R["lst"]["hamtad"], "fel": R["lst"]["fel"],
                    "antal_ytor": len(R["lst"]["ytor"]),
                    "traffar": [[y["typ"], y["traffar_atgard"]] for y in R["lst"]["ytor"]]},
            "utelamnat": {"backar": R["streams_utelamnade"], "sma_sjoar": R["sma_sjoar_utelamnade"]},
            "strandskydd_m": STRANDSKYDD_M, "fri_passage_m": FRI_PASSAGE_M, "geometry": geo,
            "regelversion": REGLER["version"],
            "timings": {"data_s": round(R["t_data"], 2), "berakning_s": round(R["t_compute"] - R["t_data"], 2),
                        "rendering_s": round(time.perf_counter() - t1, 2), "totalt_s": round(time.perf_counter() - t0, 2)},
            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({k: m.get(k) for k in ("file", "avvisad", "skala", "timings", "sha256")}, ensure_ascii=False, indent=1))
