"""Kvalitetsgrind för månfas-affischen. Oberoende av generatorn (Skyfield/DE421):
  * belysning och elongation: Meeus kap. 47 (månen) + kap. 25 (solen) + kap. 48 (fasvinkel), oberoende_mane.py,
  * huvudfasernas tidpunkter (fasnamnet på dagen): Meeus kap. 49,
  * ΔT: Espenak & Meeus 2006, lokal tid -> UTC med zoneinfo (samma tidszonsdatabas, egen omräkning).
Krav: belysning ≤ 1 procentenhet mot Meeus (beräknat värde och tryckt %), rätt fasnamn, rätt sida belyst för orten.
Läser tillbaka PDF:en: sidformat (A3 + A4), inbäddade typsnitt, alla texter (namn, datum, ort, fas, %), språk, källor,
layout (marginaler, inga överlapp, ingen text över en måne), kontrast, och månarnas form mäts i rastret
(belyst andel inverteras ur pixlarna, belyst sida, pixelavvikelse mot idealformen). Determinism: omkörning ger samma PDF.

Körning: python grind_manfas.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
"""
import calendar, hashlib, json, math, os, re, subprocess, sys, tempfile, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import fitz
import numpy as np

import oberoende_mane as om

ROOT = Path(__file__).parent
MM = 72 / 25.4
A3 = (297 * MM, 420 * MM)
TOL_K = 0.01          # beräknad belysning mot Meeus (andel)
TOL_PRINT_PP = 1.0    # tryckt heltals-% mot Meeus (procentenheter)
TOL_RASTER_MAIN = 0.015
TOL_RASTER_ROW = 0.05
EDGE_S = 180          # huvudfas inom 3 min från midnatt: båda dagarnas namn godtas
MUST_CREDIT = ["Skyfield", "DE421", "Meeus", "OFL"]
BASE14 = {"Helvetica", "Times-Roman", "Courier", "Symbol", "ZapfDingbats", "Helvetica-Bold", "Times-Bold"}


