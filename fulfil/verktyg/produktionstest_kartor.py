"""Produktionstest för kartprodukterna (historisk, stadskarta, karlekskarta):
  1. 5 exempelordrar per produkt genom generator + grind (tider mäts),
  2. felinjektion: medvetet inlagda fel – grinden måste fälla varje.

Körning: python verktyg/produktionstest_kartor.py [historisk|stadskarta|karlekskarta ...]
         -> agentbutik/prototyp/ut/<produkt>/ + produktionstest_kartor_<produkt>.json
Historisk kräver nätet (Lantmäteriets FTP, Overpass, Nominatim) första gången; allt cachas sedan.
"""
import json, os, subprocess, sys, time
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
from datetime import datetime
from pathlib import Path

FULFIL = Path(__file__).resolve().parent.parent
OUT = FULFIL.parent.parent / "prototyp" / "ut"

HIST = [
    {"id": "h1_ystad", "text": "Familjen Nilsson", "address": "Stora Östergatan 20", "place": "Ystad", "lat": 55.4295, "lon": 13.82, "languages": ["sv", "en"]},
    {"id": "h2_vaxholm", "text": "Hemma i Vaxholm", "address": "Hamngatan 5", "place": "Vaxholm", "lat": 59.4025, "lon": 18.3513, "languages": ["sv"]},
    {"id": "h3_sigtuna", "text": "", "address": "Stora gatan 30", "place": "Sigtuna", "lat": 59.6173, "lon": 17.7236, "languages": ["sv"]},
    {"id": "h4_arvika", "text": "Arvika genom tiden", "address": "", "place": "Arvika", "lat": 59.6553, "lon": 12.5852, "languages": ["sv"]},
    {"id": "h5_hagersten", "text": "For Grandma", "address": "Västertorpsvägen 100", "place": "Stockholm", "lat": 59.3294, "lon": 18.0687, "languages": ["en"]},
]
STAD = [
    {"id": "s1_stockholm", "text": "", "place": "Stockholm", "country": "Sweden", "cc": "SE", "lat": 59.3294, "lon": 18.0687, "style": "natt", "radius_km": 3, "languages": ["en"]},
    {"id": "s2_goteborg", "text": "", "place": "Göteborg", "country": "Sverige", "cc": "SE", "lat": 57.7072, "lon": 11.9668, "style": "klassisk", "radius_km": 3, "languages": ["sv"]},
    {"id": "s3_visby", "text": "Visby", "place": "Visby", "country": "Gotland", "cc": "SE", "lat": 57.6409, "lon": 18.296, "style": "sepia", "radius_km": 1.5, "languages": ["sv"]},
    {"id": "s4_lisboa", "text": "Lisboa", "place": "Lisbon", "country": "Portugal", "cc": "PT", "lat": 38.7169, "lon": -9.1399, "style": "blueprint", "radius_km": 2.5, "languages": ["en"]},
    {"id": "s5_vaxholm", "text": "Vaxholm", "place": "Vaxholm", "country": "Sweden", "cc": "SE", "lat": 59.4025, "lon": 18.3513, "style": "natt", "radius_km": 1.5, "languages": ["en"]},
]
KARL = [
    {"id": "k1_europa", "text": "Anna & Erik", "style": "ljus", "languages": ["en", "sv"], "places": [
        {"place": "Paris", "country": "France", "cc": "FR", "date": "2018-06-21", "label": "Where we met", "lat": 48.8534, "lon": 2.3488},
        {"place": "Stockholm", "country": "Sweden", "cc": "SE", "date": "2019-12-24", "label": "First home", "lat": 59.3294, "lon": 18.0687},
        {"place": "Rome", "country": "Italy", "cc": "IT", "date": "2022-05-14", "label": "Engaged", "lat": 41.8919, "lon": 12.5113},
        {"place": "Vaxholm", "country": "Sweden", "cc": "SE", "date": "2023-08-19", "label": "Married", "lat": 59.4025, "lon": 18.3513}]},
    {"id": "k2_varlden", "text": "Sara & Tom", "style": "natt", "languages": ["sv"], "places": [
        {"place": "New York", "country": "USA", "cc": "US", "date": "2015-03-02", "label": "Där vi möttes", "lat": 40.7143, "lon": -74.006},
        {"place": "Stockholm", "country": "Sverige", "cc": "SE", "date": "2017-07-01", "label": "Första hemmet", "lat": 59.3294, "lon": 18.0687},
        {"place": "Tokyo", "country": "Japan", "cc": "JP", "date": "2019-10-10", "label": "Förlovning", "lat": 35.6895, "lon": 139.6917}]},
    {"id": "k3_nara", "text": "Us", "style": "ljus", "languages": ["en"], "places": [
        {"place": "Uppsala", "country": "Sweden", "cc": "SE", "date": "2020-02-14", "label": "First date", "lat": 59.8585, "lon": 17.6454},
        {"place": "Stockholm", "country": "Sweden", "cc": "SE", "date": "2021-05-01", "label": "Moved in", "lat": 59.3294, "lon": 18.0687}]},
    {"id": "k4_fem", "text": "Lena & Jonas", "style": "natt", "languages": ["de"], "places": [
        {"place": "Hamburg", "country": "Deutschland", "cc": "DE", "date": "2010-08-08", "label": "Kennengelernt", "lat": 53.5511, "lon": 9.9937},
        {"place": "Wien", "country": "Österreich", "cc": "AT", "date": "2012-01-20", "label": "", "lat": 48.2085, "lon": 16.3721},
        {"place": "Barcelona", "country": "Spanien", "cc": "ES", "date": "2014-06-30", "label": "Verlobt", "lat": 41.3888, "lon": 2.159},
        {"place": "München", "country": "Deutschland", "cc": "DE", "date": "2015-09-12", "label": "Hochzeit", "lat": 48.1374, "lon": 11.5755},
        {"place": "Berlin", "country": "Deutschland", "cc": "DE", "date": "2018-04-02", "label": "Unser Zuhause", "lat": 52.5244, "lon": 13.4105}]},
    {"id": "k5_soder", "text": "Mia & Leo", "style": "ljus", "languages": ["en"], "places": [
        {"place": "Sydney", "country": "Australia", "cc": "AU", "date": "2016-12-31", "label": "New Year's Eve", "lat": -33.8679, "lon": 151.2073},
        {"place": "Auckland", "country": "New Zealand", "cc": "NZ", "date": "2018-02-10", "label": "Engaged", "lat": -36.8485, "lon": 174.7633},
        {"place": "Cape Town", "country": "South Africa", "cc": "ZA", "date": "2021-11-20", "label": "Married", "lat": -33.9258, "lon": 18.4232}]},
]
INJ = {
    "historisk": ["fel_blad", "forskjutning", "lag_upplosning", "saknad_kallhanvisning", "fel_artal", "tom_panel", "fel_adress",
                  "skarv", "tom_yta", "avklippt_etikett"],
    "stadskarta": ["saknad_attribution", "tomt_omrade", "fel_centrum", "lag_upplosning"],
    "karlekskarta": ["fel_koordinat", "fel_ordning", "fel_avstand", "saknad_kalla"],
}
GEN = {"historisk": ("historisk.py", "grind_historisk.py", HIST), "stadskarta": ("stadskarta.py", "grind_stadskarta.py", STAD),
       "karlekskarta": ("karlekskarta.py", "grind_karlekskarta.py", KARL)}


