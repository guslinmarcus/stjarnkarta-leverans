"""Kvalitetsgrind för klubbkalendern (BESLUTSSYSTEM §2 fråga 2 + klass C-kravet "felinjektionstest om kod").

Läser tillbaka PDF:en och prövar den mot KÄLLOR SOM ÄR OBEROENDE AV GENERATORNS EGEN KOD:
  * dagantal och veckodagsposition: klustras fram ur de egna, renderade datumstämplarna (ingen tillit till
    generatorns interna layoutkonstanter) och jämförs mot Pythons inbyggda `calendar`-modul (annan
    implementation än foreningskalender.py:s egen datumloop).
  * helgdagar/namnsdagar: dagar_sverige.py (självt verifierat mot Lag 1989:253 + Meeus/Jones/Butcher-
    påskformeln, se dagar_sverige.verifiera_oberoende()).
  * månfaser: oberoende.moon_phases_year() – Meeus algoritm 49, helt skild kodväg från generatorns
    Skyfield/DE421-beräkning.
  * klubbens händelser, sponsorer och färg: mot ordern/metan (skickades in av kunden, inte av generatorn).

Körning: python grind_foreningskalender.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
"""
import calendar as CAL
import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).parent))
import dagar_sverige as DS
import oberoende as OB

ISO_RE = re.compile(r"\b(2027-\d{2}-\d{2})\b")
YEAR = DS.AR
VECKODAGAR = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]
MANFAS_NAMN = ["Nymåne", "Första kvarteret", "Fullmåne", "Sista kvarteret"]


def cluster(vals, tol=3.0):
    """Klustrar en lista av tal (x0 eller y0) till gruppcentra. Enkel greedy-klustring, oberoende av
    genereatorns exakta layoutkonstanter (marginal, kolumnbredd)."""
    vals = sorted(vals)
    groups = []
    for v in vals:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def cell_index(v, centers):
    return min(range(len(centers)), key=lambda i: abs(centers[i] - v))


def analysera_manadssida(page, month):
    """Returnerar {iso: {"rad","kol","text"}} genom att klustra fram rutnätet ur sidans egna ord –
    ingen kunskap om generatorns marginal/kolumnbredd används."""
    words = page.get_text("words")  # (x0,y0,x1,y1,text,block,line,word_no)
    iso_words = [w for w in words if ISO_RE.fullmatch(w[4])]
    if not iso_words:
        return {}, []
    xs = cluster([w[0] for w in iso_words], tol=5.0)
    ys = cluster([w[1] for w in iso_words], tol=5.0)
    xs.sort(); ys.sort()
    # radhöjd/kolumnbredd = medianavstånd mellan klustercentra (för generösa cellgränser)
    row_gap = (ys[1] - ys[0]) if len(ys) > 1 else 200.0
    col_gap = (xs[1] - xs[0]) if len(xs) > 1 else 200.0
    celler = {}
    for w in iso_words:
        iso = w[4]
        kol = cell_index(w[0], xs)
        rad = cell_index(w[1], ys)
        y_top = ys[rad] - row_gap + 4
        y_bot = w[3] + 1
        x_left = xs[kol] - 2
        x_right = xs[kol] + col_gap - 2
        alla = [ww[4] for ww in words if x_left <= ww[0] <= x_right and y_top <= ww[1] <= y_bot]
        celler[iso] = {"rad": rad, "kol": kol, "text": " ".join(alla), "ord": alla, "bbox": (x_left, y_top, x_right, y_bot)}
    return celler, xs


