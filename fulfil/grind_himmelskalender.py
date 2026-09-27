"""Kvalitetsgrind för himmelskalendern. Oberoende av generatorn (Skyfield/DE421):
  * månfaser: Meeus kap. 49,           * sol upp/ned: NOAA (Meeus kap. 25),
  * månens belysning: Meeus kap. 48,    * planeter: JPL Standish Keplerelement,
  * meteorsvärmar: egen solongitud + egen avskrift av IMO:s tabell,
  * månförmörkelser: NASA:s tabell (Espenak), solförmörkelser: NASA:s Besselska element.
Läser tillbaka PDF:en: varje dagsruta tolkas från texten (datum → uppgång/nedgång, faser, svärmar,
förmörkelser), sidopanelens planeter och fullmånenamn, och månens form mäts i rastret.

Körning: python grind_himmelskalender.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
"""
import hashlib, json, math, os, re, subprocess, sys, tempfile, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import fitz
import numpy as np

import oberoende as ob

ROOT = Path(__file__).parent
NASA = ROOT / "data" / "nasa"
A4L = (841.89, 595.28)
TOL_PHASE_S = 120
TOL_SUN_MIN = 2
# egen avskrift av IMO:s standardtabell (kod: solens longitud J2000, ZHR, radiantens deklination)
IMO = {"QUA": (283.15, 80, 49), "LYR": (32.32, 18, 34), "ETA": (45.5, 50, -1), "SDA": (127.0, 25, -16),
       "PER": (140.0, 100, 58), "DRA": (195.4, 10, 54), "ORI": (208.0, 20, 16), "LEO": (235.27, 15, 22),
       "GEM": (262.2, 150, 33), "URS": (270.7, 10, 76)}
MUST_CREDIT = ["Skyfield", "DE421", "IMO", "Meeus", "NOAA", "Fred Espenak", "OFL"]
PLANETS = ["mercury", "venus", "mars", "jupiter", "saturn"]


def hm(s):
    h, m = s.split(":"); return int(h) * 60 + int(m)


def local_min(dt, tz):
    d = dt.astimezone(tz); return d.hour * 60 + d.minute + d.second / 60


def parse_cells(page, grid_right):
    """Dagsrutor ur PDF-texten: dagnummer (Serif 13 pt) och raderna under, kopplade via x-läge och höjd."""
    days, lines = [], []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            txt = "".join(s["text"] for s in l["spans"]).strip()
            x0, y0, x1, y1 = l["bbox"]
            if x0 > grid_right:
                continue
            sp = l["spans"][0]
            if "Serif" in sp["font"] and abs(sp["size"] - 13) < 0.5 and txt.isdigit():
                days.append((int(txt), x0, y0))
            else:
                lines.append((txt, x0, y0))
    cells = {d: [] for d, _, _ in days}
    for txt, x0, y0 in lines:
        cand = [(y0 - dy, d) for d, dx, dy in days if abs(dx - x0) < 3 and 0 < y0 - dy < 140]
        if cand:
            cells[min(cand)[1]].append(txt)
    return cells


def parse_sidebar(page, x_min):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            if l["bbox"][0] >= x_min:
                out.append((l["bbox"][1], "".join(s["text"] for s in l["spans"]).strip()))
    return [t for _, t in sorted(out)]


def gate_planets(lat, lon, tz, year, month):
    """Samma regel som produkten, men med NOAA-sol och Standish-planeter. Returnerar (synlig-lista, gränsfall-set)."""
    d = date(year, month, 15)
    sr, e1 = ob.sun_event_utc(d, lat, lon, rising=True)
    ss, e2 = ob.sun_event_utc(d, lat, lon, rising=False)
    sr2, _ = ob.sun_event_utc(d + timedelta(days=1), lat, lon, rising=True)
    if not sr or not ss:
        return None, None
    # lokala datum kan förskjutas vid stora tidszoner – håll uppgång före nedgång
    if ss < sr:
        ss += timedelta(days=1)
    inst = {"E": ss + timedelta(minutes=45), "M": sr - timedelta(minutes=45)}
    if sr2:
        if sr2 < ss:
            sr2 += timedelta(days=1)
        inst["N"] = ss + (sr2 - ss) / 2
    vis, border = {p: [] for p in PLANETS}, {p: set() for p in PLANETS}
    for k, when in inst.items():
        jd = ob.jd_from_dt(when)
        salt, _ = ob.sun_altaz(jd, lat, lon)
        for p in PLANETS:
            ra, dec = ob.planet_radec(p, jd)
            alt, _ = ob.radec_to_altaz(ra, dec, lat, lon, jd)
            if salt <= -3 and alt >= 5:
                vis[p].append(k)
            if abs(alt - 5) < 2.0 or abs(salt + 3) < 1.5:
                border[p].add(k)
    return vis, border


