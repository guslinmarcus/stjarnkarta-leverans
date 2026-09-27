"""Generator: "The Moon on the day you were born" – månfas-affisch som PDF (vektor).

Sida 1 = A3 stående, sida 2 = samma affisch i A4 (ISO-proportion, skalad). En fil per språk.
Innehåll: månens exakta fas och belysta andel på datumet/tiden/orten (utan tid: kl. 12 lokal tid), den belysta
sidan åt rätt håll för orten (norra halvklotet: tilltagande = höger sida ljus; södra: spegelvänt, och månens
ansikte vänt upp och ned), namn, datum, fasnamn och %. Valfritt: en rad med månens fas varje dag i månaden
(row=month) eller veckan runt dagen (row=week). Familjeläge (mode=family): 2–6 personers födelsemånar.
Beräkning: Skyfield + JPL DE421 (almanac.fraction_illuminated / moon_phase / moon_phases).
Stilar: mork (mörk/guld), ljus (ljus minimal), akvarell (procedurell akvarell), barnrum (pastell).
Språk: en, sv, de, fr, es. Typsnitt: Noto, Great Vibes, Varela Round (SIL OFL 1.1). Ingen AI i produkten.

Orderfält: id, product="manfas", mode single|family, text (familjerubrik i familjeläge), heading born|wedding|met|none,
style, row none|month|week, languages, moons=[{name, date, time|None, place, country, lat, lon, timezone}].

Körning:  python manfas.py order.json -> <ut>/<id>_<språk>.pdf + <ut>/<id>_meta.json
Felinjektion (bara tester): FELINJEKTION=<namn>, se FEL-användningen nedan.
"""
import calendar, hashlib, io, json, math, os, sys, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from skyfield import almanac
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import formorkelse as F  # Sky-laddning (DE421), wrap/fit_font, månadsnamn

GENERATOR_VERSION = "manfas/0.1.0"
ROOT = Path(__file__).parent
FEL = os.environ.get("FELINJEKTION", "")
A3 = (297 * mm, 420 * mm)
SCALE_A4 = 210 / 297
for _n, _f in {"Script": "GreatVibes-Regular.ttf", "Round": "VarelaRound-Regular.ttf"}.items():
    if _n not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_n, str(ROOT / "fonts" / _f)))

# ------------------------------------------------------------------ stilar (färger 0..1; lit/dark används av grinden)
STYLES = {
    "mork": {"label": "Midnight & gold", "page": (0.035, 0.055, 0.11), "text": (0.96, 0.93, 0.85), "accent": (0.85, 0.76, 0.52),
             "mute": (0.66, 0.65, 0.62), "lit": (0.96, 0.93, 0.83), "dark": (0.11, 0.14, 0.23), "maria": (0.70, 0.66, 0.56),
             "rim": (0.85, 0.76, 0.52), "frame": "dubbel", "fonts": ("Serif", "SerifIt", "Sans"), "name_size": 50,
             "glow": True, "stars": (0.95, 0.9, 0.75)},
    "ljus": {"label": "Minimal light", "page": (0.975, 0.97, 0.96), "text": (0.11, 0.11, 0.11), "accent": (0.11, 0.11, 0.11),
             "mute": (0.40, 0.40, 0.40), "lit": (0.89, 0.885, 0.87), "dark": (0.16, 0.16, 0.17), "maria": (0.66, 0.655, 0.64),
             "rim": (0.11, 0.11, 0.11), "frame": "linje", "fonts": ("Sans", "Sans", "Sans"), "name_size": 44, "upper": True},
    "akvarell": {"label": "Watercolour", "page": (0.985, 0.97, 0.94), "text": (0.12, 0.14, 0.30), "accent": (0.20, 0.22, 0.38),
                 "mute": (0.40, 0.40, 0.48), "lit": (1.0, 0.975, 0.90), "dark": (0.13, 0.14, 0.27), "maria": (0.74, 0.72, 0.68),
                 "rim": (0.20, 0.22, 0.38), "frame": "ingen", "fonts": ("Script", "SerifIt", "Sans"), "name_size": 72,
                 "wash": [(18, 28, 72), (46, 36, 98), (16, 70, 96), (70, 38, 92)]},
    "barnrum": {"label": "Nursery pastel", "page": (0.985, 0.90, 0.89), "text": (0.28, 0.28, 0.38), "accent": (0.66, 0.33, 0.33),
                "mute": (0.42, 0.40, 0.47), "lit": (1.0, 0.95, 0.78), "dark": (0.36, 0.42, 0.58), "maria": (0.90, 0.78, 0.55),
                "rim": (0.80, 0.52, 0.50), "frame": "rundad", "fonts": ("Round", "Round", "Round"), "name_size": 54,
                "clouds": (1, 1, 1), "stars": (0.93, 0.70, 0.55)},
}
HEADINGS = ("born", "wedding", "met", "none")
ROWS = ("none", "month", "week")

