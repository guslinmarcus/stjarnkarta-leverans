"""Stresstest för födelsetavlan: N slumpade ordrar (barn + husdjur, alla 4 stilar, metriskt/imperialt,
med/utan familjerad, med/utan väder) genom generator + grind, plus felinjektion (medvetet inlagda fel
som grinden MÅSTE fälla – kravet är minst 10 olika planterade fel).

    python verktyg/test_fodelsetavla.py [N=20]   -> ut/fodelsetavla_test/rapport.json
"""
import gzip, json, os, random, sys, time, traceback
from pathlib import Path

FULFIL = Path(__file__).resolve().parent.parent
OUT = FULFIL / "ut" / "fodelsetavla_test"
sys.path.insert(0, str(FULFIL))
os.environ["STJARN_OUT"] = str(OUT)

NAMN_BARN = ["Signe", "Ben", "Elias", "Astrid", "Noah", "Freya", "Oscar", "Wilma", "Leo", "Alice"]
NAMN_HUSDJUR = ["Bruno", "Molly", "Findus", "Bella", "Charlie"]
FAMILJ = [["Mamma Anna", "Pappa Erik"], ["Mamma Sara", "Storasyster Wilma", "Storebror Max"], []]
PLATSER_SE = [("Vaxholm", 59.4025, 18.3529, "Europe/Stockholm", "SE", "Sverige"),
              ("Stockholm", 59.3293, 18.0686, "Europe/Stockholm", "SE", "Sverige"),
              ("Kiruna", 67.8558, 20.2253, "Europe/Stockholm", "SE", "Sverige"),
              ("Malmö", 55.6050, 13.0038, "Europe/Stockholm", "SE", "Sverige")]
PLATSER_UTLAND = [("Austin", 30.2672, -97.7431, "America/Chicago", "US", "USA"),
                   ("London", 51.5072, -0.1276, "Europe/London", "GB", "United Kingdom"),
                   ("Berlin", 52.5200, 13.4050, "Europe/Berlin", "DE", "Deutschland")]
STYLES = ["natur", "nordisk_minimal", "nattstjarna", "ballong"]


def units_for(lang, cc):
    if lang != "en":
        return "metric"
    return "imperial" if cc in ("US", "GB") else "metric"


def make_barn(rng, i):
    plats = rng.choice(PLATSER_SE + PLATSER_UTLAND)
    place, lat, lon, tz, cc, country = plats
    lang = rng.choice(["sv", "en", "de"]) if cc == "SE" else rng.choice(["en"]) if cc in ("US", "GB") else "de"
    units = units_for(lang, cc)
    y = rng.randint(1980, 2026)
    fam = rng.choice(FAMILJ)
    return {"id": f"barn_{i:03d}", "product": "fodelsetavla", "variant": "barn", "name": rng.choice(NAMN_BARN),
            "date": f"{y}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "time": None if rng.random() < 0.2 else f"{rng.randint(0, 23):02d}:{rng.choice([0, 15, 30, 45]):02d}",
            "place": place, "country": country, "country_cc": cc, "lat": lat, "lon": lon, "timezone": tz,
            "weight_g": rng.randint(2400, 4600), "height_cm": rng.randint(45, 56),
            "text": rng.choice(["", "Vårt lilla under", "Welcome to the world", "Unser kleines Wunder"]),
            "family": fam, "weather_requested": cc == "SE" and rng.random() < 0.7, "units": units,
            "style": rng.choice(STYLES), "accent": rng.choice(["rosa", "mint"]), "languages": [lang]}


def make_husdjur(rng, i):
    plats = rng.choice(PLATSER_SE + PLATSER_UTLAND)
    place, lat, lon, tz, cc, country = plats
    lang = "sv" if cc == "SE" else rng.choice(["en", "de"])
    y = rng.randint(2000, 2026)
    return {"id": f"pet_{i:03d}", "product": "fodelsetavla", "variant": "husdjur", "name": rng.choice(NAMN_HUSDJUR),
            "date": f"{y}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}", "time": None,
            "place": place, "country": country, "country_cc": cc, "lat": lat, "lon": lon, "timezone": tz,
            "units": units_for(lang, cc), "style": rng.choice(STYLES), "languages": [lang]}


def one(order, fel=""):
    import fodelsetavla, grind_fodelsetavla
    os.environ["FELINJEKTION"] = fel
    fodelsetavla.FEL = fel
    p = OUT / f"{order['id']}.json"
    p.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    t = time.perf_counter()
    fodelsetavla.generate(p)
    tg = time.perf_counter() - t
    r = grind_fodelsetavla.run(OUT / f"{order['id']}_meta.json")
    return {"id": order["id"], "style": order["style"], "variant": order["variant"], "lang": order["languages"][0],
            "godkand": r["godkand"], "grindar": r["antal_grindar"],
            "underkanda": [(u["grind"], u["detalj"][:200]) for u in r["underkanda"]],
            "tid_generator_s": round(tg, 2), "tid_totalt_s": round(time.perf_counter() - t, 2)}


