"""OpenStreetMap-data för kartprodukterna: hämtning (Overpass, Nominatim) med cache och artig takt,
geometribyggen (ringar, kustlinje → land) och vektorritning i reportlab.

Villkor som koden följer (lästa 2026-09-27):
* DRIFT: Geofabrik-extrakt (ODbL, fri nedladdning) som bearbetas lokalt/i GitHub Actions med pyosmium
  (osmextract.py). Ingen extern OSM-API-tjänst används för kundordrar.
* Bara utveckling/test i låg takt (OSM_SOURCE=overpass): Overpass på Private.coffee (ber kommersiella användare om
  bidrag – därför inte i drift). overpass-api.de används aldrig: "Commercial use should use self-hosted or paid
  Overpass servers". Egen User-Agent, cache, minst 2 s mellan frågor.
* Nominatim: bara utveckling/reserv, enstaka uppslag med cache (max 1/s). I drift slås adressen upp i
  Geofabrik-extraktets addr:*-taggar (osmextract.addresses).
* Data: ODbL 1.0. En tryckt karta är ett "Produced Work" – kräver källnotis "© OpenStreetMap contributors"
  och licensnamnet på produkten. Vi blandar aldrig OSM-data med egen databas som sprids vidare.
"""
import gzip
import hashlib
import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = Path(os.environ.get("OSM_CACHE", ROOT / "data" / "cache" / "osm"))
UA = "MoodlySverige-kartleverans/0.3 (made-to-order print maps; contact via stjarnkarta-leverans portal)"
OVERPASS = ["https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
NOMINATIM = "https://nominatim.openstreetmap.org/search"
_last = {"overpass": 0.0, "nominatim": 0.0}

try:
    import truststore; truststore.inject_into_ssl()
except Exception:
    pass


def _cache_path(kind, key):
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / f"{kind}_{hashlib.sha1(key.encode()).hexdigest()[:20]}.json.gz"


def _http(url, data=None, timeout=180):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def overpass(query, max_age_days=60):
    """Kör en Overpass-fråga (cachas). Returnerar (json, källa)."""
    cp = _cache_path("ovp", query)
    if cp.exists() and time.time() - cp.stat().st_mtime < max_age_days * 86400:
        d = json.load(gzip.open(cp, "rt", encoding="utf-8"))
        return d, d.get("_endpoint", "cache")
    err = None
    for ep in OVERPASS:
        for attempt in range(3):
            wait = 2.0 - (time.time() - _last["overpass"])
            if wait > 0:
                time.sleep(wait)
            try:
                _last["overpass"] = time.time()
                raw = _http(ep, urllib.parse.urlencode({"data": query}).encode())
                d = json.loads(raw)
                if "remark" in d and "runtime error" in d.get("remark", ""):
                    raise RuntimeError(d["remark"][:200])
                d["_endpoint"] = ep
                json.dump(d, gzip.open(cp, "wt", encoding="utf-8"))
                return d, ep
            except urllib.error.HTTPError as e:
                err = e
                if e.code in (429, 504):
                    time.sleep(30); continue
                break
            except Exception as e:  # nätverksfel – försök nästa instans
                err = e
                break
    raise RuntimeError(f"overpass misslyckades: {err!r}")


def nominatim(q, countrycodes=None):
    """Geokoda en adress (cachas). Returnerar lista av träffar (Nominatims jsonv2)."""
    key = json.dumps([q, countrycodes], ensure_ascii=False)
    cp = _cache_path("nom", key)
    if cp.exists():
        return json.load(gzip.open(cp, "rt", encoding="utf-8"))
    wait = 1.1 - (time.time() - _last["nominatim"])
    if wait > 0:
        time.sleep(wait)
    params = {"q": q, "format": "jsonv2", "limit": 3, "addressdetails": 1}
    if countrycodes:
        params["countrycodes"] = countrycodes
    _last["nominatim"] = time.time()
    d = json.loads(_http(NOMINATIM + "?" + urllib.parse.urlencode(params), timeout=60))
    json.dump(d, gzip.open(cp, "wt", encoding="utf-8"))
    return d


# ---------------------------------------------------------------- frågor
HIGHWAY_MAJOR = "motorway|trunk|primary|motorway_link|trunk_link|primary_link"
HIGHWAY_MID = "secondary|tertiary|secondary_link|tertiary_link"
HIGHWAY_MINOR = "unclassified|residential|living_street|pedestrian|road"


def query_map(s, w, n, e, roads=True, minor=True, water=True, green=True, rail=True, rich=False):
    """Datamängden som en JSON-sträng (cachenyckel och det som sparas i meta, så att grinden läser samma data).
    rich=True lägger till byggnader, markanvändning (åker, äng, bebyggelse) och ortnamn (place-noder) –
    nyckeln tas bara med när den är satt, så att äldre cachenycklar (stadskarta) är oförändrade."""
    d = {"bbox": [round(s, 6), round(w, 6), round(n, 6), round(e, 6)], "roads": roads, "minor": minor,
         "water": water, "green": green, "rail": rail}
    if rich:
        d["rich"] = True
    return json.dumps(d, sort_keys=True)


def fetch(spec):
    """OSM-data för spec (se query_map). Drift: Geofabrik + pyosmium. Returnerar (json, källa)."""
    cp = _cache_path("osm", spec)
    if cp.exists():
        d = json.load(gzip.open(cp, "rt", encoding="utf-8"))
        return d, d.get("_endpoint", "cache")
    sp = json.loads(spec)
    if os.environ.get("OSM_SOURCE") == "overpass":  # endast utveckling/test
        d, ep = overpass(overpass_ql(sp))
    else:
        import osmextract
        d = osmextract.extract(sp)
        ep = d["_endpoint"]
    json.dump(d, gzip.open(cp, "wt", encoding="utf-8"))
    return d, ep


def addresses(lat, lon, radius_km=25.0):
    """Adresser och gatunamn kring orten ur Geofabrik-extraktet (cachas per ort). Nyckeln sparas i meta."""
    key = json.dumps({"adresser": [round(lat, 4), round(lon, 4)], "radie_km": radius_km}, sort_keys=True)
    cp = _cache_path("adr", key)
    if cp.exists():
        return json.load(gzip.open(cp, "rt", encoding="utf-8")), key
    import osmextract
    d = osmextract.addresses(round(lat, 4), round(lon, 4), radius_km)
    json.dump(d, gzip.open(cp, "wt", encoding="utf-8"))
    return d, key


def load_addresses(key):
    cp = _cache_path("adr", key)
    if not cp.exists():
        k = json.loads(key)
        return addresses(k["adresser"][0], k["adresser"][1], k["radie_km"])[0]
    return json.load(gzip.open(cp, "rt", encoding="utf-8"))


def overpass_ql(sp, timeout=180):
    s, w, n, e = sp["bbox"]
    roads, minor, water, green, rail = sp["roads"], sp["minor"], sp["water"], sp["green"], sp["rail"]
    bb = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    parts = []
    if roads:
        cls = "|".join(x for x in (HIGHWAY_MAJOR, HIGHWAY_MID, HIGHWAY_MINOR if minor else "") if x)
        parts.append(f'way["highway"~"^({cls})$"]{bb};')
    if rail:
        parts.append(f'way["railway"="rail"]["service"!~"."]{bb};')
    if water:
        parts += [f'way["natural"="water"]{bb};', f'relation["natural"="water"]{bb};',
                  f'way["waterway"="riverbank"]{bb};', f'relation["waterway"="riverbank"]{bb};',
                  f'way["landuse"="reservoir"]{bb};', f'way["natural"="coastline"]{bb};',
                  f'way["waterway"~"^(river|canal)$"]{bb};']
    if green:
        parts += [f'way["leisure"="park"]{bb};', f'relation["leisure"="park"]{bb};',
                  f'way["landuse"="forest"]{bb};', f'way["natural"="wood"]{bb};',
                  f'relation["landuse"="forest"]{bb};', f'relation["natural"="wood"]{bb};']
    return f"[out:json][timeout:{timeout}][maxsize:536870912];(" + "".join(parts) + ");out geom;"


# ---------------------------------------------------------------- geometri
def _key(p):
    return (round(p[0], 7), round(p[1], 7))


def join_lines(lines):
    """Slå ihop polylinjer (listor av (lon, lat)) som delar ändpunkter. Riktning bevaras om möjligt."""
    lines = [list(l) for l in lines if len(l) >= 2]
    out = []
    while lines:
        cur = lines.pop()
        changed = True
        while changed and _key(cur[0]) != _key(cur[-1]):
            changed = False
            for i, l in enumerate(lines):
                if _key(l[0]) == _key(cur[-1]):
                    cur = cur + l[1:]
                elif _key(l[-1]) == _key(cur[0]):
                    cur = l + cur[1:]
                elif _key(l[-1]) == _key(cur[-1]):
                    cur = cur + l[::-1][1:]
                elif _key(l[0]) == _key(cur[0]):
                    cur = l[::-1] + cur[1:]
                else:
                    continue
                lines.pop(i); changed = True
                break
        out.append(cur)
    return out


def join_directed(lines):
    """Som join_lines men utan att vända riktning (kustlinjen: land till vänster)."""
    lines = [list(l) for l in lines if len(l) >= 2]
    starts = {}
    for i, l in enumerate(lines):
        starts.setdefault(_key(l[0]), []).append(i)
    used = [False] * len(lines)
    out = []
    for i in range(len(lines)):
        if used[i]:
            continue
        used[i] = True
        cur = list(lines[i])
        # bakåt: finns en linje som slutar där cur börjar?
        while True:
            ext = False
            for j, l in enumerate(lines):
                if not used[j] and _key(l[-1]) == _key(cur[0]):
                    cur = l + cur[1:]; used[j] = True; ext = True; break
            if not ext or _key(cur[0]) == _key(cur[-1]):
                break
        while _key(cur[0]) != _key(cur[-1]):
            nxt = [j for j in starts.get(_key(cur[-1]), []) if not used[j]]
            if not nxt:
                break
            j = nxt[0]; used[j] = True; cur = cur + lines[j][1:]
        out.append(cur)
    return out


def parse_features(d):
    """OSM-element → {'roads': [(klass, [(lon,lat)…])], 'rail': […], 'water': [ringlistor], 'rivers': [...],
    'green': [ringlistor], 'coast': [linjer]}"""
    F = {"roads": [], "rail": [], "water": [], "rivers": [], "green": [], "coast": [],
         "buildings": [], "fields": [], "meadow": [], "urban": [], "places": [], "water_names": []}
    for el in d.get("elements", []):
        t = el.get("tags", {})
        if el["type"] == "node":
            if t.get("place") and t.get("name"):
                F["places"].append((t["place"], t["name"], el["lon"], el["lat"], int(t.get("population", "0") or 0) if str(t.get("population", "0")).isdigit() else 0))
            continue
        if el["type"] == "way" and "geometry" in el:
            pts0 = [(g["lon"], g["lat"]) for g in el["geometry"] if g]
            if t.get("place") in ("island", "islet") and t.get("name") and len(pts0) >= 4:
                F["places"].append((t["place"], t["name"], sum(p[0] for p in pts0) / len(pts0), sum(p[1] for p in pts0) / len(pts0), 0))
                if not ("natural" in t or "landuse" in t or "building" in t):
                    continue
            if t.get("building") and len(pts0) >= 4:
                F["buildings"].append([pts0]); continue
            lu = t.get("landuse")
            if lu in ("farmland", "meadow", "residential", "industrial", "commercial", "retail", "allotments", "orchard") and len(pts0) >= 4                     and _key(pts0[0]) == _key(pts0[-1]):
                F["fields" if lu in ("farmland", "allotments", "orchard") else "meadow" if lu == "meadow" else "urban"].append([pts0]); continue
            if t.get("name") and len(pts0) >= 4 and (t.get("natural") == "water" or t.get("landuse") == "reservoir"):
                F["water_names"].append((t["name"], pts0))
            pts = [(g["lon"], g["lat"]) for g in el["geometry"] if g]
            if len(pts) < 2:
                continue
            closed = _key(pts[0]) == _key(pts[-1]) and len(pts) >= 4
            if "highway" in t:
                F["roads"].append((t["highway"].replace("_link", ""), pts))
            elif t.get("railway") == "rail":
                F["rail"].append(pts)
            elif t.get("natural") == "coastline":
                F["coast"].append(pts)
            elif t.get("waterway") in ("river", "canal"):
                F["rivers"].append((t["waterway"], pts))
            elif closed and (t.get("natural") == "water" or t.get("waterway") == "riverbank" or t.get("landuse") == "reservoir"):
                F["water"].append([pts])
            elif closed and (t.get("leisure") == "park" or t.get("landuse") == "forest" or t.get("natural") == "wood"):
                F["green"].append([pts])
        elif el["type"] == "relation":
            outer, inner = [], []
            for m in el.get("members", []):
                if m.get("type") != "way" or "geometry" not in m:
                    continue
                pts = [(g["lon"], g["lat"]) for g in m["geometry"] if g]
                (inner if m.get("role") == "inner" else outer).append(pts)
            rings = [r for r in join_lines(outer) + join_lines(inner) if len(r) >= 4 and _key(r[0]) == _key(r[-1])]
            if not rings:
                continue
            if t.get("natural") == "water" or t.get("waterway") == "riverbank":
                F["water"].append(rings)
                if t.get("name"):
                    F["water_names"].append((t["name"], max(rings, key=len)))
            elif t.get("landuse") in ("farmland", "meadow", "residential", "industrial", "commercial", "retail"):
                F["fields" if t["landuse"] == "farmland" else "meadow" if t["landuse"] == "meadow" else "urban"].append(rings)
            elif t.get("building"):
                F["buildings"].append(rings)
            elif t.get("place") in ("island", "islet") and t.get("name"):
                r = max(rings, key=len)
                F["places"].append((t["place"], t["name"], sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r), 0))
            else:
                F["green"].append(rings)
    return F