# ------------------------------------------------------------------ texter (fasta mallar)
MONTHS = dict(F.MONTHS)
MONTHS["fr"] = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
WEEKDAYS = {"en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            "sv": ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"],
            "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
            "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
            "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]}
L = {
    "en": {"phases": ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon", "Waning Gibbous",
                      "Last Quarter", "Waning Crescent"],
           "illum": "{p}% illuminated",
           "heading": {"born": "The Moon on the day you were born", "wedding": "The Moon on our wedding day",
                       "met": "The Moon on the night we met", "family": "The Moon on the days we were born"},
           "row": "The Moon, day by day", "noon": "Moon shown at 12:00 noon local time",
           "credit": "Calculated for the place and time with Skyfield (MIT) and the JPL ephemeris DE421, and checked automatically "
                     "against an independent calculation (Meeus, Astronomical Algorithms). Fonts: Noto, Great Vibes, Varela Round "
                     "(SIL OFL 1.1). Moodly Sverige."},
    "sv": {"phases": ["Nymåne", "Tilltagande skära", "Första kvarteret", "Tilltagande måne", "Fullmåne", "Avtagande måne",
                      "Sista kvarteret", "Avtagande skära"],
           "illum": "{p} % belyst",
           "heading": {"born": "Månen den dag du föddes", "wedding": "Månen på vår bröllopsdag",
                       "met": "Månen den natt vi träffades", "family": "Månen de dagar vi föddes"},
           "row": "Månen dag för dag", "noon": "Månen visas kl. 12.00 lokal tid",
           "credit": "Beräknat för platsen och tiden med Skyfield (MIT) och JPL:s efemerid DE421, och kontrollerat automatiskt "
                     "mot en oberoende beräkning (Meeus, Astronomical Algorithms). Typsnitt: Noto, Great Vibes, Varela Round "
                     "(SIL OFL 1.1). Moodly Sverige."},
    "de": {"phases": ["Neumond", "Zunehmende Sichel", "Erstes Viertel", "Zunehmender Mond", "Vollmond", "Abnehmender Mond",
                      "Letztes Viertel", "Abnehmende Sichel"],
           "illum": "{p} % beleuchtet",
           "heading": {"born": "Der Mond am Tag deiner Geburt", "wedding": "Der Mond an unserem Hochzeitstag",
                       "met": "Der Mond in der Nacht, als wir uns trafen", "family": "Der Mond an den Tagen unserer Geburt"},
           "row": "Der Mond Tag für Tag", "noon": "Mond um 12:00 Uhr Ortszeit",
           "credit": "Berechnet für Ort und Zeit mit Skyfield (MIT) und der JPL-Ephemeride DE421, automatisch geprüft gegen eine "
                     "unabhängige Berechnung (Meeus, Astronomical Algorithms). Schriften: Noto, Great Vibes, Varela Round "
                     "(SIL OFL 1.1). Moodly Sverige."},
    "fr": {"phases": ["Nouvelle lune", "Premier croissant", "Premier quartier", "Lune gibbeuse croissante", "Pleine lune",
                      "Lune gibbeuse décroissante", "Dernier quartier", "Dernier croissant"],
           "illum": "éclairée à {p} %",
           "heading": {"born": "La Lune le jour de ta naissance", "wedding": "La Lune le jour de notre mariage",
                       "met": "La Lune la nuit de notre rencontre", "family": "La Lune les jours de nos naissances"},
           "row": "La Lune jour après jour", "noon": "Lune représentée à midi, heure locale",
           "credit": "Calculé pour le lieu et l'heure avec Skyfield (MIT) et l'éphéméride DE421 du JPL, puis vérifié "
                     "automatiquement par un calcul indépendant (Meeus, Astronomical Algorithms). Polices : Noto, Great Vibes, "
                     "Varela Round (SIL OFL 1.1). Moodly Sverige."},
    "es": {"phases": ["Luna nueva", "Luna creciente", "Cuarto creciente", "Gibosa creciente", "Luna llena", "Gibosa menguante",
                      "Cuarto menguante", "Luna menguante"],
           "illum": "iluminada al {p} %",
           "heading": {"born": "La Luna el día en que naciste", "wedding": "La Luna el día de nuestra boda",
                       "met": "La Luna la noche en que nos conocimos", "family": "La Luna los días en que nacimos"},
           "row": "La Luna día a día", "noon": "Luna a las 12:00, hora local",
           "credit": "Calculado para el lugar y la hora con Skyfield (MIT) y la efeméride DE421 del JPL, y comprobado "
                     "automáticamente con un cálculo independiente (Meeus, Astronomical Algorithms). Tipografía: Noto, Great Vibes, "
                     "Varela Round (SIL OFL 1.1). Moodly Sverige."},
}
# ord som bara får förekomma i sitt eget språk (grinden letar efter dem i fel språk)
LANG_WORDS = {"en": ["illuminated", "Full Moon", "Waxing", "Waning"], "sv": ["belyst", "Fullmåne", "Tilltagande", "Avtagande"],
              "de": ["beleuchtet", "Vollmond", "Zunehmend", "Abnehmend"], "fr": ["éclairée", "Pleine lune", "gibbeuse", "croissant"],
              "es": ["iluminada", "Luna llena", "Gibosa", "menguante"]}
