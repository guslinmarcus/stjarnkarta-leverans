"""Förhandskollens index: alla namngivna golfbanor i de byggda regionregistren med grindens svar per bana.

Köparen ska kunna se INNAN köpet om banan går att rita (portalen /golf-kolla, worker/src/golfkolla.js). Indexet byggs ur
samma register och samma grindkod som ordern (golfdata.hole_summary + golfbana.course_check), så svaret "fungerar" betyder
att banan har kompletta numrerade hål med en green vid varje håls slut i den OSM-data som ordern använder.

  python fulfil/verktyg/golfindex.py [region ...]     (utan argument: alla register i data/cache/golf/)
  -> worker/src/golfindex.json (bundlas i Workern; OSM-data © OpenStreetMap contributors, ODbL)

Innehåll: banor (namn, alternativa namn, anläggning, läge, status, antal hål, kontur för förhandsvisningen), orter
(samma ortregister och samma regel som fulfil.geocode: exakt namn, störst befolkning i landet) inom 40 km från en bana,
länder (samma namnlista som fulfil.load_geo) och regionernas polygoner (Geofabrik, förenklade) för "inte förhandskollad".
"""
import gzip
import json
import math
import sys
import time
from pathlib import Path

F = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(F))
import golfdata as G  # noqa: E402
import golfbana as B  # noqa: E402
import fulfil  # noqa: E402

OUT = F.parent / "worker" / "src" / "golfindex.json"
TOL_REGION = 0.0005  # grader (≈ 150–200 m): regiongränsen avgör vilket register ordern använder


