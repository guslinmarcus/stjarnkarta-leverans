"""Stresstest för "hitta hit"-kartan (brollopskarta.py): ett par verkliga ordrar (1-3 platser, med och utan
gatuadress) genom generator + grind, plus felinjektion (medvetet inlagda fel som grinden MÅSTE fälla).

    python verktyg/test_brollopskarta.py   -> ut/brollopskarta_test/rapport.json
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

FULFIL = Path(__file__).resolve().parent.parent
OUT = FULFIL / "ut" / "brollopskarta_test"
sys.path.insert(0, str(FULFIL))
os.environ["STJARN_OUT"] = str(OUT)

FEL_TYPER = ["saknad_attribution", "fel_avstand", "fel_tid", "fel_koordinat", "fel_ordning", "rutt_pa_fel_vag",
             "saknad_markor", "tomt_omrade", "lag_upplosning", "text_kapad"]


def base_orders():
    import fulfil
    g = fulfil.geocode("Ystad", "Sverige")
    name, lat, lon, cc, pop, tz = g
    p1 = {"role": "vigsel", "label": "", "place": "Ystad", "country": "Sverige", "cc": cc, "lat": round(lat, 4),
          "lon": round(lon, 4), "pop": pop, "address": "Stora Ostergatan 20"}
    p2 = {"role": "mottagning", "label": "", "place": "Ystad", "country": "Sverige", "cc": cc, "lat": round(lat, 4),
          "lon": round(lon, 4), "pop": pop, "address": ""}
    p3 = {"role": "hotell", "label": "Grand Hotel", "place": "Ystad", "country": "Sverige", "cc": cc,
          "lat": round(lat + 0.010, 4), "lon": round(lon + 0.006, 4), "pop": pop, "address": ""}
    return [
        {"id": "bk_2platser_klassisk", "product": "brollopskarta", "text": "Anna & Erik", "date": "2026-06-13",
         "style": "klassisk", "places": [p1, p2], "languages": ["sv"]},
        {"id": "bk_3platser_natt", "product": "brollopskarta", "text": "Signe & Ben", "date": "2026-08-01",
         "style": "natt", "places": [p1, p2, p3], "languages": ["en"]},
        {"id": "bk_1plats_sepia", "product": "brollopskarta", "text": "Kalaset hos oss", "date": "",
         "style": "sepia", "places": [p2], "languages": ["de"]},
        {"id": "bk_blueprint", "product": "brollopskarta", "text": "The Lind family", "date": "2026-09-05",
         "style": "blueprint", "places": [p1, p2], "languages": ["en"]},
    ]


def one(order, fel=""):
    os.environ["FELINJEKTION"] = fel
    import importlib
    import brollopskarta
    import grind_brollopskarta
    importlib.reload(brollopskarta)  # FEL läses från os.environ vid importtillfället
    importlib.reload(grind_brollopskarta)
    p = OUT / f"{order['id']}.json"
    p.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    t = time.perf_counter()
    brollopskarta.generate(p)
    tg = time.perf_counter() - t
    r = grind_brollopskarta.run(OUT / f"{order['id']}_meta.json")
    return {"id": order["id"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
            "underkanda": [(u["grind"], u["detalj"][:200]) for u in r["underkanda"]],
            "tid_generator_s": round(tg, 2), "tid_totalt_s": round(time.perf_counter() - t, 2)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    orders = base_orders()
    t0 = time.perf_counter()
    res = []
    for o in orders:
        try:
            res.append(one(o))
        except SystemExit as e:
            res.append({"id": o["id"], "godkand": False, "underkanda": [("generator_vagrade", str(e.code))]})
        except Exception:
            res.append({"id": o["id"], "godkand": False, "underkanda": [("krasch", traceback.format_exc()[-600:])]})
        r = res[-1]
        print(f"{o['id']}: {'OK' if r['godkand'] else 'UNDERKÄND ' + str(r['underkanda'])[:300]}", flush=True)
    inj = []
    base = orders[0]
    for fel in FEL_TYPER:
        oo = dict(base, id=f"{base['id']}_FEL_{fel}")
        try:
            r = one(oo, fel)
            inj.append({"fel": fel, "order": oo["id"], "fangad": not r["godkand"], "grindar_som_slog": [u[0] for u in r["underkanda"]]})
        except SystemExit as e:
            inj.append({"fel": fel, "order": oo["id"], "fangad": True, "grindar_som_slog": [f"generator vägrade: {e.code}"]})
        print(inj[-1], flush=True)
    os.environ["FELINJEKTION"] = ""
    rap = {"ordrar": len(res), "godkanda": sum(r["godkand"] for r in res), "resultat": res,
           "felinjektion": {"antal": len(inj), "fangade": sum(x["fangad"] for x in inj), "detaljer": inj},
           "total_tid_s": round(time.perf_counter() - t0, 1)}
    (OUT / "rapport.json").write_text(json.dumps(rap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in rap.items() if k != "resultat"}, ensure_ascii=False, indent=1))
    if rap["felinjektion"]["fangade"] < 8:
        sys.exit(1)


if __name__ == "__main__":
    main()
