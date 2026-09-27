"""Kvalitetsgrind för förmörkelseguiden. Oberoende av generatorn:
  * räknar själv med NASA:s Besselska element (Espenak/Meeus, VSOP87/ELP2000) – inte Skyfield/DE421,
  * kontrollerar sin egen metod mot NASA:s JSEX-utdata för 20 orter vid varje körning (självtest),
  * läser tillbaka den färdiga PDF:en (text, typsnitt, sidformat) och rastrerar den:
      - andelen synlig sol i varje ritad skiva mot grindens egen täckningsgrad vid samma tidpunkt,
      - NASA:s centrallinje och gränser (Espenak) mot det ritade bandet på kartan,
      - köparens ort och solens position på himmelsbilden,
  * kör om generatorn och kräver byte-identisk PDF.

Körning: python grind_formorkelse.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
"""
import hashlib, json, math, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import fitz
import numpy as np

import oberoende as ob

ROOT = Path(__file__).parent
NASA = ROOT / "data" / "nasa"
A4 = (595.28, 841.89)
A3 = (841.89, 1190.55)
TOL_S = 60.0          # kravet: ≤ 1 min
TOL_OBSC = 1.0        # kravet: ≤ 1 procentenhet
MUST_CREDIT = ["Skyfield", "DE421", "Natural Earth", "Fred Espenak", "OFL"]
FOREIGN = {"sv": ["Finsternis", "Eclipse begins", "Totalidad", "Total eclipse"],
           "en": ["Förmörkelsen", "Finsternis", "Totalidad"],
           "de": ["Förmörkelsen", "Eclipse begins", "Totalidad"],
           "es": ["Förmörkelsen", "Finsternis", "Eclipse begins"]}
SAFETY_KEYS = ["s1", "s2", "s3", "s4", "s6"]


def bessel():
    el = json.load(open(NASA / "besselska_element_2027.json", encoding="utf-8"))
    return ob.Bessel(el["2027-08-02"]), ob.Bessel(el["2027-08-02_canon"])


def selftest(Bc):
    """Grindens egen metod mot NASA JSEX (samma element) – bevisar att facit räknas rätt."""
    ref = json.load(open(NASA / "referens_orter_2027-08-02.json", encoding="utf-8"))
    worst_s, worst_o, n = 0.0, 0.0, 0
    for r in ref["orter"]:
        L = Bc.local(r["lat"], r["lon"], r["alt_m"])
        for k in ("c1", "max", "c4"):
            d = Bc.ut(L["t_" + k])
            h = d.hour + d.minute / 60 + d.second / 3600 + d.microsecond / 3.6e9
            worst_s = max(worst_s, abs(h - r[k + "_ut_h"]) * 3600)
        worst_o = max(worst_o, abs(L["obscuration"] - r["obscuration"]) * 100)
        n += (L["type"] == r["type"])
    return worst_s, worst_o, n, len(ref["orter"])


def sun_fraction(img, x, y, r, sc):
    """Andel solfärgade pixlar inom cirkeln (x,y,r i pt, y från sidans överkant)."""
    X, Y, Rp = x * sc, y * sc, r * sc
    x0, x1, y0, y1 = int(X - Rp) - 1, int(X + Rp) + 2, int(Y - Rp) - 1, int(Y + Rp) + 2
    sub = img[max(0, y0):y1, max(0, x0):x1].astype(int)
    yy, xx = np.mgrid[max(0, y0):y1, max(0, x0):x1]
    inside = (xx + 0.5 - X) ** 2 + (yy + 0.5 - Y) ** 2 <= (Rp * 0.985) ** 2
    R_, G_, B_ = sub[..., 0], sub[..., 1], sub[..., 2]
    sunpx = (R_ > 200) & (G_ > 140) & (B_ < 150) & (R_ - B_ > 90)
    return float(sunpx[inside].mean()) if inside.any() else -1.0


