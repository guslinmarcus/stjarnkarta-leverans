"""Grind för förhandskollen (/golf-kolla): Workerns svar (worker/src/golfkolla.js + golfindex.json) ska vara exakt samma som
orderns svar (fulfil.geocode + golfdata.resolve_with_facility + golfbana.course_check) – för VARJE bana i indexet.

  1. alla banor: fråga = banans namn + närmaste ort + land -> samma status (ok / course_holes_incomplete / course_ambiguous /
     course_not_found) och samma bana (eller samma alternativ) i JS och Python
  2. fasta fall: Falsterbo (ok), Pebble Beach (ofullständig), okänd bana, okänt land, ort utanför förhandskollade regioner
  3. varje "fungerar"-bana har en förhandsvisning med kontur och lika många hållinjer som hål

Kör regionerna en i taget (ett register i minnet åt gången).   python fulfil/verktyg/test_golfkolla.py
"""
import json
import subprocess
import sys
from pathlib import Path

F = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(F))
import fulfil  # noqa: E402
import golfdata as G  # noqa: E402
import golfbana as B  # noqa: E402

W = F.parent / "worker" / "src"
IDX = json.loads((W / "golfindex.json").read_text(encoding="utf-8"))
JS = """
import { readFileSync } from "node:fs";
import { checkCourse } from "%s";
const idx = JSON.parse(readFileSync(%s, "utf8"));
const qs = JSON.parse(readFileSync(0, "utf8"));
const out = qs.map(([c, t, l]) => { const r = checkCourse(idx, c, t, l);
  return { status: r.status, id: r.course ? r.course.id : "", opts: (r.options || []).map(o => o.c.id).sort() }; });
process.stdout.write(JSON.stringify(out));
""" % ((W / "golfkolla.js").as_uri(), json.dumps(str(W / "golfindex.json")))


def js(queries):
    p = subprocess.run(["node", "--input-type=module", "-e", JS], input=json.dumps(queries), capture_output=True, text=True,
                       encoding="utf-8", check=True)
    return json.loads(p.stdout)


def py(reg, regdir, rid, course, town, country):
    g = fulfil.geocode(town, country)
    if not g:
        return {"status": "not_covered", "id": "", "opts": []}
    c, err, det = G.resolve_with_facility(course, g[1], g[2], reg)
    if err == "course_ambiguous":
        names = det["alternativ"]
        return {"status": err, "id": "", "opts_n": sorted(names)}
    if err:
        return {"status": err, "id": "", "opts": []}
    _, err2, _ = B.course_check(reg, regdir, c)
    return {"status": err2 or "ok", "id": f"{rid}:{c['osm']}", "opts": []}


GF = json.load(open(F / "data" / "cache" / "geofabrik" / "index-v1.json", encoding="utf-8"))


def region_of(lat, lon):
    """osmextract.region_for_bbox med indexfilen inläst en gång (samma regel: minsta region som täcker rutan)."""
    import osmextract
    s, w, n, e = lat - 0.005, lon - 0.005, lat + 0.005, lon + 0.005
    best = None
    for f in GF["features"]:
        g = f.get("geometry")
        if not f["properties"].get("urls", {}).get("pbf") or not g or f["properties"]["id"] in osmextract.SAKNAS:
            continue
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        if not all(any(osmextract._ring_contains(p[0], x, y) for p in polys) for x, y in [(w, s), (e, s), (e, n), (w, n)]):
            continue
        xs = [c[0] for p in polys for c in p[0]]; ys = [c[1] for p in polys for c in p[0]]
        a = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if best is None or a < best[0]:
            best = (a, f["properties"]["id"])
    return best[1] if best else ""


def main():
    """Varje bana frågas med sin närmaste ort; Python-svaret tas ur registret för den region ordern skulle använda
    (osmextract-regeln på ortens läge), precis som i drift."""
    fel, n = [], 0
    by_r = {}
    for e in IDX["banor"]:
        if not e["ort"]:
            continue
        g = fulfil.geocode(e["ort"], e["cc"])
        by_r.setdefault(region_of(g[1], g[2]) if g else "", []).append(e)
    dirs = {}
    for p in G.CACHE.iterdir():
        if (p / "register.json.gz").exists() and not p.name.endswith(".part"):
            dirs[p.name.rsplit("_", 1)[0]] = p
    byid = {e["id"]: e for e in IDX["banor"]}
    for rid, es in sorted(by_r.items()):
        if rid.replace("/", "_") not in dirs:  # orten ligger i en region utan register: kollen ska säga "inte kollad"
            rj = js([(e["n"], e["ort"], e["cc"]) for e in es])
            for e, a in zip(es, rj):
                n += 1
                if a["status"] != "not_covered":
                    fel.append((rid, e["n"], e["ort"], a, "not_covered"))
            print(f"{rid or '(ingen)'}: {len(es)} frågor utan register, fel hittills {len(fel)}", flush=True)
            continue
        d = dirs[rid.replace("/", "_")]
        reg = G.load_register(d)
        qs = [(e["n"], e["ort"], e["cc"]) for e in es if e["ort"]]
        rj = js(qs)
        for (course, town, cc), a in zip(qs, rj):
            b = py(reg, d, rid, course, town, cc)
            n += 1
            if a["status"] != b["status"]:
                fel.append((rid, course, town, cc, a, b)); continue
            if b["status"] in ("ok", "course_holes_incomplete") and a["id"] != b["id"]:
                # samma bana karterad två gånger (dubblett som indexet slagit ihop) räknas lika
                ea, eb = byid.get(a["id"]), byid.get(b["id"])
                if not (ea and eb is None and ea["n"] == course):
                    fel.append((rid, course, town, cc, a, b)); continue
            if b["status"] == "course_ambiguous":
                la = sorted(byid[i]["n"] for i in a["opts"])
                if len(la) != len(b["opts_n"]):
                    fel.append((rid, course, town, cc, a, b))
        del reg
        print(f"{rid}: {len(qs)} frågor, fel hittills {len(fel)}", flush=True)
    fasta = [(("Falsterbo Golfklubb", "Skanör med Falsterbo", "Sweden"), "ok"),
             (("Falsterbo", "Höllviken", "Sverige"), "ok"),
             (("Pebble Beach Golf Links", "Carmel-by-the-Sea", "USA"), "course_holes_incomplete"),
             (("Pebble Beach", "Monterey", "United States"), "course_holes_incomplete"),
             (("Nonexistent Links", "Danderyd", "Sweden"), "course_not_found"),
             (("Falsterbo", "Skanör med Falsterbo", "Atlantis"), "country_not_found"),
             (("Golf Club München", "München", "Germany"), "not_covered")]
    for (q, want), a in zip(fasta, js([q for q, _ in fasta])):
        n += 1
        if a["status"] != want:
            fel.append(("fast", *q, a, want))
    for e in IDX["banor"]:
        if e["s"] == "ok":
            n += 1
            g = e.get("g")
            if not g or not g["o"] or len(g["l"]) != e["h"]:
                fel.append(("forhandsvisning", e["n"], e["id"]))
    for f in fel[:40]:
        print("FEL", f)
    print(f"{n} kontroller, {len(fel)} fel")
    return 1 if fel else 0


if __name__ == "__main__":
    sys.exit(main())