FOREIGN = {l: [w for k, ws in LANG_WORDS.items() if k != l for w in ws] for l in LANG_WORDS}

# månens "ansikte": förenklade mare-fält (x, y, rx, ry, vinkel) i enhetsskivan, norra halvklotets vy (norr upp)
MARIA = [(-0.52, 0.12, 0.30, 0.42, 20), (-0.22, 0.46, 0.26, 0.21, -10), (0.24, 0.38, 0.17, 0.15, 0), (0.33, 0.10, 0.20, 0.16, 15),
         (0.70, 0.30, 0.11, 0.09, 0), (0.55, -0.20, 0.12, 0.17, 10), (0.38, -0.32, 0.09, 0.09, 0), (-0.16, -0.36, 0.17, 0.13, 0),
         (-0.46, -0.42, 0.10, 0.10, 0), (0.0, 0.76, 0.45, 0.07, 0), (-0.05, 0.10, 0.12, 0.10, 0)]


def fmt_date(lang, d, hm=None):
    wd, mo = WEEKDAYS[lang][d.weekday()], MONTHS[lang][d.month - 1]
    if lang == "en":
        s = f"{wd}, {mo} {d.day}, {d.year}"
        if hm:
            h, m = hm; s += f" · {(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
    elif lang == "sv":
        s = f"{wd} {d.day} {mo} {d.year}" + (f" · kl. {hm[0]:02d}.{hm[1]:02d}" if hm else "")
    elif lang == "de":
        s = f"{wd}, {d.day}. {mo} {d.year}" + (f" · {hm[0]:02d}:{hm[1]:02d} Uhr" if hm else "")
    elif lang == "fr":
        s = f"{wd} {d.day} {mo} {d.year}" + (f" · {hm[0]} h {hm[1]:02d}" if hm else "")
    else:
        s = f"{wd}, {d.day} de {mo} de {d.year}" + (f" · {hm[0]:02d}:{hm[1]:02d}" if hm else "")
    return s[0].upper() + s[1:]


def fmt_short(lang, d):
    mo = MONTHS[lang][d.month - 1][:3]
    return f"{mo} {d.day}" if lang == "en" else f"{d.day}. {mo}" if lang == "de" else f"{d.day} {mo}"


def pct(frac):
    return int(math.floor(frac * 100 + 0.5))


def phase_line(lang, m):
    return f"{L[lang]['phases'][m['phase']]} · {L[lang]['illum'].format(p=pct(m['frac']))}"


def place_line(m):
    return f"{m['place']}, {m['country']}" if m.get("country") else m["place"]


def coord_line(lang, m):
    lat, lon = m["lat"], m["lon"]
    e, w = {"en": "EW", "sv": "ÖV", "de": "OW", "fr": "EO", "es": "EO"}[lang]
    s = f"{abs(lat):.2f}° {'N' if lat >= 0 else 'S'} · {abs(lon):.2f}° {e if lon >= 0 else w}"
    return s + (f" · {L[lang]['noon']}" if not m.get("time") else "")


