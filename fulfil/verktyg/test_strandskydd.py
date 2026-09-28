"""Produktionstest för kartunderlaget till strandskyddsdispens (strandskydd.py + grind_strandskydd.py).

  1. Riktiga platser (hav, Mälaren, Vättern) med brygga, bod och altan, förhandsvisning och slutversion – alla
     ska passera grinden
  2. Köparfel: brygga långt från stranden, bod i vattnet, bod 400 m från stranden, för lång brygga, okänd åtgärd,
     plats utanför Sverige – ska avvisas med rätt felkod och utan fil
  3. Felinjektion: 15 planterade fel (FELINJEKTION=…) – grinden ska underkänna med rätt kontroll
  4. Determinism: samma order i två separata processer -> identisk PDF (sha256)
Varje körning sker i en egen process (sekventiellt). Mäter tid och högsta privata minne per process.

  python fulfil/verktyg/test_strandskydd.py            (hela testet)
  python fulfil/verktyg/test_strandskydd.py --kor order.json utkatalog   (en order: generator + grind, internt)
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import hashlib, json, math, shutil, subprocess, sys, threading, time
from pathlib import Path

F = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(F))
UT = F / "ut" / "strandtest"

PLATSER = [  # namn, lat, lon, kommun (klickpunkt nära stranden; testet letar upp strandlinjen som en kund klickar på kartan)
    ("vaxholm", 59.4025, 18.3480, "Vaxholm"),
    ("sigtuna", 59.6170, 17.7230, "Sigtuna"),
    ("hjo", 58.3040, 14.2860, "Hjo"),
]
FELINJ = {"saknad_attribution": "attribution_detalj", "fel_skala": "strandlinje_pa_ratt_plats",
          "forskjuten_strandlinje": "strandlinje_pa_ratt_plats", "fel_buffert": "hundrameterslinje_avstand",
          "saknad_reservation": "reservation_utvidgat", "fel_area": "atgard1_area", "fel_avstand": "atgard1_avstand",
          "saknad_norrpil": "norrpil_detalj", "forbjudet_ord": "forbjudna_ord", "fel_bryggmatt": "plan_matt",
          "saknad_tomtplats": "tomtplats_ritad", "saknad_sidfot": "sidfot_sida1", "fel_atgard_lage": "atgard1_lage",
          "saknad_oversiktsmarkering": "oversikt_markering", "fel_skalstock": "skalstock_detalj"}


# ------------------------------------------------------------------ en körning (i egen process)
def kor_en(order_path, out_dir):
    import psutil
    peak = [0]

    def mon():
        p = psutil.Process()
        while True:
            try:
                peak[0] = max(peak[0], p.memory_info().private)
            except Exception:
                pass
            time.sleep(0.05)
    threading.Thread(target=mon, daemon=True).start()
    import strandskydd as G
    import grind_strandskydd as Q
    t = time.perf_counter()
    m = G.generate(json.load(open(order_path, encoding="utf-8")), out_dir)
    tg = time.perf_counter() - t
    meta_path = Path(out_dir) / f"{m['order']['id']}_meta.json"
    t = time.perf_counter()
    q = Q.run(meta_path)
    tq = time.perf_counter() - t
    print(json.dumps({"avvisad": m.get("avvisad"), "sha256": m.get("sha256"), "gron": q["godkand_av_grind"],
                      "kontroller": q["antal_kontroller"], "underkanda": [c["grind"] for c in q["underkanda"]],
                      "underkanda_detalj": q["underkanda"][:6], "skala": m.get("skala"),
                      "tid_generator_s": round(tg, 1), "tid_grind_s": round(tq, 1), "timings": m.get("timings"),
                      "minne_mb": round(peak[0] / 2 ** 20)}, ensure_ascii=False))


def subkor(order, env_extra=None, tag=None):
    tag = tag or order["id"]
    d = UT / tag; d.mkdir(parents=True, exist_ok=True)
    op = d / "order.json"; json.dump(order, open(op, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    env = dict(os.environ); env.pop("FELINJEKTION", None); env.update(env_extra or {}); env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-X", "utf8", __file__, "--kor", str(op), str(d)], capture_output=True, text=True, env=env,
                       encoding="utf-8")
    line = [l for l in r.stdout.splitlines() if l.startswith("{")]
    if not line:
        return {"krasch": (r.stderr or r.stdout)[-1500:]}
    return json.loads(line[-1])


# ------------------------------------------------------------------ platsval (som när en kund klickar på kartan)
def punkter(lat, lon):
    import numpy as np
    import kartgeo as K
    import strandskydd as G
    import strandskydd_data as D
    E0, N0 = (round(float(v), 1) for v in K.to_sweref(lat, lon))
    osm, _ = D.fetch(D.spec_for_point(lat, lon))
    Fe = G.parse(osm, E0, N0); W = G.Vatten(Fe, 1500); S, _, _ = G.shoreline(Fe, W)
    d, q = G.seg_dist(np.array([[0.0, 0.0]]), S)
    q = q[0]

    def ll(p):
        la, lo = K.from_sweref(E0 + p[0], N0 + p[1]); return round(float(la), 7), round(float(lo), 7)

    def land_at(dist):
        for ang in range(0, 360, 10):
            u = np.array([math.sin(math.radians(ang)), math.cos(math.radians(ang))])
            c = q + u * dist
            box = G.rect(c[0], c[1], 6, 6, 0)
            if not W.in_water(G.densify(box, 0.5)).any() and G.distance_poly(box, S) > dist * 0.7:
                return c
        return None

    def water_at(dist):
        for ang in range(0, 360, 10):
            u = np.array([math.sin(math.radians(ang)), math.cos(math.radians(ang))])
            c = q + u * dist
            if W.in_water(G.rect(c[0], c[1], 6, 6, 0)).all():
                return c
        return None
    far = None
    for dist in (420, 480, 550):
        far = land_at(dist)
        if far is not None:
            break
    return {"strand": ll(q), "land30": ll(land_at(30)), "land60": ll(land_at(60)), "vatten": ll(water_at(25)),
            "langt": ll(far) if far is not None else None}


def order(i, plats, kommun, atg, **kw):
    o = {"id": f"ss{i:02d}_{plats}", "fastighet": f"{kommun.upper()} TESTET 1:{i}", "kommun": kommun,
         "sokande": {"namn": "Testperson Testsson"}, "version": "forhandsvisning", "created": "2026-09-27T00:00:00Z",
         "atgarder": atg}
    o.update(kw)
    return o


def main():
    shutil.rmtree(UT, ignore_errors=True); UT.mkdir(parents=True)
    rap = {"platser": [], "koparfel": [], "felinjektion": [], "determinism": None}
    t_all = time.perf_counter()
    P = {}
    for name, lat, lon, kom in PLATSER:
        t = time.perf_counter()
        P[name] = punkter(lat, lon)
        print(f"platsval {name}: {time.perf_counter() - t:.0f} s {P[name]}", flush=True)
    V, Sg, Hj = P["vaxholm"], P["sigtuna"], P["hjo"]
    brygga = lambda p, **k: dict({"typ": "brygga", "lat": p[0], "lon": p[1], "langd": 12, "bredd": 2, "hojd_over_vatten": 0.6,
                                   "vattendjup_yttre": 1.8, "vattendjup_inre": 0.3, "forankring": "stolpar"}, **k)
    bod = lambda p, **k: dict({"typ": "bod", "lat": p[0], "lon": p[1], "langd": 4, "bredd": 3, "hojd": 2.8, "riktning": 15}, **k)
    altan = lambda p, **k: dict({"typ": "altan", "lat": p[0], "lon": p[1], "langd": 6, "bredd": 4, "hojd": 0.5, "riktning": 0}, **k)
    goda = [
        order(1, "vaxholm", "Vaxholm", [brygga(V["strand"]), bod(V["land30"])]),
        order(2, "vaxholm", "Vaxholm", [brygga(V["strand"]), bod(V["land30"])], version="slutversion",
              bekraftelse={"namn": "Testperson Testsson", "datum": "2026-09-27"}),
        order(3, "sigtuna", "Sigtuna", [brygga(Sg["strand"], langd=18, bredd=2.4, forankring="pontoner", vattendjup_yttre=2.5),
                                        altan(Sg["land30"])]),
        order(4, "hjo", "Hjo", [bod(Hj["land60"], langd=5, bredd=3.5, riktning=40)]),
        order(5, "hjo", "Hjo", [brygga(Hj["strand"], langd=8, bredd=1.5, forankring="stenkistor", hojd_over_vatten=0.8)]),
    ]
    for o in goda:
        r = subkor(o)
        rap["platser"].append({"id": o["id"], **r})
        print(o["id"], "GRÖN" if r.get("gron") else "RÖD", r.get("underkanda"), r.get("krasch", "")[:300],
              r.get("tid_generator_s"), "s", r.get("minne_mb"), "MB", flush=True)
    kf = [
        (order(20, "vaxholm", "Vaxholm", [brygga(V["land60"])]), "brygga_ej_vid_strand"),
        (order(21, "vaxholm", "Vaxholm", [bod(V["vatten"])]), "atgard_i_vatten"),
        (order(22, "vaxholm", "Vaxholm", [bod(V["langt"])]) if V["langt"] else None, "utanfor_strandskydd"),
        (order(23, "vaxholm", "Vaxholm", [brygga(V["strand"], langd=45)]), "matt_utanfor_granser"),
        (order(24, "vaxholm", "Vaxholm", [dict(bod(V["land30"]), typ="garage")]), "okand_atgard"),
        (order(25, "oslo", "Oslo", [bod((59.91, 10.75))]), "utanfor_sverige"),
        (order(26, "vaxholm", "Vaxholm", [brygga(V["strand"], forankring="rep")]), "forankring_okand"),
    ]
    for o, kod in kf:
        if o is None:
            rap["koparfel"].append({"kod": kod, "ok": None, "not": "ingen testpunkt 420–550 m från stranden"}); continue
        r = subkor(o)
        ok = (r.get("avvisad") or {}).get("kod") == kod and r.get("gron")
        rap["koparfel"].append({"id": o["id"], "vantad": kod, "fick": (r.get("avvisad") or {}).get("kod"), "ok": ok,
                                "text": (r.get("avvisad") or {}).get("text")})
        print("köparfel", kod, "OK" if ok else f"FEL {r}", flush=True)
    base = goda[0]
    for fel, grind in FELINJ.items():
        r = subkor(dict(base, id=f"fi_{fel}"), {"FELINJEKTION": fel}, tag=f"fi_{fel}")
        caught = (not r.get("avvisad")) and (not r.get("gron")) and grind in (r.get("underkanda") or [])
        rap["felinjektion"].append({"fel": fel, "vantad_grind": grind, "fangad": caught, "underkanda": r.get("underkanda"),
                                    "krasch": r.get("krasch")})
        print("felinjektion", fel, "FÅNGAD" if caught else f"MISSAD {r.get('underkanda')} {r.get('krasch', '')[:200]}", flush=True)
    a = subkor(dict(base, id="det_a"), tag="det_a"); b = subkor(dict(base, id="det_a"), tag="det_b")
    rap["determinism"] = {"a": a.get("sha256"), "b": b.get("sha256"), "identisk": a.get("sha256") == b.get("sha256") and a.get("sha256") is not None}
    print("determinism", rap["determinism"], flush=True)
    rap["sammanfattning"] = {
        "goda_grona": f"{sum(1 for r in rap['platser'] if r.get('gron'))}/{len(rap['platser'])}",
        "koparfel_ratt": f"{sum(1 for r in rap['koparfel'] if r.get('ok'))}/{len(rap['koparfel'])}",
        "felinjektion_fangade": f"{sum(1 for r in rap['felinjektion'] if r['fangad'])}/{len(rap['felinjektion'])}",
        "determinism": rap["determinism"]["identisk"],
        "max_minne_mb": max([r.get("minne_mb") or 0 for r in rap["platser"]] + [0]),
        "total_tid_s": round(time.perf_counter() - t_all)}
    json.dump(rap, open(UT / "rapport.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(rap["sammanfattning"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--kor":
        kor_en(sys.argv[2], sys.argv[3])
    else:
        main()