def signed_area(ring):
    a = 0.0
    for i in range(len(ring) - 1):
        a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return a / 2


def _clip_polyline(pts, x0, y0, x1, y1):
    """Klipp en polylinje mot rektangeln. Returnerar bitar (listor av punkter) som ligger inuti,
    med exakta skärningspunkter på kanten."""
    def inside(p):
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    def seg_clip(p, q):  # Liang–Barsky
        dx, dy = q[0] - p[0], q[1] - p[1]
        t0, t1 = 0.0, 1.0
        for pp, qq in ((-dx, p[0] - x0), (dx, x1 - p[0]), (-dy, p[1] - y0), (dy, y1 - p[1])):
            if pp == 0:
                if qq < 0:
                    return None
            else:
                r = qq / pp
                if pp < 0:
                    t0 = max(t0, r)
                else:
                    t1 = min(t1, r)
        if t0 > t1:
            return None
        return (p[0] + t0 * dx, p[1] + t0 * dy), (p[0] + t1 * dx, p[1] + t1 * dy)

    pieces, cur = [], None
    for i in range(len(pts) - 1):
        c = seg_clip(pts[i], pts[i + 1])
        if c is None:
            if cur:
                pieces.append(cur); cur = None
            continue
        a, b = c
        if cur is None:
            cur = [a, b]
        else:
            cur.append(b)
        if not inside(pts[i + 1]) or b != pts[i + 1]:
            pieces.append(cur); cur = None
    if cur:
        pieces.append(cur)
    return [p for p in pieces if len(p) >= 2]