def expected_texts(order, lang, data):
    """Alla textrader grinden kräver i PDF:en (samma mallar som ritas)."""
    out = []
    if order["mode"] == "family":
        out.append(L[lang]["heading"]["family"])
        if order.get("text"):
            out.append(order["text"])
    elif order.get("heading", "born") != "none":
        out.append(L[lang]["heading"][order.get("heading", "born")])
    for m in data["moons"]:
        out += [m["name"], fmt_date(lang, date.fromisoformat(m["date"]), m.get("hm")), phase_line(lang, m), place_line(m)]
    return [t for t in out if t]


# ------------------------------------------------------------------ beräkning
def phase_index(principal, elong):
    if principal is not None:
        return 2 * principal
    return 1 if elong < 90 else 3 if elong < 180 else 5 if elong < 270 else 7


def compute(order, sky):
    ts, eph = sky.ts, sky.eph
    f_phase = almanac.moon_phases(eph)
    out = {"moons": []}
    for i, mo in enumerate(order["moons"]):
        tz = ZoneInfo(mo["timezone"])
        d = date.fromisoformat(mo["date"])
        if not (date(1900, 1, 1) <= d <= date(2050, 12, 31)):
            raise SystemExit("date_out_of_range")
        hm = tuple(int(x) for x in mo["time"].split(":")[:2]) if mo.get("time") else None
        h, mi = hm if hm else (12, 0)
        local = datetime(d.year, d.month, d.day, h, mi, tzinfo=tz)
        if FEL == "tidszon_fel":
            local = local.replace(tzinfo=timezone.utc)
        if FEL == "datum_fel":
            local += timedelta(days=1)
        t = ts.from_datetime(local)
        frac = float(almanac.fraction_illuminated(eph, "moon", t))
        elong = float(almanac.moon_phase(eph, t).degrees)
        m0 = datetime(d.year, d.month, d.day, tzinfo=tz)
        t0, t1 = ts.from_datetime(m0), ts.from_datetime(m0 + timedelta(days=1))
        tt, ph = almanac.find_discrete(t0, t1, f_phase)
        principal = int(ph[0]) if len(ph) else None
        rec = {**mo, "hm": list(hm) if hm else None, "utc": t.utc_datetime().isoformat(), "local": local.isoformat(),
               "frac": frac, "elong": elong, "waxing": elong < 180, "south": mo["lat"] < 0,
               "principal": principal, "principal_utc": tt[0].utc_datetime().isoformat() if len(ph) else None}
        rec["phase"] = phase_index(principal, elong)
        if FEL == "belysning_fel" and i == 0:
            rec["frac"] = frac + 0.08 if frac < 0.9 else frac - 0.08
        if FEL == "belysning_liten" and i == 0:
            rec["frac"] = frac + 0.015 if frac < 0.95 else frac - 0.015
        if FEL == "fasnamn_fel" and i == 0:
            rec["phase"] = (rec["phase"] + 1) % 8
        if FEL == "orientering_fel" and i == 0:
            rec["south"] = not rec["south"]
        # rad: samma klockslag varje dag i månaden / veckan runt dagen
        if order["mode"] == "single" and order.get("row", "none") != "none":
            if order["row"] == "month":
                days = [date(d.year, d.month, k) for k in range(1, calendar.monthrange(d.year, d.month)[1] + 1)]
            else:
                days = [d + timedelta(days=k) for k in range(-3, 4)]
            if FEL == "rad_fel":
                days_calc = [x + timedelta(days=1) for x in days]
            else:
                days_calc = days
            lts = [datetime(x.year, x.month, x.day, h, mi, tzinfo=tz) for x in days_calc]
            tr = ts.from_datetimes(lts)
            fr = almanac.fraction_illuminated(eph, "moon", tr)
            el = almanac.moon_phase(eph, tr).degrees
            rec["row"] = [{"date": x.isoformat(), "utc": lt.astimezone(timezone.utc).isoformat(), "frac": float(a), "waxing": bool(b < 180),
                           "is_day": x == d} for x, lt, a, b in zip(days, lts, fr, el)]
        out["moons"].append(rec)
    if FEL == "familj_fel" and len(out["moons"]) >= 2:
        a, b = out["moons"][0], out["moons"][1]
        for k in ("frac", "elong", "waxing", "phase", "utc", "principal"):
            a[k], b[k] = b[k], a[k]
    return out


