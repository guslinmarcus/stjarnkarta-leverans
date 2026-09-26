"""Kvalitetsgrind för stjärnkartan. Oberoende av generatorn: egen astronomi (Meeus),
läser den färdiga PDF:en (text, typsnitt, mått) och rastrerar den i 300 dpi.

Körning: python kvalitetsgrind.py ut/<id>_meta.json  -> ut/<id>_qc.json, exitkod 0 = GODKÄND
"""
import hashlib, json, math, os, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np

A3 = (841.89, 1190.55)  # pt
ROOT = Path(__file__).parent


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

    for lang, pdf in meta["files"].items():
        doc = fitz.open(pdf); page = doc[0]
        # 5. Format
        w, h = page.rect.width, page.rect.height
        chk(f"{lang}_sidformat_A3", abs(w - A3[0]) < 1.5 and abs(h - A3[1]) < 1.5, f"{w:.1f}×{h:.1f} pt")
        chk(f"{lang}_en_sida", len(doc) == 1, f"{len(doc)} sidor")
        # 6. Typsnitt inbäddade
        fonts = page.get_fonts(full=True)
        emb = all(f[1] not in ("n/a", "") for f in fonts)  # ext n/a = ej inbäddat
        chk(f"{lang}_typsnitt_inbaddade", emb and len(fonts) >= 2, f"{[f[3] for f in fonts]}")
        # 7. Texten finns exakt och utan trasiga tecken
        txt = page.get_text()
        t = meta["texts"][lang]
        need = [o["name"], t["title"], t["date"], t["coord"]]
        missing = [s for s in need if s not in txt]
        chk(f"{lang}_text_komplett", not missing, f"saknas: {missing}" if missing else "namn, titel, datum, koordinater")
        chk(f"{lang}_inga_trasiga_tecken", "�" not in txt and "■" not in txt, "U+FFFD/■ ej funnet")
        # 8. Licens/attribution på produkten
        credit_ok = all(k in txt for k in ["Yale Bright Star", "Olaf Frohn", "BSD", "DE421"])
        chk(f"{lang}_attribution", credit_ok, "Yale BSC, DE421, d3-celestial/BSD")
        # 9. Layout: all text inom 10 mm marginal, ingen text inuti himmelsskivan
        cx, cy, R = meta["geometry_pt"]; cy_top = h - cy
        bad = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                x0, y0, x1, y1 = l["bbox"]
                s = "".join(sp["text"] for sp in l["spans"])
                if x0 < 28.3 or x1 > w - 28.3 or y0 < 28.3 or y1 > h - 28.3:
                    bad.append(("marginal", s[:30]))
                # hörnen på textraden får inte ligga inne i cirkeln
                for (px, py) in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]:
                    if math.hypot(px - cx, py - cy_top) < R - 1:
                        bad.append(("i_cirkeln", s[:30])); break
        chk(f"{lang}_layout", not bad, f"{bad[:3]}" if bad else "ok")
        # 10. Språkkontroll: månadsnamn och inga ord från andra språks mallar
        foreign = {"sv": ["Sternenhimmel", "night sky"], "en": ["Stjärnhimlen", "Sternenhimmel"],
                   "de": ["Stjärnhimlen", "night sky"]}[lang]
        chk(f"{lang}_sprak", not any(f in txt for f in foreign), "inga främmande mallord")
        # 11. Raster 300 dpi: stjärnorna syns där beräkningen säger
        pix = page.get_pixmap(dpi=300, colorspace=fitz.csGRAY)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        sc = 300 / 72; hits = 0
        for s in meta["check_stars"]:
            X = int(s["page_x_pt"] * sc); Y = int((h - s["page_y_pt"]) * sc)
            if img[max(0, Y - 3):Y + 4, max(0, X - 3):X + 4].max() > 180:
                hits += 1
        chk(f"{lang}_raster_stjarnor_pa_plats", hits == len(meta["check_stars"]),
            f"{hits}/{len(meta['check_stars'])} ljusa stjärnor funna i 300 dpi-bilden")
        chk(f"{lang}_raster_storlek", (pix.width, pix.height) == (3508, 4961), f"{pix.width}×{pix.height} px")
        bright = (img > 128).mean()
        chk(f"{lang}_ej_tom", 0.001 < bright < 0.2, f"andel ljusa pixlar {bright:.4f}")

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
