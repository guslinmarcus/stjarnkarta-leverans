"""Generator: Klubbkalender 2027 – väggkalender/PDF för en idrottsförenings lag (FORENINGAR.md §2.3 nr 1).

Innehåll: 13 sidor (omslag + 12 månadssidor). Varje månadssida: kalenderrutnät måndag–söndag med svenska
helgdagar (röd dag, Lag 1989:253) och namnsdagar (dagar_sverige.py), månfasernas fyra huvudfaser (Skyfield +
JPL DE421 – samma metod som himmelskalender.py), klubbens egna händelser (matcher/cuper/andra datum),
klubbens namn/lag/färger i en rubrikrand, och en sponsorrad i sidfoten. Ingen bild av spelare i MVP:t
(FORENINGAR.md §3.5: bara text som ledaren själv skriver in – inga foton eller efternamn på barn).

Varje dagsruta har en liten (5 pt) fullständig datumstämpel (ÅÅÅÅ-MM-DD) under dagsnumret. Det är avsiktligt
en läsbar produktionsdetalj (många planerare gör likadant), och det gör att kvalitetsgrinden kan läsa tillbaka
exakt vilket textblock som hör till vilket kalenderdatum utan att gissa på pixelpositioner.

Order (JSON):
  id, product="foreningskalender", language="sv" (MVP: bara svenska – helgdagar/namnsdagar är svenska),
  style: klassisk|mork|lekfull|minimal, format: A4|A3,
  club: {name, team?, sport?, venue?, colors:[hex, hex?]},
  events: [{date:"YYYY-MM-DD", title, type: match|cup|training|event, home?: bool}],
  sponsors: [namn, ...]?, footer_text?: str

Körning:  python foreningskalender.py order.json -> <ut>/<id>.pdf + <ut>/<id>_meta.json
Felinjektion (bara tester): FELINJEKTION=<namn>, se FEL-användningen nedan (9 st, kravet är minst 8).
"""
import calendar as CAL
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from skyfield import almanac

import formorkelse as F  # Sky-laddning (DE421), wrap/para/fit_font, typsnittsregistrering (Serif/SerifIt/Sans)
import dagar_sverige as DS

GENERATOR_VERSION = "foreningskalender/0.1.0"
ROOT = Path(__file__).parent
YEAR = DS.AR  # 2027
TZ = ZoneInfo("Europe/Stockholm")
FEL = os.environ.get("FELINJEKTION", "")
MONTHNAMN = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september",
             "oktober", "november", "december"]
VECKODAGAR = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]
MANFAS_NAMN = ["Nymåne", "Första kvarteret", "Fullmåne", "Sista kvarteret"]
# Korta koder i kalenderrutan (inte "Nymå"/"Förs" etc – de kolliderar med vanliga ord som "Första maj" och
# "Nyårsdagen". Versaler + unika bokstavskombinationer som aldrig förekommer som svenskt ord i övrigt,
# så att grinden kan slå exakt på ordet utan att träffa fel).
MANFAS_KOD = {0: "NY", 1: "FQ", 2: "FULL", 3: "LQ"}
EVENT_FARG = {"match": (0.85, 0.25, 0.20), "cup": (0.80, 0.55, 0.10), "training": (0.30, 0.45, 0.75),
              "event": (0.45, 0.35, 0.65)}

A4 = (297 * mm, 210 * mm)  # LIGGANDE – matchar Gelatos enda 13-bladiga väggkalenderprodukt (se tryck/foreningskalender_offert.py)
A3 = (420 * mm, 297 * mm)  # liggande av samma skäl (samma layout återanvänds, även om Gelato saknar en 13-bladig A3-produkt – se offerten)


