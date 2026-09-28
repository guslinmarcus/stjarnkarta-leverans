# -*- coding: utf-8 -*-
"""Kvalitetsgrind för flygresekartan. Oberoende av generatorn:
  * flygplatserna slås upp på nytt ur flygdata (egen instans, samma datafil men egen kod),
  * avstånden kontrolleras med Vincentys formel på WGS 84-ellipsoiden (generatorn använder haversine),
  * storcirkelbågen räknas om oberoende (egen slerp-implementation) i tre kontrollpunkter (25/50/75 %) och
    jämförs mot den faktiska ritade linjen i PDF:en (via den inversa projektionen, samma frame/extent som
    generatorn skrev i meta - se dokstycket i grind_karlekskarta.py för samma resonemang),
  * flygplatsmarkörerna (cirklar, skiljs från flygplansikonerna via ritoperationstyp - kurvor vs räta linjer)
    ska ligga på rätt koordinat,
  * ingen text får överlappa annan text, all text ska ligga innanför säker yta,
  * källhänvisning till OpenFlights (ODbL-krav) ska finnas, inga tecken får sakna glyf i typsnittet.

Körning: python grind_flygresekarta.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
"""
import json
import math
import re
import sys
import time
from pathlib import Path

import fitz

import flygdata as FD

SIDOR_MM = {"A4": (210.0, 297.0), "A3": (297.0, 420.0), "50x70": (500.0, 700.0)}
MARGIN_SAFE_MM = 6.0
MUST = ["OpenFlights"]


def vincenty(lat1, lon1, lat2, lon2):
    a, f = 6378137.0, 1 / 298.257223563
    b = (1 - f) * a
    L = math.radians(lon2 - lon1)
    U1, U2 = math.atan((1 - f) * math.tan(math.radians(lat1))), math.atan((1 - f) * math.tan(math.radians(lat2)))
    sU1, cU1, sU2, cU2 = math.sin(U1), math.cos(U1), math.sin(U2), math.cos(U2)
    lam = L
    for _ in range(200):
        sl, cl = math.sin(lam), math.cos(lam)
        ss = math.hypot(cU2 * sl, cU1 * sU2 - sU1 * cU2 * cl)
        if ss == 0:
            return 0.0
        cs = sU1 * sU2 + cU1 * cU2 * cl
        sig = math.atan2(ss, cs)
        sa = cU1 * cU2 * sl / ss
        c2a = 1 - sa * sa
        c2sm = cs - 2 * sU1 * sU2 / c2a if c2a else 0
        C = f / 16 * c2a * (4 + f * (4 - 3 * c2a))
        lp = lam
        lam = L + (1 - C) * f * sa * (sig + C * ss * (c2sm + C * cs * (-1 + 2 * c2sm * c2sm)))
        if abs(lam - lp) < 1e-12:
            break
    u2 = c2a * (a * a - b * b) / (b * b)
    A = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    B = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    ds = B * ss * (c2sm + B / 4 * (cs * (-1 + 2 * c2sm * c2sm) - B / 6 * c2sm * (-3 + 4 * ss * ss) * (-3 + 4 * c2sm * c2sm)))
    return b * A * (sig - ds) / 1000


def slerp_point(lat1, lon1, lat2, lon2, f):
    """Egen, från grunden skriven slerp (samma matematik som generatorns, men egen kod - kontrollerar
    att generatorns implementation faktiskt gör vad den påstår, inte bara att den är intern-konsekvent)."""
    phi1, lam1 = math.radians(lat1), math.radians(lon1)
    phi2, lam2 = math.radians(lat2), math.radians(lon2)
    x1, y1, z1 = math.cos(phi1) * math.cos(lam1), math.cos(phi1) * math.sin(lam1), math.sin(phi1)
    x2, y2, z2 = math.cos(phi2) * math.cos(lam2), math.cos(phi2) * math.sin(lam2), math.sin(phi2)
    d = math.acos(max(-1.0, min(1.0, x1 * x2 + y1 * y2 + z1 * z2)))
    if d < 1e-12:
        return lat1, lon1
    A_ = math.sin((1 - f) * d) / math.sin(d)
    B_ = math.sin(f * d) / math.sin(d)
    x, y, z = A_ * x1 + B_ * x2, A_ * y1 + B_ * y2, A_ * z1 + B_ * z2
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def inv_merc(x, y):
    return math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2), math.degrees(x)


