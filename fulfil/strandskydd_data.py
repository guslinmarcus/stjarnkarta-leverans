"""Datalager för strandskyddskartan: OpenStreetMap ur Geofabrik-extrakt (ODbL), bearbetat lokalt med pyosmium.

* DRIFT och test: samma väg som osmextract.py – Geofabriks fria extrakt, ingen extern API-tjänst
  (community-Overpass används aldrig här, inte ens i utveckling).
* Vi hämtar det som strandskyddskartan behöver: strandlinjer (natural=coastline), sjöar och vattenytor
  (natural=water, waterway=riverbank, landuse=reservoir – som vägar och multipolygoner), vattendrag som linjer
  (waterway=river/stream/canal), byggnader, befintliga bryggor (man_made=pier), vägar och ortnamn.
* Resultatet har samma form som Overpass "out geom" och cachas per ruta (data/cache/strandskydd/).
  Cachenyckeln (spec-strängen) sparas i orderns meta så att grinden läser exakt samma data.
* Minne: nodindex på disk (osmextract._node_index) – ≈ 1 GB privat minne för hela Sverige-extraktet.

Ingen ritkod och ingen regelkod här; generatorn och grinden gör sina egna tolkningar av datan.
"""
import array
import gzip
import hashlib
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = Path(os.environ.get("STRAND_CACHE", ROOT / "data" / "cache" / "strandskydd"))
SPEC_VERSION = 1

WATER_TAGS = {("natural", "water"), ("natural", "coastline"), ("waterway", "riverbank"), ("landuse", "reservoir")}
WATERWAY_LINES = {"river", "stream", "canal"}
PLACE_KINDS = {"city", "town", "village", "hamlet", "suburb", "quarter", "neighbourhood", "island", "islet",
               "locality", "isolated_dwelling", "farm"}


def spec_for(lat, lon, half_m):
    """Spec-sträng (cachenyckel) för en kvadratisk ruta runt (lat, lon) med halva sidan half_m meter."""
    import math
    dlat = half_m / 111_000.0
    dlon = half_m / (111_000.0 * max(0.2, math.cos(math.radians(lat))))
    d = {"strandskydd": SPEC_VERSION,
         "bbox": [round(lat - dlat, 6), round(lon - dlon, 6), round(lat + dlat, 6), round(lon + dlon, 6)]}
    return json.dumps(d, sort_keys=True)


TILE_M = 1000.0
TILE_HALF_M = 2300.0


def spec_for_point(lat, lon):
    """Cachenyckel för 1 km-rutan (SWEREF 99 TM) som punkten ligger i; uttaget täcker rutans mitt ± 2,3 km,
    alltså minst 1,6 km runt varje punkt i rutan. Närliggande order delar samma uttag."""
    import kartgeo as K
    e, n = (float(v) for v in K.to_sweref(lat, lon))
    et, nt = round(e / TILE_M) * TILE_M, round(n / TILE_M) * TILE_M
    la, lo = (float(v) for v in K.from_sweref(et, nt))
    return spec_for(la, lo, TILE_HALF_M)


def _cache_path(spec):
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / f"strand_{hashlib.sha1(spec.encode()).hexdigest()[:20]}.json.gz"


def _want_way(t):
    if "highway" in t or "building" in t:
        return True
    if (("natural", t.get("natural")) in WATER_TAGS or ("waterway", t.get("waterway")) in WATER_TAGS
            or ("landuse", t.get("landuse")) in WATER_TAGS):
        return True
    if t.get("waterway") in WATERWAY_LINES:
        return True
    if t.get("man_made") == "pier":
        return True
    if t.get("place") in ("island", "islet"):
        return True
    return False


def _want_rel(t):
    if t.get("type") not in ("multipolygon", None):
        return False
    return t.get("natural") == "water" or t.get("waterway") == "riverbank" or t.get("landuse") == "reservoir" \
        or "building" in t or t.get("man_made") == "pier"


def extract(spec):
    """Kör pyosmium över Geofabrik-extraktet som täcker rutan. Returnerar Overpass-lik JSON."""
    import osmium
    import osmextract as X  # regionval, nedladdning och nodindex (ingen ritkod)
    s, w, n, e = json.loads(spec)["bbox"]
    pbf, rid, url, t_dl = X.pbf_for_bbox(s, w, n, e)
    t0 = time.perf_counter()
    hdr = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING).header()
    ts = hdr.get("osmosis_replication_timestamp") or ""
    rels = {}
    for r in osmium.FileProcessor(str(pbf), osmium.osm.RELATION):
        tg = dict(r.tags)
        if _want_rel(tg):
            rels[r.id] = (tg, [(m.ref, m.role) for m in r.members if m.type == "w"])
    need = {}
    for rid_, (tg, mem) in rels.items():
        for ref, role in mem:
            need.setdefault(ref, []).append(rid_)
    ways, member_geom, hit_rel = [], {}, set()
    mw, ms = 0.35, 0.25      # vanliga vägar: första noden inom ~20 km
    rw, rs = 2.5, 1.5        # relationsmedlemmar (stora sjöar) får större marginal
    storage, idxfile = X._node_index()
    fp = osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY).with_locations(storage).with_filter(
        osmium.filter.EntityFilter(osmium.osm.WAY))
    for wy in fp:
        in_need = wy.id in need
        tags = wy.tags
        if not in_need and (len(tags) == 0 or not _want_way(tags)):
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
        except Exception:
            continue
        inter = not (max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n)
        if in_need:
            member_geom[wy.id] = (xs, ys)
            if inter:
                hit_rel.update(need[wy.id])
        if inter and len(tags) and _want_way(tags):
            ways.append({"type": "way", "id": wy.id, "tags": dict(tags),
                         "geometry": [{"lat": y, "lon": x} for x, y in zip(xs, ys)]})
    del fp
    import gc; gc.collect()
    if idxfile is not None:
        try:
            idxfile.unlink()
        except OSError:
            pass
    els = ways
    for nd in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(osmium.filter.KeyFilter("place")):
        tg = nd.tags
        if tg.get("place") in PLACE_KINDS and "name" in tg:
            lo_, la_ = nd.location.lon, nd.location.lat
            if w <= lo_ <= e and s <= la_ <= n:
                els.append({"type": "node", "id": nd.id, "lat": la_, "lon": lo_, "tags": dict(tg)})
    for rid_ in sorted(hit_rel):
        tg, mem = rels[rid_]
        members = []
        for ref, role in mem:
            g = member_geom.get(ref)
            if g:
                members.append({"type": "way", "ref": ref, "role": role,
                                "geometry": [{"lat": y, "lon": x} for x, y in zip(*g)]})
        if members:
            els.append({"type": "relation", "id": rid_, "tags": tg, "members": members})
    els.sort(key=lambda el: (el["type"], el["id"]))  # determinism
    return {"version": 0.6, "generator": "pyosmium " + getattr(osmium, "__version__", "4"),
            "osm3s": {"timestamp_osm_base": ts,
                      "copyright": "The data included in this document is from www.openstreetmap.org. "
                                   "The data is made available under ODbL."},
            "_endpoint": f"geofabrik:{rid} ({url})",
            "_tider": {"hamtning_s": round(t_dl, 1), "bearbetning_s": round(time.perf_counter() - t0, 1)},
            "elements": els}