def hex_to_rgb01(h):
    h = (h or "").lstrip("#")
    if len(h) != 6:
        return (0.10, 0.35, 0.20)  # standardgrönt om klubben inte angett en giltig färg
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# ------------------------------------------------------------------ stilar (4 st, KARNAN §6 / NISCHRAMVERK §7)
STYLES = {
    "klassisk": {"label": "Klassisk vit", "page": (1, 1, 1), "ink": (0.10, 0.10, 0.10), "mute": (0.45, 0.45, 0.45),
                 "grid": (0.80, 0.80, 0.80), "rod_tint": (1.0, 0.90, 0.88), "weekend_tint": (0.96, 0.96, 0.96),
                 "fonts": ("Serif", "Sans"), "day_size": 15, "corner": 0, "cell_pad": 2.2 * mm},
    "mork": {"label": "Midnight & guld", "page": (0.06, 0.07, 0.10), "ink": (0.95, 0.93, 0.86), "mute": (0.62, 0.60, 0.55),
             "grid": (0.28, 0.28, 0.33), "rod_tint": (0.30, 0.14, 0.14), "weekend_tint": (0.11, 0.12, 0.16),
             "fonts": ("Serif", "Sans"), "day_size": 15, "corner": 0, "cell_pad": 2.2 * mm},
    "lekfull": {"label": "Lekfull rund", "page": (1.0, 0.99, 0.94), "ink": (0.14, 0.12, 0.10), "mute": (0.50, 0.46, 0.40),
                "grid": (0.88, 0.84, 0.70), "rod_tint": (1.0, 0.85, 0.55), "weekend_tint": (0.97, 0.94, 0.82),
                "fonts": ("Round", "Round"), "day_size": 16, "corner": 2.4 * mm, "cell_pad": 2.6 * mm},
    "minimal": {"label": "Minimal linje", "page": (1, 1, 1), "ink": (0.05, 0.05, 0.05), "mute": (0.55, 0.55, 0.55),
                "grid": (0.90, 0.90, 0.90), "rod_tint": (0.99, 0.94, 0.93), "weekend_tint": (1, 1, 1),
                "fonts": ("Sans", "Sans"), "day_size": 13, "corner": 0, "cell_pad": 2.0 * mm, "bar_h": 3.0 * mm},
}
for _n, _f in {"Round": "VarelaRound-Regular.ttf"}.items():
    if _n not in pdfmetrics.getRegisteredFontNames():
        from reportlab.pdfbase.ttfonts import TTFont
        pdfmetrics.registerFont(TTFont(_n, str(ROOT / "fonts" / _f)))


# ------------------------------------------------------------------ beräkning
def manfas_dagar(sky):
    """{iso_datum: fasindex 0-3} för dagens som (lokal tid, Europe/Stockholm) innehåller en huvudfas."""
    t0 = sky.ts.from_datetime(datetime(YEAR, 1, 1, tzinfo=TZ) - timedelta(days=2))
    t1 = sky.ts.from_datetime(datetime(YEAR + 1, 1, 1, tzinfo=TZ) + timedelta(days=2))
    t, ph = almanac.find_discrete(t0, t1, almanac.moon_phases(sky.eph))
    out = {}
    for ti, p in zip(t, ph):
        loc = ti.utc_datetime().astimezone(TZ)
        if loc.year != YEAR:
            continue
        iso = loc.date().isoformat()
        if FEL == "manfas_fel":
            iso = (loc.date() + timedelta(days=2)).isoformat()
        out[iso] = int(p)
    return out


def bygg_manadsdagar(month):
    """[(iso, veckodagsindex 0=mån) ...] för alla dagar i månaden, i ordning. Egen datumiteration (avsiktligt
    OBEROENDE av python-modulen calendar, som grinden i stället använder för korskontroll)."""
    d = date(YEAR, month, 1)
    out = []
    while d.month == month:
        out.append((d.isoformat(), d.weekday()))
        d += timedelta(days=1)
    if FEL == "dagantal_fel":
        out = out[:-1]  # sista dagen i månaden saknas
    if FEL == "veckodag_fel":
        out = [(iso, (wd + 1) % 7) for iso, wd in out]  # hela rutnätet en kolumn fel
    return out


def handelser_for_manad(events, month):
    per_dag = {}
    for e in events:
        try:
            d = date.fromisoformat(e["date"])
        except (KeyError, ValueError):
            continue
        if FEL == "handelse_fel" and e.get("type") == "match":
            continue  # matcher tappas bort
        iso = e["date"]
        if FEL == "handelse_datum_fel":
            iso = (d + timedelta(days=1)).isoformat()
            d = d + timedelta(days=1)
        if d.year == YEAR and d.month == month:
            per_dag.setdefault(iso, []).append(e)
    return per_dag