def farg_vid(page, x0, y0, x1, y1):
    """Medelfärg (0..1 RGB) i ett litet rektangelutsnitt av sidan, via rasterisering (oberoende av vilka
    RGB-tal generatorns kod råkade skriva – mäter det som faktiskt trycks/visas)."""
    zoom = 3
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(x0, y0, x1, y1))
    n = pix.n
    data = pix.samples
    if not data:
        return None
    tot = [0, 0, 0]
    cnt = 0
    step = max(1, (len(data) // n) // 400)  # sampla max ~400 pixlar
    for i in range(0, len(data) - n + 1, n * step):
        tot[0] += data[i]; tot[1] += data[i + 1]; tot[2] += data[i + 2]
        cnt += 1
    if not cnt:
        return None
    return tuple((t / cnt) / 255.0 for t in tot)


def hex_to_rgb01(h):
    h = (h or "").lstrip("#")
    if len(h) != 6:
        return (0.10, 0.35, 0.20)
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def farg_dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def run(meta_path):
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    pdf_path = Path(meta_path).with_name(f"{meta['id']}.pdf")
    fel, varn = [], []
    ok_delar = {}

    if not pdf_path.exists():
        fel.append(f"pdf saknas: {pdf_path}")
        return _skriv(meta_path, meta, False, fel, varn, ok_delar)

    import hashlib
    sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if sha != meta.get("pdf_sha256"):
        fel.append("pdf_sha256 stämmer inte med filen på disk (metan hör inte längre ihop med pdf:en)")

    doc = fitz.open(str(pdf_path))
    if doc.page_count != 13:
        fel.append(f"fel antal sidor: {doc.page_count}, väntat 13 (1 omslag + 12 månader)")

    style = meta.get("style")
    if style not in ("klassisk", "mork", "lekfull", "minimal"):
        fel.append(f"okänd stil i metan: {style!r}")

    # -------- oberoende källa 1: dagar_sverige (i sig verifierad mot Lag 1989:253) --------
    try:
        DS.verifiera_oberoende()
        ok_delar["helgdagar_kalla_oberoende_verifierad"] = True
    except AssertionError as e:
        fel.append(f"dagar_sverige-datafilen underkänd av sin egen oberoende kontroll: {e}")

    alla_handelser = defaultdict(list)
    for e in meta.get("events", []):
        try:
            date.fromisoformat(e["date"])
            alla_handelser[e["date"]].append(e)
        except (KeyError, ValueError):
            pass

    # -------- oberoende källa 2: python calendar-modulen (helt skild kod från generatorns datumloop) --------
    manad_fel = 0
    namnsdag_fel = 0
    helgdag_fel = 0
    handelse_fel = 0
    hittade_dagar_per_manad = {}
    forsta_manadssida = None
    for m in range(1, 13):
        page = doc[m]  # sida 0 = omslag
        celler, xs = analysera_manadssida(page, m)
        antal_ratt = CAL.monthrange(YEAR, m)[1]
        funna_isos = set(celler.keys())
        forvantade = {date(YEAR, m, d).isoformat() for d in range(1, antal_ratt + 1)}
        if funna_isos != forvantade:
            saknas = forvantade - funna_isos
            extra = funna_isos - forvantade
            manad_fel += 1
            if saknas:
                fel.append(f"månad {m}: {len(saknas)} dag(ar) saknas i renderingen, t.ex. {sorted(saknas)[:3]}")
            if extra:
                fel.append(f"månad {m}: {len(extra)} oväntade datum, t.ex. {sorted(extra)[:3]}")
        hittade_dagar_per_manad[m] = celler

        # veckodagsposition: jämför klustrad kolumn mot calendar.weekday() (måndag=0)
        if len(xs) == 7:
            for iso, c in celler.items():
                d = date.fromisoformat(iso)
                ratt_kol = d.weekday()
                if c["kol"] != ratt_kol:
                    manad_fel += 1
                    fel.append(f"{iso}: ligger i kolumn {c['kol']} ({VECKODAGAR[c['kol']]}) men ska vara "
                               f"kolumn {ratt_kol} ({VECKODAGAR[ratt_kol]}) enligt calendar.weekday()")
                    break  # en rapport räcker per månad, annars svämmar felen över
        elif style is not None:
            varn.append(f"månad {m}: hittade {len(xs)} kolumner (väntat 7) – kan bero på annat fel ovan")

        # helgdagar/namnsdagar mot dagar_sverige (självt primärkälleverifierat)
        for iso, c in celler.items():
            namn = DS.namnsdag(iso)
            if namn and not any(n in c["text"] for n in namn):
                namnsdag_fel += 1
            helg = DS.helgdag(iso)
            if helg and helg not in c["text"]:
                helgdag_fel += 1
            if DS.rod_dag(iso):
                bg = farg_vid(page, *c["bbox"])
                # en röd dag ska INTE ha exakt samma bakgrund som en vanlig vardag mitt i veckan
                vanlig_iso = None
                for cand in celler:
                    dd = date.fromisoformat(cand)
                    if dd.weekday() < 5 and not DS.rod_dag(cand):
                        vanlig_iso = cand; break
                if vanlig_iso and bg and farg_dist(bg, farg_vid(page, *celler[vanlig_iso]["bbox"])) < 0.01:
                    helgdag_fel += 1

        # klubbens händelser
        for iso, evs in alla_handelser.items():
            d = date.fromisoformat(iso)
            if d.month != m:
                continue
            c = celler.get(iso)
            for e in evs:
                titel = e.get("title", "")
                hittad = c and titel[:15] in c["text"]
                if not hittad:
                    # sök på grannceller +-1 dag (fångar datumförskjutningsfel tydligt i felmeddelandet)
                    for delta in (-1, 1):
                        granne = (d + timedelta(days=delta)).isoformat()
                        gc = celler.get(granne)
                        if gc and titel[:15] in gc["text"]:
                            handelse_fel += 1
                            fel.append(f"händelse {titel!r} ({iso}) hittades i stället på {granne} – datumfel")
                            break
                    else:
                        handelse_fel += 1
                        fel.append(f"händelse {titel!r} saknas på {iso} (och närliggande dagar)")

    if namnsdag_fel:
        fel.append(f"{namnsdag_fel} namnsdag(ar) matchar inte dagar_sverige.py")
    if helgdag_fel:
        fel.append(f"{helgdag_fel} helgdag/röd-dag-avvikelse(r) mot dagar_sverige.py")
    ok_delar["dagantal_och_veckodag"] = manad_fel == 0
    ok_delar["namnsdagar"] = namnsdag_fel == 0
    ok_delar["helgdagar"] = helgdag_fel == 0
    ok_delar["handelser"] = handelse_fel == 0

    # -------- oberoende källa 3: Meeus-algoritmen (oberoende.py) för månfaser --------
    egna_faser = OB.moon_phases_year(YEAR)
    forvantade_dagar = set()
    for fas, dt in egna_faser:
        if dt.year not in (YEAR - 1, YEAR, YEAR + 1):
            continue
        for skift in (-1, 0, 1):  # tidszon/avrundning kan flytta lokalt datum ±1 dygn
            forvantade_dagar.add((dt.date() + timedelta(days=skift)).isoformat())
    MANFAS_KODER = {"NY", "FQ", "FULL", "LQ"}  # exakta ord-tokens (versaler) – samma koder som generatorn
    manfas_fel = 0
    for m in range(1, 13):
        celler = hittade_dagar_per_manad[m]
        for iso, c in celler.items():
            has_icon = bool(MANFAS_KODER & set(c["ord"]))
            if has_icon and iso not in forvantade_dagar:
                manfas_fel += 1
                fel.append(f"{iso}: månfasmarkering finns men ingen huvudfas enligt Meeus-beräkningen nära det datumet")
    if manfas_fel:
        fel.append(f"{manfas_fel} månfasmarkering(ar) stämmer inte mot oberoende Meeus-beräkning (±1 dygn)")
    ok_delar["manfaser"] = manfas_fel == 0

    # -------- sponsorer --------
    sponsorer = meta.get("sponsors") or []
    if sponsorer:
        hela_texten = "\n".join(doc[i].get_text() for i in range(doc.page_count))
        saknade = [s for s in sponsorer if s not in hela_texten]
        if saknade:
            fel.append(f"sponsor(er) saknas i pdf:en: {saknade}")
    ok_delar["sponsorer"] = sponsorer == [] or not saknade if sponsorer else True

    # -------- klubbfärg --------
    club = meta.get("club", {})
    farger = club.get("colors") or []
    if farger:
        forvantad = hex_to_rgb01(farger[0])
        # rubrikbanderollen på en månadssida ligger alltid 18-26 mm från sidans topp (samma i A4 och A3 –
        # se foreningskalender.rubrikrad), och innehåller bara färgfyllning där (texten ligger ovanför/under).
        jan = doc[1]
        r = jan.rect
        mm = 2.834645669
        # Smalaste stilens rubrikrand är bara 3 mm hög (minimal) – sampla ett fönster som ligger innanför
        # ALLA stilars rand. Randen ligger 16 mm från sidans topp (liggande format, foreningskalender.TOP_H_MARGIN),
        # så 13.3-15.7 mm från toppen träffar bandet oavsett om det är 3 eller 8 mm högt.
        band = farg_vid(jan, r.width * 0.08, 13.3 * mm, r.width * 0.55, 15.7 * mm)
        if band is None or farg_dist(band, forvantad) > 0.12:
            fel.append(f"klubbens färg {farger[0]} syns inte tydligt i månadssidans rubrikrand "
                       f"(uppmätt {tuple(round(x,2) for x in (band or (0,0,0)))}, väntat {tuple(round(x,2) for x in forvantad)})")
    ok_delar["klubbfarg"] = not farger or "syns inte tydligt" not in " ".join(fel)

    return _skriv(meta_path, meta, len(fel) == 0, fel, varn, ok_delar)


def _skriv(meta_path, meta, godkand, fel, varn, ok_delar):
    res = {"id": meta.get("id"), "godkand": godkand, "fel": fel, "varningar": varn, "delar": ok_delar,
           "felinjektion_i_metan": meta.get("felinjektion", "")}
    out = Path(meta_path).with_name(f"{meta.get('id','okand')}_qc.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    r = run(sys.argv[1])
    print(json.dumps(r, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
