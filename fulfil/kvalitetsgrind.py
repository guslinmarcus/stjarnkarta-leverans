"""Kvalitetsgrind för stjärnkartan. Oberoende av generatorn: egen astronomi (Meeus),
läser den färdiga PDF:en (text, typsnitt, mått) och rastrerar den i 300 dpi.

Gäller alla stilar (midnatt, minimal, akvarell, hjarta, manfas, barnrum). Bildkontrollerna är
färgbaserade: stjärnorna letas upp i RGB-rastret med stilens stjärnfärg, kontrasten mäts i rastret (WCAG),
hjärtformen och månfas-raden kontrolleras med egna formler.

Körning: python kvalitetsgrind.py ut/<id>_meta.json  -> ut/<id>_qc.json, exitkod 0 = GODKÄND
"""
import hashlib, json, math, os, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np

A3 = (841.89, 1190.55)  # pt
DATUM_MANADER = {  # grindens egen tabell – får inte importeras från generatorn
    "en": "January February March April May June July August September October November December".split(),
    "sv": "januari februari mars april maj juni juli augusti september oktober november december".split(),
    "de": "Januar Februar März April Mai Juni Juli August September Oktober November Dezember".split(),
    "fr": "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split(),
}
MM = 72 / 25.4
ROOT = Path(__file__).parent
KANDA_STILAR = {"midnatt", "minimal", "akvarell", "hjarta", "manfas", "barnrum"}