def run(meta_path):
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})

    flyglist = o["flights"]
    chk("antal_flygningar", 1 <= len(flyglist) <= 10, f"{len(flyglist)} flygningar (tillåtet 1-10)")
    chk("format_kant", o.get("format", "A3") in SIDOR_MM, f"format {o.get('format')}")

    # 1. egen flygplatsuppslagning
    bad = []
    for leg in flyglist:
        for sida in ("from", "to"):
            ap = leg[sida]
            kod = ap.get("iata") or ap.get("icao") or ap.get("stad")
            egen = FD.hitta(kod, ap.get("land", ""))
            if not egen:
                bad.append(f"{kod}: hittas inte i egen uppslagning"); continue
            d = vincenty(egen.lat, egen.lon, ap["lat"], ap["lon"])
            if d > 2.0:
                bad.append(f"{kod}: {d:.1f} km från egen uppslagning")
    chk("flygplatser_mot_egen_uppslagning", not bad, "alla flygplatser stämmer (≤ 2 km)" if not bad else "; ".join(bad[:10]))

    for lang, pdf in meta["files"].items():
        doc = fitz.open(pdf)
        page = doc[0]
        geo = meta["geometry"][lang]
        H = page.rect.height
        X0, Y0, X1, Y1 = geo["extent_merc"]
        fx, fy, fw, fh = geo["frame_pt"]
        W_mm, H_mm = SIDOR_MM.get(o.get("format", "A3"), SIDOR_MM["A3"])
        chk(f"{lang}_sidformat", abs(page.rect.width - W_mm * 72 / 25.4) < 2 and abs(page.rect.height - H_mm * 72 / 25.4) < 2,
            f"{page.rect.width:.0f}×{page.rect.height:.0f} pt (förväntat {W_mm}×{H_mm} mm)")

        def tp(lon, lat):
            x = math.radians(lon); y = math.log(math.tan(math.pi / 4 + math.radians(max(-80, min(80, lat))) / 2))
            return fx + (x - X0) / (X1 - X0) * fw, fy + (y - Y0) / (Y1 - Y0) * fh

        # 2. markörer: fyllda cirklar (kurvor i ritoperationerna) i routefärgen, skiljs från flygplansikonerna (bara räta linjer)
        drawings = page.get_drawings()
        markers = []
        for d in drawings:
            col = d.get("fill")
            if not col or d["rect"].width < 3:
                continue
            has_curve = any(it[0] == "c" for it in d["items"])
            if has_curve:
                r = d["rect"]
                markers.append(((r.x0 + r.x1) / 2, H - (r.y0 + r.y1) / 2))
        uniq_airports = geo.get("airports", [])
        chk(f"{lang}_antal_markorer", len(markers) == len(uniq_airports),
            f"{len(markers)} markörer i PDF:en, {len(uniq_airports)} unika flygplatser")
        errs = []
        for ap in uniq_airports:
            x_true, y_true = tp(ap["lon"], ap["lat"])
            best = min((math.hypot(mx - x_true, my - y_true) for mx, my in markers), default=9e9)
            errs.append((ap.get("iata") or ap.get("icao"), round(best / 72 * 25.4, 3)))
        worst = max((e for _, e in errs), default=99)
        chk(f"{lang}_markorer_pa_ratt_koordinat", worst <= 1.0, f"största avvikelse {worst:.2f} mm (gräns 1,0 mm): {errs}")

        # 3. storcirkelbågen: egen slerp i tre kontrollpunkter per flygning, jämfört mot ritad linje
        lines = [d for d in drawings if d.get("color") and not d.get("fill") and
                 any(it[0] == "l" for it in d["items"])]
        line_pts_px = []
        for d in lines:
            for it in d["items"]:
                if it[0] == "l":
                    line_pts_px.append((it[1].x, H - it[1].y)); line_pts_px.append((it[2].x, H - it[2].y))
        arc_bad = []
        for leg in flyglist:
            a, b = leg["from"], leg["to"]
            for f in (0.25, 0.5, 0.75):
                lat_e, lon_e = slerp_point(a["lat"], a["lon"], b["lat"], b["lon"], f)
                xe, ye = tp(lon_e, lat_e)
                nearest = min((math.hypot(xe - px, ye - py) for px, py in line_pts_px), default=9e9)
                if nearest / 72 * 25.4 > 2.5:
                    arc_bad.append(f"{a.get('iata')}-{b.get('iata')}@{f}: {nearest / 72 * 25.4:.2f} mm från ritad linje")
        chk(f"{lang}_storcirkelbage_korrekt", not arc_bad, "alla kontrollpunkter ligger på den ritade bågen (≤ 2,5 mm)"
            if not arc_bad else "; ".join(arc_bad[:10]))

        # 4. text: inga överlapp, allt innanför säker yta, avstånd/källa/tecken
        spans = []
        for blk in page.get_text("dict")["blocks"]:
            for line in blk.get("lines", []):
                for sp in line.get("spans", []):
                    spans.append(sp["bbox"])
        overlap = 0
        for i in range(len(spans)):
            for j in range(i + 1, len(spans)):
                a_, b_ = spans[i], spans[j]
                if a_[2] > b_[0] and b_[2] > a_[0] and a_[3] > b_[1] and b_[3] > a_[1]:
                    overlap += 1
        chk(f"{lang}_ingen_textoverlapp", overlap == 0, "ingen textöverlapp" if overlap == 0 else f"{overlap} överlappande textpar")
        margin_pt = MARGIN_SAFE_MM * 72 / 25.4
        outside = [s for s in spans if s[0] < margin_pt - 0.5 or s[2] > page.rect.width - margin_pt + 0.5
                   or s[1] < margin_pt - 0.5 or s[3] > page.rect.height - margin_pt + 0.5]
        chk(f"{lang}_text_inom_marginal", not outside, "all text innanför säker yta" if not outside else f"{len(outside)} textrader utanför")
        text = page.get_text()
        nums = [int(re.sub(r"[^0-9]", "", m)) for m in re.findall(r"(?m)^([0-9][0-9,  ]*) km$", text)]
        truth = [vincenty(leg["from"]["lat"], leg["from"]["lon"], leg["to"]["lat"], leg["to"]["lon"]) for leg in flyglist]
        okd = len(nums) == len(truth) and all(abs(n - t) <= max(2, 0.005 * t) for n, t in zip(nums, truth))
        chk(f"{lang}_avstand_mot_vincenty", okd, f"tryckt {nums} km, Vincenty {[round(t) for t in truth]} km (tolerans 0,5 %)")
        bad_ch = text.count(chr(0)) + text.count(chr(0xFFFD))
        chk(f"{lang}_inga_saknade_tecken", bad_ch == 0, "alla tecken finns i typsnitten" if not bad_ch else f"{bad_ch} tecken saknar glyf")
        missing = [m for m in MUST if m not in text]
        chk(f"{lang}_kallhanvisning", not missing, "OpenFlights-källan finns" if not missing else f"saknas: {missing}")
    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "product": "flygresekarta", "godkand": passed, "antal_grindar": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)}, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
