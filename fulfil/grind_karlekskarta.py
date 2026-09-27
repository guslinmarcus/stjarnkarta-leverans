"""Kvalitetsgrind för kärlekskartan. Oberoende av generatorn:
  * orterna slås upp på nytt ur GeoNames-filen med grindens egen läsare och normalisering,
  * hjärtmarkörerna hittas i PDF:ens ritoperationer (fyllfärgen), och deras mitt räknas tillbaka till
    latitud/longitud med grindens egen invers Mercator – ska ligga ≤ 0,8 mm (på papperet) från orten,
  * linjerna ska gå markör → markör i datumordning (ändpunkterna ur ritoperationerna),
  * avstånden kontrolleras med Vincentys formel på WGS 84-ellipsoiden (annan metod än generatorns haversine),
  * datum i PDF-texten ska stå i kronologisk ordning, källhänvisningar ska finnas, texten ska kunna läsas ut.

Körning: python grind_karlekskarta.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
"""
import gzip
import json
import math
import re
import sys
import time
import unicodedata
from pathlib import Path

import fitz

ROOT = Path(__file__).parent
MARK = (0.831, 0.180, 0.310)
TOL_MM = 0.8
MUST = ["Natural Earth", "GeoNames"]


def _n(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(ch)).strip()


def geonames(queries):
    """Egen uppslagning: alla namnvarianter, störst befolkning vinner inom landet."""
    want = {_n(q[0]): None for q in queries}
    best = {}
    with gzip.open(ROOT / "data" / "orter.tsv.gz", "rt", encoding="utf-8") as f:
        for line in f:
            name, lat, lon, cc, pop, tz, names = line.rstrip("\n").split("\t")
            for v in names.split("|"):
                if v in want:
                    best.setdefault(v, []).append((int(pop or 0), float(lat), float(lon), cc))
    return best


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


def inv_merc(x, y):
    return math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2), math.degrees(x)