def rel_lum(rgb):
    def ch(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((rel_lum(a), rel_lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def idx_from(principal, elong):
    if principal is not None:
        return 2 * principal
    return 1 if elong < 90 else 3 if elong < 180 else 5 if elong < 270 else 7


def facit(day, hm, tz):
    """Grindens egna värden för en måne: UTC, k, elongation, godtagna fasindex."""
    h, mi = hm if hm else (12, 0)
    local = datetime(day.year, day.month, day.day, h, mi, tzinfo=tz)
    utc = local.astimezone(timezone.utc)
    k, el, _ = om.moon_state_utc(utc)
    m0 = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)
    m1 = (datetime(day.year, day.month, day.day, tzinfo=tz) + timedelta(days=1)).astimezone(timezone.utc)
    acc = set()
    for a, b in ((m0 - timedelta(seconds=EDGE_S), m1 + timedelta(seconds=EDGE_S)),
                 (m0 + timedelta(seconds=EDGE_S), m1 - timedelta(seconds=EDGE_S))):
        ph = om.principal_phases_between(a, b)
        acc.add(idx_from(ph[0][0] if ph else None, el))
    return {"utc": utc, "k": k, "elong": el, "accepted": acc}


def model_lit(u, v, k, right):
    xt = (1 - 2 * k) * np.sqrt(np.clip(1 - v * v, 0, 1))
    return (u > xt) if right else (-u > xt)


def measure(page, scale, g, lit_rgb, dark_rgb, k_ref, right_ref):
    """Mäter en måne i rastret. g = geometri i A3-pt (reportlab, origo nere till vänster)."""
    ph = page.rect.height
    cx, cy, r = g["cx"] * scale, ph - g["cy"] * scale, g["r"] * scale
    dpi = int(min(600, max(72, 260 * 72 / r)))
    clip = fitz.Rect(cx - r * 1.02, cy - r * 1.02, cx + r * 1.02, cy + r * 1.02)
    pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=fitz.csRGB, alpha=False)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).astype(np.float32) / 255
    z = dpi / 72
    ys, xs = np.mgrid[0:pix.height, 0:pix.width]
    x_pt = clip.x0 + (xs + 0.5) / z
    y_pt = clip.y0 + (ys + 0.5) / z
    u, v = (x_pt - cx) / r, -(y_pt - cy) / r
    mask = u * u + v * v <= 0.95 ** 2
    dl = ((a - np.array(lit_rgb, np.float32)) ** 2).sum(-1)
    dd = ((a - np.array(dark_rgb, np.float32)) ** 2).sum(-1)
    lit = dl < dd
    meas = lit[mask].mean()
    right = bool(u[mask & lit].mean() > 0) if (mask & lit).any() else right_ref
    uu, vv = u[mask], v[mask]

    def inv(target, upper):
        """Största/minsta k vars idealform ger andelen target (modellen mättas nära 0 och 1 inom masken)."""
        lo, hi = 0.0, 1.0
        for _ in range(22):
            mid = (lo + hi) / 2
            f = model_lit(uu, vv, mid, right).mean()
            if (f <= target) if upper else (f < target):
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    k_lo, k_hi = inv(meas - 0.002, False), inv(meas + 0.002, True)
    k_meas = min(max(k_ref, k_lo), k_hi)  # närmaste k som är förenligt med pixlarna
    mism = float((lit[mask] != model_lit(u[mask], v[mask], k_ref, right_ref)).mean())
    return k_meas, right, mism


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
    o, data, sty = meta["order"], meta["data"], meta["style"]
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})

    import manfas as MF  # bara textmallarna (L, datumformat), ingen beräkning
    # ---------------------------------------------------------------- facit
    fac, row_fac = [], []
    worst_k, bad_k, bad_ph, bad_or, bad_t = 0.0, [], [], [], []
    for i, (om_, m) in enumerate(zip(o["moons"], data["moons"])):
        tz = ZoneInfo(om_["timezone"])
        d = date.fromisoformat(om_["date"])
        hm = tuple(int(x) for x in om_["time"].split(":")[:2]) if om_.get("time") else None
        f = facit(d, hm, tz); fac.append(f)
        if abs((datetime.fromisoformat(m["utc"]) - f["utc"]).total_seconds()) > 1 or m["date"] != om_["date"]:
            bad_t.append(f"{i}: generator {m['utc']} grind {f['utc'].isoformat()}")
        e = abs(m["frac"] - f["k"]); worst_k = max(worst_k, e)
        if e > TOL_K:
            bad_k.append(f"{i}: {m['frac'] * 100:.2f} % mot Meeus {f['k'] * 100:.2f} %")
        if m["phase"] not in f["accepted"]:
            bad_ph.append(f"{i}: fas {m['phase']} mot Meeus {sorted(f['accepted'])}")
        amb = f["k"] < 0.01 or f["k"] > 0.99
        if m["south"] != (om_["lat"] < 0) or (not amb and m["waxing"] != (f["elong"] < 180)):
            bad_or.append(f"{i}: tilltagande {m['waxing']}/söder {m['south']}, grind {f['elong'] < 180}/{om_['lat'] < 0}")
        # dag-för-dag-raden
        rf = []
        if o["mode"] == "single" and o.get("row", "none") != "none":
            want = ([date(d.year, d.month, k) for k in range(1, calendar.monthrange(d.year, d.month)[1] + 1)] if o["row"] == "month"
                    else [d + timedelta(days=k) for k in range(-3, 4)])
            got = [date.fromisoformat(x["date"]) for x in m.get("row", [])]
            if got != want:
                bad_t.append(f"rad: fel dagar {got[:3]}…")
            for x, md in zip(want, m.get("row", [])):
                g = facit(x, hm, tz); rf.append(g)
                e = abs(md["frac"] - g["k"]); worst_k = max(worst_k, e)
                if e > TOL_K:
                    bad_k.append(f"rad {x}: {md['frac'] * 100:.2f} % mot {g['k'] * 100:.2f} %")
                if not (g["k"] < 0.01 or g["k"] > 0.99) and md["waxing"] != (g["elong"] < 180):
                    bad_or.append(f"rad {x}: tilltagande fel")
        row_fac.append(rf)
    n_moons = len(fac) + sum(len(r) for r in row_fac)
    chk("tid_och_tidszon", not bad_t, "lokal tid -> UTC stämmer för alla månar" if not bad_t else f"{bad_t[:3]}")
    chk("belysning_mot_meeus", not bad_k, f"{n_moons} månar, största avvikelse {worst_k * 100:.3f} procentenheter (gräns {TOL_K * 100:.0f})"
        if not bad_k else f"{bad_k[:3]}")
    chk("fasnamn_mot_meeus", not bad_ph, f"{len(fac)} fasnamn enligt Meeus kap. 49 + elongation" if not bad_ph else f"{bad_ph[:3]}")
    chk("orientering", not bad_or, "belyst sida och halvklot stämmer" if not bad_or else f"{bad_or[:3]}")
    # kontrast (stilens färger)
    cr = {k: contrast(sty[k], sty["page"]) for k in ("text", "accent", "mute")}
    chk("kontrast", cr["text"] >= 4.5 and cr["mute"] >= 4.5 and cr["accent"] >= 3.0,
        ", ".join(f"{k} {v:.1f}:1" for k, v in cr.items()) + " (krav 4,5 / 4,5 / 3)")

    user = [o.get("text") or ""] + [x for mo in o["moons"] for x in (mo["name"], mo["place"], mo.get("country") or "")]
    # ---------------------------------------------------------------- PDF:en
    for lang, pdf in meta["files"].items():
        lx = MF.L[lang]
        doc = fitz.open(pdf)
        geo = meta["geometry"][lang]
        chk(f"{lang}_sidor", len(doc) == 2, f"{len(doc)} sidor (A3 + A4)")
        sz_ok = len(doc) == 2 and abs(doc[0].rect.width - A3[0]) < 1.5 and abs(doc[0].rect.height - A3[1]) < 1.5 \
            and abs(doc[1].rect.width - 210 * MM) < 1.5 and abs(doc[1].rect.height - 297 * MM) < 1.5
        chk(f"{lang}_sidformat", sz_ok, " / ".join(f"{p.rect.width / MM:.0f}×{p.rect.height / MM:.0f} mm" for p in doc))
        fonts, emb = set(), True
        for p in doc:
            for f_ in p.get_fonts(full=True):
                base = f_[3].split("+")[-1]
                fonts.add(base); emb &= f_[1] not in ("n/a", "") and base not in BASE14
        chk(f"{lang}_typsnitt_inbaddade", emb and len(fonts) >= 1, f"{sorted(fonts)}")
        alltxt = "\n".join(p.get_text() for p in doc)
        flat = re.sub(r"\s+", " ", alltxt)
        chk(f"{lang}_inga_trasiga_tecken", "�" not in alltxt and "■" not in alltxt, "U+FFFD/■ ej funnet")
        chk(f"{lang}_kallor", all(k in flat for k in MUST_CREDIT), ", ".join(MUST_CREDIT))
        own = flat
        for u in user:
            if u:
                own = own.replace(u, " ").replace(u.upper(), " ")
        bad = [w for w in MF.FOREIGN[lang] if w in own]
        chk(f"{lang}_sprak", not bad, f"främmande ord: {bad}" if bad else "inga främmande mallord")
        illum_re = re.escape(lx["illum"]).replace(re.escape("{p}"), r"(\d{1,3})")
        phase_re = re.compile(r"^(.+) · " + illum_re + r"$")
        for pi, page in enumerate(doc):
            sc = geo["pages"][pi]["scale"]
            W, H = page.rect.width, page.rect.height
            L_ = lines_of(page)
            texts = [t for t, *_ in L_]
            tag = f"{lang}_s{pi + 1}"
            # rubrik
            req = []
            if o["mode"] == "family":
                req.append(lx["heading"]["family"])
                if o.get("text"):
                    req.append(o["text"])
            elif o.get("heading", "born") != "none":
                req.append(lx["heading"][o.get("heading", "born")])
            miss = [r for r in req if r not in texts]
            # per måne: namn, datum, ort, fas-rad i månens kolumn under skivan
            bad_txt, bad_pr, worst_p = [], [], 0.0
            for g in geo["disks"]:
                i = g["i"]; mo = o["moons"][i]; f = fac[i]
                cx, top = g["cx"] * sc, H - (g["cy"] - g["r"]) * sc
                col = [(t, bb) for t, bb, *_ in L_ if abs((bb.x0 + bb.x1) / 2 - cx) < g["bw"] * sc / 2 and top - 2 < bb.y0 < top + 95 * MM * sc]
                ct = [t for t, _ in col]
                nm = mo["name"].upper() if sty.get("upper") else mo["name"]
                hm = tuple(int(x) for x in mo["time"].split(":")[:2]) if mo.get("time") else None
                for want in (nm, MF.fmt_date(lang, date.fromisoformat(mo["date"]), hm), MF.place_line(mo)):
                    if want not in ct:
                        bad_txt.append(f"{i}: saknar {want!r}")
                ph = [phase_re.match(t) for t in ct]
                ph = [x for x in ph if x]
                if len(ph) != 1:
                    bad_pr.append(f"{i}: {len(ph)} fasrader"); continue
                name, p = ph[0].group(1), int(ph[0].group(2))
                okname = name in {lx["phases"][a] for a in f["accepted"]}
                e = abs(p - f["k"] * 100); worst_p = max(worst_p, e)
                if not okname or e > TOL_PRINT_PP:
                    bad_pr.append(f"{i}: tryckt {name!r} {p} %, Meeus {[lx['phases'][a] for a in f['accepted']]} {f['k'] * 100:.2f} %")
            chk(f"{tag}_texter", not miss and not bad_txt, "rubrik, namn, datum och ort finns" if not (miss or bad_txt) else f"{(miss + bad_txt)[:3]}")
            chk(f"{tag}_tryckt_fas_och_procent", not bad_pr,
                f"{len(geo['disks'])} fasrader, största avvikelse tryckt % mot Meeus {worst_p:.2f} procentenheter (gräns 1)"
                if not bad_pr else f"{bad_pr[:3]}")
            # raden: dagnummer/datum under varje liten måne
            if geo["row"]:
                rbad = []
                if lx["row"] not in texts:
                    rbad.append("rubrik saknas")
                m0 = data["moons"][0]
                for g in geo["row"]:
                    dd = date.fromisoformat(m0["row"][g["j"]]["date"])
                    want = str(dd.day) if o["row"] == "month" else MF.fmt_short(lang, dd)
                    cx, cyb = g["cx"] * sc, H - (g["cy"] - g["r"]) * sc
                    if not any(t == want and abs((bb.x0 + bb.x1) / 2 - cx) < 2 * MM * sc and 0 < bb.y0 - cyb < 12 * MM * sc for t, bb, *_ in L_):
                        rbad.append(want)
                chk(f"{tag}_rad_datum", not rbad, f"{len(geo['row'])} dagar märkta" if not rbad else f"saknas: {rbad[:4]}")
            # layout: marginaler, överlapp, text över måne
            lay = []
            marg = 14 * MM * sc
            circles = [(g["cx"] * sc, H - g["cy"] * sc, g["r"] * sc) for g in geo["disks"] + geo["row"]]
            for t, bb, *_ in L_:
                if bb.x0 < marg or bb.x1 > W - marg or bb.y0 < marg or bb.y1 > H - marg:
                    lay.append(f"utanför marginal: {t[:20]!r}")
                for ccx, ccy, rr in circles:
                    nx, ny = min(max(ccx, bb.x0), bb.x1), min(max(ccy, bb.y0), bb.y1)
                    if (nx - ccx) ** 2 + (ny - ccy) ** 2 < (rr * 0.995) ** 2:
                        lay.append(f"över måne: {t[:20]!r}"); break
            for a in range(len(L_)):
                for b in range(a + 1, len(L_)):
                    r1, r2 = L_[a][1], L_[b][1]
                    ix = max(0, min(r1.x1, r2.x1) - max(r1.x0, r2.x0)); iy = max(0, min(r1.y1, r2.y1) - max(r1.y0, r2.y0))
                    small = min(r1.width * r1.height, r2.width * r2.height)
                    if small > 0 and ix * iy > 0.15 * small:
                        lay.append(f"överlapp: {L_[a][0][:15]!r}/{L_[b][0][:15]!r}")
            chk(f"{tag}_layout", not lay, "marginaler, inga överlapp, ingen text över en måne" if not lay else f"{lay[:3]}")
            # raster
            rb, worst_r, worst_mis = [], 0.0, 0.0
            for g in geo["disks"]:
                f = fac[g["i"]]; mo = o["moons"][g["i"]]
                right_ref = (f["elong"] < 180) != (mo["lat"] < 0)
                km, right, mis = measure(page, sc, g, sty["lit"], sty["dark"], f["k"], right_ref)
                e = abs(km - f["k"]); worst_r = max(worst_r, e); worst_mis = max(worst_mis, mis)
                if e > TOL_RASTER_MAIN or (0.03 < f["k"] < 0.97 and (right != right_ref or mis > 0.03)):
                    rb.append(f"{g['i']}: ritad {km * 100:.1f} % ({'höger' if right else 'vänster'}), Meeus {f['k'] * 100:.1f} % "
                              f"({'höger' if right_ref else 'vänster'}), pixelavvikelse {mis * 100:.1f} %")
            for g in geo["row"]:
                f = row_fac[0][g["j"]]
                right_ref = (f["elong"] < 180) != (o["moons"][0]["lat"] < 0)
                km, right, mis = measure(page, sc, g, sty["lit"], sty["dark"], f["k"], right_ref)
                e = abs(km - f["k"]); worst_r = max(worst_r, e)
                if e > TOL_RASTER_ROW or (0.08 < f["k"] < 0.92 and right != right_ref):
                    rb.append(f"rad {g['j']}: ritad {km * 100:.1f} % mot {f['k'] * 100:.1f} %")
            chk(f"{tag}_raster_manens_form", not rb,
                f"{len(geo['disks']) + len(geo['row'])} månar mätta, största skillnad {worst_r * 100:.2f} procentenheter "
                f"(gräns {TOL_RASTER_MAIN * 100:.1f} stor / {TOL_RASTER_ROW * 100:.0f} liten), pixelavvikelse max {worst_mis * 100:.2f} %"
                if not rb else f"{rb[:3]}")
        doc.close()

    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, STJARN_OUT=td)
        of = Path(td) / "order.json"; of.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "manfas.py"), str(of)], env=env, capture_output=True)
        same = r.returncode == 0 and all(
            hashlib.sha256((Path(td) / f"{o['id']}_{l}.pdf").read_bytes()).hexdigest() == meta["sha256"][l] for l in meta["files"])
    chk("determinism", same, "identisk PDF vid omkörning" if same else "PDF skiljer sig vid omkörning")

    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "product": "manfas", "godkand": passed, "antal_grindar": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)}, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
