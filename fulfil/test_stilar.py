"""Stresstest: N slumpade ordrar per stil genom generator + kvalitetsgrind, plus mutationstester
(avsiktliga fel som grinden MÅSTE fånga). Resultat -> ut/stiltest/rapport.json

    python test_stilar.py [N=30]
"""
import gzip, json, os, random, sys, time, copy
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "ut" / "stiltest"
sys.path.insert(0, str(ROOT))

NAMES = ["Anna & Erik", "Emma & James", "Welcome, Olivia", "Sofia Lindqvist", "Elias", "Jürgen & Märta",
         "Zoë & Chloé", "Håkan Öberg", "For Mum, with love", "Our first home", "Class of 2026", "Noah",
         "In loving memory of Margaret", "Björn & Søren", "Maximilian-Alexander von Hohenstein", "Łukasz & Ewa",
         "Happy 50th, David", "The night we met", "Ingrid", "Aoife & Seán", "Mia & Leo – forever", "Grandpa Joe"]


def pick_places(rng, n):
    rows = []
    for line in gzip.open(ROOT / "data" / "orter.tsv.gz", "rt", encoding="utf-8"):
        name, lat, lon, cc, pop, tz, _ = line.rstrip("\n").split("\t")
        if int(pop or 0) > 150000:
            rows.append((name, float(lat), float(lon), tz))
    return rng.sample(rows, n)


def one(order):
    os.environ["STJARN_OUT"] = str(OUT)
    import stjarnkarta, kvalitetsgrind
    p = OUT / f"{order['id']}.json"
    p.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    t = time.perf_counter()
    stjarnkarta.generate(p)
    r = kvalitetsgrind.run(OUT / f"{order['id']}_meta.json")
    return {"id": order["id"], "style": order["style"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
            "underkanda": [(u["grind"], u["detalj"]) for u in r["underkanda"]], "tid_s": round(time.perf_counter() - t, 2)}


def mutation(args):
    """Förstör facit/rastret på kända sätt – grinden ska underkänna."""
    kind, meta_path = args
    import kvalitetsgrind, fitz
    meta = json.load(open(meta_path, encoding="utf-8"))
    m = copy.deepcopy(meta)
    if kind == "fel_stjarnfarg":  # stjärnorna ritade i fel färg jämfört med stilen
        m["style"]["colors"]["star"] = [0.5, 0.0, 0.5]
    elif kind == "flyttad_stjarna":  # generatorn placerar en stjärna 3 mm fel
        m["check_stars"][0]["page_x_pt"] += 8.5
    elif kind == "fel_manfas":  # månfas-raden ritad för fel dag
        m["moon_row"][3]["frac"] = min(1, m["moon_row"][3]["frac"] + 0.3)
    elif kind == "fel_tid":  # UTC-tiden förskjuten 2 h (tidszonsfel)
        from datetime import datetime, timedelta
        d = datetime.fromisoformat(m["utc"].replace("Z", "+00:00")) + timedelta(hours=2)
        m["utc"] = d.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif kind == "okand_stil":
        m["style"]["name"] = "neon"
    elif kind == "lag_kontrast":  # text i nästan samma färg som papperet
        m["style"]["colors"]["text"] = [c * 255 / 255 for c in m["style"]["colors"].get("page", [1, 1, 1])]
    mp = Path(meta_path).with_name(Path(meta_path).stem + f"_MUT_{kind}.json")
    json.dump(m, open(mp, "w", encoding="utf-8"), ensure_ascii=False)
    r = kvalitetsgrind.run(mp)
    return {"mutation": kind, "meta": Path(meta_path).name, "fangad": not r["godkand"],
            "grindar_som_slog": [u["grind"] for u in r["underkanda"]]}


def main(n):
    import stjarnkarta
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260927)
    orders = []
    for sname, st in stjarnkarta.STYLES.items():
        places = pick_places(rng, n)
        for i in range(n):
            place, lat, lon, tz = places[i]
            y = rng.randint(1950, 2030)
            orders.append({"id": f"{sname}_{i:02d}", "name": rng.choice(NAMES), "place": place[:40], "lat": round(lat, 4),
                           "lon": round(lon, 4), "timezone": tz,
                           "datetime_local": f"{y}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T{rng.randint(0, 23):02d}:{rng.choice([0, 15, 30, 45]):02d}",
                           "languages": [rng.choice(["en", "sv", "de"])], "style": sname,
                           "palette": rng.choice(list(st["palettes"])),
                           "frame": rng.choice(list(stjarnkarta.FRAMES)) if rng.random() < 0.5 else None,
                           "font": rng.choice(list(stjarnkarta.FONT_SETS)) if rng.random() < 0.4 else None})
    t = time.perf_counter()
    with Pool(max(2, os.cpu_count() - 1)) as pool:
        res = pool.map(one, orders)
        muts = [("fel_stjarnfarg", OUT / "akvarell_00_meta.json"), ("flyttad_stjarna", OUT / "minimal_00_meta.json"),
                ("fel_manfas", OUT / "manfas_00_meta.json"), ("fel_tid", OUT / "hjarta_00_meta.json"),
                ("okand_stil", OUT / "barnrum_00_meta.json"), ("lag_kontrast", OUT / "midnatt_00_meta.json")]
        mres = pool.map(mutation, muts)
    per = {}
    for r in res:
        d = per.setdefault(r["style"], {"ordrar": 0, "godkanda": 0, "underkanda": []})
        d["ordrar"] += 1; d["godkanda"] += r["godkand"]
        if not r["godkand"]:
            d["underkanda"].append({"id": r["id"], "grindar": r["underkanda"]})
    rep = {"datum": time.strftime("%Y-%m-%d %H:%M"), "per_stil": per, "mutationer": mres,
           "total_tid_s": round(time.perf_counter() - t, 1), "snitt_tid_per_order_s": round(sum(r["tid_s"] for r in res) / len(res), 2)}
    json.dump(rep, open(OUT / "rapport.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
