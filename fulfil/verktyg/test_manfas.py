"""Stresstest för månfas-affischen: N slumpade ordrar per stil genom generator + grind, plus felinjektion
(medvetet inlagda fel i generatorn som grinden MÅSTE fälla). Körs sekventiellt (lågt minne).

    python verktyg/test_manfas.py [N=30]   -> ut/manfas_test/rapport.json
"""
import gzip, json, os, random, sys, time, traceback
from pathlib import Path

FULFIL = Path(__file__).resolve().parent.parent
OUT = FULFIL / "ut" / "manfas_test"
sys.path.insert(0, str(FULFIL))
os.environ["STJARN_OUT"] = str(OUT)

NAMES = ["Olivia Bennett", "Signe", "Ben", "Jürgen Müller", "Zoë & Chloé", "Håkan Öberg", "Élodie Lefèvre", "Łukasz",
         "Aoife Ní Bhriain", "María José García", "Noah", "Maximilian-Alexander von Hohenstein", "Mia & Leo", "Grandpa Joe",
         "Astrid", "Søren", "François", "Inés", "Björn Nilsson", "Welcome, little Freya", "Luna", "Ava Grace", "Theo",
         "Isla Rose", "Mateo", "Emma & James", "Ingrid", "Nils-Erik", "Camille", "Lucía"]
FAMILY = ["The Lind family", "Familjen Guslin", "Familie Schmidt", "Famille Martin", "Familia García", "Our moons", "", "Mama & kids"]
EXTRA_PLACES = [("Sydney", -33.8679, 151.2073, "Australia/Sydney", "AU"), ("Cape Town", -33.9258, 18.4232, "Africa/Johannesburg", "ZA"),
                ("Buenos Aires", -34.6132, -58.3772, "America/Argentina/Buenos_Aires", "AR"), ("Auckland", -36.8485, 174.7633, "Pacific/Auckland", "NZ"),
                ("Tromsø", 69.6496, 18.957, "Europe/Oslo", "NO"), ("Quito", -0.2299, -78.525, "America/Guayaquil", "EC")]


def places(rng):
    lander = {}
    for line in gzip.open(FULFIL / "data" / "lander.tsv.gz", "rt", encoding="utf-8"):
        iso, name = line.rstrip("\n").split("\t"); lander.setdefault(iso, name)
    rows = []
    for line in gzip.open(FULFIL / "data" / "orter.tsv.gz", "rt", encoding="utf-8"):
        name, lat, lon, cc, pop, tz, _ = line.rstrip("\n").split("\t")
        if int(pop or 0) > 300000:
            rows.append((name, float(lat), float(lon), tz, cc))
    rows += EXTRA_PLACES * 20  # fler södra halvklotet/polcirkeln/ekvatorn
    return rows, lander


def person(rng, rows, lander, name):
    p = rng.choice(rows)
    y = rng.randint(1900, 2050)
    return {"name": name, "date": f"{y}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "time": None if rng.random() < 0.5 else f"{rng.randint(0, 23):02d}:{rng.choice([0, 7, 15, 30, 45, 59]):02d}",
            "place": p[0][:40], "country": lander.get(p[4], p[4])[:40], "lat": round(p[1], 4), "lon": round(p[2], 4), "timezone": p[3]}


def make_orders(n):
    import manfas
    rng = random.Random(20260928)
    rows, lander = places(rng)
    orders = []
    for st in manfas.STYLES:
        for i in range(n):
            fam = rng.random() < 0.3
            o = {"id": f"{st}_{i:02d}", "product": "manfas", "style": st, "languages": [rng.choice(["en", "sv", "de", "fr", "es"])]}
            if fam:
                k = rng.randint(2, 6)
                o.update(mode="family", text=rng.choice(FAMILY), heading="born", row="none",
                         moons=[person(rng, rows, lander, nm) for nm in rng.sample(NAMES, k)])
            else:
                o.update(mode="single", text="", heading=rng.choice(["born", "born", "wedding", "met", "none"]),
                         row=rng.choice(["none", "month", "week"]), moons=[person(rng, rows, lander, rng.choice(NAMES))])
            orders.append(o)
    return orders


