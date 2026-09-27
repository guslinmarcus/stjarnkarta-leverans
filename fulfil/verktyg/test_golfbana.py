"""Produktionstest för golfbanekartan (golfbana.py + grind_golfbana.py) via samma väg som fulfil.py (make_order + produce).

  1. 8 kända banor (Sverige, Storbritannien, USA, Spanien) × 4 stilar, olika språk och markerade hål – alla ska godkännas
  2. köparfel: tvetydig bana, ofullständiga hål i OSM, okänd bana, hål som inte finns – ska vägras med rätt felkod
  3. felinjektion: 8 planterade fel – grinden ska underkänna med rätt grind
  4. determinism: samma order två gånger i separata processer -> identisk PDF (sha256)
Mäter tider och högsta privata minne.   python fulfil/verktyg/test_golfbana.py
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import hashlib, json, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path

import psutil

F = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(F))
import fulfil  # noqa: E402

UT = F / "ut" / "golftest"
PEAK = [0]


def mon():
    p = psutil.Process()
    while True:
        try:
            PEAK[0] = max(PEAK[0], p.memory_info().private)
        except Exception:
            pass
        time.sleep(0.1)


BANOR = [  # namn som köparen skriver, ort, land
    ("Falsterbo Golfklubb", "Skanör med Falsterbo", "Sweden"),
    ("Stockholms Golfklubb", "Danderyd", "Sverige"),
    ("Ljunghusens GK", "Höllviken", "Sweden"),
    ("Old Course", "St Andrews", "United Kingdom"),
    ("Royal Birkdale", "Southport", "United Kingdom"),
    ("East Lake Golf Club", "Atlanta", "USA"),
    ("Spyglass Hill", "Carmel-by-the-Sea", "USA"),
    ("Valderrama", "San Roque", "Spain"),
]
STILAR = ["klassisk", "vintage", "minimal", "mork"]
SPRAK = ["en", "sv", "de"]
KOPARFEL = [
    ({"course": "Carnoustie", "city": "Carnoustie", "country": "UK"}, "course_ambiguous"),
    ({"course": "Halmstad Golfklubb", "city": "Halmstad", "country": "Sweden"}, "course_ambiguous"),
    ({"course": "Augusta National", "city": "Augusta", "country": "USA"}, "course_holes_incomplete"),
    ({"course": "Pebble Beach Golf Links", "city": "Carmel-by-the-Sea", "country": "USA"}, "course_holes_incomplete"),
    ({"course": "Nonexistent Links", "city": "Danderyd", "country": "Sweden"}, "course_not_found"),
    ({"course": "Falsterbo", "city": "Skanör med Falsterbo", "country": "Sweden", "hole": "19"}, "hole_not_found"),
    ({"course": "Valderrama", "city": "Nowhereville", "country": "Spain"}, "city_not_found"),
]
FELINJ = {"saknad_attribution": "attribution", "tomt_hal": "hal_ritade", "saknat_nummer": "halnummer", "fel_rotation": "hal_ritade",
          "utanfor_marginal": "inom_marginal", "fel_markering": "markerat_hal", "fel_par": "_par", "lag_upplosning": "upplosning"}


def job(i, **kw):
    j = {"id": f"g{i:03d}{'0' * 20}"[:24], "product": "golfbana", "text": "", "lang": "en", "style": "klassisk", "created": "2026-09-27T00:00:00Z"}
    j.update(kw)
    return j


def kor(j, wd):
    order, err = fulfil.make_order(j)
    if err:
        return None, err, None
    t = time.perf_counter()
    r, pdf = fulfil.produce(order, wd)
    dt = time.perf_counter() - t
    if r is None:
        return None, pdf, dt
    return r, pdf, dt


def main():
    threading.Thread(target=mon, daemon=True).start()
    shutil.rmtree(UT, ignore_errors=True); UT.mkdir(parents=True)
    rapport = {"banor": [], "koparfel": [], "felinjektion": [], "determinism": None}
    t0 = time.perf_counter()
    i = 0
    for bi, (name, city, country) in enumerate(BANOR):
        for si, st in enumerate(STILAR):
            i += 1
            lang = SPRAK[(bi + si) % 3]
            extra = {}
            if si in (0, 3):
                extra = {"hole": str([7, 12, 3, 16, 5, 9, 14, 11][bi]), "player": ["Anna Lind", "Erik", "James Miller", "Sofía García"][si % 4],
                         "date": "2026-06-12", "text": ["Hole in one", "Our first round"][si % 2]}
            j = job(i, course=name, city=city, country=country, style=st, lang=lang, **extra)
            wd = UT / f"{bi}_{st}"; wd.mkdir()
            r, pdf, dt = kor(j, wd)
            rad = {"bana": name, "stil": st, "sprak": lang, "hal": extra.get("hole"), "tid_s": round(dt or 0, 2)}
            if r is None:
                rad.update(godkand=False, fel=pdf)
            else:
                meta = json.load(open(wd / f"{j['id']}_meta.json", encoding="utf-8"))
                rad.update(godkand=r["godkand"], grindar=r["antal_grindar"], underkanda=[(u["grind"], u["detalj"][:200]) for u in r["underkanda"]],
                           osm=meta["bana"]["osm"], osm_namn=meta["bana"]["namn"], antal_hal=len(meta["hal"]), rot=meta["projektion"]["rot_grader"],
                           pdf=str(pdf))
            rapport["banor"].append(rad)
            print(("OK  " if rad["godkand"] else "FEL ") + json.dumps(rad, ensure_ascii=False)[:300], flush=True)
    for k, (kw, want) in enumerate(KOPARFEL):
        j = job(100 + k, **kw)
        wd = UT / f"kf{k}"; wd.mkdir()
        r, err, dt = kor(j, wd)
        det = None
        fj = wd / f"{j['id']}_fel.json"
        if fj.exists():
            det = json.load(open(fj, encoding="utf-8"))["detalj"]
        ok = r is None and err == want
        rapport["koparfel"].append({"fall": kw, "vantat": want, "fick": err if r is None else "levererad", "ok": ok, "detalj": det})
        print(("OK  " if ok else "FEL ") + f"köparfel {kw['course']}: väntat {want}, fick {err}  {json.dumps(det, ensure_ascii=False)[:200]}", flush=True)
    base = job(200, course="Falsterbo Golfklubb", city="Skanör med Falsterbo", country="Sweden", hole="7", player="Anna", date="2026-06-12")
    for k, (fel, grind) in enumerate(FELINJ.items()):
        os.environ["FELINJEKTION"] = fel
        import importlib, golfbana
        importlib.reload(golfbana)
        wd = UT / f"fi_{fel}"; wd.mkdir()
        r, pdf, dt = kor(dict(base, id=f"f{k:03d}{'0' * 20}"[:24]), wd)
        os.environ["FELINJEKTION"] = ""
        importlib.reload(golfbana)
        fangad = r is not None and not r["godkand"] and any(grind in u["grind"] for u in r["underkanda"])
        rapport["felinjektion"].append({"fel": fel, "vantad_grind": grind, "fangad": fangad,
                                        "underkanda": [u["grind"] for u in (r or {"underkanda": []})["underkanda"]]})
        print(("OK  " if fangad else "FEL ") + f"felinjektion {fel}: {[u['grind'] for u in (r or {'underkanda': []})['underkanda']]}", flush=True)
    # determinism: två separata processer
    hs = []
    for n in range(2):
        wd = Path(tempfile.mkdtemp())
        o, _ = fulfil.make_order(dict(base, id="d" + "0" * 23, style="vintage"))
        (wd / "o.json").write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
        subprocess.run([sys.executable, str(F / "golfbana.py"), str(wd / "o.json")], env=dict(os.environ, STJARN_OUT=str(wd)), check=True, capture_output=True)
        hs.append(hashlib.sha256((wd / f"{o['id']}_en.pdf").read_bytes()).hexdigest())
        shutil.rmtree(wd, ignore_errors=True)
    rapport["determinism"] = {"sha256": hs, "lika": hs[0] == hs[1]}
    print(("OK  " if hs[0] == hs[1] else "FEL ") + f"determinism {hs[0][:16]} {hs[1][:16]}")
    ok_b = sum(r["godkand"] for r in rapport["banor"])
    rapport["sammanfattning"] = {"banor_godkanda": f"{ok_b}/{len(rapport['banor'])}",
                                 "koparfel_ratt": f"{sum(x['ok'] for x in rapport['koparfel'])}/{len(rapport['koparfel'])}",
                                 "felinjektion_fangad": f"{sum(x['fangad'] for x in rapport['felinjektion'])}/{len(rapport['felinjektion'])}",
                                 "determinism": rapport["determinism"]["lika"], "total_s": round(time.perf_counter() - t0, 1),
                                 "tid_per_order_s_max": max(r["tid_s"] for r in rapport["banor"]),
                                 "topp_privat_minne_mb": round(PEAK[0] / 1e6)}
    (UT / "rapport.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rapport["sammanfattning"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