def simplify(pts, tol):
    """Douglas–Peucker på (x, y) i meter."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]; bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        best, bi = -1, None
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / L if (dx or dy) else math.hypot(px - ax, py - ay)
            if d > best:
                best, bi = d, i
        if bi is not None and best > tol:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def preview(c, holes):
    """Banans kontur + hållinjer i en ruta 0…1000 (norr uppåt), förenklade – bara för förhandsvisningen."""
    lat0, lon0 = c["center"]
    kx = 111320.0 * math.cos(math.radians(lat0)); ky = 110540.0

    def m(p):
        return ((p[0] - lon0) * kx, (p[1] - lat0) * ky)
    rings = [[m(p) for p in r] for r in c["outer"]]
    lines = [[m(p) for p in h["pts"]] for h in holes]
    xs = [p[0] for r in rings for p in r] + [p[0] for l in lines for p in l]
    ys = [p[1] for r in rings for p in r] + [p[1] for l in lines for p in l]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    s = 1000.0 / max(x1 - x0, y1 - y0, 1.0)
    tol = max(x1 - x0, y1 - y0) / 400.0

    def q(pts):
        return [[round((x - x0) * s), round((y1 - y) * s)] for x, y in pts]
    return {"w": round((x1 - x0) * s), "h": round((y1 - y0) * s),
            "o": [q(simplify(r, tol)) for r in rings if len(r) >= 4],
            "l": [q(simplify(l, tol)) for l in lines]}


def country_names():
    _, lander = fulfil.load_geo()
    return dict(sorted(lander.items()))


def region_polys(rids):
    idx = json.load(open(F / "data" / "cache" / "geofabrik" / "index-v1.json", encoding="utf-8"))
    out = {}
    for f in idx["features"]:
        rid = f["properties"]["id"]
        if rid not in rids or not f.get("geometry"):
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        rings = []
        for p in polys:
            r = simplify([tuple(x) for x in p[0]], TOL_REGION)
            if len(r) >= 4:
                rings.append([[round(x, 4), round(y, 4)] for x, y in r])
        xs = [c[0] for p in polys for c in p[0]]; ys = [c[1] for p in polys for c in p[0]]
        # yta = samma mått som osmextract.region_for_bbox använder för att välja MINSTA regionen som täcker orten
        out[rid] = {"namn": f["properties"].get("name", rid), "grupp": f["properties"].get("parent", ""), "ringar": rings,
                    "yta": (max(xs) - min(xs)) * (max(ys) - min(ys))}
    return out


def build(regdirs):
    t0 = time.perf_counter()
    orter, _ = fulfil.load_geo()
    courses, regions, datum = [], [], {}
    for d in regdirs:
        reg = G.load_register(d)
        rid = reg["region"]; regions.append(rid); datum[rid] = reg["osm_datum"]
        by_id = {c["osm"]: c for c in reg["banor"]}
        named = [c for c in reg["banor"] if G.all_names(c["tags"])]
        # samma dubblettregel som resolve: samma namn, mitt < 300 m isär -> den med flest hål
        uniq = []
        for c in sorted(named, key=lambda c: (-len(c["holes"]), c["osm"])):
            if not any(c["tags"].get("name") == u["tags"].get("name") and G.dist_km(*c["center"], *u["center"]) < 0.3
                       and c.get("parent") != u["osm"] and u.get("parent") != c["osm"] for u in uniq):  # anläggning ≠ delbana
                uniq.append(c)
        for c in sorted(uniq, key=lambda c: c["osm"]):
            base, err, det = B.course_check(reg, d, c)
            kids = [o for o in reg["banor"] if o.get("parent") == c["osm"] and G.all_names(o["tags"])
                    and G.hole_summary(reg, o)["antal"] >= G.MIN_HOLES]
            par = by_id.get(c.get("parent"))
            e = {"id": f"{rid}:{c['osm']}", "n": c["tags"].get("name", "") or G.all_names(c["tags"])[0],
                 "a": sorted(set(G.all_names(c["tags"])) - {c["tags"].get("name", "")}),
                 "p": (G.all_names(par["tags"]) or [""])[0] if par else "", "pid": f"{rid}:{par['osm']}" if par else "",
                 "lat": round(c["center"][0], 5), "lon": round(c["center"][1], 5), "r": rid,
                 "s": "ok" if err is None else "inc", "h": base["summary"]["antal"],
                 "nr": len(base["summary"]["numrerade"]),
                 # banans ord (golfdata.tokens över alla namnvarianter) – förberäknade så att Workern inte behöver
                 # normalisera 4 000 namn per anrop (CPU-gräns 10 ms) och så att orden är exakt Pythons
                 "t": sorted(set(t for n_ in G.all_names(c["tags"]) for t in G.tokens(n_)))}
            if len(kids) >= 2:
                e["fac"] = 1  # anläggning med flera delbanor: köparen väljer delbana ("Anläggning – Delbana")
            if err is None:
                e["g"] = preview(c, base["holes"])
            elif det:
                e["ug"] = len(det.get("utan_green") or [])
            courses.append(e)
        print(f"{rid}: {len(uniq)} banor, {sum(1 for x in courses if x['r'] == rid and x['s'] == 'ok')} fungerar", flush=True)
        del reg
    # alla banors land via närmaste ort i ortregistret
    allp = {}
    for recs in orter.values():
        for r in recs:
            allp[(r[0], r[1], r[2])] = r
    grid = {}
    for r in allp.values():
        grid.setdefault((int(r[1] // 0.5), int(r[2] // 0.5)), []).append(r)

    def nearby(lat, lon, km):
        out = []
        for gx in range(int(lat // 0.5) - 1, int(lat // 0.5) + 2):
            for gy in range(int(lon // 0.5) - 2, int(lon // 0.5) + 3):
                for r in grid.get((gx, gy), ()):
                    dd = G.dist_km(lat, lon, r[1], r[2])
                    if dd <= km:
                        out.append((dd, r))
        return out
    towns = {}
    for e in courses:
        nb = nearby(e["lat"], e["lon"], G.SEARCH_KM)
        e["cc"] = min(nb, key=lambda x: x[0])[1][3] if nb else ""
        close = [x for x in nb if x[1][3] == e["cc"]]
        e["ort"] = min(close, key=lambda x: x[0])[1][0] if close else ""
    # orter: samma regel som fulfil.geocode – namnnyckel -> största orten med det namnet i landet (hela landet, inte bara nära)
    ccs = {e["cc"] for e in courses}
    for key, recs in orter.items():
        for r in recs:
            if r[3] in ccs:
                cur = towns.setdefault(r[3], {}).get(key)
                if cur is None or r[4] > cur[2]:
                    towns[r[3]][key] = [round(r[1], 4), round(r[2], 4), r[4]]
    # bara nycklar vars största ort ligger inom 40 km från någon bana behövs (övriga: "ingen förhandskollad bana nära orten")
    near_courses = {}
    for e in courses:
        near_courses.setdefault(e["cc"], []).append((e["lat"], e["lon"]))
    for cc, tmap in towns.items():
        pts = near_courses.get(cc, [])
        cells = {}
        for la, lo in pts:
            cells.setdefault((int(la // 0.5), int(lo // 0.5)), []).append((la, lo))
        for k, v in list(tmap.items()):
            ok = any(G.dist_km(v[0], v[1], la, lo) <= G.SEARCH_KM + 1
                     for gx in range(int(v[0] // 0.5) - 1, int(v[0] // 0.5) + 2)
                     for gy in range(int(v[1] // 0.5) - 2, int(v[1] // 0.5) + 3) for la, lo in cells.get((gx, gy), ()))
            if ok:
                tmap[k] = [v[0], v[1]]
            else:
                del tmap[k]
    idx = {"v": 1, "byggt": time.strftime("%Y-%m-%d"), "osm_datum": datum, "sokradie_km": G.SEARCH_KM, "min_hal": G.MIN_HOLES,
           "stop": sorted(G.STOP), "lander": country_names(), "regioner": region_polys(set(regions)), "orter": towns,
           "banor": courses, "licens": "Kartdata © OpenStreetMap contributors, ODbL 1.0"}
    OUT.write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n_ok = sum(1 for e in courses if e["s"] == "ok")
    print(f"{OUT}: {len(courses)} banor, {n_ok} fungerar, {OUT.stat().st_size / 1e6:.2f} MB, "
          f"gzip {len(gzip.compress(OUT.read_bytes())) / 1e6:.2f} MB, {time.perf_counter() - t0:.0f} s")
    return idx


if __name__ == "__main__":
    want = sys.argv[1:]
    dirs = sorted(p for p in G.CACHE.iterdir() if p.is_dir() and (p / "register.json.gz").exists()
                  and not p.name.endswith(".part") and (not want or any(p.name.startswith(w + "_") for w in want)))
    # nyaste registret per region
    best = {}
    for p in dirs:
        rid = p.name.rsplit("_", 1)[0]
        best[rid] = p
    build([best[k] for k in sorted(best)])