# ------------------------------------------------------------------ ritning
def seed_of(*parts):
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def lit_path(c, cx, cy, r, frac, right, n=96):
    k = 1 - 2 * frac
    s = 1 if right else -1
    p = c.beginPath()
    pts = [(cx + s * r * math.sin(math.pi * i / n), cy + r * math.cos(math.pi * i / n)) for i in range(n + 1)]
    pts += [(cx + s * k * r * math.sin(math.pi * (n - i) / n), cy + r * math.cos(math.pi * (n - i) / n)) for i in range(n + 1)]
    p.moveTo(*pts[0])
    for q in pts[1:]:
        p.lineTo(*q)
    p.close()
    return p


def draw_moon(c, cx, cy, r, frac, waxing, south, st, face=True, rim=True):
    """Mörk skiva + belyst del (terminatorn = halvellips). Norra halvklotet: tilltagande = höger sida ljus."""
    right = waxing != south
    c.setFillColorRGB(*st["dark"]); c.circle(cx, cy, r, stroke=0, fill=1)
    if frac > 0.0005:
        p = lit_path(c, cx, cy, r, frac, right)
        c.setFillColorRGB(*st["lit"]); c.drawPath(p, stroke=0, fill=1)
        if face:
            c.saveState(); c.clipPath(lit_path(c, cx, cy, r, frac, right), stroke=0, fill=0)
            c.setFillColorRGB(*st["maria"])
            rot = 180 if south else 0  # södra halvklotet ser månen upp och ned
            c.setFillAlpha(0.30)
            for x, y, rx, ry, ang in MARIA:  # tre förskjutna lager ger mjuka, oregelbundna kanter
                for dx, dy, sc in ((0, 0, 1.0), (0.18, -0.12, 0.78), (-0.15, 0.14, 0.62)):
                    c.saveState(); c.translate(cx, cy); c.rotate(rot); c.translate((x + dx * rx) * r, (y + dy * ry) * r); c.rotate(ang + 25 * dx)
                    c.ellipse(-rx * sc * r, -ry * sc * r, rx * sc * r, ry * sc * r, stroke=0, fill=1); c.restoreState()
            c.setFillAlpha(1)
            c.restoreState()
    if rim:
        c.setStrokeColorRGB(*st["rim"]); c.setLineWidth(max(0.4, r / 180)); c.circle(cx, cy, r, stroke=1, fill=0)


def draw_frame(c, st, W, H):
    i = 12 * mm
    c.setStrokeColorRGB(*st["accent"])
    if st["frame"] == "linje":
        c.setLineWidth(0.7); c.rect(i, i, W - 2 * i, H - 2 * i, stroke=1, fill=0)
    elif st["frame"] == "dubbel":
        c.setLineWidth(1.1); c.rect(i, i, W - 2 * i, H - 2 * i, stroke=1, fill=0)
        j = i + 2.4 * mm; c.setLineWidth(0.4); c.rect(j, j, W - 2 * j, H - 2 * j, stroke=1, fill=0)
    elif st["frame"] == "rundad":
        c.setLineWidth(1.4); c.roundRect(i, i, W - 2 * i, H - 2 * i, 10 * mm, stroke=1, fill=0)


def star_shape(c, x, y, r):
    p = c.beginPath()
    for k in range(10):
        a = math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.45
        (p.moveTo if k == 0 else p.lineTo)(x + rr * math.cos(a), y + rr * math.sin(a))
    p.close(); c.drawPath(p, stroke=0, fill=1)


