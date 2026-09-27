"""OpenStreetMap-data ur Geofabrik-extrakt, bearbetade lokalt med pyosmium (ingen extern API-tjänst i drift).

* Källa: https://download.geofabrik.de/ – fria nedladdningar, "ODbL 1.0", uppdateras dagligen. Regionen väljs ur
  Geofabriks index (index-v1.json): minsta region vars polygon täcker hela rutan. Filen cachas i
  data/cache/geofabrik/ och hämtas om när den är äldre än OSM_MAX_AGE_DAYS (30).
* Utdata har samma form som Overpass "out geom" (ways med geometry, relationer med medlemmarnas geometri),
  så att resten av koden och grindarna läser samma sak oavsett källa.
* Adresser: addr:housenumber/addr:street ur samma extrakt (ersätter Nominatim i drift).

Två genomläsningar av extraktet per ruta: relationer (utan koordinater, sekunder) och vägar med nodkoordinater
(Sverige ≈ 820 MB: ≈ 2,5 min på Marcus dator). Resultatet cachas per ruta.
"""
import array
import json
import math
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
GF = Path(os.environ.get("GEOFABRIK_CACHE", ROOT / "data" / "cache" / "geofabrik"))
INDEX_URL = "https://download.geofabrik.de/index-v1.json"
MAX_AGE_DAYS = float(os.environ.get("OSM_MAX_AGE_DAYS", "30"))
UA = "MoodlySverige-kartleverans/0.3 (made-to-order print maps)"

HIGHWAY_ALL = {"motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link", "secondary", "tertiary",
               "secondary_link", "tertiary_link", "unclassified", "residential", "living_street", "pedestrian", "road"}
HIGHWAY_NO_MINOR = HIGHWAY_ALL - {"unclassified", "residential", "living_street", "pedestrian", "road"}


def _get(url, dest, timeout=600):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    os.replace(tmp, dest)


def _fresh(p, days):
    return p.exists() and time.time() - p.stat().st_mtime < days * 86400


def _ring_contains(ring, x, y):
    ins = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]; xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi:
            ins = not ins
        j = i
    return ins


def region_for_bbox(s, w, n, e):
    """Minsta Geofabrik-region som täcker rutans fyra hörn. Returnerar (id, pbf-url)."""
    ip = GF / "index-v1.json"
    if not _fresh(ip, 30):
        _get(INDEX_URL, ip)
    idx = json.load(open(ip, encoding="utf-8"))
    corners = [(w, s), (e, s), (e, n), (w, n)]
    best = None
    for f in idx["features"]:
        pbf = f["properties"].get("urls", {}).get("pbf")
        g = f.get("geometry")
        if not pbf or not g:
            continue
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        ok = all(any(_ring_contains(p[0], x, y) for p in polys) for x, y in corners)
        if not ok:
            continue
        xs = [c[0] for p in polys for c in p[0]]; ys = [c[1] for p in polys for c in p[0]]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if best is None or area < best[0]:
            best = (area, f["properties"]["id"], pbf)
    if best is None:
        raise RuntimeError("overpass misslyckades: ingen Geofabrik-region täcker platsen")
    return best[1], best[2]


def pbf_for_bbox(s, w, n, e):
    rid, url = region_for_bbox(s, w, n, e)
    p = GF / f"{rid.replace('/', '_')}-latest.osm.pbf"
    t = 0.0
    if not _fresh(p, MAX_AGE_DAYS):
        t0 = time.perf_counter()
        try:
            _get(url, p, timeout=1800)
        except Exception as ex:
            raise RuntimeError(f"overpass misslyckades: Geofabrik-hämtning {url}: {ex!r}")
        t = time.perf_counter() - t0
    return p, rid, url, t


def _want_way(tags, spec):
    hw = tags.get("highway")
    if hw is not None:
        if spec.get("roads", True) and hw in (HIGHWAY_ALL if spec.get("minor", True) else HIGHWAY_NO_MINOR):
            return True
        return False
    if spec.get("rail", True) and tags.get("railway") == "rail" and "service" not in tags:
        return True
    if spec.get("water", True):
        if tags.get("natural") in ("water", "coastline") or tags.get("waterway") in ("riverbank", "river", "canal") \
                or tags.get("landuse") == "reservoir":
            return True
    if spec.get("green", True):
        if tags.get("leisure") == "park" or tags.get("landuse") == "forest" or tags.get("natural") == "wood":
            return True
    return False


def _want_rel(tags, spec):
    if spec.get("water", True) and (tags.get("natural") == "water" or tags.get("waterway") == "riverbank"):
        return True
    if spec.get("green", True) and (tags.get("leisure") == "park" or tags.get("landuse") == "forest" or tags.get("natural") == "wood"):
        return True
    return False


