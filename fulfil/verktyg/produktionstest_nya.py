"""Produktionstest för förmörkelseguiden och himmelskalendern:
  1. generatorn mot NASA:s referensorter (20 orter, JSEX) – bara beräkningen,
  2. 5 exempelordrar per produkt genom generator + grind (tider mäts),
  3. felinjektion: medvetet inlagda fel i den tryckta produkten – grinden måste fälla varje.

Körning: python verktyg/produktionstest_nya.py   -> agentbutik/prototyp/ut/{formorkelse,himmelskalender}/
"""
import json, os, subprocess, sys, time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows-konsolen är annars cp1252 och kraschar på t.ex. "ΔT"
except Exception:
    pass

FULFIL = Path(__file__).resolve().parent.parent
OUT = FULFIL.parent.parent / "prototyp" / "ut"
sys.path.insert(0, str(FULFIL))

ECL = [
    {"id": "f1_stockholm", "text": "Familjen Lind", "place": "Stockholm", "lat": 59.3293, "lon": 18.0686, "timezone": "Europe/Stockholm", "languages": ["sv", "en"]},
    {"id": "f2_sevilla", "text": "Para la familia García", "place": "Sevilla", "lat": 37.3886, "lon": -5.9823, "timezone": "Europe/Madrid", "languages": ["es"]},
    {"id": "f3_malaga", "text": "Lucía & Mateo", "place": "Málaga", "lat": 36.7202, "lon": -4.4203, "timezone": "Europe/Madrid", "languages": ["es", "en"]},
    {"id": "f4_luxor", "text": "Our Nile trip", "place": "Luxor", "lat": 25.6989, "lon": 32.6421, "timezone": "Africa/Cairo", "languages": ["en"]},
    {"id": "f5_berlin", "text": "Für Oma Hilde", "place": "Berlin", "lat": 52.5244, "lon": 13.4105, "timezone": "Europe/Berlin", "languages": ["de"]},
    {"id": "f6_paris", "text": "Pour Élodie & Théo", "place": "Paris", "lat": 48.8534, "lon": 2.3488, "timezone": "Europe/Paris", "languages": ["fr"]},
    {"id": "f7_tanger", "text": "Notre voyage au Maroc", "place": "Tanger", "lat": 35.7673, "lon": -5.7998, "timezone": "Africa/Casablanca", "languages": ["fr", "en"]},
]
CAL = [
    {"id": "k1_vaxholm", "text": "Till Signe", "place": "Vaxholm", "lat": 59.4025, "lon": 18.3513, "timezone": "Europe/Stockholm", "languages": ["sv", "en"]},
    {"id": "k2_kiruna", "text": "", "place": "Kiruna", "lat": 67.8558, "lon": 20.2253, "timezone": "Europe/Stockholm", "languages": ["sv"]},
    {"id": "k3_hamburg", "text": "Für Ben", "place": "Hamburg", "lat": 53.5511, "lon": 9.9937, "timezone": "Europe/Berlin", "languages": ["de"]},
    {"id": "k4_madrid", "text": "Para Carmen", "place": "Madrid", "lat": 40.4165, "lon": -3.7026, "timezone": "Europe/Madrid", "languages": ["es"]},
    {"id": "k5_newyork", "text": "For Maya", "place": "New York", "lat": 40.7143, "lon": -74.006, "timezone": "America/New_York", "languages": ["en"]},
]
INJ = {
    "formorkelse": ["tid_c1_plus2min", "tackning_plus3", "skiva_fel", "bana_forskjuten", "markor_fel",
                    "saknad_sakerhet", "fel_sprak", "berakning_fel"],
    "himmelskalender": ["manfas_fel", "manfas_tid_fel", "sol_fel", "planet_fel", "berakning_fel"],
}
GEN = {"formorkelse": ("formorkelse.py", "grind_formorkelse.py"), "himmelskalender": ("himmelskalender.py", "grind_himmelskalender.py")}