def with_geonames(product, order):
    """Koordinaterna sätts som i produktionen: fulfil.geocode (GeoNames), avrundade till 4 decimaler."""
    sys.path.insert(0, str(FULFIL))
    import fulfil as FF
    o = json.loads(json.dumps(order))
    if product == "karlekskarta":
        for p in o["places"]:
            g = FF.geocode(p["place"], p.get("cc") or p.get("country", ""))
            p["lat"], p["lon"] = round(g[1], 4), round(g[2], 4)
    else:
        g = FF.geocode(o["place"], "SE" if product == "historisk" else (o.get("cc") or o.get("country", "")))
        o["lat"], o["lon"] = round(g[1], 4), round(g[2], 4)
        if product == "historisk":
            o["pop"] = g[4]
    return o


def run_one(product, order, outdir, fel=""):
    order = with_geonames(product, order)
    outdir.mkdir(parents=True, exist_ok=True)
    op = outdir / f"{order['id']}.json"
    op.write_text(json.dumps(dict(order, product=product, timezone="Europe/Stockholm"), ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, STJARN_OUT=str(outdir), FELINJEKTION=fel, PYTHONIOENCODING="utf-8")
    g, q, _ = GEN[product]
    t = time.perf_counter()
    r = subprocess.run([sys.executable, str(FULFIL / g), str(op)], env=env, capture_output=True, text=True, encoding="utf-8")
    tg = time.perf_counter() - t
    if r.returncode:
        return {"id": order["id"], "gen_fel": (r.stderr or r.stdout)[-600:], "tid_generator_s": round(tg, 1)}
    t = time.perf_counter()
    subprocess.run([sys.executable, str(FULFIL / q), str(outdir / f"{order['id']}_meta.json")], env=env, capture_output=True, text=True, encoding="utf-8")
    tq = time.perf_counter() - t
    qc = json.load(open(outdir / f"{order['id']}_qc.json", encoding="utf-8"))
    meta = json.load(open(outdir / f"{order['id']}_meta.json", encoding="utf-8"))
    return {"id": order["id"], "sprak": order["languages"], "tid_generator_s": round(tg, 1), "tid_grind_s": round(tq, 1),
            "generatorns_deltider": meta.get("timings"), "godkand": qc["godkand"], "antal_grindar": qc["antal_grindar"],
            "underkanda": [c["grind"] + ": " + c["detalj"][:200] for c in qc["underkanda"]]}


def main():
    prods = sys.argv[1:] or list(GEN)
    for product in prods:
        res = {"datum": datetime.now().isoformat(timespec="seconds"), "produkt": product}
        od = OUT / product
        rows = []
        for o in GEN[product][2]:
            rows.append(run_one(product, o, od)); print(json.dumps(rows[-1], ensure_ascii=False)[:600], flush=True)
        inj = []
        base = GEN[product][2][0]
        for fel in INJ[product]:
            r = run_one(product, dict(base, id=f"inj_{fel}", languages=base["languages"][:1]), od / "felinjektion", fel)
            inj.append({"felinjektion": fel, "falld": r.get("godkand") is False,
                        "av": [u.split(":")[0] for u in r.get("underkanda", [])], "gen_fel": r.get("gen_fel")})
            print(json.dumps(inj[-1], ensure_ascii=False)[:400], flush=True)
        res.update(ordrar=rows, godkanda=sum(1 for r in rows if r.get("godkand")), antal=len(rows),
                   snitt_generator_s=round(sum(r.get("tid_generator_s", 0) for r in rows) / len(rows), 1),
                   snitt_grind_s=round(sum(r.get("tid_grind_s", 0) for r in rows) / len(rows), 1),
                   felinjektion=inj, fallda=sum(i["falld"] for i in inj), injektioner=len(inj))
        json.dump(res, open(OUT / f"produktionstest_kartor_{product}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(product, {k: v for k, v in res.items() if k not in ("ordrar", "felinjektion")}, flush=True)


if __name__ == "__main__":
    main()