def extract(spec):
    """spec: {"bbox": [s, w, n, e], "roads", "minor", "water", "green", "rail"} -> Overpass-lik JSON."""
    import osmium
    s, w, n, e = spec["bbox"]
    pbf, rid, url, t_dl = pbf_for_bbox(s, w, n, e)
    t0 = time.perf_counter()
    hdr = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING).header()
    ts = hdr.get("osmosis_replication_timestamp") or ""
    rels = {}
    for r in osmium.FileProcessor(str(pbf), osmium.osm.RELATION):
        tg = dict(r.tags)
        if tg.get("type") in ("multipolygon", None) and _want_rel(tg, spec):
            rels[r.id] = (tg, [(m.ref, m.role) for m in r.members if m.type == "w"])
    need = {}
    for rid_, (tg, mem) in rels.items():
        for ref, role in mem:
            need.setdefault(ref, []).append(rid_)
    ways, member_geom, hit_rel = [], {}, set()
    # snabb förkastning på första nodens läge (Python-anropen per nod är det som kostar):
    # vanliga vägar m.m. längre än ~30 km är sällsynta; relationsmedlemmar (stora sjöar) får större marginal
    mw, ms = 0.35, 0.25
    rw, rs = 2.5, 1.5
    for wy in osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY).with_locations().with_filter(
            osmium.filter.EntityFilter(osmium.osm.WAY)):
        in_need = wy.id in need
        tags = wy.tags
        if not in_need:
            if len(tags) == 0 or not _want_way(tags, spec):
                continue
        nds = wy.nodes
        try:
            l0 = nds[0].location
            x0, y0 = l0.lon, l0.lat
        except Exception:
            continue
        if in_need:
            if not (w - rw <= x0 <= e + rw and s - rs <= y0 <= n + rs):
                continue
        elif not (w - mw <= x0 <= e + mw and s - ms <= y0 <= n + ms):
            continue
        try:
            xs = array.array("d", (nd.lon for nd in nds)); ys = array.array("d", (nd.lat for nd in nds))
        except Exception:  # nod saknas i extraktet (vid regionens kant)
            continue
        inter = not (max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n)
        if in_need:
            member_geom[wy.id] = (xs, ys)
            if inter:
                hit_rel.update(need[wy.id])
        if inter and len(tags) and _want_way(tags, spec):
            ways.append({"type": "way", "id": wy.id, "tags": dict(tags),
                         "geometry": [{"lat": y, "lon": x} for x, y in zip(xs, ys)]})
    els = ways
    for rid_ in hit_rel:
        tg, mem = rels[rid_]
        members = []
        for ref, role in mem:
            g = member_geom.get(ref)
            if g:
                members.append({"type": "way", "ref": ref, "role": role,
                                "geometry": [{"lat": y, "lon": x} for x, y in zip(*g)]})
        if members:
            els.append({"type": "relation", "id": rid_, "tags": tg, "members": members})
    return {"version": 0.6, "generator": "pyosmium " + getattr(osmium, "__version__", "4"),
            "osm3s": {"timestamp_osm_base": ts, "copyright": "The data included in this document is from www.openstreetmap.org. The data is made available under ODbL."},
            "_endpoint": f"geofabrik:{rid} ({url})", "_tider": {"hamtning_s": round(t_dl, 1), "bearbetning_s": round(time.perf_counter() - t0, 1)},
            "elements": els}


def addresses(lat, lon, radius_km=25.0):
    """Alla adresser (addr:street + addr:housenumber) och namngivna vägar inom radien – för lokal geokodning."""
    import osmium
    dlat = radius_km / 111.0; dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    s, w, n, e = lat - dlat, lon - dlon, lat + dlat, lon + dlon
    pbf, rid, url, _ = pbf_for_bbox(s, w, n, e)
    adr, streets = [], {}
    fp = osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY).with_locations().with_filter(
        osmium.filter.KeyFilter("addr:housenumber", "highway"))
    for o in fp:
        tg = o.tags
        if "addr:street" in tg and "addr:housenumber" in tg:
            try:
                if o.is_node():
                    x, y = o.location.lon, o.location.lat
                else:
                    pts = [(nd.lon, nd.lat) for nd in o.nodes]
                    x, y = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            except Exception:
                continue
            if s <= y <= n and w <= x <= e:
                adr.append({"gata": tg["addr:street"], "nr": tg["addr:housenumber"], "ort": tg.get("addr:city", ""),
                            "lat": y, "lon": x, "osm": f"{'node' if o.is_node() else 'way'}/{o.id}"})
        elif o.is_way() and "highway" in tg and "name" in tg:
            try:
                pts = [(nd.lon, nd.lat) for nd in o.nodes]
            except Exception:
                continue
            mx, my = pts[len(pts) // 2]
            if s <= my <= n and w <= mx <= e:
                streets.setdefault(tg["name"], []).append({"lat": my, "lon": mx, "osm": f"way/{o.id}"})
    return {"region": rid, "radie_km": radius_km, "center": [lat, lon], "adresser": adr, "gator": streets}