def rel_lum(rgb):
    def ch(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted([rel_lum(a), rel_lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def stereo(alt, az, R):
    """Egen stereografisk projektion (zenit i mitten, horisont på R, norr upp, öster vänster)."""
    r = R * math.tan(math.radians(90 - alt) / 2)
    return -r * math.sin(math.radians(az)), r * math.cos(math.radians(az))


def heart_poly(shape, n=720):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((x * shape["scale"], y * shape["scale"] + shape["oy"]))
    return pts


def pip(x, y, poly):
    inside = False; j = len(poly) - 1
    for i in range(len(poly)):
        (xi, yi), (xj, yj) = poly[i], poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


_CMAPS = {}


def font_cmaps():
    """PostScript-namn -> teckentabell, läst direkt ur typsnittsfilerna (fontTools, oberoende av reportlab)."""
    if not _CMAPS:
        from fontTools.ttLib import TTFont
        for f in (ROOT / "fonts").glob("*.ttf"):
            tt = TTFont(str(f), lazy=True)
            _CMAPS[tt["name"].getDebugName(6)] = set(tt.getBestCmap())
    return _CMAPS


def jd_utc(iso):
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return d.timestamp() / 86400 + 2440587.5


def altaz_meeus(ra_h, dec, lat, lon, jd):
    """Oberoende beräkning: precession (Meeus 21 låg precision) + GMST (Meeus 12.4)."""
    years = (jd - 2451545.0) / 365.25
    ra = math.radians(ra_h * 15); de = math.radians(dec)
    dra_s = (3.075 + 1.336 * math.sin(ra) * math.tan(de)) * years
    dde_as = 20.04 * math.cos(ra) * years
    ra += math.radians(dra_s * 15 / 3600); de += math.radians(dde_as / 3600)
    T = (jd - 2451545.0) / 36525
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * T * T) % 360
    H = math.radians((gmst + lon) % 360) - ra
    phi = math.radians(lat)
    alt = math.asin(math.sin(phi) * math.sin(de) + math.cos(phi) * math.cos(de) * math.cos(H))
    az = math.atan2(math.sin(H), math.cos(H) * math.sin(phi) - math.tan(de) * math.cos(phi))
    return math.degrees(alt), (math.degrees(az) + 180) % 360


def moon_illum_meeus(jd):
    T = (jd - 2451545.0) / 36525
    D = math.radians((297.8501921 + 445267.1114034 * T) % 360)
    M = math.radians((357.5291092 + 35999.0502909 * T) % 360)
    Mp = math.radians((134.9633964 + 477198.8675055 * T) % 360)
    i = 180 - math.degrees(D) - 6.289 * math.sin(Mp) + 2.100 * math.sin(M) - 1.274 * math.sin(2 * D - Mp) \
        - 0.658 * math.sin(2 * D) - 0.214 * math.sin(2 * Mp) - 0.110 * math.sin(D)
    return (1 + math.cos(math.radians(i))) / 2


def angsep(a1, z1, a2, z2):
    a1, z1, a2, z2 = map(math.radians, (a1, z1, a2, z2))
    c = math.sin(a1) * math.sin(a2) + math.cos(a1) * math.cos(a2) * math.cos(z1 - z2)
    return math.degrees(math.acos(max(-1, min(1, c))))


def run(meta_path):
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]; jd = jd_utc(meta["utc"])
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})

    # 1. Astronomi: 15 ljusaste stjärnor mot oberoende beräkning
    errs = [angsep(s["alt"], s["az"], *altaz_meeus(s["ra_h"], s["dec"], o["lat"], o["lon"], jd))
            for s in meta["check_stars"]]
    chk("astronomi_stjarnpositioner", max(errs) < 0.5, f"max avvikelse {max(errs):.3f}° (gräns 0,5°)")

    # 2. Polstjärnan / himmelspolens höjd = latitud
    if o["lat"] > 0:
        pa, _ = altaz_meeus(2.5302, 89.2641, o["lat"], o["lon"], jd)  # Polaris J2000
        chk("polstjarnans_hojd", abs(pa - o["lat"]) < 1.0, f"Polaris {pa:.2f}° vs lat {o['lat']:.2f}°")
    # 3. Månfas oberoende
    mi = moon_illum_meeus(jd)
    chk("manfas", abs(mi - meta["moon_frac"]) < 0.02, f"Meeus {mi:.3f} vs generator {meta['moon_frac']:.3f}")
    # 4. Rimligt antal stjärnor ovan horisonten (~halva katalogen till mag 5,5)
    n = meta["stars_plotted"]
    chk("antal_stjarnor", 900 < n < 1900, f"{n} stjärnor ritade")

    st = meta.get("style") or {"name": "midnatt", "shape": {"type": "circle"}, "colors": {
        "star": (1, 0.98, 0.93), "text": (0.96, 0.93, 0.85)}, "inner_margin_pt": 28.3}
    chk("stil_kand", st["name"] in KANDA_STILAR, st["name"])
    cx, cy, R = meta["geometry_pt"]
    shape = st["shape"]
    hpoly = heart_poly(shape) if shape["type"] == "heart" else None
    # 4b. Oberoende projektion: egen alt/az (Meeus) + egen stereografisk projektion -> sidposition
    perr, outside = [], 0
    for s in meta["check_stars"]:
        a, z = altaz_meeus(s["ra_h"], s["dec"], o["lat"], o["lon"], jd)
        px, py = stereo(a, z, R)
        perr.append(math.hypot(cx + px - s["page_x_pt"], cy + py - s["page_y_pt"]) / MM)
        inside = math.hypot(px, py) < R if hpoly is None else pip(px, py, hpoly)
        outside += not inside
    chk("projektion_sidposition", bool(perr) and max(perr) < 1.0, f"max {max(perr or [0]):.3f} mm (gräns 1 mm)")
    chk("kontrollstjarnor_i_himmelsytan", len(meta["check_stars"]) >= 10 and outside == 0,
        f"{len(meta['check_stars'])} kontrollstjärnor, {outside} utanför {shape['type']}")
    # 4c. Månfas-raden (stil manfas): varje måne mot Meeus, tilltagande/avtagande mot Meeus
    mrow = meta.get("moon_row")
    if mrow:
        bad = []
        for md in mrow:
            j = jd_utc(md["utc"])
            mi = moon_illum_meeus(j)
            wax = moon_illum_meeus(j + 0.25) > moon_illum_meeus(j - 0.25)
            if abs(mi - md["frac"]) > 0.02 or (wax != md["waxing"] and 0.02 < mi < 0.98):
                bad.append((md["day"], round(mi, 3), round(md["frac"], 3), wax, md["waxing"]))
        chk("manfas_rad_astronomi", len(mrow) == 7 and not bad, f"{bad}" if bad else "7 månar stämmer mot Meeus")
    cmaps = font_cmaps()

    for lang, pdf in meta["files"].items():
        doc = fitz.open(pdf); page = doc[0]
        # 5. Format
        w, h = page.rect.width, page.rect.height
        chk(f"{lang}_sidformat_A3", abs(w - A3[0]) < 1.5 and abs(h - A3[1]) < 1.5, f"{w:.1f}×{h:.1f} pt")
        chk(f"{lang}_en_sida", len(doc) == 1, f"{len(doc)} sidor")
        # 6. Typsnitt inbäddade
        fonts = page.get_fonts(full=True)
        emb = all(f[1] not in ("n/a", "") for f in fonts)  # ext n/a = ej inbäddat
        chk(f"{lang}_typsnitt_inbaddade", emb and len(fonts) >= 1, f"{[f[3] for f in fonts]}")
        # 7. Texten finns exakt och utan trasiga tecken
        txt = page.get_text()
        t = meta["texts"][lang]
        shown = t.get("name", o["name"])
        need = [shown, t["title"], t["date"], t["coord"]]
        missing = [s for s in need if s not in txt]
        chk(f"{lang}_text_komplett", not missing and shown.casefold() == o["name"].casefold(),
            f"saknas: {missing}" if missing else "namn, titel, datum, koordinater")
        # 7a. Datumformat per språk, räknat oberoende av generatorns mallar (egen månadstabell här)
        dl = o["datetime_local"]; yy_, mo_, dd_ = int(dl[:4]), int(dl[5:7]), int(dl[8:10]); hm_ = dl[11:16]
        mn = DATUM_MANADER[lang][mo_ - 1]
        want = {"en": f"{mn} {dd_}, {yy_} at {hm_}", "sv": f"{dd_} {mn} {yy_} kl. {hm_}",
                "de": f"{dd_}. {mn} {yy_} um {hm_} Uhr", "fr": f"{'1er' if dd_ == 1 else dd_} {mn} {yy_} à {hm_}"}[lang]
        chk(f"{lang}_datumformat", want in txt, f"väntat \"{want}\"")
        chk(f"{lang}_inga_trasiga_tecken", "�" not in txt and "■" not in txt, "U+FFFD/■ ej funnet")
        # 7b. Varje tecken finns i det typsnitt det är satt med (annars blir det tomrutor i trycket)
        spans = [sp for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for sp in l["spans"]]
        miss = set()
        for sp in spans:
            ps = sp["font"].split("+")[-1]
            cm = cmaps.get(ps)
            if cm is None:
                miss.add(("okänt typsnitt", ps)); continue
            for ch in sp["text"]:
                if not ch.isspace() and ord(ch) not in cm:
                    miss.add((ps, ch))
        chk(f"{lang}_glyfer_finns", not miss, f"{sorted(miss)[:5]}" if miss else "alla tecken finns i sina typsnitt")
        # 8. Licens/attribution på produkten
        credit_ok = all(k in txt for k in ["Yale Bright Star", "Olaf Frohn", "BSD", "DE421"])
        chk(f"{lang}_attribution", credit_ok, "Yale BSC, DE421, d3-celestial/BSD")
        # 9. Layout: all text innanför ramens marginal, ingen text inuti himmelsskivan eller över månarna
        cy_top = h - cy; mg = st.get("inner_margin_pt", 28.3) - 0.5
        bad = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                x0, y0, x1, y1 = l["bbox"]
                s = "".join(sp["text"] for sp in l["spans"])
                if x0 < mg or x1 > w - mg or y0 < mg or y1 > h - mg:
                    bad.append(("marginal", s[:30]))
                for (px, py) in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]:
                    if math.hypot(px - cx, py - cy_top) < R - 1:
                        bad.append(("i_cirkeln", s[:30])); break
                for md in (mrow or []):
                    my_top = h - md["y_pt"]; r = md["r_pt"] + 1.6 * MM
                    if x0 < md["x_pt"] + r and x1 > md["x_pt"] - r and y0 < my_top + r and y1 > my_top - r:
                        bad.append(("over_manen", s[:30])); break
        chk(f"{lang}_layout", not bad, f"{bad[:3]}" if bad else "ok")
        # 10. Språkkontroll: månadsnamn och inga ord från andra språks mallar
        foreign = {"sv": ["Sternenhimmel", "night sky", "ciel étoilé"], "en": ["Stjärnhimlen", "Sternenhimmel", "ciel étoilé"],
                   "de": ["Stjärnhimlen", "night sky", "ciel étoilé"],
                   "fr": ["Stjärnhimlen", "Sternenhimmel", "night sky"]}[lang]
        chk(f"{lang}_sprak", not any(f in txt for f in foreign), "inga främmande mallord")
        # 11. Raster 300 dpi i färg: stjärnorna syns där beräkningen säger, i stilens stjärnfärg
        pix = page.get_pixmap(dpi=300, colorspace=fitz.csRGB)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).astype(np.int16)
        sc = 300 / 72; hits = 0
        star = np.array([v * 255 for v in st["colors"]["star"]])
        for s in meta["check_stars"]:
            X = int(s["page_x_pt"] * sc); Y = int((h - s["page_y_pt"]) * sc)
            win = img[max(0, Y - 3):Y + 4, max(0, X - 3):X + 4].reshape(-1, 3)
            if np.sqrt(((win - star) ** 2).sum(1)).min() < 70:
                hits += 1
        chk(f"{lang}_raster_stjarnor_pa_plats", hits == len(meta["check_stars"]),
            f"{hits}/{len(meta['check_stars'])} ljusa stjärnor funna i 300 dpi-bilden")
        chk(f"{lang}_raster_storlek", (pix.width, pix.height) == (3508, 4961), f"{pix.width}×{pix.height} px")
        # himlens mitt i rastret (halva radien – ligger alltid i himmelsytan, även för hjärtat)
        Xc, Yc, Rp = cx * sc, (h - cy) * sc, R * sc
        yy, xx = np.mgrid[0:pix.height:4, 0:pix.width:4]
        disk = np.hypot(xx - Xc, yy - Yc) < Rp * 0.5
        sky_px = img[0:pix.height:4, 0:pix.width:4][disk]
        dist_star = np.sqrt(((sky_px - star) ** 2).sum(1))
        frac = float((dist_star < 40).mean())
        chk(f"{lang}_ej_tom", 0.0005 < frac < 0.2, f"andel stjärnpixlar i himlens mitt {frac:.4f}")
        # 11b. Kontrast (WCAG): text mot papper >= 4,5; stjärnor mot himlens värsta 10 % >= 3
        page_rgb = img[int(h / 2 * sc), int(5 * MM * sc)]
        text_rgb = [v * 255 for v in st["colors"]["text"]]
        cr_text = contrast(text_rgb, page_rgb)
        nonstar = sky_px[dist_star >= 40]
        lum = np.array([rel_lum(p) for p in nonstar[:: max(1, len(nonstar) // 4000)]])
        ls = rel_lum(star)
        worst = float(np.percentile(lum, 90 if ls > 0.5 else 10))
        cr_star = (max(ls, worst) + 0.05) / (min(ls, worst) + 0.05)
        chk(f"{lang}_kontrast", cr_text >= 4.5 and cr_star >= 3.0, f"text {cr_text:.1f}:1, stjärnor {cr_star:.1f}:1")
        # 11c. Formen: hjärtat klipper verkligen himlen (punkter i cirkeln men utanför hjärtat = papper)
        if hpoly is not None:
            probes, badp = 0, 0
            for ang in range(0, 360, 10):
                for rr in (0.97, 0.9):
                    px, py = R * rr * math.cos(math.radians(ang)), R * rr * math.sin(math.radians(ang))
                    if any(pip(px + dx, py + dy, hpoly) for dx in (-6, 0, 6) for dy in (-6, 0, 6)):
                        continue
                    probes += 1
                    v = img[int((h - (cy + py)) * sc), int((cx + px) * sc)]
                    if np.sqrt(((v - page_rgb) ** 2).sum()) > 30:
                        badp += 1
            chk(f"{lang}_hjartform", probes >= 10 and badp == 0, f"{probes} punkter utanför hjärtat, {badp} ej papper")
        # 11d. Månfas-raden i rastret: belyst andel av varje måne ≈ beräknad
        if mrow:
            moon_rgb = np.array([v * 255 for v in st["colors"]["moon"]])
            errs = []
            for md in mrow:
                X, Y, r = md["x_pt"] * sc, (h - md["y_pt"]) * sc, md["r_pt"] * sc * 0.97
                y0_, y1_, x0_, x1_ = int(Y - r), int(Y + r) + 1, int(X - r), int(X + r) + 1
                sub = img[y0_:y1_, x0_:x1_]
                gy, gx = np.mgrid[y0_:y1_, x0_:x1_]
                inside = np.hypot(gx - X, gy - Y) < r
                lit = float((np.sqrt(((sub - moon_rgb) ** 2).sum(-1)) < 60)[inside].mean())
                errs.append(abs(lit - md["frac"]))
            chk(f"{lang}_manfas_rad_raster", max(errs) < 0.08, f"max avvikelse belyst yta {max(errs):.3f} (gräns 0,08)")

    # 12. Determinism/idempotens: generera igen i temp-mapp och jämför hash
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, STJARN_OUT=td)
        order_file = Path(td) / "order.json"; order_file.write_text(json.dumps(o), encoding="utf-8")
        subprocess.run([sys.executable, str(ROOT / "stjarnkarta.py"), str(order_file)], env=env,
                       check=True, capture_output=True)
        same = all(hashlib.sha256((Path(td) / f"{o['id']}_{l}.pdf").read_bytes()).hexdigest() == meta["sha256"][l]
                   for l in meta["files"])
    chk("determinism", same, "identisk PDF vid omkörning" if same else "PDF skiljer sig vid omkörning")

    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "godkand": passed, "antal_grindar": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    out = Path(meta_path).with_name(f"{o['id']}_qc.json")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)},
                     ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