# ------------------------------------------------------------------ ritning
TOP_H_MARGIN = 16 * mm   # avstånd från sidans topp ner till rubrikbandets underkant (liggande format, kompakt)
TITLE_H = 16 * mm        # plats för månadsnamnet OCH veckodagsraden, med tydligt mellanrum mellan dem
BOTTOM_H = 15 * mm       # plats för sponsorraden under rutnätet, med marginal så den inte nuddar sista dagsraden


def rubrikrad(c, W, top_h, club, st, page_w_margin):
    bar_h = st.get("bar_h", 8 * mm)
    prim = hex_to_rgb01(FARG_STANDARD if FEL == "farg_fel" else (club.get("colors") or [None])[0])
    c.setFillColorRGB(*prim)
    c.rect(0, top_h, W, bar_h, stroke=0, fill=1)
    c.setFillColorRGB(*st["ink"])
    namn = club.get("name", "Klubben")
    if club.get("team"):
        namn += " " + club["team"]
    c.setFont(st["fonts"][0], 13)
    c.drawString(page_w_margin, top_h + bar_h + 2.6 * mm, namn)


FARG_STANDARD = "#808080"  # neutral gråton – ska aldrig råka matcha en klubbs egen färg (felinjektion farg_fel)


def rita_manad(c, month, order, sky, manfas, W, H, st):
    club = order.get("club", {})
    margin = 10 * mm
    top_h = H - TOP_H_MARGIN
    rubrikrad(c, W, top_h, club, st, margin)
    c.setFillColorRGB(*st["mute"])
    c.setFont(st["fonts"][1], 9)
    c.drawRightString(W - margin, top_h + st.get("bar_h", 8 * mm) + 2.6 * mm, f"{YEAR}")
    c.setFillColorRGB(*st["ink"])
    c.setFont(st["fonts"][0], 16)
    c.drawString(margin, top_h - 6.5 * mm, MONTHNAMN[month - 1].capitalize())

    grid_top = top_h - TITLE_H
    grid_bottom = BOTTOM_H  # lämnar plats för sponsorrad
    grid_h = grid_top - grid_bottom
    col_w = (W - 2 * margin) / 7

    c.setFont(st["fonts"][1], 8)
    for i, vd in enumerate(VECKODAGAR):
        c.setFillColorRGB(*st["mute"])
        c.drawCentredString(margin + i * col_w + col_w / 2, grid_top + 2 * mm, vd.upper())

    dagar = bygg_manadsdagar(month)
    handelser = handelser_for_manad(order.get("events") or [], month)
    forsta_wd = dagar[0][1] if dagar else 0
    rad0 = 0
    col0 = forsta_wd
    # Radhöjden anpassas efter hur många veckorader MÅNADEN faktiskt behöver (4, 5 eller 6) i stället för
    # ett fast antal – ger mer plats per dag i liggande format, som har mindre höjd att röra sig med än stående.
    antal_rader = -(-(col0 + len(dagar)) // 7) if dagar else 6  # heltalsuppåtrundning
    row_h = grid_h / max(antal_rader, 1)
    for idx, (iso, wd) in enumerate(dagar):
        # position: fortsätt löpande från (rad0, col0)
        pos = col0 + idx
        rad, kol = divmod(pos, 7)
        x = margin + kol * col_w
        y_top = grid_top - rad * row_h
        y_bot = y_top - row_h
        rod = DS.rod_dag(iso) if FEL != "helgdag_fel" else False
        helg = DS.helgdag(iso) if FEL != "helgdag_fel" else None
        is_weekend = wd >= 5
        bg = st["rod_tint"] if rod else (st["weekend_tint"] if is_weekend else st["page"])
        if bg != st["page"]:
            c.setFillColorRGB(*bg)
            c.rect(x, y_bot, col_w, row_h, stroke=0, fill=1)
        c.setStrokeColorRGB(*st["grid"])
        c.setLineWidth(0.4)
        c.rect(x, y_bot, col_w, row_h, stroke=1, fill=0)
        pad = st["cell_pad"]
        moon_slot = 4.2 * mm  # reserverad bredd i övre högra hörnet, så text aldrig läggs där
        text_width = col_w - 2 * pad - moon_slot
        day_baseline = y_top - pad - st["day_size"] * 0.8
        c.setFillColorRGB(*st["ink"])
        c.setFont(st["fonts"][0], st["day_size"])
        c.drawString(x + pad, day_baseline, str(int(iso[-2:])))
        # liten fullständig datumstämpel (grinden läser detta) – 5 pt, dämpad färg
        c.setFont("Sans", 5)
        c.setFillColorRGB(*st["mute"])
        c.drawString(x + pad, y_bot + 1.6 * mm, iso)
        # namnsdag – egen rad UNDER dagnumret (aldrig i samma rad som texten om händelser)
        cursor = day_baseline - 3.2 * mm
        ndag_iso = iso
        if FEL == "namnsdag_fel":
            d_ = date.fromisoformat(iso) + timedelta(days=1)
            ndag_iso = d_.isoformat() if d_.year == YEAR else iso
        namn = DS.namnsdag(ndag_iso)
        if namn:
            kandidat = ", ".join(namn[:2])
            if len(F.wrap(kandidat, st["fonts"][1], 6, text_width)) > 1:
                kandidat = namn[0]  # två namn får inte plats – visa bara det första hellre än att kapa
            c.setFont(st["fonts"][1], 6)
            c.setFillColorRGB(*st["mute"])
            c.drawString(x + pad, cursor, kandidat)
            cursor -= 3.0 * mm
        if helg:
            hsize = F.fit_font(helg, st["fonts"][1], 6.5, text_width, minsize=4.5)
            c.setFont(st["fonts"][1], hsize)
            c.setFillColorRGB(*(0.55, 0.12, 0.10) if bg != st["page"] else st["mute"])
            for ln in [r for r in F.wrap(helg, st["fonts"][1], hsize, text_width) if r][:2]:
                c.drawString(x + pad, cursor, ln)
                cursor -= 2.6 * mm
        # månfas – eget hörn uppe till höger, kolliderar aldrig med text (reserverad moon_slot)
        fas = manfas.get(iso)
        if fas is not None:
            cx, cy, r = x + col_w - pad - moon_slot / 2, y_top - pad - moon_slot / 2, 1.7 * mm
            c.setStrokeColorRGB(*st["ink"])
            c.setFillColorRGB(*st["ink"])
            c.circle(cx, cy, r, stroke=1, fill=(fas == 2))
            c.setFont(st["fonts"][1], 4.5)
            c.setFillColorRGB(*st["mute"])
            c.drawCentredString(cx, cy - r - 2.4 * mm, MANFAS_KOD[fas])
        # händelser – staplas UNDER namnsdag/helgdag, aldrig i dagnumrets rad. Hela titeln ska synas (radbryts
        # i stället för att kapas – en avkapad klubbtext ("Hemma vs Any B...") är sämre än en extra rad).
        evs = handelser.get(iso, [])
        min_y = y_bot + 3.2 * mm  # lämna plats åt datumstämpeln längst ner
        esize = 5.6
        for e in evs[:2]:
            if cursor - 2.6 * mm < min_y:
                break
            farg = EVENT_FARG.get(e.get("type", "event"), EVENT_FARG["event"])
            c.setFillColorRGB(*farg)
            c.rect(x + pad, cursor - 2.0 * mm, 1.8 * mm, 1.8 * mm, stroke=0, fill=1)
            titel_w = text_width - 2.6 * mm
            size = F.fit_font(e.get("title", ""), st["fonts"][1], esize, titel_w, minsize=4.2)
            rader = F.wrap(e.get("title", ""), st["fonts"][1], size, titel_w)
            max_rader = max(1, int((cursor - min_y) // (2.6 * mm)))
            c.setFont(st["fonts"][1], size)
            c.setFillColorRGB(*st["ink"])
            for i, ln in enumerate(rader[:min(2, max_rader)]):
                c.drawString(x + pad + 2.6 * mm, cursor - 1.8 * mm - i * 2.6 * mm, ln)
            cursor -= 2.0 * mm + max(1, min(2, len(rader))) * 2.6 * mm

    # sponsorrad – under rutnätet (grid_bottom), med egen marginal så den aldrig nuddar sista dagsraden
    sponsorer = [] if FEL == "sponsor_fel" else (order.get("sponsors") or [])
    if sponsorer:
        c.setFont(st["fonts"][1], 7.5)
        c.setFillColorRGB(*st["mute"])
        c.drawCentredString(W / 2, 6 * mm, "Sponsras av: " + " · ".join(sponsorer))


def rita_omslag(c, order, W, H, st):
    club = order.get("club", {})
    c.setFillColorRGB(*st["page"])
    c.rect(0, 0, W, H, stroke=0, fill=1)
    prim = hex_to_rgb01(FARG_STANDARD if FEL == "farg_fel" else (club.get("colors") or [None])[0])
    band_h = 44 * mm  # proportionerligt mindre band i liggande format (mindre sidhöjd att dela på)
    c.setFillColorRGB(*prim)
    c.rect(0, H - band_h, W, band_h, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(st["fonts"][0], 26)
    namn = club.get("name", "Klubben")
    if club.get("team"):
        namn += " " + club["team"]
    c.drawCentredString(W / 2, H - 21 * mm, namn)
    c.setFont(st["fonts"][1], 12)
    c.drawCentredString(W / 2, H - 30 * mm, club.get("sport", ""))
    c.setFillColorRGB(*st["ink"])
    c.setFont(st["fonts"][0], 46)
    c.drawCentredString(W / 2, H / 2 + 4 * mm, str(YEAR))
    c.setFont(st["fonts"][1], 10)
    c.setFillColorRGB(*st["mute"])
    c.drawCentredString(W / 2, H / 2 - 8 * mm, "Klubbkalender – helgdagar, namnsdagar, månfaser och lagets egna datum")
    if club.get("venue"):
        c.drawCentredString(W / 2, H / 2 - 13 * mm, club["venue"])
    sponsorer = [] if FEL == "sponsor_fel" else (order.get("sponsors") or [])
    if sponsorer:
        c.setFont(st["fonts"][1], 9)
        c.drawCentredString(W / 2, 15 * mm, "Sponsras av: " + " · ".join(sponsorer))
    c.setFont("Sans", 7)
    c.setFillColorRGB(*st["mute"])
    c.drawCentredString(W / 2, 10 * mm, "Skapad automatiskt av Moodly · gladloft.com/klubb")


def generate(order_path):
    order = json.loads(Path(order_path).read_text(encoding="utf-8"))
    if order.get("language", "sv") != "sv":
        raise ValueError("foreningskalender stödjer bara language=sv (svenska helgdagar/namnsdagar) i MVP:t")
    style = order.get("style", "klassisk")
    if style not in STYLES:
        raise ValueError(f"okänd stil {style!r}, tillåtna: {sorted(STYLES)}")
    st = STYLES[style]
    fmt = order.get("format", "A4")
    W, H = A3 if fmt == "A3" else A4
    out_dir = Path(os.environ.get("STJARN_OUT", ROOT / "ut"))  # fulfil.py sätter STJARN_OUT till en temp-katalog per order
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{order['id']}.pdf"

    sky = F.Sky()
    manfas = manfas_dagar(sky)

    c = canvas.Canvas(str(out_path), pagesize=(W, H))
    rita_omslag(c, order, W, H, st)
    c.showPage()
    for m in range(1, 13):
        c.setFillColorRGB(*st["page"])
        c.rect(0, 0, W, H, stroke=0, fill=1)
        rita_manad(c, m, order, sky, manfas, W, H, st)
        c.showPage()
    c.save()

    meta = {
        "id": order["id"], "product": "foreningskalender", "generator_version": GENERATOR_VERSION,
        "style": style, "format": fmt, "year": YEAR, "club": order.get("club", {}),
        "events": order.get("events", []), "sponsors": order.get("sponsors", []),
        "felinjektion": FEL, "dagar_kalla_meta": DS.META,
        "pdf_sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
        "genererad": datetime.now(TZ).isoformat(),
        "sidor": 13,
    }
    (out_dir / f"{order['id']}_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    p = generate(sys.argv[1])
    print("Skrivet:", p)
