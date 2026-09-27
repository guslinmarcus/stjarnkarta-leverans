"""Golfbanor ur OpenStreetMap: register per Geofabrik-region + uppslag av köparens bana (namn + ort/land).

Drift = samma kedja som stadskartan (osmextract.py): Geofabrik-extrakt (ODbL, fri nedladdning, daglig uppdatering),
bearbetade lokalt med pyosmium. Ingen extern OSM-API i drift.

Registret byggs EN gång per region och extraktdatum (cachas; extraktet hämtas om efter OSM_MAX_AGE_DAYS):
  pass 1  relationer (C++-filter på nyckel): golfbanor, golfobjekt och skog/vatten som multipolygon
  pass 2  alla vägar med nodkoordinater (nodindex på disk, sparse_file_array – som osmextract): banornas polygoner
          och alla golf=*-objekt sparas med geometri; skog/vatten/gräs sparas bara som id + första nodens läge
  pass 3  (id-filter i C++) geometri för de skog/vatten-vägar vars första nod ligger vid en bana
Minnet: nodindexet ligger på disk. (IdTracker/IdFilter på NODER används inte: de allokerar en tät bitkarta över
hela id-rymden, ≈ 1,7 GB uppmätt för Sverige 2026-09-27.)
Utdata: data/cache/golf/<region>_<datum>/register.json.gz (banor, hål, föräldraanläggning) + en fil per bana med
dess OSM-objekt. Generator och grind läser samma filer.
"""
import array
import gzip
import json
import math
import os
import re
import shutil
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = Path(os.environ.get("GOLF_CACHE", ROOT / "data" / "cache" / "golf"))
SEARCH_KM = 40.0
MIN_HOLES = 9
# ord som inte skiljer banor åt ("Falsterbo Golfklubb" = "Falsterbo GK" = "Falsterbo")
STOP = {"golf", "club", "clubs", "klubb", "golfklubb", "golfklubben", "golfclub", "golfbana", "golfbanan", "bana", "banan",
        "gc", "gk", "g", "cc", "country", "links", "course", "courses", "the", "and", "och", "und", "of", "at", "de", "del",
        "la", "el", "le", "les", "der", "die", "das", "y", "et", "golfplatz", "golfanlage", "campo", "rcg", "resort",
        "golfbaan", "golfbane", "golfbanen", "gcc", "ab", "ltd", "inc", "llc", "gl", "golfing", "golfpark"}
NAME_KEYS = ("name", "official_name", "alt_name", "short_name", "name:en", "name:sv", "name:de", "name:es", "loc_name", "old_name")
CONTEXT_NATURAL = {"water", "wood", "scrub", "heath", "sand", "beach", "wetland", "grassland"}
CONTEXT_LANDUSE = {"forest", "grass", "meadow", "reservoir", "basin"}
BBOX_MARGIN_DEG = 0.004  # ≈ 300–450 m: skog och vatten strax utanför banans gräns ritas också


def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    return s.replace("&", " and ").replace("ß", "ss").replace("ø", "o").replace("æ", "ae").replace("ł", "l")


def tokens(s):
    return [t for t in re.findall(r"[a-z0-9]+", norm(s)) if t not in STOP]


def all_names(tags):
    out = []
    for k in NAME_KEYS:
        v = tags.get(k)
        if v:
            out += [x.strip() for x in v.split(";") if x.strip()]
    return out


# ------------------------------------------------------------------ geometri (lon, lat)
def ring_area_m2(ring, lat0):
    k = 111320.0 * math.cos(math.radians(lat0))
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        a += (x1 * k) * (y2 * 110540.0) - (x2 * k) * (y1 * 110540.0)
    return abs(a) / 2


def point_in_rings(rings, x, y):
    """Jämn-udda-regeln över alla ringar (yttre + inre) – samma som fyllregeln i PDF:en."""
    ins = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]; xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi:
                ins = not ins
            j = i
    return ins


def _key(p):
    return (round(p[0], 7), round(p[1], 7))


def join_rings(lines):
    """Sätt ihop relationsmedlemmar (polylinjer) till slutna ringar."""
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
        if len(cur) >= 4 and _key(cur[0]) == _key(cur[-1]):
            out.append(cur)
    return out


# ------------------------------------------------------------------ register per region
def _pbf_date(pbf):
    import osmium
    hdr = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING).header()
    return (hdr.get("osmosis_replication_timestamp") or "")[:10] or time.strftime("%Y-%m-%d", time.gmtime(Path(pbf).stat().st_mtime))