def run_one(product, order, outdir, fel=""):
    outdir.mkdir(parents=True, exist_ok=True)
    op = outdir / f"{order['id']}.json"
    op.write_text(json.dumps(dict(order, product=product), ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, STJARN_OUT=str(outdir), FELINJEKTION=fel, PYTHONIOENCODING="utf-8")
    g, q = GEN[product]
    t = time.perf_counter()
    r = subprocess.run([sys.executable, str(FULFIL / g), str(op)], env=env, capture_output=True, text=True, encoding="utf-8")
    tg = time.perf_counter() - t
    if r.returncode:
        return {"id": order["id"], "gen_fel": r.stderr[-400:]}
    t = time.perf_counter()
    subprocess.run([sys.executable, str(FULFIL / q), str(outdir / f"{order['id']}_meta.json")], env=env, capture_output=True, text=True, encoding="utf-8")
    tq = time.perf_counter() - t
    qc = json.load(open(outdir / f"{order['id']}_qc.json", encoding="utf-8"))
    return {"id": order["id"], "plats": order["place"], "sprak": order["languages"], "tid_generator_s": round(tg, 2),
            "tid_grind_s": round(tq, 2), "godkand": qc["godkand"], "antal_grindar": qc["antal_grindar"],
            "underkanda": [c["grind"] + ": " + c["detalj"][:160] for c in qc["underkanda"]]}


def nasa_reference():
    import formorkelse as F
    sky = F.Sky()
    ref = json.load(open(FULFIL / "data" / "nasa" / "referens_orter_2027-08-02.json", encoding="utf-8"))
    rows, worst_t, worst_o, worst_d = [], 0.0, 0.0, 0.0
    h = lambda iso: (lambda d: d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6)(datetime.fromisoformat(iso))
    for r in ref["orter"]:
        c = F.local_circumstances(sky, r["lat"], r["lon"], r["alt_m"])
        dt = {k: h(c["contacts_utc"][k]) - r[k + "_ut_h"] * 3600 for k in ("c1", "max", "c4")}
        do = (c["obscuration"] - r["obscuration"]) * 100
        dd = (c.get("duration_s", 0) - r.get("duration_s", 0)) if r["type"] == "total" else 0.0
        worst_t, worst_o, worst_d = max(worst_t, *map(abs, dt.values())), max(worst_o, abs(do)), max(worst_d, abs(dd))
        rows.append({"ort": r["name"], "typ_nasa": r["type"], "typ_vi": c["type"], "diff_s": {k: round(v, 1) for k, v in dt.items()},
                     "diff_tackning_procentenheter": round(do, 3), "diff_totalitet_s": round(dd, 1)})
    return {"orter": len(rows), "typ_ratt": sum(r["typ_nasa"] == r["typ_vi"] for r in rows), "max_tid_s": round(worst_t, 1),
            "max_tackning_procentenheter": round(worst_o, 3), "max_totalitet_s": round(worst_d, 1),
            "not": "NASA-värdena bygger på ΔT = 76,0 s, Skyfield använder IERS-prognos ≈ 69,1 s för 2027 – förklarar ~7 s av tidsskillnaden.",
            "detaljer": rows}


def main():
    res = {"datum": datetime.now().isoformat(timespec="seconds")}
    t = time.perf_counter()
    res["nasa_referens"] = nasa_reference()
    res["nasa_referens"]["tid_s"] = round(time.perf_counter() - t, 1)
    print("NASA:", {k: v for k, v in res["nasa_referens"].items() if k != "detaljer"}, flush=True)
    for product, orders in (("formorkelse", ECL), ("himmelskalender", CAL)):
        od = OUT / product
        rows = []
        for o in orders:
            rows.append(run_one(product, o, od)); print(rows[-1], flush=True)
        inj = []
        for fel in INJ[product]:
            r = run_one(product, dict(orders[0], id=f"inj_{fel}", languages=orders[0]["languages"][:1]), od / "felinjektion", fel)
            inj.append({"felinjektion": fel, "falld": r.get("godkand") is False, "av": [u.split(":")[0] for u in r.get("underkanda", [])],
                        "gen_fel": r.get("gen_fel")})
            print(inj[-1], flush=True)
        res[product] = {"ordrar": rows, "godkanda": sum(1 for r in rows if r.get("godkand")), "antal": len(rows),
                        "snitt_generator_s": round(sum(r.get("tid_generator_s", 0) for r in rows) / len(rows), 2),
                        "snitt_grind_s": round(sum(r.get("tid_grind_s", 0) for r in rows) / len(rows), 2),
                        "felinjektion": inj, "fallda": sum(i["falld"] for i in inj), "injektioner": len(inj)}
    json.dump(res, open(OUT / "produktionstest_nya_resultat.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({p: {k: v for k, v in res[p].items() if k not in ("ordrar", "felinjektion")} for p in ("formorkelse", "himmelskalender")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