def fetch(spec):
    """OSM-data för spec (cachas). Returnerar (json, källa)."""
    cp = _cache_path(spec)
    if cp.exists():
        d = json.load(gzip.open(cp, "rt", encoding="utf-8"))
        return d, d.get("_endpoint", "cache")
    d = extract(spec)
    json.dump(d, gzip.open(cp, "wt", encoding="utf-8"))
    return d, d["_endpoint"]


# ------------------------------------------------------------------ Länsstyrelsernas strandskyddsytor (CC0)
LST_REST = ("https://ext-geodata-nationella-visning.lansstyrelsen.se/arcgis/rest/services/LST/"
            "LST_strandskydd_visning_EXT/MapServer")
LST_META = ("https://ext-geodatakatalog.lansstyrelsen.se/GeodataKatalogen/srv/api/records/GetMetaDataById"
            "?id=5f5bea23-5cb6-4b43-bbbd-1dca5c5a5607")
LST_LAYERS = {0: "Länsstyrelsens strandskyddsytor", 1: "Kommunalt upphävda strandskyddsytor"}


def lst_spec(E0, N0, half):
    return json.dumps({"lst": 1, "env": [round(E0 - half), round(N0 - half), round(E0 + half), round(N0 + half)]},
                      sort_keys=True)


def lst_fetch(spec):
    """Polygoner (SWEREF 99 TM) ur Länsstyrelsernas nationella strandskyddstjänst, lager 0 och 1. Licens CC0 1.0
    (metadatapost 5f5bea23-…). Tjänsten visar INTE det generella strandskyddet. Cachas per ruta.
    Returnerar {"hamtad": datum, "ytor": [{"lager", "typ", "kommunkod", "ringar": [[[e, n], ...]]}], "fel": None|text}."""
    import subprocess
    import urllib.parse
    cp = CACHE / f"lst_{hashlib.sha1(spec.encode()).hexdigest()[:20]}.json.gz"
    if cp.exists():
        return json.load(gzip.open(cp, "rt", encoding="utf-8"))
    env = json.loads(spec)["env"]
    out = {"hamtad": time.strftime("%Y-%m-%d"), "ytor": [], "fel": None, "kalla": LST_REST, "metadata": LST_META}
    try:
        for lid, lname in LST_LAYERS.items():
            q = urllib.parse.urlencode({"geometry": ",".join(str(v) for v in env), "geometryType": "esriGeometryEnvelope",
                                        "inSR": 3006, "outSR": 3006, "spatialRel": "esriSpatialRelIntersects",
                                        "outFields": "*",
                                        "returnGeometry": "true", "f": "json"})
            raw = subprocess.run(["curl", "-s", "--max-time", "60", f"{LST_REST}/{lid}/query?{q}"],
                                 capture_output=True, check=True).stdout
            d = json.loads(raw)
            if "error" in d:
                raise RuntimeError(str(d["error"])[:200])
            for f in d.get("features", []):
                a = f.get("attributes", {})
                rings = [[[round(x, 2), round(y, 2)] for x, y in r] for r in f.get("geometry", {}).get("rings", [])]
                out["ytor"].append({"lager": lname, "typ": a.get("strandskyddstyp") or ("upphävt" if lid == 1 else ""),
                                    "kommunkod": a.get("kommunkod"), "land": a.get("omfattning_land"),
                                    "vatten": a.get("omfattning_vatten"), "ringar": rings})
    except Exception as ex:  # tjänsten nere: ingen fil utan uppgift – generatorn skriver att uppgiften saknas
        out = {"hamtad": time.strftime("%Y-%m-%d"), "ytor": [], "fel": f"{type(ex).__name__}: {ex}"[:200],
               "kalla": LST_REST, "metadata": LST_META}
        return out
    out["ytor"].sort(key=lambda y: (y["lager"], y["typ"] or "", json.dumps(y["ringar"])[:200]))
    CACHE.mkdir(parents=True, exist_ok=True)
    json.dump(out, gzip.open(cp, "wt", encoding="utf-8"))
    return out