def one(order, fel=""):
    import importlib
    os.environ["FELINJEKTION"] = fel
    import manfas, grind_manfas
    manfas.FEL = fel
    p = OUT / f"{order['id']}.json"
    p.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    t = time.perf_counter()
    meta = manfas.generate(p)
    tg = time.perf_counter() - t
    r = grind_manfas.run(OUT / f"{order['id']}_meta.json")
    return {"id": order["id"], "style": order["style"], "mode": order["mode"], "row": order.get("row"), "lang": order["languages"][0],
            "godkand": r["godkand"], "grindar": r["antal_grindar"], "underkanda": [(u["grind"], u["detalj"][:300]) for u in r["underkanda"]],
            "tid_generator_s": round(tg, 2), "tid_totalt_s": round(time.perf_counter() - t, 2)}


INJ = {  # felinjektion: (namn, vilken slags order den kräver)
    "belysning_fel": "single", "belysning_liten": "single", "fasnamn_fel": "single", "orientering_fel": "single",
    "tidszon_fel": "single", "datum_fel": "single", "rad_fel": "row", "familj_fel": "family", "text_utanfor": "single",
    "text_overlapp": "single", "typsnitt_fel": "single", "sprak_fel": "nonen", "saknad_text": "single",
}


def main(n):
    OUT.mkdir(parents=True, exist_ok=True)
    orders = make_orders(n)
    t0 = time.perf_counter()
    res = []
    for k, o in enumerate(orders):
        try:
            res.append(one(o))
        except SystemExit as e:
            res.append({"id": o["id"], "style": o["style"], "godkand": False, "underkanda": [("generator_vagrade", str(e.code))]})
        except Exception:
            res.append({"id": o["id"], "style": o["style"], "godkand": False, "underkanda": [("krasch", traceback.format_exc()[-600:])]})
        r = res[-1]
        print(f"[{k + 1}/{len(orders)}] {o['id']} {'OK' if r['godkand'] else 'UNDERKÄND ' + str(r['underkanda'])[:300]}", flush=True)
    # felinjektion: två ordrar per fel, olika stilar
    inj = []
    rng = random.Random(7)
    for fel, kind in INJ.items():
        cand = [o for o in orders if (kind == "family" and o["mode"] == "family") or (kind == "row" and o.get("row") in ("month", "week"))
                or (kind == "single" and o["mode"] == "single") or (kind == "nonen" and o["languages"][0] != "en" and o["mode"] == "single")]
        if fel == "orientering_fel":  # bara meningsfullt när skivan inte är nästan full/ny
            cand = [o for o in cand if 0.1 < next(r_ for r_ in [json.load(open(OUT / f"{o['id']}_meta.json", encoding="utf-8"))])["data"]["moons"][0]["frac"] < 0.9]
        styles = {}
        for o in rng.sample(cand, len(cand)):
            styles.setdefault(o["style"], o)
        for o in list(styles.values())[:2]:
            oo = dict(o, id=o["id"] + "_FEL_" + fel)
            try:
                r = one(oo, fel)
                inj.append({"fel": fel, "order": oo["id"], "fangad": not r["godkand"], "grindar_som_slog": [u[0] for u in r["underkanda"]]})
            except SystemExit as e:
                inj.append({"fel": fel, "order": oo["id"], "fangad": True, "grindar_som_slog": [f"generator vägrade: {e.code}"]})
            print(inj[-1], flush=True)
    os.environ["FELINJEKTION"] = ""
    per = {}
    for r in res:
        s = per.setdefault(r["style"], {"ordrar": 0, "godkanda": 0, "tid_s": []})
        s["ordrar"] += 1; s["godkanda"] += r["godkand"]; s["tid_s"].append(r.get("tid_totalt_s", 0))
    for s in per.values():
        ts = s.pop("tid_s"); s["tid_median_s"] = sorted(ts)[len(ts) // 2]; s["tid_max_s"] = max(ts)
    rap = {"ordrar": len(res), "godkanda": sum(r["godkand"] for r in res), "per_stil": per,
           "felinjektion": {"antal": len(inj), "fangade": sum(x["fangad"] for x in inj), "detaljer": inj},
           "total_tid_s": round(time.perf_counter() - t0, 1), "resultat": res}
    (OUT / "rapport.json").write_text(json.dumps(rap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in rap.items() if k not in ("resultat",)} | {"felinjektion": {k: v for k, v in rap["felinjektion"].items() if k != "detaljer"}},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
