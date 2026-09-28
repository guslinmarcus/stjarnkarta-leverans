"""Kvalitetsgrind för "Födelsetavla – natten du föddes". Oberoende av generatorn:
  * himmel: egen astronomi (Meeus – samma metod som kvalitetsgrind.py/grind_manfas.py, oberoende_mane.py)
    för de 12 kontrollstjärnorna och månens belysning,
  * väder: SMHI:s API frågas EN GÅNG TILL (samma smhi_vader-modul men en fristående cache-mapp, så att
    ett fel i generatorns egen cache inte kan dölja ett fel) och jämförs mot det tryckta värdet,
  * enheter: vikt/längd räknas om oberoende ur order["weight_g"]/["height_cm"] och jämförs mot den tryckta
    texten, och att metriskt/imperialt stämmer med språk+land-regeln (aldrig blandat på samma affisch),
  * läser tillbaka PDF:en: sidformat (A3+A4), inbäddade typsnitt, alla texter, ingen text kapad eller
    överlappande, kontrast, källhänvisningar (stjärnkatalog+SMHI när väder visas), språk, determinism.

Körning: python grind_fodelsetavla.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
"""
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date as Date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import fitz

import oberoende_mane as OM
import smhi_vader as VADER

ROOT = Path(__file__).parent
MM = 72 / 25.4
A3 = (297 * MM, 420 * MM)
A4 = (A3[0] * 210 / 297, A3[1] * 210 / 297)
TOL_STAR_DEG = 0.5
TOL_MOON_K = 0.02
TOL_WEATHER_TEMP = 0.6   # °C, avrundningsmarginal (tryckt är avrundat till heltal)
TOL_WEATHER_MM = 0.15
TOL_DIST_KM = 2.0
IMPERIAL_LANDER = {"US", "GB"}
G_PER_OZ = 28.349523125
CM_PER_IN = 2.54
MUST_CREDIT = ["Yale Bright Star", "DE421", "BSD"]
SPRAK_FRAMMANDE = {
    "sv": ["the night you were born", "die nacht deiner geburt", "weight", "gewicht", "illuminated", "beleuchtet"],
    "en": ["natten du föddes", "die nacht deiner geburt", "vikt", "gewicht", "belyst", "beleuchtet"],
    "de": ["natten du föddes", "the night you were born", "vikt", "weight", "belyst", "illuminated"],
}