def decorate(c, st, W, H, disks, keep_out, seed):
    """Stjärnor/moln – aldrig innanför en måne eller en textzon."""
    rng = np.random.default_rng(seed)

    def free(x, y, pad):
        if any((x - dx) ** 2 + (y - dy) ** 2 < (dr * 1.12 + pad) ** 2 for dx, dy, dr in disks):
            return False
        return not any(x0 - pad < x < x1 + pad and y0 - pad < y < y1 + pad for x0, y0, x1, y1 in keep_out)

    if st.get("stars"):
        c.setFillColorRGB(*st["stars"])
        n = 140 if st.get("glow") else 26
        for _ in range(n * 4):
            if n <= 0:
                break
            x, y = rng.uniform(18 * mm, W - 18 * mm), rng.uniform(26 * mm, H - 18 * mm)
            if not free(x, y, 3 * mm):
                continue
            n -= 1
            if st.get("glow"):
                c.setFillAlpha(float(rng.uniform(0.35, 0.95))); c.circle(x, y, float(rng.uniform(0.25, 0.8)) * mm, stroke=0, fill=1)
            else:
                star_shape(c, x, y, float(rng.uniform(1.6, 3.2)) * mm)
        c.setFillAlpha(1)
    if st.get("clouds"):
        c.setFillColorRGB(*st["clouds"])
        for dx, dy, dr in disks[:1]:
            for side in (-1, 1):
                bx, by = dx + side * dr * 1.02, dy - dr * 0.78
                for k in range(5):
                    r = (6 + 3.5 * rng.random()) * mm
                    x = bx + (k - 2) * 6.5 * mm * side
                    y = by + (3.5 * mm if k in (1, 2) else 0)
                    # molnet får inte gå in i månskivan (grinden mäter skivan) eller täcka text
                    if math.hypot(x - dx, y - dy) - r > dr + 1.5 * mm and not any(
                            x0 - r < x < x1 + r and y0 - r < y < y1 + r for x0, y0, x1, y1 in keep_out):
                        c.circle(x, y, r, stroke=0, fill=1)


def text_c(c, s, x, y, font, size, color):
    c.setFont(font, size); c.setFillColorRGB(*color); c.drawCentredString(x, y, s)
    w = pdfmetrics.stringWidth(s, font, size)
    return (x - w / 2, y - size * 0.3, x + w / 2, y + size * 0.85)


def font_for(text, preferred, fallback="Serif"):
    cmap = pdfmetrics.getFont(preferred).face.charToGlyph
    return preferred if all(ord(ch) in cmap for ch in text if not ch.isspace()) else fallback


def layout(order, data):
    """Geometri i A3-koordinater (pt): månar, textrader. Returnerar dict som ritas och sparas i meta."""
    W, H = A3
    moons = data["moons"]
    geo = {"disks": [], "row": []}
    if order["mode"] == "family":
        n = len(moons)
        rows = {2: [2], 3: [2, 1], 4: [2, 2], 5: [3, 2], 6: [3, 3]}[n]
        top, bot = H - (78 if order.get("text") else 62) * mm, 34 * mm
        inner_w = W - 44 * mm
        bh = (top - bot) / len(rows)
        text_h = 40 * mm
        R = min(min(0.36 * inner_w / r_ for r_ in rows), (bh - text_h) / 2 * 0.9)
        k = 0
        for ri, cols in enumerate(rows):
            bw = inner_w / cols
            y_block_top = top - ri * bh
            cy = y_block_top - (bh - (2 * R + text_h)) / 2 - R
            for ci in range(cols):
                cx = 22 * mm + bw * (ci + 0.5)
                geo["disks"].append({"i": k, "cx": cx, "cy": cy, "r": R, "bw": bw}); k += 1
    else:
        row = order.get("row", "none")
        R = 95 * mm if row == "none" else 80 * mm
        cy = H * 0.60 if row == "none" else 275 * mm
        geo["disks"].append({"i": 0, "cx": W / 2, "cy": cy, "r": R, "bw": W - 44 * mm})
        if row != "none":
            rr = moons[0]["row"]
            if row == "month":
                n1 = (len(rr) + 1) // 2
                for j, md in enumerate(rr):
                    line = 0 if j < n1 else 1
                    cnt = n1 if line == 0 else len(rr) - n1
                    jj = j if line == 0 else j - n1
                    gap = (W - 50 * mm) / 16
                    x = W / 2 + (jj - (cnt - 1) / 2) * gap
                    geo["row"].append({"j": j, "cx": x, "cy": (62 - 23 * line) * mm, "r": 5.3 * mm})
            else:
                gap = 33 * mm
                for j, md in enumerate(rr):
                    geo["row"].append({"j": j, "cx": W / 2 + (j - 3) * gap, "cy": 52 * mm, "r": 9 * mm})
    return geo