def run(meta_path):
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})
    places = sorted(o["places"], key=lambda p: p["date"])
    chk("antal_platser", 2 <= len(places) <= 5, f"{len(places)} platser (tillåtet 2–5)")
    # 1. egen GeoNames-uppslagning
    gn = geonames([(p["place"], p.get("country", "")) for p in places])
    bad = []
    for p in places:
        c = gn.get(_n(p["place"]), [])
        cc = p.get("cc")
        if cc:
            c = [x for x in c if x[3] == cc]
        if not c:
            bad.append(f"{p['place']}: hittas inte"); continue
        pop, la, lo, _ = max(c)
        d = vincenty(la, lo, p["lat"], p["lon"])
        if d > 1.0:
            bad.append(f"{p['place']}: {d:.1f} km från GeoNames")
    chk("orter_mot_geonames", not bad, "alla orter stämmer med egen uppslagning (≤ 1 km)" if not bad else "; ".join(bad))
    for lang, pdf in meta["files"].items():
        doc = fitz.open(pdf)
        page = doc[0]
        geo = meta["geometry"][lang]
        H = page.rect.height
        X0, Y0, X1, Y1 = geo["extent_merc"]
        fx, fy, fw, fh = geo["frame_pt"]
        # 2. markörer ur ritoperationerna
        hearts = []
        for d in page.get_drawings():
            col = d.get("fill")
            if col and all(abs(a - b) < 0.01 for a, b in zip(col, MARK)) and d["rect"].width > 8:
                r = d["rect"]
                hearts.append(((r.x0 + r.x1) / 2, H - (r.y0 + r.y1) / 2, r))
        chk(f"{lang}_antal_markorer", len(hearts) == len(places), f"{len(hearts)} hjärtan i PDF:en, {len(places)} platser")
        # hjärtformens (x = 16 sin³t, y = 13 cos t − 5 cos 2t − 2 cos 3t − cos 4t, förskjuten +2,5) bbox-mitt i förhållande till ankaret
        ys = [13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
              for t in (2 * math.pi * i / 100 for i in range(101))]
        yoff = ((max(ys) + min(ys)) / 2 + 2.5) / 32
        errs = []
        used = set()
        for p in places:
            best = None
            for k, (hx, hy, r) in enumerate(hearts):
                s = r.width  # hjärtat är 32 enheter brett = s
                ax, ay = hx, hy - yoff * s
                lat, lon = inv_merc(X0 + (ax - fx) / fw * (X1 - X0), Y0 + (ay - fy) / fh * (Y1 - Y0))
                x_true = fx + (math.radians(p["lon"]) - X0) / (X1 - X0) * fw
                y_true = fy + (math.log(math.tan(math.pi / 4 + math.radians(max(-80, min(80, p["lat"]))) / 2)) - Y0) / (Y1 - Y0) * fh
                e_mm = math.hypot(ax - x_true, ay - y_true) / 72 * 25.4
                if best is None or e_mm < best[0]:
                    best = (e_mm, k, lat, lon)
            if best:
                used.add(best[1]); errs.append((p["place"], round(best[0], 3)))
        worst = max((e for _, e in errs), default=99)
        chk(f"{lang}_markorer_pa_ratt_koordinat", worst <= TOL_MM and len(used) == len(places),
            f"största avvikelse {worst:.2f} mm på papperet (gräns {TOL_MM} mm): {errs}")
        # 3. linjerna: ändpunkter i datumordning
        lines = [d for d in page.get_drawings() if d.get("dashes") not in (None, "[] 0") and d.get("color") and not d.get("fill")]
        ends = []
        for d in lines:
            it = [x for x in d["items"] if x[0] == "l"]
            if it:
                ends.append(((it[0][1].x, H - it[0][1].y), (it[-1][2].x, H - it[-1][2].y)))
        exp = []
        for a, b in zip(places, places[1:]):
            pa = (fx + (math.radians(a["lon"]) - X0) / (X1 - X0) * fw,
                  fy + (math.log(math.tan(math.pi / 4 + math.radians(a["lat"]) / 2)) - Y0) / (Y1 - Y0) * fh)
            pb = (fx + (math.radians(b["lon"]) - X0) / (X1 - X0) * fw,
                  fy + (math.log(math.tan(math.pi / 4 + math.radians(b["lat"]) / 2)) - Y0) / (Y1 - Y0) * fh)
            exp.append((pa, pb))
        okl = len(ends) == len(exp) and all(math.dist(e[0], x[0]) < 3 and math.dist(e[1], x[1]) < 3 for e, x in zip(ends, exp))
        chk(f"{lang}_linje_i_datumordning", okl, f"{len(ends)} linjer, förväntat {len(exp)} i datumordning" + ("" if okl else " – ändpunkter stämmer inte"))
        # 4. text: avstånd och datum
        text = page.get_text()
        nums = [int(re.sub(r"[^0-9]", "", m)) for m in re.findall(r"(?m)^([0-9][0-9,  ]*) km$", text)]
        truth = [vincenty(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in zip(places, places[1:])]
        okd = len(nums) == len(truth) and all(abs(n - t) <= max(2, 0.005 * t) for n, t in zip(nums, truth))
        chk(f"{lang}_avstand_mot_vincenty", okd, f"tryckt {nums} km, Vincenty {[round(t) for t in truth]} km (tolerans 0,5 %)")
        tot = re.search(r"([\d , ]+) km (together|tillsammans|gemeinsam)", text)
        tv = int(re.sub(r"[^\d]", "", tot.group(1))) if tot else -1
        chk(f"{lang}_totalavstand", tot and abs(tv - sum(truth)) <= max(3, 0.005 * sum(truth)), f"tryckt {tv} km, Vincenty {sum(truth):.0f} km")
        bad_ch = text.count(chr(0)) + text.count(chr(0xFFFD))
        chk(f"{lang}_inga_saknade_tecken", bad_ch == 0, "alla tecken finns i typsnitten" if not bad_ch else f"{bad_ch} tecken saknar glyf")
        missing = [m for m in MUST if m not in text]
        chk(f"{lang}_kallhanvisning", not missing, "Natural Earth och GeoNames finns" if not missing else f"saknas: {missing}")
        years = [int(y) for y in re.findall(r"\b(1[89]\d\d|20\d\d)\b", text)]
        chk(f"{lang}_datum_tryckta", all(str(int(p["date"][:4])) in text for p in places), f"årtal i texten: {years[:12]}")
        chk(f"{lang}_sidformat", abs(page.rect.width - 841.9) < 2 and abs(page.rect.height - 1190.6) < 2, f"{page.rect.width:.0f}×{page.rect.height:.0f} pt (A3)")
    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "product": "karlekskarta", "godkand": passed, "antal_grindar": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)}, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