def _kind(tg):
    """Vilket lager ett OSM-objekt hör till: 'course', 'golf', 'context' eller None."""
    if tg.get("leisure") == "golf_course":
        return "course"
    if "golf" in tg:
        return "golf"
    if tg.get("natural") in CONTEXT_NATURAL or tg.get("landuse") in CONTEXT_LANDUSE \
            or tg.get("waterway") in ("riverbank", "river", "stream", "canal") or tg.get("leisure") == "park":
        return "context"
    return None


def _node_store():
    import osmium.index
    CACHE.mkdir(parents=True, exist_ok=True)
    for old in CACHE.glob("nodindex_*.bin"):  # rester från tidigare körningar
        try:
            old.unlink()
        except OSError:
            pass
    f = CACHE / f"nodindex_{os.getpid()}.bin"
    return osmium.index.create_map(f"sparse_file_array,{f}"), f


def build_register(pbf, rid):
    """Hela regionens golfbanor i en genomläsning med nodkoordinater. Returnerar (register, {bana: [objekt]})."""
    import numpy as np
    import osmium
    t0 = time.perf_counter()
    rels = {}
    for r in osmium.FileProcessor(str(pbf), osmium.osm.RELATION).with_filter(
            osmium.filter.KeyFilter("leisure", "golf", "natural", "landuse", "waterway")):
        tg = dict(r.tags)
        k = _kind(tg)
        if k == "course" or (k and tg.get("type") == "multipolygon"):
            rels[r.id] = (k, tg, [(m.ref, m.role) for m in r.members if m.type == "w"])
    member_of = {}
    for rid_, (k, tg, mem) in rels.items():
        for ref, role in mem:
            member_of.setdefault(ref, []).append(rid_)
    t1 = time.perf_counter()
    idx, idxfile = _node_store()
    full = {}  # way-id -> (taggar, [(lon, lat)]) för banor, golfobjekt och deras relationsmedlemmar
    cand, cx, cy = array.array("q"), array.array("d"), array.array("d")  # skog/vatten: id + första nodens läge
    fp = osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY).with_locations(idx).with_filter(
        osmium.filter.EntityFilter(osmium.osm.WAY))
    n_tagged = 0
    for wy in fp:
        tags = wy.tags
        mem = member_of.get(wy.id)
        if not len(tags) and mem is None:
            continue
        n_tagged += 1
        k = _kind(tags) if len(tags) else None
        golf_member = mem is not None and any(rels[m][0] in ("course", "golf") for m in mem)
        if k in ("course", "golf") or golf_member:
            try:
                pts = [(round(nd.lon, 7), round(nd.lat, 7)) for nd in wy.nodes]
            except Exception:
                continue
            full[wy.id] = (dict(tags) if k in ("course", "golf") else {}, pts)
        if k == "context" or (mem is not None and not golf_member):
            try:
                l0 = wy.nodes[0].location
                cand.append(wy.id); cx.append(l0.lon); cy.append(l0.lat)
            except Exception:
                continue
    del fp
    t2 = time.perf_counter()
    courses = []
    for wid, (tg, pts) in full.items():
        if tg.get("leisure") == "golf_course" and len(pts) >= 4 and _key(pts[0]) == _key(pts[-1]):
            courses.append({"osm": f"way/{wid}", "tags": tg, "outer": [pts], "inner": []})
    for rid_, (k, tg, mem) in rels.items():
        if k != "course":
            continue
        outer = join_rings([full[ref][1] for ref, role in mem if role != "inner" and ref in full])
        inner = join_rings([full[ref][1] for ref, role in mem if role == "inner" and ref in full])
        if outer:
            courses.append({"osm": f"relation/{rid_}", "tags": tg, "outer": outer, "inner": inner})
    courses.sort(key=lambda c: c["osm"])
    for c in courses:
        xs_ = [p[0] for r in c["outer"] for p in r]; ys_ = [p[1] for r in c["outer"] for p in r]
        c["bbox"] = [min(ys_), min(xs_), max(ys_), max(xs_)]
        c["center"] = [(c["bbox"][0] + c["bbox"][2]) / 2, (c["bbox"][1] + c["bbox"][3]) / 2]
        c["area_m2"] = round(sum(ring_area_m2(r, c["center"][0]) for r in c["outer"]) - sum(ring_area_m2(r, c["center"][0]) for r in c["inner"]))
    grid = {}  # rutnät 0,05° över banornas utvidgade rutor
    for i, c in enumerate(courses):
        s, w, n, e = c["bbox"]; m = BBOX_MARGIN_DEG
        for gx in range(int(math.floor((w - m) / 0.05)), int(math.floor((e + m) / 0.05)) + 1):
            for gy in range(int(math.floor((s - m) / 0.05)), int(math.floor((n + m) / 0.05)) + 1):
                grid.setdefault((gx, gy), []).append(i)

    def near(x, y, m=BBOX_MARGIN_DEG):
        out = []
        for i in grid.get((int(math.floor(x / 0.05)), int(math.floor(y / 0.05))), ()):
            s, w, n, e = courses[i]["bbox"]
            if s - m <= y <= n + m and w - m <= x <= e + m:
                out.append(i)
        return out
    want, ctx_rel = {}, {}
    for wid, x, y in zip(np.frombuffer(cand, dtype=np.int64).tolist(), np.frombuffer(cx).tolist(), np.frombuffer(cy).tolist()):
        cs = near(x, y)
        if not cs:
            continue
        if wid in member_of:
            for r_ in member_of[wid]:
                if rels[r_][0] == "context":
                    ctx_rel.setdefault(r_, set()).update(cs)
        else:
            want[wid] = cs
    for r_ in ctx_rel:
        for ref, role in rels[r_][2]:
            want.setdefault(ref, [])
    geo = {}
    if want:
        for wy in osmium.FileProcessor(str(pbf), osmium.osm.WAY).with_filter(osmium.filter.IdFilter(list(want))):
            try:
                pts = []
                for n_ in wy.nodes:
                    l = idx.get(n_.ref)
                    pts.append((round(l.lon, 7), round(l.lat, 7)))
            except Exception:
                continue
            geo[wy.id] = (dict(wy.tags), pts)
    del idx
    import gc; gc.collect()
    try:
        idxfile.unlink()
    except OSError:
        pass
    t3 = time.perf_counter()
    feats = {c["osm"]: [] for c in courses}

    def add(f, x, y, m):
        for i in near(x, y, m):
            feats[courses[i]["osm"]].append(f)
    for wid in sorted(full):
        tg, pts = full[wid]
        if tg and tg.get("leisure") != "golf_course":
            x, y = pts[len(pts) // 2]
            add({"osm": f"way/{wid}", "tags": tg, "rings": [pts]}, x, y, 0.0015)
    for rid_ in sorted(rels):
        k, tg, mem = rels[rid_]
        src = full if k == "golf" else geo if k == "context" else None
        if src is None:
            continue
        outer = join_rings([src[ref][1] for ref, role in mem if role != "inner" and ref in src])
        inner = join_rings([src[ref][1] for ref, role in mem if role == "inner" and ref in src])
        if not outer:
            continue
        f = {"osm": f"relation/{rid_}", "tags": tg, "rings": outer + inner, "multi": True}
        if k == "golf":
            add(f, *outer[0][0], 0.0015)
        else:
            for i in sorted(ctx_rel.get(rid_, ())):
                feats[courses[i]["osm"]].append(f)
    for wid in sorted(want):
        if want[wid] and wid in geo:
            tg, pts = geo[wid]
            for i in want[wid]:
                feats[courses[i]["osm"]].append({"osm": f"way/{wid}", "tags": tg, "rings": [pts]})
    hl = []
    for wid in sorted(full):
        tg, pts = full[wid]
        if tg.get("golf") == "hole" and len(pts) >= 2:
            hl.append({"osm": f"way/{wid}", "ref": tg.get("ref", ""), "par": tg.get("par", ""), "pts": pts})
    for c in courses:
        c["holes"] = []
    for i, h in enumerate(hl):  # hålets mittpunkt i banans polygon; en anläggning får alla sina delbanors hål
        p = h["pts"]
        mx, my = ((p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2) if len(p) == 2 else p[len(p) // 2]
        for j in near(mx, my, 0):
            if point_in_rings(courses[j]["outer"] + courses[j]["inner"], mx, my):
                courses[j]["holes"].append(i)
    for c in courses:  # förälder = minsta större bana vars polygon omsluter banans mitt
        best = None
        x, y = c["center"][1], c["center"][0]
        for j in near(x, y, 0):
            o = courses[j]
            if o is not c and o["area_m2"] > c["area_m2"] * 1.15 and point_in_rings(o["outer"] + o["inner"], x, y):
                if best is None or o["area_m2"] < best["area_m2"]:
                    best = o
        c["parent"] = best["osm"] if best else None
    reg = {"region": rid, "pbf": Path(pbf).name, "osm_datum": _pbf_date(pbf), "banor": courses, "hal": hl,
           "tider": {"relationer_s": round(t1 - t0, 1), "vagar_med_noder_s": round(t2 - t1, 1), "kontext_s": round(t3 - t2, 1),
                     "sammanstallning_s": round(time.perf_counter() - t3, 1)},
           "antal": {"taggade_vagar": n_tagged, "golfvagar": len(full), "kontextkandidater": len(cand), "kontextvagar": len(geo)}}
    return reg, feats


def _dir(rid, d):
    return CACHE / f"{rid.replace('/', '_')}_{d}"


def register_for_point(lat, lon):
    """Registret för regionen som täcker punkten (Geofabrik-filen hämtas/cachas av osmextract). -> (register, mapp)"""
    import osmextract
    pbf, rid, url, _ = osmextract.pbf_for_bbox(lat - 0.005, lon - 0.005, lat + 0.005, lon + 0.005)
    return _register(pbf, rid, url)


def register_for_region(rid):
    """Registret för en namngiven Geofabrik-region (t.ex. 'england') – för förhandskollens index (verktyg/golfindex.py)."""
    import osmextract
    ip = osmextract.GF / "index-v1.json"
    if not osmextract._fresh(ip, 30):
        osmextract._get(osmextract.INDEX_URL, ip)
    idx = json.load(open(ip, encoding="utf-8"))
    url = next(f["properties"]["urls"]["pbf"] for f in idx["features"] if f["properties"]["id"] == rid)
    pbf = osmextract.GF / f"{rid.replace('/', '_')}-latest.osm.pbf"
    if not osmextract._fresh(pbf, osmextract.MAX_AGE_DAYS):
        osmextract._get(url, pbf, timeout=3600)
    return _register(pbf, rid, url)


def _register(pbf, rid, url):
    d = _dir(rid, _pbf_date(pbf))
    rp = d / "register.json.gz"
    if rp.exists():
        return json.load(gzip.open(rp, "rt", encoding="utf-8")), d
    for old in CACHE.glob(f"{rid.replace('/', '_')}_*"):
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    reg, feats = build_register(pbf, rid)
    reg["url"] = url
    tmp = d.with_name(d.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
    for cid, fs in feats.items():
        with gzip.open(tmp / (cid.replace("/", "_") + ".json.gz"), "wt", encoding="utf-8") as f:
            json.dump({"bana": cid, "objekt": fs}, f)
    with gzip.open(tmp / "register.json.gz", "wt", encoding="utf-8") as f:
        json.dump(reg, f)
    os.replace(tmp, d)
    return reg, d


def load_register(regdir):
    return json.load(gzip.open(Path(regdir) / "register.json.gz", "rt", encoding="utf-8"))


def course_features(regdir, cid):
    """Banans OSM-objekt (golf=*, skog/vatten kring banan) ur registrets cache."""
    return json.load(gzip.open(Path(regdir) / (cid.replace("/", "_") + ".json.gz"), "rt", encoding="utf-8"))["objekt"]


# ------------------------------------------------------------------ uppslag
def dist_km(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = 0.5 - math.cos((la2 - la1) * p) / 2 + math.cos(la1 * p) * math.cos(la2 * p) * (1 - math.cos((lo2 - lo1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))


def _tok_match(q, cand):
    """Ett frågeord matchar ett namnord om de är lika, eller om båda är ≥ 5 tecken och nästan lika (stavfel)."""
    from rapidfuzz import fuzz
    for c in cand:
        if q == c or (len(q) >= 5 and len(c) >= 5 and fuzz.ratio(q, c) >= 88):
            return True
    return False


def hole_summary(reg, c):
    """Hålen i banan: antal, numren (heltal) och om de är 1…n utan luckor och dubbletter (n ≥ 9)."""
    hs = [reg["hal"][i] for i in c["holes"]]
    nums = sorted(int(h["ref"]) for h in hs if re.fullmatch(r"\d{1,2}", h["ref"] or ""))
    n = len(hs)
    return {"antal": n, "numrerade": nums, "komplett": n >= MIN_HOLES and nums == list(range(1, n + 1))}


def resolve(name, lat, lon, reg, radius_km=SEARCH_KM):
    """Köparens bana -> (bana, None, detalj) eller (None, felkod, detalj).
    Poäng = andel av frågans ord som finns i banans namn (alla namnvarianter) eller i den omslutande anläggningens namn.
    Bland banor med högst poäng vinner den vars EGET namn täcker flest frågeord. Är flera kvar, eller är vinnaren en
    anläggning med flera namngivna delbanor om vardera ≥ 9 hål, är frågan tvetydig – köparen får välja bland namnen."""
    q = tokens(name)
    if not q:
        return None, "course_not_found", {"orsak": "inget sökbart ord i bannamnet"}
    by_id = {c["osm"]: c for c in reg["banor"]}
    near = [c for c in reg["banor"] if dist_km(lat, lon, *c["center"]) <= radius_km and all_names(c["tags"])]
    scored = []
    for c in near:
        own = set(t for n in all_names(c["tags"]) for t in tokens(n))
        par = by_id.get(c.get("parent"))
        ctx = own | (set(t for n in all_names(par["tags"]) for t in tokens(n)) if par else set())
        cov = sum(1 for t in own if any(t == x for x in q)) / max(1, len(own))  # andel av banans egna ord som köparen skrev exakt
        scored.append((sum(_tok_match(t, ctx) for t in q) / len(q), sum(_tok_match(t, own) for t in q), c, cov))
    if not scored or max(s[0] for s in scored) < 0.66:
        return None, "course_not_found", {"sokradie_km": radius_km, "banor_nara": len(near),
                                           "narmast": sorted((round(dist_km(lat, lon, *c["center"]), 1), c["tags"].get("name", "")) for c in near)[:8],
                                           "alternativ": [n_ for _, n_ in sorted((round(dist_km(lat, lon, *c["center"]), 1), c["tags"].get("name", ""))
                                                                                  for c in near)[:8]]}
    best = max(s[0] for s in scored)
    top = [s for s in scored if s[0] == best]
    mo = max(s[1] for s in top)
    top = [s for s in top if s[1] == mo]
    mc = max(s[3] for s in top)
    top = [s for s in top if s[3] == mc]
    uniq = []  # samma bana karterad två gånger (samma namn, mitt < 300 m isär) räknas som en
    for s in sorted(top, key=lambda s: (-len(s[2]["holes"]), s[2]["osm"])):
        c = s[2]
        if not any(c["tags"].get("name") == u["tags"].get("name") and dist_km(*c["center"], *u["center"]) < 0.3 for u in uniq):
            uniq.append(c)
    if len(uniq) > 1:  # en anläggning och dess egna delbanor: delbanorna är alternativen
        ids = {c["osm"] for c in uniq}
        kids = [c for c in uniq if c.get("parent") in ids]
        if kids and len(kids) == len(uniq) - 1:
            uniq = kids
    if len(uniq) > 1:  # lika bra namnträffar: den närmaste vinner om den är klart närmast orten (≤ 10 km och < 1/3 av tvåans avstånd)
        dd = sorted((dist_km(lat, lon, *c["center"]), c["osm"], c) for c in uniq)
        if dd[0][0] <= 10 and dd[0][0] < dd[1][0] / 3:
            uniq = [dd[0][2]]
    if len(uniq) > 1:
        return None, "course_ambiguous", {"alternativ": sorted(f"{c['tags'].get('name', '')} ({dist_km(lat, lon, *c['center']):.0f} km)"
                                                               if sum(1 for u in uniq if u["tags"].get("name") == c["tags"].get("name")) > 1
                                                               else c["tags"].get("name", "") for c in uniq)}
    c = uniq[0]
    kids = [o for o in reg["banor"] if o.get("parent") == c["osm"] and all_names(o["tags"]) and hole_summary(reg, o)["antal"] >= MIN_HOLES]
    if len(kids) >= 2:
        return None, "course_ambiguous", {"alternativ": sorted(f"{c['tags'].get('name', '')} – {k['tags'].get('name', '')}" for k in kids),
                                           "anlaggning": c["osm"]}
    return c, None, {"poang": round(best, 3), "egna_ord": mo, "toppkandidater": len(top), "fraga_ord": q}


def resolve_with_facility(name, lat, lon, reg):
    """Som resolve, men "Anläggning – Delbana" (köparen valde bland alternativen) slås upp exakt."""
    m = re.split(r"\s[–-]\s", name, maxsplit=1)
    if len(m) == 2:
        c, err, det = resolve(m[0], lat, lon, reg)
        par = det.get("anlaggning") if err == "course_ambiguous" else (c["osm"] if c else None)
        if par:
            hit = [k for k in reg["banor"] if k.get("parent") == par and norm(k["tags"].get("name", "")) == norm(m[1])]
            if len(hit) == 1:
                return hit[0], None, {"poang": 1.0, "via_anlaggning": par, "fraga_ord": tokens(name)}
    return resolve(name, lat, lon, reg)