def render_page(c, order, data, lang, st, geo, seed, wash_png):
    W, H = A3
    lx = L[lang]
    f_name, f_title, f_body = st["fonts"]
    up = st.get("upper")
    c.setFillColorRGB(*st["page"]); c.rect(0, 0, W, H, stroke=0, fill=1)
    draw_frame(c, st, W, H)
    boxes = []
    text_fn = text_c
    # rubrik
    if order["mode"] == "family":
        hd = lx["heading"]["family"]
        boxes.append(text_fn(c, hd, W / 2, H - 36 * mm, f_title, F.fit_font(hd, f_title, 16, W - 60 * mm, 10), st["accent"]))
        if order.get("text"):
            t = order["text"]; fn = font_for(t, f_name)
            size = F.fit_font(t, fn, st["name_size"] * 0.9, W - 60 * mm, 18)
            boxes.append(text_fn(c, t, W / 2, H - 58 * mm, fn, size, st["text"]))
    elif order.get("heading", "born") != "none":
        hd = lx["heading"][order.get("heading", "born")]
        size = F.fit_font(hd, f_title, 19, W - 60 * mm, 11)
        boxes.append(text_fn(c, hd, W / 2, H - 38 * mm, f_title, size, st["accent"]))
    disks = [(g["cx"], g["cy"], g["r"]) for g in geo["disks"]]
    # akvarell: tvätt bakom varje måne
    if wash_png is not None:
        img = ImageReader(io.BytesIO(wash_png))
        for k, (cx, cy, r) in enumerate(disks):
            Rw = r * 1.13
            p = c.beginPath(); p.circle(cx, cy, Rw)
            c.saveState(); c.clipPath(p, stroke=0, fill=0)
            off = (k % 3) * 0.12 * Rw
            c.drawImage(img, cx - Rw - off, cy - Rw - off * 0.6, 2 * Rw + 2 * off, 2 * Rw + 2 * off)
            c.restoreState()
    if st.get("glow"):
        for cx, cy, r in disks:
            for kk, a in ((1.10, 0.05), (1.05, 0.07), (1.02, 0.09)):
                c.setFillColorRGB(*st["lit"]); c.setFillAlpha(a); c.circle(cx, cy, r * kk, stroke=0, fill=1)
            c.setFillAlpha(1)
    for g in geo["disks"]:
        m = data["moons"][g["i"]]
        draw_moon(c, g["cx"], g["cy"], g["r"], m["frac"], m["waxing"], m["south"], st)
    # texter under månarna
    for g in geo["disks"]:
        m = data["moons"][g["i"]]
        cx, cy, r, bw = g["cx"], g["cy"], g["r"], g["bw"]
        fam = order["mode"] == "family"
        nm = m["name"].upper() if up else m["name"]
        fn = font_for(nm, f_name)
        size = F.fit_font(nm, fn, st["name_size"] * (0.5 if fam else 1), bw - 8 * mm, 12 if fam else 20)
        ext = r * (1.13 if wash_png is not None else 1.10 if st.get("glow") else 1.0)
        asc = pdfmetrics.getFont(fn).face.ascent / 1000 * size  # typsnittets egen överhöjd (skrivstil är hög)
        y = cy - ext - (7 if fam else 12) * mm - asc
        if not (FEL == "saknad_text" and g["i"] == 0):
            xx = cx + (160 * mm if FEL == "text_utanfor" and g["i"] == 0 else 0)
            boxes.append(text_fn(c, nm, xx, y, fn, size, st["text"]))
        y -= (9 if fam else 16) * mm
        ds = fmt_date(lang, date.fromisoformat(m["date"]), m.get("hm"))
        yy = y + 16 * mm if FEL == "text_overlapp" and g["i"] == 0 else y
        boxes.append(text_fn(c, ds, cx, yy, f_body, F.fit_font(ds, f_body, 9 if fam else 14, bw - 8 * mm, 6.5), st["text"]))
        y -= (6.5 if fam else 11) * mm
        pl = place_line(m)
        pf = "Helvetica" if FEL == "typsnitt_fel" and g["i"] == 0 else f_body
        boxes.append(text_fn(c, pl, cx, y, pf, F.fit_font(pl, f_body, 8 if fam else 12, bw - 8 * mm, 6), st["mute"]))
        y -= (8 if fam else 15) * mm
        lang_ph = "en" if FEL == "sprak_fel" else lang
        ph = phase_line(lang_ph, m)
        boxes.append(text_fn(c, ph, cx, y, f_title, F.fit_font(ph, f_title, 10 if fam else 17, bw - 8 * mm, 7), st["accent"]))
        if not fam:
            y -= 9 * mm
            cl = coord_line(lang, m)
            boxes.append(text_fn(c, cl, cx, y, f_body, F.fit_font(cl, f_body, 8.5, bw - 8 * mm, 6), st["mute"]))
    # dag-för-dag-rad
    if geo["row"]:
        m = data["moons"][0]
        rr = m["row"]
        ytop = max(g["cy"] + g["r"] for g in geo["row"])
        boxes.append(text_fn(c, lx["row"], W / 2, ytop + 7 * mm, f_title, 11, st["accent"]))
        for g in geo["row"]:
            md = rr[g["j"]]
            draw_moon(c, g["cx"], g["cy"], g["r"], md["frac"], md["waxing"], m["south"], st, face=False, rim=True)
            if md["is_day"]:
                c.setStrokeColorRGB(*st["accent"]); c.setLineWidth(0.9); c.circle(g["cx"], g["cy"], g["r"] + 1.8 * mm, stroke=1, fill=0)
            dd = date.fromisoformat(md["date"])
            lab = str(dd.day) if order["row"] == "month" else fmt_short(lang, dd)
            boxes.append(text_fn(c, lab, g["cx"], g["cy"] - g["r"] - (5 if order["row"] == "month" else 7) * mm, f_body,
                                 7.5 if order["row"] == "month" else 9, st["mute"]))
    # källrad
    c.setFont("Sans", 5.6); c.setFillColorRGB(*st["mute"])
    yy = 20 * mm
    for ln in F.wrap(lx["credit"], "Sans", 5.6, W - 60 * mm):
        c.drawCentredString(W / 2, yy, ln); yy -= 7
    decorate(c, st, W, H, disks + [(g["cx"], g["cy"], g["r"] + 2 * mm) for g in geo["row"]], boxes + [(20 * mm, 0, W - 20 * mm, 23 * mm)], seed)
    return boxes