FELTYPER = ["fel_stjarnposition", "fel_manfas", "fel_vader", "saknad_vader_kalla", "text_utanfor", "text_overlapp",
            "fel_enhet", "fel_vikt_langd", "fel_familj", "fel_tidszon", "sprak_fel", "saknad_attribution",
            "lag_kontrast", "fel_avstand_station"]


def main(n):
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260928)
    orders = [make_barn(rng, i) for i in range(n)] + [make_husdjur(rng, i) for i in range(max(4, n // 4))]
    # samma väg som driften: portalens jobb -> fulfil.make_order (fångade 2026-09-28 att husdjur saknade "units")
    import fulfil
    for jid, job in (("portal_barn", {"text": "Signe", "variant": "barn", "date": "2026-03-03", "time": "07:22", "city": "Vaxholm",
                                      "country": "Sverige", "style": "natur", "weight_g": "3450", "height_cm": "51", "lang": "sv"}),
                     ("portal_husdjur", {"text": "Bruno", "variant": "husdjur", "date": "2026-05-01", "city": "Stockholm",
                                         "country": "Sweden", "style": "nordisk_minimal", "lang": "en"}),
                     ("portal_husdjur_us", {"text": "Molly", "variant": "husdjur", "date": "2025-10-10", "city": "New York",
                                            "country": "USA", "style": "ballong", "accent": "mint", "lang": "en"})):
        o, err = fulfil.make_order(dict(job, id=jid, product="fodelsetavla"))
        if err:
            sys.exit(f"make_order vägrade {jid}: {err}")
        orders.append(o)
    res = []
    t0 = time.perf_counter()
    for k, o in enumerate(orders):
        try:
            res.append(one(o))
        except SystemExit as e:
            res.append({"id": o["id"], "style": o["style"], "godkand": False, "underkanda": [("generator_vagrade", str(e.code))]})
        except Exception:
            res.append({"id": o["id"], "style": o["style"], "godkand": False, "underkanda": [("krasch", traceback.format_exc()[-500:])]})
        r = res[-1]
        print(f"[{k + 1}/{len(orders)}] {o['id']} {'OK' if r['godkand'] else 'UNDERKÄND ' + str(r['underkanda'])[:200]}", flush=True)

    # felinjektion: kräver en riktig svensk barn-order med väder + familj för att alla feltyper ska kunna testas
    bas_barn = dict(orders[0], id="fel_bas", place="Vaxholm", country="Sverige", country_cc="SE",
                     lat=59.4025, lon=18.3529, timezone="Europe/Stockholm", languages=["sv"], units="metric",
                     weather_requested=True, family=["Mamma Anna", "Pappa Erik", "Storasyster Wilma"])
    inj = []
    for fel in FELTYPER:
        oo = dict(bas_barn, id=f"fel_bas_{fel}")
        try:
            r = one(oo, fel)
            inj.append({"fel": fel, "order": oo["id"], "fangad": not r["godkand"], "grindar_som_slog": [u[0] for u in r["underkanda"]]})
        except SystemExit as e:
            inj.append({"fel": fel, "order": oo["id"], "fangad": True, "grindar_som_slog": [f"generator vägrade: {e.code}"]})
        except Exception:
            inj.append({"fel": fel, "order": oo["id"], "fangad": False, "grindar_som_slog": [f"krasch: {traceback.format_exc()[-300:]}"]})
        print(inj[-1], flush=True)
    os.environ["FELINJEKTION"] = ""

    per = {}
    for r in res:
        s = per.setdefault(r["style"], {"ordrar": 0, "godkanda": 0})
        s["ordrar"] += 1; s["godkanda"] += r["godkand"]
    rap = {"ordrar": len(res), "godkanda": sum(r["godkand"] for r in res), "per_stil": per,
           "felinjektion": {"antal_feltyper": len(inj), "fangade": sum(x["fangad"] for x in inj),
                            "krav_minst": 10, "detaljer": inj},
           "total_tid_s": round(time.perf_counter() - t0, 1), "resultat": res}
    (OUT / "rapport.json").write_text(json.dumps(rap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in rap.items() if k != "resultat"}, ensure_ascii=False, indent=1))
    return rap


if __name__ == "__main__":
    rap = main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
    ok = rap["godkanda"] == rap["ordrar"] and rap["felinjektion"]["fangade"] >= 10
    sys.exit(0 if ok else 1)