def coast_land_polygons(coast_xy, x0, y0, x1, y1):
    """Kustlinjer (projicerade, y norrut, land till vänster) → (landpolygoner, har_kust).
    Algoritm: klipp kedjorna mot rutan, gå sedan moturs längs kanten från varje utgång till nästa ingång."""
    chains = join_directed(coast_xy)
    P = 2 * (x1 - x0) + 2 * (y1 - y0)

    def tpos(p):
        x, y = p
        eps = 1e-6 * P
        if abs(y - y0) < eps:
            return x - x0
        if abs(x - x1) < eps:
            return (x1 - x0) + (y - y0)
        if abs(y - y1) < eps:
            return (x1 - x0) + (y1 - y0) + (x1 - x)
        return 2 * (x1 - x0) + (y1 - y0) + (y1 - y)

    def snap(p):  # ändpunkt inuti rutan (trasig kustdata) → närmaste kant
        x, y = p
        d = [(x - x0, (x0, y)), (x1 - x, (x1, y)), (y - y0, (x, y0)), (y1 - y, (x, y1))]
        return min(d)[1]

    corners = [(0.0, (x0, y0)), (x1 - x0, (x1, y0)), ((x1 - x0) + (y1 - y0), (x1, y1)),
               (2 * (x1 - x0) + (y1 - y0), (x0, y1))]
    pieces, islands, lakes = [], [], []
    for ch in chains:
        closed = _key(ch[0]) == _key(ch[-1])
        if closed and all(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in ch):
            (islands if signed_area(ch) > 0 else lakes).append(ch)
            continue
        for pc in _clip_polyline(ch, x0, y0, x1, y1):
            a, b = pc[0], pc[-1]
            if min(abs(a[0] - x0), abs(a[0] - x1), abs(a[1] - y0), abs(a[1] - y1)) > 1e-6 * P:
                pc = [snap(a)] + pc
            if min(abs(b[0] - x0), abs(b[0] - x1), abs(b[1] - y0), abs(b[1] - y1)) > 1e-6 * P:
                pc = pc + [snap(b)]
            pieces.append(pc)
    polys = []
    used = [False] * len(pieces)
    for i in range(len(pieces)):
        if used[i]:
            continue
        poly, cur, guard = [], i, 0
        while guard < len(pieces) + 2:
            guard += 1
            used[cur] = True
            poly += pieces[cur]
            te = tpos(pieces[cur][-1])
            best, bd = None, None
            for j, pc in enumerate(pieces):
                if used[j] and j != i:
                    continue
                d = (tpos(pc[0]) - te) % P
                if bd is None or d < bd:
                    best, bd = j, d
            # hörn som passeras moturs
            for tc, c in sorted(corners, key=lambda c: (c[0] - te) % P):
                if 0 < (tc - te) % P < bd:
                    poly.append(c)
            if best == i or best is None:
                break
            cur = best
        poly.append(poly[0])
        polys.append(poly)
    has_coast = bool(pieces or islands)
    return polys + islands, lakes, has_coast


