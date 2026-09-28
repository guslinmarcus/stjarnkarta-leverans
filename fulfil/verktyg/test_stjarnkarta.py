"""Produktionstest för den ursprungliga stjärnkartan (stjarnkarta.py + kvalitetsgrind.py): körs genom samma
väg som fulfil.py (product saknas/=stjarnkarta) – flera verkliga ordrar (stilar, palett, ram, typsnitt,
norra/södra halvklotet, flerspråkiga PDF:er) samt felinjektion (medvetet förvanskad facit-meta – grinden
MÅSTE fälla varje). Fanns tidigare bara som prototyp/produktionstest.py mot den föråldrade
prototyp/stjarnkarta.py; den här körs mot den driftade fulfil/stjarnkarta.py.

    python verktyg/test_stjarnkarta.py   -> ut/stjarnkarta_test/rapport.json
"""
import copy
import json
import os
import sys
import time
import traceback
from pathlib import Path

FULFIL = Path(__file__).resolve().parent.parent
OUT = FULFIL / "ut" / "stjarnkarta_test"
sys.path.insert(0, str(FULFIL))
os.environ["STJARN_OUT"] = str(OUT)


def base_orders():
    return [
        {"id": "sk_midnatt_guld", "name": "Familjen Nilsson", "place": "Stockholm", "lat": 59.3293, "lon": 18.0686,
         "timezone": "Europe/Stockholm", "datetime_local": "2026-06-13T22:30", "languages": ["sv", "en"]},
        {"id": "sk_minimal_sand", "name": "Zoë & Åke", "place": "Hamburg", "lat": 53.5511, "lon": 9.9937,
         "timezone": "Europe/Berlin", "datetime_local": "1994-11-02T04:15", "languages": ["de"],
         "style": "minimal", "palette": "sand", "frame": "linje", "font": "sans"},
        {"id": "sk_akvarell_hav", "name": "Chloé & Léo", "place": "Paris", "lat": 48.8566, "lon": 2.3522,
         "timezone": "Europe/Paris", "datetime_local": "2030-02-28T19:45", "languages": ["fr", "en"],
         "style": "akvarell", "palette": "hav"},
        {"id": "sk_hjarta_vinrod", "name": "Signe & Ben", "place": "New York", "lat": 40.7128, "lon": -74.0060,
         "timezone": "America/New_York", "datetime_local": "2026-08-01T21:00", "languages": ["en"],
         "style": "hjarta", "palette": "vinrod", "frame": "dubbel", "font": "skrivstil"},
        {"id": "sk_manfas_kol", "name": "Björn Ångström", "place": "Kapstaden", "lat": -33.9249, "lon": 18.4241,
         "timezone": "Africa/Johannesburg", "datetime_local": "2026-01-15T20:00", "languages": ["en", "sv"],
         "style": "manfas", "palette": "kol"},
        {"id": "sk_barnrum_mint", "name": "Olle & Signe", "place": "Auckland", "lat": -36.8485, "lon": 174.7633,
         "timezone": "Pacific/Auckland", "datetime_local": "2026-12-24T21:30", "languages": ["en"],
         "style": "barnrum", "palette": "mint", "frame": "rundad", "font": "rund"},
        {"id": "sk_kiruna_sommar", "name": "Jürgen Weiß", "place": "Kiruna", "lat": 67.8558, "lon": 20.2253,
         "timezone": "Europe/Stockholm", "datetime_local": "1958-03-03T23:59", "languages": ["sv", "de"]},
    ]


def one(order):
    import importlib
    import stjarnkarta
    import kvalitetsgrind
    importlib.reload(stjarnkarta)
    importlib.reload(kvalitetsgrind)
    p = OUT / f"{order['id']}.json"
    p.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    t = time.perf_counter()
    stjarnkarta.generate(p)
    tg = time.perf_counter() - t
    r = kvalitetsgrind.run(OUT / f"{order['id']}_meta.json")
    return {"id": order["id"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
            "underkanda": [(u["grind"], u["detalj"][:200]) for u in r["underkanda"]],
            "tid_generator_s": round(tg, 2), "tid_totalt_s": round(time.perf_counter() - t, 2)}


def injicera(fel, meta):
    """Förvanskar facit-meta enligt fulfil/prototyp/produktionstest.py – grinden ska jämföra facit mot den
    redan renderade (oförändrade) PDF:en och fälla på diskrepansen."""
    m = copy.deepcopy(meta)
    m["order"] = dict(m["order"], id=f"sk_inj_{fel}")
    if fel == "stjarna_2grader_fel":
        m["check_stars"][0]["az"] = m["check_stars"][0]["az"] + 2
    elif fel == "manfas_10proc_fel":
        m["moon_frac"] = min(1.0, m["moon_frac"] + 0.10)
    elif fel == "fel_sidtext_datum":
        lang0 = m["order"]["languages"][0]
        m["texts"][lang0]["date"] = "31 februari 2024"
    elif fel == "stjarna_fel_sidposition":
        m["check_stars"][0]["page_x_pt"] = m["check_stars"][0]["page_x_pt"] + 40
    elif fel == "okand_stil":
        m["style"]["name"] = "diamant"
    elif fel == "for_fa_kontrollstjarnor":
        m["check_stars"] = m["check_stars"][:5]
    elif fel == "antal_stjarnor_orimligt":
        m["stars_plotted"] = 50
    elif fel == "hash_manipulerad":
        lang0 = m["order"]["languages"][0]
        m["sha256"][lang0] = "0" * 64
    elif fel == "geometri_fel_radie":
        m["geometry_pt"][2] = m["geometry_pt"][2] * 1.5
    else:
        raise ValueError(fel)
    return m


FEL_TYPER = ["stjarna_2grader_fel", "manfas_10proc_fel", "fel_sidtext_datum", "stjarna_fel_sidposition",
             "okand_stil", "for_fa_kontrollstjarnor", "antal_stjarnor_orimligt", "hash_manipulerad",
             "geometri_fel_radie"]


def main():
    import kvalitetsgrind
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

    base_meta = json.load(open(OUT / f"{orders[0]['id']}_meta.json", encoding="utf-8"))
    inj = []
    for fel in FEL_TYPER:
        m = injicera(fel, base_meta)
        mp = OUT / f"sk_inj_{fel}_meta.json"
        json.dump(m, open(mp, "w", encoding="utf-8"), ensure_ascii=False)
        try:
            r = kvalitetsgrind.run(mp)
            inj.append({"fel": fel, "fangad": not r["godkand"], "grindar_som_slog": [c["grind"] for c in r["underkanda"]]})
        except Exception:
            inj.append({"fel": fel, "fangad": True, "grindar_som_slog": [f"krasch: {traceback.format_exc()[-200:]}"]})
        print(inj[-1], flush=True)

    rap = {"ordrar": len(res), "godkanda": sum(r["godkand"] for r in res), "resultat": res,
           "felinjektion": {"antal": len(inj), "fangade": sum(x["fangad"] for x in inj), "detaljer": inj},
           "total_tid_s": round(time.perf_counter() - t0, 1)}
    (OUT / "rapport.json").write_text(json.dumps(rap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in rap.items() if k != "resultat"}, ensure_ascii=False, indent=1))
    if rap["godkanda"] < len(orders) or rap["felinjektion"]["fangade"] < 8:
        sys.exit(1)


if __name__ == "__main__":
    main()