def render(order, data, lang, path, wash_png):
    st = STYLES[order["style"]]
    geo = layout(order, data)
    c = canvas.Canvas(str(path), pagesize=A3, invariant=1, initialFontName="Sans", initialFontSize=10)
    c.setTitle(f"{data['moons'][0]['name']} – {data['moons'][0]['place']}"); c.setAuthor(GENERATOR_VERSION)
    seed = seed_of(order["id"], order["style"])
    render_page(c, order, data, lang, st, geo, seed, wash_png)
    c.showPage()
    c.setPageSize((A3[0] * SCALE_A4, A3[1] * SCALE_A4))
    c.saveState(); c.scale(SCALE_A4, SCALE_A4)
    render_page(c, order, data, lang, st, geo, seed, wash_png)
    c.restoreState(); c.showPage(); c.save()
    return {"pages": [{"size": list(A3), "scale": 1.0}, {"size": [A3[0] * SCALE_A4, A3[1] * SCALE_A4], "scale": SCALE_A4}],
            "disks": geo["disks"], "row": geo["row"]}


def validate(order):
    if order.get("style") not in STYLES:
        raise SystemExit("internal")
    if order.get("mode") not in ("single", "family") or order.get("row", "none") not in ROWS or order.get("heading", "born") not in HEADINGS:
        raise SystemExit("internal")
    n = len(order.get("moons") or [])
    if (order["mode"] == "single" and n != 1) or (order["mode"] == "family" and not 2 <= n <= 6):
        raise SystemExit("people_count")


def generate(order_path):
    timings = {}
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    validate(order)
    sky = F.Sky()
    timings["ladda_data_s"] = time.perf_counter() - t0
    t1 = time.perf_counter()
    data = compute(order, sky)
    timings["berakna_s"] = time.perf_counter() - t1
    wash = None
    t_w = time.perf_counter()
    if order["style"] == "akvarell":
        from stjarnkarta import watercolor_png
        wash = watercolor_png(seed_of(order["id"], "akvarell"), STYLES["akvarell"]["wash"], px=1100)
    timings["akvarell_s"] = time.perf_counter() - t_w
    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, geos = {}, {}
    t2 = time.perf_counter()
    for lang in order["languages"]:
        path = out / f"{order['id']}_{lang}.pdf"
        geos[lang] = render(order, data, lang, path, wash)
        files[lang] = str(path)
    timings["rendera_s"] = time.perf_counter() - t2
    st = STYLES[order["style"]]
    meta = {"generator": GENERATOR_VERSION, "product": "manfas", "order": order, "files": files, "data": data,
            "geometry": geos, "style": {"name": order["style"], **{k: st[k] for k in ("page", "text", "accent", "mute", "lit", "dark")},
                                        "fonts": list(st["fonts"]), "upper": bool(st.get("upper"))},
            "timings": timings, "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"]}, indent=1))