# ---------------------------------------------------------------- ritning (reportlab, vektor)
ROAD_W = {"motorway": 1.0, "trunk": 0.9, "primary": 0.8, "secondary": 0.62, "tertiary": 0.5,
          "unclassified": 0.34, "residential": 0.3, "living_street": 0.26, "pedestrian": 0.26, "road": 0.26}

STYLES = {
    # namn: bakgrund(land), vatten, grönt, väg-färg per nivå (större, mellan, mindre), järnväg, textfärg, bredd-faktor
    "klassisk": dict(land=(0.975, 0.965, 0.945), water=(0.73, 0.82, 0.88), green=(0.90, 0.92, 0.86),
                     road=((0.12, 0.12, 0.12), (0.22, 0.22, 0.22), (0.35, 0.35, 0.35)), rail=(0.45, 0.45, 0.45),
                     ink=(0.1, 0.1, 0.1), paper=(1, 1, 1), wf=1.0, label="Classic"),
    "natt": dict(land=(0.06, 0.08, 0.14), water=(0.02, 0.03, 0.07), green=(0.07, 0.10, 0.15),
                 road=((0.93, 0.80, 0.52), (0.84, 0.72, 0.48), (0.62, 0.55, 0.42)), rail=(0.5, 0.45, 0.35),
                 ink=(0.93, 0.85, 0.66), paper=(0.06, 0.08, 0.14), wf=1.0, label="Midnight gold"),
    "sepia": dict(land=(0.95, 0.91, 0.83), water=(0.80, 0.74, 0.63), green=(0.91, 0.87, 0.77),
                  road=((0.33, 0.22, 0.13), (0.42, 0.30, 0.19), (0.52, 0.40, 0.28)), rail=(0.5, 0.4, 0.3),
                  ink=(0.30, 0.20, 0.12), paper=(0.97, 0.94, 0.88), wf=1.0, label="Vintage sepia"),
    "blueprint": dict(land=(0.08, 0.24, 0.45), water=(0.05, 0.17, 0.33), green=(0.09, 0.26, 0.47),
                      road=((0.97, 0.98, 1.0), (0.86, 0.91, 0.97), (0.70, 0.79, 0.90)), rail=(0.75, 0.82, 0.92),
                      ink=(0.97, 0.98, 1.0), paper=(0.08, 0.24, 0.45), wf=0.9, label="Blueprint"),
}