def run(meta_path):
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]; data = meta["data"]; Y = meta["year"]
    lat, lon = o["lat"], o["lon"]
    tz = ZoneInfo(o["timezone"])
    y0, y1 = datetime(Y, 1, 1, tzinfo=tz), datetime(Y + 1, 1, 1, tzinfo=tz)
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})

    import himmelskalender as HK  # bara textmallarna och namnlistorna, ingen beräkning
    # ---- facit
    gph = [(k, dt) for k, dt in ob.moon_phases_year(Y) if y0 <= dt.astimezone(tz) < y1]
    gen_ph = [(p["phase"], datetime.fromisoformat(p["utc"])) for p in data["phases"]]
    same_seq = [k for k, _ in gph] == [k for k, _ in gen_ph]
    worst = max(abs((a[1] - b[1]).total_seconds()) for a, b in zip(gph, gen_ph)) if same_seq else 1e9
    chk("manfaser_mot_meeus", same_seq and worst <= TOL_PHASE_S,
        f"{len(gph)} faser, största avvikelse {worst:.0f} s (gräns {TOL_PHASE_S} s)" if same_seq else f"olika antal/ordning: {len(gph)} vs {len(gen_ph)}")
    # sol upp/ned: NOAA-händelser grupperade per lokalt datum. Toleransen skalas upp när solen skär horisonten
    # mycket flackt (nära midnattssol/polarnatt), motsvarande 0,1° i höjd – annars minst 2 minuter.
    grise, gset = {}, {}
    d = date(Y, 1, 1) - timedelta(days=2)
    while d <= date(Y + 1, 1, 2):
        for rising, store in ((True, grise), (False, gset)):
            e, _ = ob.sun_event_utc(d, lat, lon, rising)
            if e:
                loc = e.astimezone(tz)
                lst = store.setdefault(loc.date().isoformat(), [])
                if all(abs((loc - x).total_seconds()) > 1800 for x in lst):
                    lst.append(loc)
        d += timedelta(days=1)

    def sun_tol(ev):
        jd = ob.jd_from_dt(ev)
        rate = abs(ob.sun_altaz(jd + 1 / 1440, lat, lon)[0] - ob.sun_altaz(jd - 1 / 1440, lat, lon)[0]) / 2
        return max(TOL_SUN_MIN, 0.1 / max(rate, 1e-4))

    def match(store, dk, minutes):
        c = store.get(dk, [])
        return min(c, key=lambda x: abs(local_min(x, tz) - minutes)) if c else None

    worst_s, polar_mis, n_over = 0.0, [], []
    for rec in data["days"]:
        for key, store in (("rise", grise), ("set", gset)):
            v = rec[key]
            if v is None:
                if store.get(rec["date"]):
                    polar_mis.append(rec["date"])
                continue
            vd = datetime.fromisoformat(v)
            g = match(store, rec["date"], local_min(vd, tz))
            if g is None:
                polar_mis.append(rec["date"]); continue
            e = abs((vd - g).total_seconds()) / 60
            worst_s = max(worst_s, e)
            if e > sun_tol(g):
                n_over.append(rec["date"])
    chk("sol_upp_ned_mot_noaa", not n_over and len(set(polar_mis)) <= 6,
        f"största avvikelse {worst_s:.2f} min (gräns {TOL_SUN_MIN} min, vidgad vid flack horisontpassage), "
        f"utanför gränsen: {n_over[:4]}, dagar där bara ena metoden har händelse: {len(set(polar_mis))}")
    # meteorsvärmar
    gmet = {}
    for code, (L0, zhr, dec) in IMO.items():
        jd = ob.jd_from_dt(datetime(Y, 1, 1, tzinfo=timezone.utc)) - 3
        f = lambda j: (ob.sun_lon_j2000(j) - L0 + 180) % 360 - 180
        step = 1.0
        while jd < ob.jd_from_dt(datetime(Y + 1, 1, 3, tzinfo=timezone.utc)):
            if f(jd) < 0 <= f(jd + step):
                a, b = jd, jd + step
                for _ in range(40):
                    m_ = (a + b) / 2
                    a, b = (m_, b) if f(m_) < 0 else (a, m_)
                pk = ob.dt_from_jd((a + b) / 2 - ob.delta_t(Y) / 86400).astimezone(tz)
                if y0 <= pk < y1:
                    gmet[code] = (pk, zhr, dec, ob.moon_illum(ob.jd_from_dt(pk)))
            jd += step
    gm = {m["code"]: m for m in data["meteors"]}
    worst_m, bad_m = 0.0, []
    for code, (pk, zhr, dec, mi) in gmet.items():
        g = gm.get(code)
        if not g:
            bad_m.append(code); continue
        dt_h = abs((datetime.fromisoformat(g["utc"]) - pk).total_seconds()) / 3600
        worst_m = max(worst_m, dt_h)
        if dt_h > 1 or g["zhr"] != zhr or abs(g["moon_frac"] - mi) > 0.05:
            bad_m.append(code)
    chk("meteorsvarmar_mot_imo", not bad_m and len(gm) == len(gmet),
        f"{len(gmet)} svärmar, toppens tid max {worst_m:.2f} h fel (gräns 1 h), ZHR och månljus ok" if not bad_m else f"avviker: {bad_m}")
    # månförmörkelser mot NASA
    lun = json.load(open(NASA / "manformorkelser_2027.json", encoding="utf-8"))["formorkelser"]
    glun = []
    for e in lun:
        td = datetime.fromisoformat(e["datum"] + "T" + e["max_td"]).replace(tzinfo=timezone.utc)
        ut = td - timedelta(seconds=ob.delta_t(Y))
        if y0 <= ut.astimezone(tz) < y1:
            jd = ob.jd_from_dt(ut)
            ra, dec, _, _ = ob.sun_eq(jd)
            malt, _ = ob.radec_to_altaz((ra + 180) % 360, -dec, lat, lon, jd)  # månen ≈ motsatt solen
            glun.append((ut, malt))
    genl = [(datetime.fromisoformat(e["utc"]), e["visible"]) for e in data["lunar"]]
    okl = len(glun) == len(genl) and all(abs((a[0] - b[0]).total_seconds()) <= 120 and (abs(a[1]) < 2.5 or (a[1] > 0) == b[1])
                                          for a, b in zip(glun, genl))
    chk("manformorkelser_mot_nasa", okl, f"NASA {len(glun)} st, generator {len(genl)} st, tider "
        + ", ".join(f"{abs((a[0] - b[0]).total_seconds()):.0f} s" for a, b in zip(glun, genl)))
    # solförmörkelser mot Besselska element
    el = json.load(open(NASA / "besselska_element_2027.json", encoding="utf-8"))
    gsol = {}
    for day, key in (("2027-02-06", "2027-02-06"), ("2027-08-02", "2027-08-02")):
        B = ob.Bessel(el[key]); Lc = B.local(lat, lon)
        vis = Lc["type"] != "none" and Lc.get("alt_max", -1) > 0
        gsol[day] = (Lc["type"], vis, Lc.get("obscuration", 0.0), B.ut(Lc["t_max"]) if Lc["type"] != "none" else None)
    oks = True; det = []
    for e in data["solar"]:
        g = gsol[e["day"]]
        ok = e["visible"] == g[1] and (not g[1] or (e["type"] == g[0] and abs(e["obscuration"] - g[2]) <= 0.01
                                                     and abs((datetime.fromisoformat(e["max_utc"]) - g[3]).total_seconds()) <= 60))
        oks &= ok; det.append(f"{e['day']}: synlig {g[1]}, {g[2] * 100:.1f} %")
    chk("solformorkelser_mot_nasa", oks, "; ".join(det))
    # planeter
    pl_bad, pl_n = [], 0
    gpl = {}
    for m in range(1, 13):
        vis, border = gate_planets(lat, lon, tz, Y, m)
        gpl[m] = (vis, border)
        genm = data["planets"][str(m)]
        if vis is None or genm["light_nights"]:
            if (vis is None) != genm["light_nights"]:
                pl_bad.append(f"{m}: ljusa nätter olika")
            continue
        for p in PLANETS:
            pl_n += 1
            a, b = set(vis[p]), set(genm["visible"][p])
            if (a ^ b) - border[p]:
                pl_bad.append(f"{m}/{p}: grind {sorted(a)} gen {sorted(b)}")
    chk("planeter_mot_standish", not pl_bad, f"{pl_n} planet-månader, avvikelser utanför gränsfall: {pl_bad[:4]}")

    # ---- PDF:en
    for lang, pdf in meta["files"].items():
        lx = HK.L[lang]
        doc = fitz.open(pdf)
        chk(f"{lang}_sidor", len(doc) == 13, f"{len(doc)} sidor")
        sz = all(abs(p.rect.width - A4L[0]) < 1.5 and abs(p.rect.height - A4L[1]) < 1.5 for p in doc)
        chk(f"{lang}_sidformat_A4_liggande", sz, f"{doc[0].rect.width:.1f}×{doc[0].rect.height:.1f} pt")
        fonts, emb = set(), True
        for p in doc:
            for f in p.get_fonts(full=True):
                fonts.add(f[3]); emb &= f[1] not in ("n/a", "")
        chk(f"{lang}_typsnitt_inbaddade", emb and len(fonts) >= 2, f"{sorted(fonts)}")
        alltxt = "\n".join(p.get_text() for p in doc)
        flat = re.sub(r"\s+", " ", alltxt)
        chk(f"{lang}_inga_trasiga_tecken", "\ufffd" not in alltxt and "■" not in alltxt, "U+FFFD/■ ej funnet")
        chk(f"{lang}_ort_och_text", o["place"] in alltxt and (not o.get("text") or o["text"] in alltxt), "ort och personlig text finns")
        chk(f"{lang}_kallor", all(k in flat for k in MUST_CREDIT), ", ".join(MUST_CREDIT))
        bad = [w for w in HK.FOREIGN[lang] if w in alltxt]
        chk(f"{lang}_sprak", not bad, f"främmande ord: {bad}" if bad else "inga främmande mallord")
        badl = []
        for pi, p in enumerate(doc):
            for b in p.get_text("dict")["blocks"]:
                for l in b.get("lines", []):
                    x0, y0_, x1, y1_ = l["bbox"]
                    if x0 < 22.6 or x1 > p.rect.width - 22.6 or y0_ < 14 or y1_ > p.rect.height - 14:
                        badl.append((pi + 1, "".join(s["text"] for s in l["spans"])[:25]))
        chk(f"{lang}_layout", not badl, f"{badl[:3]}" if badl else "ok")

        grid_right = 200 * 72 / 25.4 + 2
        cells_all = {}
        for m in range(1, 13):
            cells = parse_cells(doc[m], grid_right)
            for dnum, lines in cells.items():
                cells_all[date(Y, m, dnum).isoformat()] = lines
        ndays = (date(Y + 1, 1, 1) - date(Y, 1, 1)).days
        chk(f"{lang}_alla_dagar_finns", len(cells_all) == ndays, f"{len(cells_all)} av {ndays} dagsrutor hittade i texten")
        # sol upp/ned ur rutorna mot NOAA
        worst, n, bad = 0.0, 0, []
        for dk, lines in cells_all.items():
            sl = [t for t in lines if re.fullmatch(r"(\d\d:\d\d)?–(\d\d:\d\d)?|—", t)]
            if len(sl) != 1:
                bad.append(dk); continue
            mt = re.fullmatch(r"(\d\d:\d\d)?–(\d\d:\d\d)?", sl[0])
            pr, ps = (mt.group(1), mt.group(2)) if mt else (None, None)
            for p_, store in ((pr, grise), (ps, gset)):
                if p_ is None:
                    if store.get(dk) and dk not in polar_mis:
                        bad.append(dk)
                    continue
                g = match(store, dk, hm(p_))
                if g is None:
                    if dk not in polar_mis:
                        bad.append(dk)
                    continue
                e = abs(hm(p_) - local_min(g, tz))
                worst = max(worst, e); n += 1
                if e > sun_tol(g) + 0.5:
                    bad.append(dk)
        chk(f"{lang}_tryckt_sol_upp_ned", not bad, f"{n} tider lästa ur PDF:en, största avvikelse mot NOAA {worst:.2f} min; fel: {bad[:4]}")
        # faser i rutorna
        bad = []
        for k, dt in gph:
            loc = dt.astimezone(tz)
            lines = cells_all.get(loc.date().isoformat(), [])
            hit = [t for t in lines if t.startswith(lx["phases"][k] + " ")]
            if not hit or abs(hm(hit[0].split()[-1]) - local_min(dt, tz)) > 2.5:
                bad.append(f"{loc.date()} {lx['phases'][k]} {hit}")
        chk(f"{lang}_tryckta_faser", not bad, f"{len(gph)} faser i rätt ruta och tid (±2 min)" if not bad else f"fel: {bad[:3]}")
        # fullmånenamn: grindens egen regel
        fulls = [dt.astimezone(tz) for k, dt in gph if k == 2]
        eq = datetime(Y, 9, 23, tzinfo=tz)
        hi = min(range(len(fulls)), key=lambda i: abs((fulls[i] - eq).total_seconds()))
        bad = []
        months_seen = set()
        for i, f in enumerate(fulls):
            name = lx["harvest"] if i == hi else lx["hunter"] if i == hi + 1 else lx["fullnames"][f.month - 1]
            if f.month in months_seen:
                name = lx["blue"]
            months_seen.add(f.month)
            side = parse_sidebar(doc[f.month], 205 * 72 / 25.4)
            if not any(name in s for s in side):
                bad.append(f"{f.date()} {name}")
        chk(f"{lang}_fullmanenamn", not bad, f"{len(fulls)} namn rätt" if not bad else f"saknas: {bad}")
        # meteorsvärmar: rätt natt och månljus i sidopanelen
        bad = []
        for code, (pk, zhr, dec, mi) in gmet.items():
            start = pk.date() - timedelta(days=1) if pk.hour < 12 else pk.date()
            near_noon = abs(pk.hour + pk.minute / 60 - 12) < 1.5
            name = lx["meteors"][code][:22]
            ok_cell = name in cells_all.get(start.isoformat(), []) or (near_noon and any(
                name in cells_all.get((start + timedelta(days=dd)).isoformat(), []) for dd in (-1, 1)))
            side = parse_sidebar(doc[start.month], 205 * 72 / 25.4)
            ok_moon = True
            never = (dec < -(90 - lat)) if lat >= 0 else (dec > 90 + lat)
            if lx["meteors"][code] in side:
                j = side.index(lx["meteors"][code])
                nxt = " ".join(side[j + 1:j + 3])
                mm = re.search(r"(\d+)\s?%", nxt)
                ok_moon = (never and lx["never"] in nxt) or (bool(mm) and abs(int(mm.group(1)) - mi * 100) <= 5)
            else:
                ok_moon = False
            if not (ok_cell and ok_moon):
                bad.append(code)
        chk(f"{lang}_tryckta_meteorsvarmar", not bad, f"{len(gmet)} svärmar på rätt natt med rätt månljus" if not bad else f"fel: {bad}")
        # förmörkelser tryckta
        bad = []
        for ut, malt in glun:
            loc = ut.astimezone(tz)
            side = " ".join(parse_sidebar(doc[loc.month], 205 * 72 / 25.4))
            tms = [hm(t) for t in re.findall(r"(\d\d:\d\d)", side)]
            if not any(abs(t - local_min(ut, tz)) <= 2.5 for t in tms):
                bad.append(f"månförmörkelse {loc.date()}")
        for day, (typ, vis, obsc, tmax) in gsol.items():
            if not vis:
                continue
            loc = tmax.astimezone(tz)
            side = " ".join(parse_sidebar(doc[loc.month], 205 * 72 / 25.4))
            tms = [hm(t) for t in re.findall(r"(\d\d:\d\d)", side)]
            pcts = [int(x) for x in re.findall(r"(\d+)\s?%", side)]
            if not any(abs(t - local_min(tmax, tz)) <= 1.5 for t in tms) or not any(abs(p - obsc * 100) <= 1.5 for p in pcts):
                bad.append(f"solförmörkelse {day}")
        chk(f"{lang}_tryckta_formorkelser", not bad, "tider och täckning i sidopanelen stämmer" if not bad else f"fel: {bad}")
        # planeter tryckta
        bad = []
        for m in range(1, 13):
            side = parse_sidebar(doc[m], 205 * 72 / 25.4)
            vis, border = gpl[m]
            for p in PLANETS:
                row = [s for s in side if s.startswith(lx["planets"][p] + ":")]
                if len(row) != 1:
                    bad.append(f"{m}/{p} saknas"); continue
                words = row[0].split(":", 1)[1]
                printed = set()
                if lx["vis"]["all"] in words:
                    printed = {"E", "N", "M"}
                elif lx["vis"]["none"] not in words:
                    printed = {k for k in ("E", "N", "M") if re.search(r"\b" + re.escape(lx["vis"][k]) + r"\b", words)}
                if vis is not None and (printed ^ set(vis[p])) - border[p]:
                    bad.append(f"{m}/{p}: tryckt {sorted(printed)} grind {sorted(vis[p])}")
        chk(f"{lang}_tryckta_planeter", not bad, "60 planetrader stämmer med grindens beräkning" if not bad else f"fel: {bad[:3]}")
        # raster: månens form varje dag
        geo = meta["geometry"][lang]["glyphs"]
        worst, badg = 0.0, []
        sc = 150 / 72
        imgs = {}
        for dk, (gx, gy, gr) in geo.items():
            m = int(dk[5:7])
            if m not in imgs:
                pix = doc[m].get_pixmap(dpi=150, colorspace=fitz.csGRAY)
                imgs[m] = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            img = imgs[m]
            X, Yp, R = gx * sc, (A4L[1] - gy) * sc, gr * sc
            ys, xs = np.mgrid[int(Yp - R) - 1:int(Yp + R) + 2, int(X - R) - 1:int(X + R) + 2]
            inside = (xs + 0.5 - X) ** 2 + (ys + 0.5 - Yp) ** 2 <= (R * 0.97) ** 2
            sub = img[ys, xs]
            lit = sub > 150
            frac = lit[inside].mean()
            d_ = date.fromisoformat(dk)
            t22 = datetime(d_.year, d_.month, d_.day, 22, tzinfo=tz)
            jd = ob.jd_from_dt(t22)
            mi = ob.moon_illum(jd)
            e = abs(frac - mi)
            worst = max(worst, e)
            if e > 0.08:
                badg.append(dk)
            if 0.12 < mi < 0.88:
                waxing = ob.moon_illum(jd + 0.1) > mi
                xl = (xs[inside & lit] + 0.5 - X).mean()
                right = xl > 0
                if right != (waxing != (lat < 0)):
                    badg.append(dk + " sida")
        chk(f"{lang}_raster_manens_form", not badg, f"{len(geo)} dagar, största skillnad belyst andel {worst * 100:.1f} %-enh. (gräns 8); fel {badg[:4]}")

    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, STJARN_OUT=td)
        of = Path(td) / "order.json"; of.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "himmelskalender.py"), str(of)], env=env, capture_output=True)
        same = r.returncode == 0 and all(
            hashlib.sha256((Path(td) / f"{o['id']}_{l}.pdf").read_bytes()).hexdigest() == meta["sha256"][l] for l in meta["files"])
    chk("determinism", same, "identisk PDF vid omkörning" if same else "PDF skiljer sig vid omkörning")

    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "product": "himmelskalender", "godkand": passed, "antal_grindar": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)}, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