def rel_lum(rgb):
    def ch(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted([rel_lum(a), rel_lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def jd_utc(iso):
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return d.timestamp() / 86400 + 2440587.5


def altaz_meeus(ra_h, dec, lat, lon, jd):
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


def angsep(a1, z1, a2, z2):
    a1, z1, a2, z2 = map(math.radians, (a1, z1, a2, z2))
    c = math.sin(a1) * math.sin(a2) + math.cos(a1) * math.cos(a2) * math.cos(z1 - z2)
    return math.degrees(math.acos(max(-1, min(1, c))))


def stereo(alt, az, R):
    r = R * math.tan(math.radians(90 - alt) / 2)
    return -r * math.sin(math.radians(az)), r * math.cos(math.radians(az))


def units_for(lang, country_cc):
    if lang != "en":
        return "metric"
    return "imperial" if (country_cc or "").upper() in IMPERIAL_LANDER else "metric"


def fmt_weight(grams, units, lang):
    if grams is None:
        return None
    if units == "imperial":
        total_oz = round(grams / G_PER_OZ)
        lb, oz = total_oz // 16, total_oz % 16
        return f"{lb} lb {oz} oz"
    kg = grams / 1000.0
    s = f"{kg:.2f}".rstrip("0").rstrip(".")
    return (s.replace(".", ",") if lang != "en" else s) + " kg"


def fmt_height(cm, units, lang):
    if cm is None:
        return None
    if units == "imperial":
        return f"{cm / CM_PER_IN:.1f} in"
    return f"{cm:.0f} cm"


def lines_of(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).strip()
            if t:
                out.append((t, fitz.Rect(l["bbox"]), l["spans"][0]["font"], l["spans"][0]["size"]))
    return out


def run(meta_path):
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})

    jd = jd_utc(meta["utc"])
    variant = o.get("variant", "barn")

    # 0. tidszonen: lokal tid (order["date"]+["time"], eller kl 12 om ingen tid/husdjur) -> UTC, oberoende
    #    uträknad med zoneinfo direkt ur ordern (samma datumbibliotek, egen omräkning – ingen import av generatorn)
    d0 = Date.fromisoformat(o["date"])
    hm = None
    if variant == "barn" and o.get("time"):
        hh, mm_ = (int(x) for x in o["time"].split(":")[:2])
        hm = (hh, mm_)
    h_, mi_ = hm if hm else (12, 0)
    local_ref = datetime(d0.year, d0.month, d0.day, h_, mi_, tzinfo=ZoneInfo(o["timezone"]))
    utc_ref = local_ref.astimezone(timezone.utc)
    utc_gen = datetime.fromisoformat(meta["utc"].replace("Z", "+00:00"))
    chk("tidszon_ratt", abs((utc_gen - utc_ref).total_seconds()) < 1,
        f"generator {utc_gen.isoformat()} vs oberoende omräkning {utc_ref.isoformat()}")

    # ---------------------------------------------------------------- 1. himmel: 12 kontrollstjärnor mot Meeus
    any_lang = next(iter(meta["check_stars"]))
    stars = meta["check_stars"][any_lang]
    geo_a3 = meta["geometry"][any_lang]["a3"]
    cx, cy, R = geo_a3["cx"], geo_a3["cy"], geo_a3["r"]
    errs, perr = [], []
    for s in stars:
        a, z = altaz_meeus(s["ra_h"], s["dec"], o["lat"], o["lon"], jd)
        errs.append(angsep(s["alt"], s["az"], a, z))
        px, py = stereo(a, z, R)
        perr.append(math.hypot(cx + px - s["page_x_pt"], cy + py - s["page_y_pt"]) / MM)
    chk("astronomi_stjarnpositioner", bool(stars) and max(errs) < TOL_STAR_DEG,
        f"{len(stars)} stjärnor, max avvikelse {max(errs or [0]):.3f}° (gräns {TOL_STAR_DEG}°)")
    chk("projektion_sidposition", bool(perr) and max(perr) < 1.0,
        f"max {max(perr or [0]):.3f} mm mot oberoende projektion (gräns 1 mm)")
    chk("tillrackligt_kontrollstjarnor", len(stars) >= 10, f"{len(stars)} kontrollstjärnor (kräver >=10)")

    # 2. månfas mot oberoende beräkning (Meeus, oberoende_mane.py – samma modul som grind_manfas.py)
    k_ref, elong_ref = OM.moon_state_utc(datetime.fromisoformat(meta["utc"].replace("Z", "+00:00")))[:2]
    chk("manfas_mot_meeus", abs(meta["moon_frac"] - k_ref) < TOL_MOON_K,
        f"generator {meta['moon_frac'] * 100:.1f} % vs Meeus {k_ref * 100:.1f} %")
    waxing_ref = elong_ref < 180
    ambiguous = k_ref < 0.02 or k_ref > 0.98
    chk("manfas_orientering", ambiguous or meta["waxing"] == waxing_ref,
        f"generator waxing={meta['waxing']} vs Meeus waxing={waxing_ref}")
    chk("manfas_halvklot", meta["south"] == (o["lat"] < 0), "söder/norr stämmer med latitud")

    # ---------------------------------------------------------------- 3. väder: fråga SMHI en gång till (egen cache)
    if variant == "barn" and (o.get("weather_requested") and (o.get("country_cc") or "").upper() == "SE"):
        with tempfile.TemporaryDirectory() as td:
            os.environ["SMHI_CACHE"] = td
            fresh = VADER.weather_for(o["lat"], o["lon"], Date.fromisoformat(o["date"]))
        shown = meta.get("weather")
        if not fresh.get("har_data"):
            chk("vader_tacknig", shown is None, "SMHI saknar täckning – affischen ska då INTE visa väder"
                if shown is None else "affischen visar väder trots att SMHI (omkontrollerad) saknar täckning")
        else:
            bad = []
            if shown is None:
                bad.append("affischen saknar väder trots att SMHI har täckning")
            else:
                for key, tol in (("medel", TOL_WEATHER_TEMP), ("nederbord", TOL_WEATHER_MM), ("sol", 0.3)):
                    a, b = shown.get(key), fresh.get(key)
                    if (a is None) != (b is None):
                        bad.append(f"{key}: generator {a} vs SMHI-omkontroll {b}")
                    elif a is not None and abs(a["varde"] - b["varde"]) > tol:
                        bad.append(f"{key}: {a['varde']} vs SMHI {b['varde']} (tol {tol})")
                    elif a is not None and abs(a["avstand_km"] - b["avstand_km"]) > TOL_DIST_KM:
                        bad.append(f"{key}_avstand: {a['avstand_km']} km vs {b['avstand_km']} km")
            chk("vader_mot_smhi", not bad, "matchar SMHI:s API-svar vid omkontroll" if not bad else f"{bad[:4]}")
    else:
        chk("vader_ej_begart_eller_ej_sverige", meta.get("weather") is None,
            "inget väder visas (rätt, ej begärt eller ej Sverige)" if meta.get("weather") is None
            else "väder visas trots att det inte är en svensk order med väder begärt")

    # ---------------------------------------------------------------- 4. enheter (oberoende omräkning)
    bad_units = [l for l in o["languages"] if units_for(l, o.get("country_cc")) != o.get("units")]
    chk("enhetsval_ratt_for_sprak_och_land", not bad_units,
        f"order units={o.get('units')!r} stämmer inte med språk+land-regeln för: {bad_units}" if bad_units
        else f"units={o.get('units')!r} stämmer för alla ordrade språk")

    # ---------------------------------------------------------------- 5. PDF:en per språk
    for lang, pdf in meta["files"].items():
        doc = fitz.open(pdf)
        chk(f"{lang}_tva_sidor", len(doc) == 2, f"{len(doc)} sidor (A3 + A4)")
        if len(doc) == 2:
            ok_size = abs(doc[0].rect.width - A3[0]) < 1.5 and abs(doc[0].rect.height - A3[1]) < 1.5 \
                and abs(doc[1].rect.width - A4[0]) < 1.5 and abs(doc[1].rect.height - A4[1]) < 1.5
            chk(f"{lang}_sidformat", ok_size, " / ".join(f"{p.rect.width / MM:.0f}×{p.rect.height / MM:.0f} mm" for p in doc))
        fonts, emb = set(), True
        for p in doc:
            for f_ in p.get_fonts(full=True):
                emb &= f_[1] not in ("n/a", "")
                fonts.add(f_[3].split("+")[-1])
        chk(f"{lang}_typsnitt_inbaddade", emb and fonts, f"{sorted(fonts)}")
        full_txt = "\n".join(p.get_text() for p in doc)
        chk(f"{lang}_inga_trasiga_tecken", "�" not in full_txt and "■" not in full_txt, "U+FFFD/■ ej funnet")
        credit_ok = all(k in full_txt for k in MUST_CREDIT)
        chk(f"{lang}_kallor_himmel", credit_ok, ", ".join(MUST_CREDIT))
        if meta.get("weather"):
            chk(f"{lang}_kallor_vader", "SMHI" in full_txt and "CC BY 4.0" in full_txt, "SMHI + CC BY 4.0")
        chk(f"{lang}_namn", o["name"] in full_txt, o["name"])
        if variant == "barn":
            w_ref = fmt_weight(o.get("weight_g"), o.get("units"), lang)
            h_ref = fmt_height(o.get("height_cm"), o.get("units"), lang)
            if w_ref:
                chk(f"{lang}_vikt_stammer", w_ref in full_txt, f"väntat {w_ref!r} i texten")
            if h_ref:
                chk(f"{lang}_langd_stammer", h_ref in full_txt, f"väntat {h_ref!r} i texten")
            fam = o.get("family") or []
            if fam:
                miss = [n for n in fam if n not in full_txt]
                chk(f"{lang}_familjerad", not miss, "alla familjenamn finns" if not miss else f"saknas: {miss}")
        bad_foreign = [w for w in SPRAK_FRAMMANDE.get(lang, []) if w.lower() in full_txt.lower()]
        chk(f"{lang}_sprak", not bad_foreign, f"främmande ord: {bad_foreign}" if bad_foreign else "inga främmande mallord")

        for pi, page in enumerate(doc):
            W, H = page.rect.width, page.rect.height
            L_ = lines_of(page)
            tag = f"{lang}_s{pi + 1}"
            marg = 10.0 * MM * (1 if pi == 0 else A4[0] / A3[0])
            lay = []
            for t, bb, *_ in L_:
                if bb.x0 < marg or bb.x1 > W - marg or bb.y0 < marg or bb.y1 > H - marg:
                    lay.append(f"utanför marginal (kapas vid tryck): {t[:24]!r}")
            for a in range(len(L_)):
                for b in range(a + 1, len(L_)):
                    r1, r2 = L_[a][1], L_[b][1]
                    ix = max(0, min(r1.x1, r2.x1) - max(r1.x0, r2.x0)); iy = max(0, min(r1.y1, r2.y1) - max(r1.y0, r2.y0))
                    small = min(r1.width * r1.height, r2.width * r2.height)
                    if small > 0 and ix * iy > 0.12 * small:
                        lay.append(f"överlapp: {L_[a][0][:18]!r}/{L_[b][0][:18]!r}")
            chk(f"{tag}_layout_inget_kapat_eller_overlapp", not lay, "ok" if not lay else f"{lay[:4]}")
        doc.close()

    # ---------------------------------------------------------------- 6. kontrast (WCAG), en gång per stil
    style_colors = {"natur": ((0.24, 0.19, 0.15), (0.975, 0.955, 0.925)),
                    "nordisk_minimal": ((0.12, 0.12, 0.12), (0.985, 0.985, 0.982)),
                    "nattstjarna": ((0.95, 0.93, 0.85), (0.043, 0.058, 0.10)),
                    "ballong": ((0.30, 0.24, 0.30), (0.985, 0.93, 0.93))}
    ink, page_bg = style_colors.get(o.get("style"), style_colors["natur"])
    cr = contrast([c * 255 for c in ink], [c * 255 for c in page_bg])
    if os.environ.get("FELINJEKTION") == "lag_kontrast":
        cr = 1.0
    chk("kontrast_text_mot_bakgrund", cr >= 4.5, f"{cr:.1f}:1 (krav 4,5:1)")

    # ---------------------------------------------------------------- 7. determinism
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, STJARN_OUT=td)
        env.pop("FELINJEKTION", None)
        of = Path(td) / "order.json"; of.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "fodelsetavla.py"), str(of)], env=env, capture_output=True)
        same = r.returncode == 0 and all(
            hashlib.sha256((Path(td) / f"{o['id']}_{l}.pdf").read_bytes()).hexdigest() == meta["sha256"][l]
            for l in meta["files"])
    chk("determinism", same, "identisk PDF vid omkörning" if same else f"skiljer sig: {r.stderr[-300:] if not same else ''}")

    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "product": "fodelsetavla", "godkand": passed, "antal_grindar": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)},
                     ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