def road_level(cls):
    if cls in ("motorway", "trunk", "primary"):
        return 0
    if cls in ("secondary", "tertiary"):
        return 1
    return 2


def project_line(proj, pts):
    """Projicera en lista (lon, lat) på en gång (proj tar numpy-vektorer)."""
    import numpy as np
    a = np.asarray(pts, float)
    x, y = proj(a[:, 0], a[:, 1])
    return list(zip(np.asarray(x, float).tolist(), np.asarray(y, float).tolist()))


def draw_features(c, F, proj, frame, bbox, style, scale_pt_per_m, skip=None):
    """Rita OSM-lagren i en ram på reportlab-canvasen.
    proj(lon, lat) -> (x, y) i meter (y norrut); bbox = (x0, y0, x1, y1) i samma enhet;
    frame = (px, py, pw, ph) i punkter; skip = set av lager att hoppa över (felinjektion)."""
    skip = skip or set()
    S = STYLES[style] if isinstance(style, str) else style
    px, py, pw, ph = frame
    x0, y0, x1, y1 = bbox
    sx, sy = pw / (x1 - x0), ph / (y1 - y0)

    def tp(x, y):
        return px + (x - x0) * sx, py + (y - y0) * sy

    def path_of(rings_xy):
        p = c.beginPath()
        for r in rings_xy:
            p.moveTo(*tp(*r[0]))
            for q in r[1:]:
                p.lineTo(*tp(*q))
            p.close()
        return p

    c.saveState()
    cl = c.beginPath(); cl.rect(px, py, pw, ph); c.clipPath(cl, stroke=0, fill=0)
    coast = [project_line(proj, l) for l in F["coast"]]
    # marginal så att kustpolygonerna stängs utanför ramen
    mx, my = (x1 - x0) * 0.02, (y1 - y0) * 0.02
    land, lakes, has_coast = coast_land_polygons(coast, x0 - mx, y0 - my, x1 + mx, y1 + my) if coast else ([], [], False)
    c.setFillColorRGB(*(S["water"] if has_coast else S["land"]))
    c.rect(px, py, pw, ph, stroke=0, fill=1)
    if has_coast:
        c.setFillColorRGB(*S["land"])
        for poly in land:
            c.drawPath(path_of([poly]), stroke=0, fill=1)
        c.setFillColorRGB(*S["water"])
        for poly in lakes:
            c.drawPath(path_of([poly]), stroke=0, fill=1)
    if "green" not in skip:
        c.setFillColorRGB(*S["green"])
        for rings in F["green"]:
            c.drawPath(path_of([project_line(proj, r) for r in rings]), stroke=0, fill=1, fillMode=0)
    if "water" not in skip:
        c.setFillColorRGB(*S["water"])
        for rings in F["water"]:
            c.drawPath(path_of([project_line(proj, r) for r in rings]), stroke=0, fill=1, fillMode=0)
        c.setStrokeColorRGB(*S["water"]); c.setLineCap(1); c.setLineJoin(1)
        for kind, pts in F["rivers"]:
            c.setLineWidth((12 if kind == "river" else 6) * scale_pt_per_m)
            xy = [tp(*q) for q in project_line(proj, pts)]
            p = c.beginPath(); p.moveTo(*xy[0])
            for q in xy[1:]:
                p.lineTo(*q)
            c.drawPath(p, stroke=1, fill=0)
    if "rail" not in skip:
        c.setStrokeColorRGB(*S["rail"]); c.setLineWidth(0.35 * S["wf"]); c.setDash(1.2, 0.9)
        for pts in F["rail"]:
            xy = [tp(*q) for q in project_line(proj, pts)]
            p = c.beginPath(); p.moveTo(*xy[0])
            for q in xy[1:]:
                p.lineTo(*q)
            c.drawPath(p, stroke=1, fill=0)
        c.setDash()
    if "roads" not in skip:
        c.setLineCap(1); c.setLineJoin(1)
        # linjebredd: fast i punkter men skalad lätt med kartskalan (mindre utsnitt → något tjockare)
        k = max(0.55, min(1.6, (scale_pt_per_m * 8000) ** 0.35)) * S["wf"]
        for level in (2, 1, 0):
            c.setStrokeColorRGB(*S["road"][level])
            for cls, pts in F["roads"]:
                if road_level(cls) != level:
                    continue
                pl = project_line(proj, pts)
                if "quadrant" in skip and pl[0][0] > (x0 + x1) / 2 and pl[0][1] > (y0 + y1) / 2:
                    continue
                c.setLineWidth(ROAD_W.get(cls, 0.26) * k)
                xy = [tp(*q) for q in pl]
                p = c.beginPath(); p.moveTo(*xy[0])
                for q in xy[1:]:
                    p.lineTo(*q)
                c.drawPath(p, stroke=1, fill=0)
    c.restoreState()
    return {"har_kust": has_coast, "land_polygoner": len(land), "vatten": len(F["water"]), "vagar": len(F["roads"]),
            "gront": len(F["green"]), "jarnvag": len(F["rail"])}


def mercator_proj(lat0, lon0):
    """Lokal Mercator i meter (skala sann vid lat0), origo i (lat0, lon0)."""
    import numpy as np
    R = 6378137.0
    k = math.cos(math.radians(lat0))
    y0 = R * math.log(math.tan(math.pi / 4 + math.radians(lat0) / 2))

    def f(lon, lat):
        return (R * np.radians(np.asarray(lon) - lon0) * k,
                (R * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) - y0) * k)

    def inv(x, y):
        lon = lon0 + np.degrees(np.asarray(x) / (R * k))
        lat = np.degrees(2 * np.arctan(np.exp((np.asarray(y) / k + y0) / R)) - np.pi / 2)
        return lon, lat
    return f, inv