def obsc_at(B, utc_iso, lat, lon, h):
    """Grindens täckningsgrad vid given UTC-tid (Besselska element)."""
    d = datetime.fromisoformat(utc_iso)
    jd = ob.jd_from_dt(d) + B.dT / 86400
    t = (jd - B.jd0) * 24
    s = B.state(t, lat, lon, h)
    if s["m"] >= s["L1"]:
        return 0.0
    k = (s["L1"] - s["L2"]) / (s["L1"] + s["L2"])
    return ob.overlap_fraction(1.0, k, 2 * s["m"] / (s["L1"] + s["L2"]))


def secs(tstr):
    p = [int(x) for x in tstr.split(":")]
    return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)


def run(meta_path):
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]
    lat, lon, h = o["lat"], o["lon"], o.get("elevation_m", 0.0)
    tz = ZoneInfo(o["timezone"])
    gen = meta["circumstances"]
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})

    B, Bc = bessel()
    ws, wo, ntyp, nref = selftest(Bc)
    chk("sjalvtest_mot_nasa_jsex", ws < 1.0 and wo < 0.1 and ntyp == nref,
        f"grindens metod mot NASA JSEX, {nref} orter: max {ws:.3f} s, {wo:.3f} %-enh., typ rätt {ntyp}/{nref}")

    G = B.local(lat, lon, h)
    chk("typ_av_formorkelse", G["type"] == gen["type"], f"grind {G['type']} / generator {gen['type']}")
    gate_utc = {k: B.ut(G["t_" + k]) for k in ("c1", "max", "c4", "c2", "c3") if "t_" + k in G}
    diffs = {}
    for k, d in gate_utc.items():
        if k in gen.get("contacts_utc", {}):
            diffs[k] = abs((datetime.fromisoformat(gen["contacts_utc"][k]) - d).total_seconds())
    chk("kontakttider", diffs and max(diffs.values()) <= TOL_S and set(diffs) == set(gate_utc),
        "avvikelse s: " + ", ".join(f"{k} {v:.1f}" for k, v in diffs.items()) + f" (gräns {TOL_S:.0f} s)")
    chk("tackningsgrad", abs(G["obscuration"] - gen["obscuration"]) * 100 <= TOL_OBSC,
        f"grind {G['obscuration'] * 100:.2f} % / generator {gen['obscuration'] * 100:.2f} % (gräns {TOL_OBSC} %-enh.)")
    if G["type"] == "total":
        dG = (G["t_c3"] - G["t_c2"]) * 3600
        chk("totalitetens_langd", abs(dG - gen.get("duration_s", -999)) <= 10, f"grind {dG:.1f} s / generator {gen.get('duration_s', 0):.1f} s")
    ga, gz = G["alt_max"], G["az_max"]
    alt, az = gen["sun_altaz"]["max"]
    chk("solens_hojd_och_riktning", abs(ga - alt) <= 0.5 and abs((gz - az + 180) % 360 - 180) <= 1.0,
        f"höjd {ga:.2f}°/{alt:.2f}°, azimut {gz:.2f}°/{az:.2f}°")
    chk("solen_over_horisonten", G.get("alt_max", -1) > 0, f"solens höjd vid maximum {G.get('alt_max', -1):.1f}°")

    # närmaste totalitet mot NASA:s gränslinjer
    nasa = json.load(open(NASA / "nasa_bana_2027.json", encoding="utf-8"))["paths"]
    lim = np.array(nasa["Northern Limit"][0] + nasa["Southern Limit"][0])
    dn = min(ob.haversine_km(lat, lon, p[0], p[1]) for p in lim) if G["type"] != "total" else 0.0
    if G["type"] != "total":
        chk("avstand_till_totalitet", abs(dn - gen["nearest_km"]) <= max(15, 0.02 * dn),
            f"NASA-gräns {dn:.0f} km / generator {gen['nearest_km']:.0f} km")

    local = {k: d.astimezone(tz) for k, d in gate_utc.items()}
    for lang, pdf in meta["files"].items():
        doc = fitz.open(pdf)
        chk(f"{lang}_sidor", len(doc) == 4, f"{len(doc)} sidor (3 guide + 1 affisch)")
        sizes = [(round(p.rect.width, 1), round(p.rect.height, 1)) for p in doc]
        okfmt = len(doc) == 4 and all(abs(s[0] - A4[0]) < 1.5 and abs(s[1] - A4[1]) < 1.5 for s in sizes[:3]) \
            and abs(sizes[3][0] - A3[0]) < 1.5 and abs(sizes[3][1] - A3[1]) < 1.5
        chk(f"{lang}_sidformat", okfmt, f"{sizes}")
        fonts = set()
        emb = True
        for p in doc:
            for f in p.get_fonts(full=True):
                fonts.add(f[3]); emb &= f[1] not in ("n/a", "")
        chk(f"{lang}_typsnitt_inbaddade", emb and len(fonts) >= 2, f"{sorted(fonts)}")
        texts = [p.get_text() for p in doc]
        alltxt = "\n".join(texts)
        flat = re.sub(r"\s+", " ", alltxt)
        chk(f"{lang}_inga_trasiga_tecken", "�" not in alltxt and "■" not in alltxt, "U+FFFD/■ ej funnet")
        # tider på sida 1 (hh:mm:ss) – varje kontakt måste finnas inom 60 s, och inga andra
        tok = re.findall(r"\b(\d{2}:\d{2}:\d{2})\b", texts[0])
        want = [local[k].hour * 3600 + local[k].minute * 60 + local[k].second + local[k].microsecond / 1e6 for k in local]
        got = [secs(t) for t in tok]
        miss = [k for k, w in zip(local, want) if not any(abs(g - w) <= TOL_S for g in got)]
        extra = [t for t, g in zip(tok, got) if not any(abs(g - w) <= TOL_S for w in want)]
        chk(f"{lang}_tider_sida1", not miss and not extra and len(tok) == len(local),
            f"tryckt {tok}; saknas {miss}; felaktiga {extra}")
        # tider på affischen och kartsidan (hh:mm)
        for pi, name in ((1, "kartsida"), (3, "affisch")):
            tk = [secs(t) for t in re.findall(r"\b(\d{2}:\d{2})\b(?!:)", texts[pi])]
            miss = [k for k in ("c1", "max", "c4")
                    if not any(abs(g - (local[k].hour * 3600 + local[k].minute * 60 + local[k].second)) <= TOL_S for g in tk)]
            chk(f"{lang}_tider_{name}", not miss, f"saknas inom 60 s: {miss}" if miss else "start, max, slut ok")
        # täckningsgrad tryckt på sida 1 och affischen
        for pi in (0, 3):
            nums = [float(x.replace(",", ".")) for x in re.findall(r"(\d{1,3}(?:[.,]\d)?)\s?%", texts[pi])]
            ok = bool(nums) and all(abs(n - G["obscuration"] * 100) <= TOL_OBSC + 0.05 for n in nums)
            chk(f"{lang}_tackning_tryckt_s{pi + 1}", ok, f"tryckt {nums} / grind {G['obscuration'] * 100:.2f}")
        # totalitet
        m = re.search(r"(\d+) min (\d+) s", texts[0])
        if G["type"] == "total":
            dG = (G["t_c3"] - G["t_c2"]) * 3600
            ok = bool(m) and abs(int(m.group(1)) * 60 + int(m.group(2)) - dG) <= 10
            chk(f"{lang}_totalitet_tryckt", ok, f"tryckt {m.group(0) if m else None} / grind {dG:.0f} s")
        else:
            chk(f"{lang}_totalitet_tryckt", m is None, "ingen totalitetslängd tryckt för partiell ort")
            km = re.findall(r"(\d[\d , ]*) km", texts[0])
            kmv = [int(re.sub(r"\D", "", x)) for x in km]
            chk(f"{lang}_avstand_tryckt", len(kmv) == 1 and abs(kmv[0] - dn) <= max(20, 0.02 * dn), f"tryckt {kmv} / NASA {dn:.0f} km")
        # solens höjd och azimut tryckta
        ma = re.search(r"(\d+)° (?:över|above|über|sobre)", texts[0])
        mz = re.search(r"(?:azimut|azimuth|Azimut|acimut) (\d+)°", texts[0])
        ok = bool(ma and mz) and abs(int(ma.group(1)) - ga) <= 1 and abs((int(mz.group(1)) - gz + 180) % 360 - 180) <= 2
        chk(f"{lang}_solposition_tryckt", ok, f"tryckt {ma.group(1) if ma else None}°/{mz.group(1) if mz else None}° mot {ga:.1f}°/{gz:.1f}°")
        # ort, text, säkerhet, källor, språk
        need = [o["place"]] + ([o["text"]] if o.get("text") else [])
        chk(f"{lang}_ort_och_text", all(n in alltxt for n in need), f"{need}")
        chk(f"{lang}_iso_12312_2", "ISO 12312-2" in texts[2], "säkerhetsstandarden nämns på säkerhetssidan")
        import formorkelse as F  # bara mallarna (text), ingen beräkning
        tx = F.T[lang]
        miss = [k for k in SAFETY_KEYS + ["s5_tot" if G["type"] == "total" else "s5_part"] if re.sub(r"\s+", " ", tx[k]) not in flat]
        chk(f"{lang}_sakerhetsrad_kompletta", not miss, f"saknas: {miss}" if miss else "alla sex säkerhetsråd tryckta")
        chk(f"{lang}_kallor", all(k in flat for k in MUST_CREDIT), "Skyfield, DE421, Natural Earth, Espenak, OFL")
        bad = [w for w in FOREIGN[lang] if w in alltxt]
        chk(f"{lang}_sprak", not bad, f"främmande ord: {bad}" if bad else "inga främmande mallord")
        # layout: all text inom 8 mm marginal
        badl = []
        for pi, p in enumerate(doc):
            w, hh = p.rect.width, p.rect.height
            for b in p.get_text("dict")["blocks"]:
                for l in b.get("lines", []):
                    x0, y0, x1, y1 = l["bbox"]
                    if x0 < 22.6 or x1 > w - 22.6 or y0 < 22.6 or y1 > hh - 22.6:
                        badl.append((pi + 1, "".join(s["text"] for s in l["spans"])[:25]))
        chk(f"{lang}_layout", not badl, f"{badl[:3]}" if badl else "ok")

        # raster: skivorna
        geo = meta["geometry"][lang]
        dpi = 110
        sc = dpi / 72
        worst = 0.0
        for pi, disks in ((0, geo["disks_page1"] + [geo["disk_big"]]), (3, geo["disks_poster"])):
            pix = doc[pi].get_pixmap(dpi=dpi)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            ph = doc[pi].rect.height
            for j, d in enumerate(disks):
                snap = meta["timeline"][j] if j < len(meta["timeline"]) else meta["timeline"][len(meta["timeline"]) // 2]
                exp = 1 - obsc_at(B, snap["utc"], lat, lon, h)
                if G["type"] == "total" and d["max"]:
                    exp = 0.0
                got = sun_fraction(img, d["x_pt"], ph - d["y_pt"], d["r_pt"], sc)
                worst = max(worst, abs(got - exp))
        chk(f"{lang}_raster_skivor", worst <= 0.04, f"största skillnad synlig sol ritad/beräknad {worst * 100:.1f} %-enh. (gräns 4)")
        # raster: kartan mot NASA:s bana
        mp = geo["map"]
        x0, y0, w, hm = mp["box_pt"]
        lon0, lon1, lat0, lat1 = mp["extent"]
        pix = doc[1].get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).astype(int)
        ph = doc[1].rect.height

        def px(la, lo):
            X = (x0 + (lo - lon0) / (lon1 - lon0) * w) * sc
            Y = (ph - (y0 + (la - lat0) / (lat1 - lat0) * hm)) * sc
            return int(X), int(Y)

        def warm(la, lo):
            X, Y = px(la, lo)
            q = img[Y, X]
            return q[0] > 150 and q[0] - q[2] > 80

        mX, mY = px(lat, lon)
        # punkter som täcks av köparens egen markering (≤ 3 mm) räknas inte
        inbox = lambda la, lo: (lon0 + 0.5 < lo < lon1 - 0.5 and lat0 + 0.8 < la < lat1 - 0.8
                                and math.hypot(px(la, lo)[0] - mX, px(la, lo)[1] - mY) > 3 * mm_px(sc))
        cen = [p for p in nasa["Central Line"][0] if inbox(*p)]
        north = [(p[0] + 0.6, p[1]) for p in nasa["Northern Limit"][0] if inbox(p[0] + 0.6, p[1])]
        south = [(p[0] - 0.6, p[1]) for p in nasa["Southern Limit"][0] if inbox(p[0] - 0.6, p[1])]
        inside_n = [(p[0] - 0.25, p[1]) for p in nasa["Northern Limit"][0] if inbox(p[0] - 0.25, p[1])]
        f_c = np.mean([warm(*p) for p in cen]) if cen else 0
        f_i = np.mean([warm(*p) for p in inside_n]) if inside_n else 0
        f_o = np.mean([not warm(*p) for p in north + south]) if north + south else 0
        chk(f"{lang}_karta_bana_mot_nasa", len(cen) > 20 and f_c >= 0.97 and f_i >= 0.95 and f_o >= 0.97,
            f"NASA-centrallinje i bandet {f_c * 100:.0f} % ({len(cen)} p), 28 km innanför nordgränsen {f_i * 100:.0f} %, "
            f"67 km utanför gränserna fritt {f_o * 100:.0f} %")
        X, Y = px(lat, lon)
        r_ = int(1.0 * mm_px(sc))
        win = img[max(0, Y - r_):Y + r_ + 1, max(0, X - r_):X + r_ + 1]
        white = ((win[..., 0] > 230) & (win[..., 1] > 230) & (win[..., 2] > 230)).any()
        chk(f"{lang}_karta_ortmarkering", white, "vit markering vid köparens koordinater" if white else "ingen markering vid köparens ort")
        # raster: himmelsbilden – solen vid maximum där grinden säger
        dome = geo["dome"]
        cx, cy = dome["center_pt"]; R = dome["R_pt"]
        rr = R * (90 - max(ga, 0)) / 90
        Xs, Ys = (cx + rr * math.sin(math.radians(gz))) * sc, (ph - (cy + rr * math.cos(math.radians(gz)))) * sc
        r_ = int(1.2 * mm_px(sc))
        win = img[int(Ys) - r_:int(Ys) + r_ + 1, int(Xs) - r_:int(Xs) + r_ + 1]
        sunok = ((win[..., 0] > 200) & (win[..., 2] < 150)).mean() > 0.3 if win.size else False
        chk(f"{lang}_himmelsbild_solposition", sunok, "solen ritad där grinden räknar (±1,2 mm)")

    # determinism
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, STJARN_OUT=td)
        of = Path(td) / "order.json"; of.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "formorkelse.py"), str(of)], env=env, capture_output=True)
        same = r.returncode == 0 and all(
            hashlib.sha256((Path(td) / f"{o['id']}_{l}.pdf").read_bytes()).hexdigest() == meta["sha256"][l] for l in meta["files"])
    chk("determinism", same, "identisk PDF vid omkörning" if same else "PDF skiljer sig vid omkörning")

    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "product": "formorkelse", "godkand": passed, "antal_grindar": len(checks),
           "facit": {"typ": G["type"], "tackning": G["obscuration"], "utc": {k: v.isoformat() for k, v in gate_utc.items()}},
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


def mm_px(sc):
    return 72 / 25.4 * sc


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)}, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
