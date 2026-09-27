"""Generator: personlig himmelskalender 2027 för köparens ort (PDF, 13 sidor A4 liggande).

Innehåll per månad: kalenderrutnät med soluppgång–solnedgång och månens fas varje dag, månfaserna med
klockslag, fullmånens traditionella namn, meteorsvärmarnas toppnätter (med månljus), förmörkelser som syns
från orten och planeternas synlighet (kväll/natt/morgon, riktning).
Beräkning: Skyfield + JPL DE421 (samma data som stjärnkartan). Solförmörkelser: formorkelse.local_circumstances.
Meteorsvärmar: IMO:s standardvärden (solens longitud vid maximum, ZHR, radiant) – fakta, se METEORS.
Typsnitt: Noto (SIL OFL 1.1). Ingen AI i produkten.

Körning:  python himmelskalender.py order.json -> <ut>/<id>_<språk>.pdf + <ut>/<id>_meta.json
Felinjektion (bara tester): FELINJEKTION=<namn>, se FEL-användningen nedan.
"""
import calendar, hashlib, json, math, os, sys, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import brentq
from skyfield import almanac, eclipselib
from skyfield.api import wgs84
from skyfield.framelib import ecliptic_J2000_frame
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics

import formorkelse as F  # Sky-laddning, solförmörkelsens lokala omständigheter, typsnitt, hjälpfunktioner

GENERATOR_VERSION = "himmelskalender/0.1.0"
YEAR = 2027
PAGE = (297 * mm, 210 * mm)  # A4 liggande
FEL = os.environ.get("FELINJEKTION", "")
BG, INK, MUTE, GOLD, SUN = (0.035, 0.055, 0.11), (0.96, 0.93, 0.85), (0.70, 0.68, 0.62), (0.85, 0.79, 0.62), (1.0, 0.80, 0.30)
CELL_BG = (0.06, 0.085, 0.16)
RED = (1.0, 0.55, 0.45)

# IMO standardvärden: kod, solens longitud vid max (J2000), ZHR, radiant RA (°), Dec (°)
METEORS = [("QUA", 283.15, 80, 230, 49), ("LYR", 32.32, 18, 271, 34), ("ETA", 45.5, 50, 338, -1),
           ("SDA", 127.0, 25, 340, -16), ("PER", 140.0, 100, 48, 58), ("DRA", 195.4, 10, 262, 54),
           ("ORI", 208.0, 20, 95, 16), ("LEO", 235.27, 15, 152, 22), ("GEM", 262.2, 150, 112, 33),
           ("URS", 270.7, 10, 217, 76)]
SOLAR_ECLIPSES = [(2027, 2, 6), (2027, 8, 2)]
PLANETS = ["mercury", "venus", "mars", "jupiter", "saturn"]
EPH_NAME = {"mercury": "mercury", "venus": "venus", "mars": "mars", "jupiter": "jupiter barycenter", "saturn": "saturn barycenter"}

L = {
    "sv": {
        "months": F.MONTHS["sv"], "wd": ["mån", "tis", "ons", "tor", "fre", "lör", "sön"],
        "phases": ["Nymåne", "Första kvarteret", "Fullmåne", "Sista kvarteret"],
        "fullnames": ["Vargmånen", "Snömånen", "Maskmånen", "Rosa månen", "Blomstermånen", "Jordgubbsmånen",
                      "Hjortmånen", "Störmånen", "Skördemånen", "Jägarmånen", "Bävermånen", "Kalla månen"],
        "harvest": "Skördemånen", "hunter": "Jägarmånen", "blue": "Blå månen",
        "meteors": {"QUA": "Kvadrantiderna", "LYR": "Lyriderna", "ETA": "Eta Akvariderna", "SDA": "Södra delta Akvariderna",
                    "PER": "Perseiderna", "DRA": "Drakoniderna", "ORI": "Orioniderna", "LEO": "Leoniderna",
                    "GEM": "Geminiderna", "URS": "Ursiderna"},
        "planets": {"mercury": "Merkurius", "venus": "Venus", "mars": "Mars", "jupiter": "Jupiter", "saturn": "Saturnus"},
        "vis": {"E": "kväll", "N": "natt", "M": "morgon", "all": "hela natten", "none": "syns inte"},
        "title": "Himmelskalender {y}", "sub": "Himlen över {place}",
        "moon": "Månen", "fullname": "Fullmånens namn", "met": "Meteorsvärmar", "ecl": "Förmörkelser", "pl": "Planeter",
        "night": "natten {a}–{b} {m}", "zhr": "upp till {z} per timme", "moonlit": "månen {p} % belyst",
        "never": "radianten går aldrig upp här",
        "daylight": "Dagsljus", "dl": "{d1}: {h1}  ·  {d2}: {h2}", "hm": "{h} h {m} min",
        "polar_day": "midnattssol", "polar_night": "solen går inte upp",
        "lunar": {0: "Halvskuggeförmörkelse av månen", 1: "Partiell månförmörkelse", 2: "Total månförmörkelse"},
        "lunar_vis": "max {t} – syns härifrån", "lunar_novis": "max {t} – månen är under horisonten här",
        "solar": {"partial": "Partiell solförmörkelse", "total": "Total solförmörkelse", "annular": "Ringformig solförmörkelse"},
        "solar_vis": "max {t}, {p} % av solen täckt", "ecl_cell_l": "Månförmörkelse", "ecl_cell_s": "Solförmörkelse",
        "legend": "I rutorna: soluppgång–solnedgång. Alla tider är lokal tid ({tz}). Månens form visas kl. 22.",
        "light_nights": "ljusa nätter – planeterna syns dåligt",
        "cover_moons": "Fullmånar {y}", "cover_met": "Årets meteorsvärmar", "cover_ecl": "Förmörkelser som syns härifrån",
        "none_ecl": "Inga förmörkelser syns härifrån {y}.",
        "credit": "Beräknat med Skyfield (MIT) och JPL:s efemerid DE421. Meteorsvärmar: IMO:s standardvärden. "
                  "Kontrollerat automatiskt mot oberoende beräkningar (Meeus, NOAA, JPL Standish, NASA – Eclipse Predictions by Fred Espenak, NASA's GSFC). "
                  "Fullmånenamnen är traditionella nordamerikanska namn. Typsnitt: Noto (SIL OFL 1.1). Moodly Sverige.",
    },
    "en": {
        "months": F.MONTHS["en"], "wd": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "phases": ["New Moon", "First Quarter", "Full Moon", "Last Quarter"],
        "fullnames": ["Wolf Moon", "Snow Moon", "Worm Moon", "Pink Moon", "Flower Moon", "Strawberry Moon",
                      "Buck Moon", "Sturgeon Moon", "Harvest Moon", "Hunter's Moon", "Beaver Moon", "Cold Moon"],
        "harvest": "Harvest Moon", "hunter": "Hunter's Moon", "blue": "Blue Moon",
        "meteors": {"QUA": "Quadrantids", "LYR": "Lyrids", "ETA": "Eta Aquariids", "SDA": "Southern Delta Aquariids",
                    "PER": "Perseids", "DRA": "Draconids", "ORI": "Orionids", "LEO": "Leonids", "GEM": "Geminids", "URS": "Ursids"},
        "planets": {"mercury": "Mercury", "venus": "Venus", "mars": "Mars", "jupiter": "Jupiter", "saturn": "Saturn"},
        "vis": {"E": "evening", "N": "night", "M": "morning", "all": "all night", "none": "not visible"},
        "title": "Sky Calendar {y}", "sub": "The sky over {place}",
        "moon": "The Moon", "fullname": "Full Moon name", "met": "Meteor showers", "ecl": "Eclipses", "pl": "Planets",
        "night": "night of {m} {a}–{b}", "zhr": "up to {z} per hour", "moonlit": "Moon {p}% lit",
        "never": "the radiant never rises here",
        "daylight": "Daylight", "dl": "{d1}: {h1}  ·  {d2}: {h2}", "hm": "{h} h {m} min",
        "polar_day": "midnight sun", "polar_night": "the Sun does not rise",
        "lunar": {0: "Penumbral lunar eclipse", 1: "Partial lunar eclipse", 2: "Total lunar eclipse"},
        "lunar_vis": "maximum {t} – visible from here", "lunar_novis": "maximum {t} – the Moon is below the horizon here",
        "solar": {"partial": "Partial solar eclipse", "total": "Total solar eclipse", "annular": "Annular solar eclipse"},
        "solar_vis": "maximum {t}, {p}% of the Sun covered", "ecl_cell_l": "Lunar eclipse", "ecl_cell_s": "Solar eclipse",
        "legend": "In each box: sunrise–sunset. All times are local time ({tz}). The Moon's shape is shown at 22:00.",
        "light_nights": "light nights – planets hard to see",
        "cover_moons": "Full Moons {y}", "cover_met": "Meteor showers of the year", "cover_ecl": "Eclipses visible from here",
        "none_ecl": "No eclipses are visible from here in {y}.",
        "credit": "Calculated with Skyfield (MIT) and the JPL ephemeris DE421. Meteor showers: IMO standard values. "
                  "Automatically checked against independent calculations (Meeus, NOAA, JPL Standish, NASA – Eclipse Predictions by Fred Espenak, NASA's GSFC). "
                  "Full Moon names are traditional North American names. Fonts: Noto (SIL OFL 1.1). Moodly Sverige.",
    },
    "de": {
        "months": F.MONTHS["de"], "wd": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
        "phases": ["Neumond", "Erstes Viertel", "Vollmond", "Letztes Viertel"],
        "fullnames": ["Wolfsmond", "Schneemond", "Wurmmond", "Rosa Mond", "Blumenmond", "Erdbeermond",
                      "Hirschmond", "Störmond", "Erntemond", "Jägermond", "Bibermond", "Kalter Mond"],
        "harvest": "Erntemond", "hunter": "Jägermond", "blue": "Blauer Mond",
        "meteors": {"QUA": "Quadrantiden", "LYR": "Lyriden", "ETA": "Eta-Aquariiden", "SDA": "Südliche Delta-Aquariiden",
                    "PER": "Perseiden", "DRA": "Draconiden", "ORI": "Orioniden", "LEO": "Leoniden", "GEM": "Geminiden", "URS": "Ursiden"},
        "planets": {"mercury": "Merkur", "venus": "Venus", "mars": "Mars", "jupiter": "Jupiter", "saturn": "Saturn"},
        "vis": {"E": "abends", "N": "nachts", "M": "morgens", "all": "die ganze Nacht", "none": "nicht sichtbar"},
        "title": "Himmelskalender {y}", "sub": "Der Himmel über {place}",
        "moon": "Der Mond", "fullname": "Name des Vollmonds", "met": "Meteorströme", "ecl": "Finsternisse", "pl": "Planeten",
        "night": "Nacht vom {a}. auf den {b}. {m}", "zhr": "bis zu {z} pro Stunde", "moonlit": "Mond {p} % beleuchtet",
        "never": "der Radiant geht hier nie auf",
        "daylight": "Tageslicht", "dl": "{d1}: {h1}  ·  {d2}: {h2}", "hm": "{h} h {m} min",
        "polar_day": "Mitternachtssonne", "polar_night": "die Sonne geht nicht auf",
        "lunar": {0: "Halbschattenfinsternis des Mondes", 1: "Partielle Mondfinsternis", 2: "Totale Mondfinsternis"},
        "lunar_vis": "Maximum {t} – von hier sichtbar", "lunar_novis": "Maximum {t} – der Mond steht hier unter dem Horizont",
        "solar": {"partial": "Partielle Sonnenfinsternis", "total": "Totale Sonnenfinsternis", "annular": "Ringförmige Sonnenfinsternis"},
        "solar_vis": "Maximum {t}, {p} % der Sonne bedeckt", "ecl_cell_l": "Mondfinsternis", "ecl_cell_s": "Sonnenfinsternis",
        "legend": "In den Feldern: Sonnenaufgang–Sonnenuntergang. Alle Zeiten in Ortszeit ({tz}). Die Mondgestalt gilt für 22 Uhr.",
        "light_nights": "helle Nächte – Planeten schwer zu sehen",
        "cover_moons": "Vollmonde {y}", "cover_met": "Meteorströme des Jahres", "cover_ecl": "Von hier sichtbare Finsternisse",
        "none_ecl": "Von hier ist {y} keine Finsternis sichtbar.",
        "credit": "Berechnet mit Skyfield (MIT) und der JPL-Ephemeride DE421. Meteorströme: IMO-Standardwerte. "
                  "Automatisch geprüft gegen unabhängige Berechnungen (Meeus, NOAA, JPL Standish, NASA – Eclipse Predictions by Fred Espenak, NASA's GSFC). "
                  "Die Vollmondnamen sind traditionelle nordamerikanische Namen. Schriften: Noto (SIL OFL 1.1). Moodly Sverige.",
    },
    "es": {
        "months": F.MONTHS["es"], "wd": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"],
        "phases": ["Luna nueva", "Cuarto creciente", "Luna llena", "Cuarto menguante"],
        "fullnames": ["Luna del Lobo", "Luna de Nieve", "Luna de Gusano", "Luna Rosa", "Luna de las Flores", "Luna de Fresa",
                      "Luna del Ciervo", "Luna del Esturión", "Luna de la Cosecha", "Luna del Cazador", "Luna del Castor", "Luna Fría"],
        "harvest": "Luna de la Cosecha", "hunter": "Luna del Cazador", "blue": "Luna Azul",
        "meteors": {"QUA": "Cuadrántidas", "LYR": "Líridas", "ETA": "Eta Acuáridas", "SDA": "Delta Acuáridas del Sur",
                    "PER": "Perseidas", "DRA": "Dracónidas", "ORI": "Oriónidas", "LEO": "Leónidas", "GEM": "Gemínidas", "URS": "Úrsidas"},
        "planets": {"mercury": "Mercurio", "venus": "Venus", "mars": "Marte", "jupiter": "Júpiter", "saturn": "Saturno"},
        "vis": {"E": "tarde", "N": "noche", "M": "madrugada", "all": "toda la noche", "none": "no visible"},
        "title": "Calendario del cielo {y}", "sub": "El cielo sobre {place}",
        "moon": "La Luna", "fullname": "Nombre de la luna llena", "met": "Lluvias de meteoros", "ecl": "Eclipses", "pl": "Planetas",
        "night": "noche del {a} al {b} de {m}", "zhr": "hasta {z} por hora", "moonlit": "Luna iluminada al {p} %",
        "never": "el radiante nunca sale aquí",
        "daylight": "Luz del día", "dl": "{d1}: {h1}  ·  {d2}: {h2}", "hm": "{h} h {m} min",
        "polar_day": "sol de medianoche", "polar_night": "el Sol no sale",
        "lunar": {0: "Eclipse lunar penumbral", 1: "Eclipse lunar parcial", 2: "Eclipse lunar total"},
        "lunar_vis": "máximo {t} – visible desde aquí", "lunar_novis": "máximo {t} – la Luna está bajo el horizonte aquí",
        "solar": {"partial": "Eclipse solar parcial", "total": "Eclipse solar total", "annular": "Eclipse solar anular"},
        "solar_vis": "máximo {t}, {p} % del Sol cubierto", "ecl_cell_l": "Eclipse lunar", "ecl_cell_s": "Eclipse solar",
        "legend": "En cada casilla: salida–puesta del Sol. Todas las horas son locales ({tz}). La forma de la Luna es a las 22:00.",
        "light_nights": "noches claras – planetas difíciles de ver",
        "cover_moons": "Lunas llenas {y}", "cover_met": "Lluvias de meteoros del año", "cover_ecl": "Eclipses visibles desde aquí",
        "none_ecl": "En {y} no hay eclipses visibles desde aquí.",
        "credit": "Calculado con Skyfield (MIT) y la efeméride DE421 del JPL. Lluvias de meteoros: valores estándar de la IMO. "
                  "Comprobado automáticamente con cálculos independientes (Meeus, NOAA, JPL Standish, NASA – Eclipse Predictions by Fred Espenak, NASA's GSFC). "
                  "Los nombres de las lunas llenas son nombres tradicionales norteamericanos. Tipografía: Noto (SIL OFL 1.1). Moodly Sverige.",
    },
}
FOREIGN = {"sv": ["Vollmond", "Full Moon", "Luna llena"], "en": ["Fullmåne", "Vollmond", "Luna llena"],
           "de": ["Fullmåne", "Full Moon", "Luna llena"], "es": ["Fullmåne", "Vollmond", "Full Moon"]}


# ------------------------------------------------------------------ beräkning
def compute(order, sky):
    ts, eph = sky.ts, sky.eph
    tz = ZoneInfo(order["timezone"])
    lat, lon = order["lat"], order["lon"]
    if FEL == "berakning_fel":
        lon += 2.0
    topo = wgs84.latlon(lat, lon)
    obs = sky.earth + topo
    y0 = datetime(YEAR, 1, 1, tzinfo=tz); y1 = datetime(YEAR + 1, 1, 1, tzinfo=tz)
    t0, t1 = ts.from_datetime(y0 - timedelta(days=2)), ts.from_datetime(y1 + timedelta(days=2))
    out = {}
    # månfaser
    t, ph = almanac.find_discrete(t0, t1, almanac.moon_phases(eph))
    out["phases"] = [{"phase": int(p), "utc": ti.utc_datetime().isoformat()} for ti, p in zip(t, ph)
                     if y0 <= ti.utc_datetime().astimezone(tz) < y1]
    # sol upp/ned per lokalt datum
    tr, yr = almanac.find_risings(obs, sky.sun, t0, t1)
    tset, ys = almanac.find_settings(obs, sky.sun, t0, t1)
    rise = {}; sset = {}
    for ti, ok in zip(tr, yr):
        if ok:
            d = ti.utc_datetime().astimezone(tz); rise.setdefault(d.date().isoformat(), d.isoformat())
    for ti, ok in zip(tset, ys):
        if ok:
            d = ti.utc_datetime().astimezone(tz); sset.setdefault(d.date().isoformat(), d.isoformat())
    days = []
    d = date(YEAR, 1, 1)
    noon_t = []
    while d.year == YEAR:
        days.append(d); d += timedelta(days=1)
    noon = ts.from_datetimes([datetime(x.year, x.month, x.day, 12, tzinfo=tz) for x in days])
    alt_noon = obs.at(noon).observe(sky.sun).apparent().altaz()[0].degrees
    ev22 = ts.from_datetimes([datetime(x.year, x.month, x.day, 22, tzinfo=tz) for x in days])
    frac = almanac.fraction_illuminated(eph, "moon", ev22)
    phase_deg = almanac.moon_phase(eph, ev22).degrees
    out["days"] = []
    for i, x in enumerate(days):
        k = x.isoformat()
        rec = {"date": k, "rise": rise.get(k), "set": sset.get(k), "moon_frac": float(frac[i]), "waxing": bool(phase_deg[i] < 180)}
        if FEL == "manfas_fel":
            rec["moon_frac"] = min(1.0, rec["moon_frac"] + 0.25) if rec["moon_frac"] < 0.5 else rec["moon_frac"] - 0.25
        if rec["rise"] is None and rec["set"] is None:
            rec["polar"] = "day" if alt_noon[i] > 0 else "night"
        out["days"].append(rec)
    # meteorsvärmar: solens longitud (J2000) = IMO-värdet
    def lam(jd):
        v = sky.earth.at(ts.tt_jd(jd)).observe(sky.sun).apparent().frame_latlon(ecliptic_J2000_frame)[1].degrees
        return v
    jds = t0.tt + np.arange(0, (t1.tt - t0.tt), 0.5)
    lams = lam(jds)
    out["meteors"] = []
    for code, L0, zhr, ra, dec in METEORS:
        dl = (lams - L0 + 180) % 360 - 180
        idx = np.where((dl[:-1] < 0) & (dl[1:] >= 0))[0]
        for i in idx:
            f = lambda jd: (lam(jd) - L0 + 180) % 360 - 180
            jd = brentq(f, jds[i], jds[i + 1], xtol=1e-7)
            tt = ts.tt_jd(jd)
            loc = tt.utc_datetime().astimezone(tz)
            if not (y0 <= loc < y1):
                continue
            start = loc.date() - timedelta(days=1) if loc.hour < 12 else loc.date()
            never = (dec < -(90 - lat)) if lat >= 0 else (dec > 90 + lat)
            out["meteors"].append({"code": code, "utc": tt.utc_datetime().isoformat(), "night_start": start.isoformat(),
                                   "zhr": zhr, "moon_frac": float(almanac.fraction_illuminated(eph, "moon", tt)),
                                   "radiant_never_rises": bool(never)})
    out["meteors"].sort(key=lambda m: m["utc"])
    # månförmörkelser
    te, yl, det = eclipselib.lunar_eclipses(t0, t1, eph)
    out["lunar"] = []
    for ti, code in zip(te, yl):
        loc = ti.utc_datetime().astimezone(tz)
        if not (y0 <= loc < y1):
            continue
        malt = obs.at(ti).observe(sky.moon).apparent().altaz()[0].degrees
        out["lunar"].append({"utc": ti.utc_datetime().isoformat(), "kind": int(code), "moon_alt": float(malt), "visible": bool(malt > 0)})
    # solförmörkelser
    out["solar"] = []
    for day in SOLAR_ECLIPSES:
        c = F.local_circumstances(sky, lat, lon, 0.0, day=day)
        vis = c["type"] != "none" and c["sun_altaz"]["max"][0] > 0
        out["solar"].append({"day": "%04d-%02d-%02d" % day, "type": c["type"], "visible": bool(vis),
                             "obscuration": c.get("obscuration", 0.0),
                             "max_utc": c.get("contacts_utc", {}).get("max"), "sun_alt_max": c.get("sun_altaz", {}).get("max", [None])[0]})
    # planeter mitt i månaden
    out["planets"] = {}
    for m in range(1, 13):
        dd = date(YEAR, m, 15)
        drec = out["days"][dd.timetuple().tm_yday - 1]
        res = {"instants": {}, "light_nights": False}
        if drec["set"] and drec["rise"]:
            ss = datetime.fromisoformat(drec["set"])
            nxt = out["days"][min(dd.timetuple().tm_yday, len(days) - 1)]
            sr_next = datetime.fromisoformat(nxt["rise"]) if nxt["rise"] else None
            sr = datetime.fromisoformat(drec["rise"])
            inst = {"E": ss + timedelta(minutes=45), "M": sr - timedelta(minutes=45)}
            if sr_next:
                inst["N"] = ss + (sr_next - ss) / 2
        else:
            inst = {}
            res["light_nights"] = True
        for key, when in inst.items():
            tt = ts.from_datetime(when)
            here = obs.at(tt)
            salt = here.observe(sky.sun).apparent().altaz()[0].degrees
            row = {"local": when.isoformat(), "sun_alt": float(salt), "planets": {}}
            for p in PLANETS:
                a, z, _ = here.observe(eph[EPH_NAME[p]]).apparent().altaz()
                row["planets"][p] = [float(a.degrees), float(z.degrees)]
            res["instants"][key] = row
        vis = {}
        for p in PLANETS:
            ks = [k for k in ("E", "N", "M") if k in res["instants"] and res["instants"][k]["sun_alt"] <= -3
                  and res["instants"][k]["planets"][p][0] >= 5]
            vis[p] = ks
        if FEL == "planet_fel":
            vis["mars"] = ["E"] if vis["mars"] != ["E"] else []
        res["visible"] = vis
        out["planets"][str(m)] = res
    return out


def full_moon_names(phases, tz, lang):
    """Fullmåne -> namn. Skördemånen = fullmånen närmast höstdagjämningen (norra halvklotet), Jägarmånen den efter."""
    fulls = [datetime.fromisoformat(p["utc"]).astimezone(tz) for p in phases if p["phase"] == 2]
    eq = datetime(YEAR, 9, 23, tzinfo=tz)
    harvest_i = min(range(len(fulls)), key=lambda i: abs((fulls[i] - eq).total_seconds()))
    names = []
    seen = {}
    for i, f in enumerate(fulls):
        if i == harvest_i:
            n = L[lang]["harvest"]
        elif i == harvest_i + 1:
            n = L[lang]["hunter"]
        else:
            n = L[lang]["fullnames"][f.month - 1]
        if seen.get(f.month):
            n = L[lang]["blue"]
        seen[f.month] = True
        names.append((f, n))
    return names


# ------------------------------------------------------------------ ritning
def moon_glyph(c, x, y, r, frac, waxing, south=False):
    """Månens form: belyst del ljus, resten mörk. Norra halvklotet: växande = höger sida ljus."""
    c.setFillColorRGB(0.16, 0.19, 0.28); c.circle(x, y, r, stroke=0, fill=1)
    right = waxing != south
    k = 1 - 2 * frac  # terminatorns x-halvaxel (i radier): +1 nymåne, -1 fullmåne
    p = c.beginPath()
    n = 40
    s = 1 if right else -1
    pts = [(x + s * r * math.sin(math.pi * i / n), y + r * math.cos(math.pi * i / n)) for i in range(n + 1)]
    pts += [(x + s * k * r * math.sin(math.pi * (n - i) / n), y + r * math.cos(math.pi * (n - i) / n)) for i in range(n + 1)]
    p.moveTo(*pts[0])
    for q in pts[1:]:
        p.lineTo(*q)
    p.close()
    c.setFillColorRGB(0.97, 0.95, 0.86); c.drawPath(p, stroke=0, fill=1)


def fmt_hm(iso, tz):
    d = datetime.fromisoformat(iso).astimezone(tz)
    d = (d + timedelta(seconds=30)).replace(second=0, microsecond=0)
    return d.strftime("%H:%M")


def hm_len(sec, lang):
    m = int(round(sec / 60))
    return L[lang]["hm"].format(h=m // 60, m=m % 60)


def render(order, data, lang, path):
    lx = L[lang]
    tz = ZoneInfo(order["timezone"])
    W, H = PAGE
    c = canvas.Canvas(str(path), pagesize=PAGE, invariant=1, initialFontName="Sans", initialFontSize=10)
    c.setTitle(f"{lx['title'].format(y=YEAR)} – {order['place']}"); c.setAuthor(GENERATOR_VERSION)
    M = 14 * mm
    names = full_moon_names(data["phases"], tz, lang)
    geo = {"glyphs": {}}
    shown_phase = {}
    for p in data["phases"]:
        iso = p["utc"]
        if FEL == "manfas_tid_fel" and p["phase"] == 2:
            iso = (datetime.fromisoformat(iso) + timedelta(minutes=10)).isoformat()
        shown_phase[p["utc"]] = iso
    south = order["lat"] < 0

    def bg():
        c.setFillColorRGB(*BG); c.rect(0, 0, W, H, stroke=0, fill=1)

    def credit():
        c.setFont("Sans", 5.8); c.setFillColorRGB(*MUTE)
        yy = 10.5 * mm
        for ln in F.wrap(lx["credit"], "Sans", 5.8, W - 2 * M):
            c.drawString(M, yy, ln); yy -= 7

    # ---------- omslag
    bg()
    c.setFillColorRGB(*GOLD); c.setFont("Sans", 10); c.drawString(M, H - 22 * mm, f"{order['place'].upper()}")
    c.setFillColorRGB(*INK); c.setFont("Serif", 34); c.drawString(M, H - 38 * mm, lx["title"].format(y=YEAR))
    c.setFont("SerifIt", 15); c.drawString(M, H - 48 * mm, lx["sub"].format(place=order["place"]))
    if order.get("text"):
        c.setFont("Sans", F.fit_font(order["text"], "Sans", 12, 150 * mm)); c.setFillColorRGB(*GOLD)
        c.drawString(M, H - 57 * mm, order["text"])
    lat, lon = order["lat"], order["lon"]
    c.setFont("Sans", 9); c.setFillColorRGB(*MUTE)
    c.drawString(M, H - 64 * mm, f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}  ·  {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}  ·  {order['timezone']}")
    # tre kolumner
    colw = (W - 2 * M - 16 * mm) / 3
    xs = [M, M + colw + 8 * mm, M + 2 * colw + 16 * mm]
    ytop = H - 78 * mm
    c.setFont("Serif", 13); c.setFillColorRGB(*INK)
    c.drawString(xs[0], ytop, lx["cover_moons"].format(y=YEAR))
    yy = ytop - 7 * mm
    for f, n in names:
        c.setFont("Sans", 8.6); c.setFillColorRGB(*INK)
        c.drawString(xs[0], yy, f"{f.day} {lx['months'][f.month - 1][:3]}  {fmt_hm(shown_phase.get(f.astimezone(timezone.utc).isoformat(), f.isoformat()), tz)}")
        c.setFillColorRGB(*GOLD); c.drawString(xs[0] + 27 * mm, yy, n)
        yy -= 5.2 * mm
    c.setFont("Serif", 13); c.setFillColorRGB(*INK); c.drawString(xs[1], ytop, lx["cover_met"])
    yy = ytop - 7 * mm
    for m in data["meteors"]:
        ns = date.fromisoformat(m["night_start"])
        c.setFont("Sans", 8.6); c.setFillColorRGB(*INK)
        c.drawString(xs[1], yy, f"{ns.day}/{ns.month}  {lx['meteors'][m['code']]}")
        c.setFillColorRGB(*MUTE)
        c.drawRightString(xs[1] + colw, yy, f"ZHR {m['zhr']} · {round(m['moon_frac'] * 100)} %")
        yy -= 5.2 * mm
    c.setFont("Serif", 13); c.setFillColorRGB(*INK); c.drawString(xs[2], ytop, lx["cover_ecl"])
    yy = ytop - 7 * mm
    anyecl = False
    for e in _eclipse_lines(data, lx, tz, lang):
        if not e["visible"]:
            continue
        anyecl = True
        c.setFont("Sans", 8.6); c.setFillColorRGB(*RED); c.drawString(xs[2], yy, e["date_label"] + "  " + e["title"])
        yy -= 4.4 * mm
        c.setFillColorRGB(*INK); c.drawString(xs[2] + 3 * mm, yy, e["detail"]); yy -= 6.5 * mm
    if not anyecl:
        c.setFont("Sans", 8.6); c.setFillColorRGB(*INK)
        for ln in F.wrap(lx["none_ecl"].format(y=YEAR), "Sans", 8.6, colw):
            c.drawString(xs[2], yy, ln); yy -= 4.4 * mm
    credit(); c.showPage()

    # ---------- månader
    ecl = _eclipse_lines(data, lx, tz, lang)
    for m in range(1, 13):
        bg()
        c.setFillColorRGB(*INK); c.setFont("Serif", 26); c.drawString(M, H - 20 * mm, f"{lx['months'][m - 1].capitalize()} {YEAR}")
        c.setFont("SerifIt", 11); c.setFillColorRGB(*GOLD)
        c.drawRightString(W - M, H - 18 * mm, lx["sub"].format(place=order["place"]))
        gx0, gx1 = M, 200 * mm
        gtop, gbot = H - 32 * mm, 22 * mm
        cw = (gx1 - gx0) / 7
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(YEAR, m)
        ch = (gtop - 5 * mm - gbot) / len(weeks)
        c.setFont("Sans", 8); c.setFillColorRGB(*MUTE)
        for i, wd in enumerate(lx["wd"]):
            c.drawString(gx0 + i * cw + 1.5 * mm, gtop - 3.5 * mm, wd)
        for wi, week in enumerate(weeks):
            for di, dnum in enumerate(week):
                if not dnum:
                    continue
                x, ytop_c = gx0 + di * cw, gtop - 5 * mm - wi * ch
                c.setFillColorRGB(*CELL_BG); c.rect(x + 0.6, ytop_c - ch + 0.6, cw - 1.2, ch - 1.2, stroke=0, fill=1)
                dd = date(YEAR, m, dnum)
                rec = data["days"][dd.timetuple().tm_yday - 1]
                c.setFillColorRGB(*INK); c.setFont("Serif", 13); c.drawString(x + 2 * mm, ytop_c - 5.6 * mm, str(dnum))
                gr = 2.6 * mm
                gxm, gym = x + cw - 2 * mm - gr, ytop_c - 2 * mm - gr
                moon_glyph(c, gxm, gym, gr, rec["moon_frac"], rec["waxing"], south)
                geo["glyphs"][rec["date"]] = [gxm, gym, gr]
                ly = ytop_c - 10.5 * mm
                # fas
                for p in data["phases"]:
                    dl = datetime.fromisoformat(shown_phase[p["utc"]]).astimezone(tz)
                    if dl.date() == dd:
                        c.setFont("Sans", 6.6); c.setFillColorRGB(*GOLD)
                        c.drawString(x + 2 * mm, ly, f"{lx['phases'][p['phase']]} {fmt_hm(shown_phase[p['utc']], tz)}")
                        ly -= 3.2 * mm
                for mt in data["meteors"]:
                    if mt["night_start"] == rec["date"]:
                        c.setFont("Sans", 6.3); c.setFillColorRGB(0.65, 0.85, 1.0)
                        c.drawString(x + 2 * mm, ly, lx["meteors"][mt["code"]][:22]); ly -= 3.2 * mm
                for e in ecl:
                    if e["local_date"] == rec["date"] and e["visible"]:
                        c.setFont("Sans", 6.3); c.setFillColorRGB(*RED)
                        c.drawString(x + 2 * mm, ly, e["cell"]); ly -= 3.2 * mm
                # sol upp–ned
                c.setFont("Sans", 6.6); c.setFillColorRGB(*MUTE)
                if rec["rise"] and rec["set"]:
                    s_ = f"{fmt_hm(rec['rise'], tz)}–{fmt_hm(rec['set'], tz)}"
                elif rec["rise"]:
                    s_ = f"{fmt_hm(rec['rise'], tz)}–"
                elif rec["set"]:
                    s_ = f"–{fmt_hm(rec['set'], tz)}"
                else:
                    s_ = "—"
                if FEL == "sol_fel" and dnum == 15:
                    hh = datetime.fromisoformat(rec["rise"]).astimezone(tz) + timedelta(minutes=5) if rec["rise"] else None
                    s_ = f"{hh.strftime('%H:%M')}–{fmt_hm(rec['set'], tz)}" if hh and rec["set"] else s_
                c.drawString(x + 2 * mm, ytop_c - ch + 2.4 * mm, s_)
        # sidopanel
        sx, sw = 206 * mm, W - M - 206 * mm
        y = H - 34 * mm

        def head(t):
            nonlocal y
            c.setFont("Serif", 11.5); c.setFillColorRGB(*INK); c.drawString(sx, y, t); y -= 5 * mm

        def line(t, col=INK, size=8.2):
            nonlocal y
            for ln in F.wrap(t, "Sans", size, sw):
                c.setFont("Sans", size); c.setFillColorRGB(*col); c.drawString(sx, y, ln); y -= 3.9 * mm

        head(lx["moon"])
        for p in data["phases"]:
            dl = datetime.fromisoformat(shown_phase[p["utc"]]).astimezone(tz)
            if dl.month == m:
                line(f"{dl.day} {lx['months'][m - 1][:3]}  {fmt_hm(shown_phase[p['utc']], tz)}  {lx['phases'][p['phase']]}")
        fn = [n for f, n in names if f.month == m]
        if fn:
            y -= 1.5 * mm; head(lx["fullname"]); line(" · ".join(fn), GOLD)
        mets = [mt for mt in data["meteors"] if date.fromisoformat(mt["night_start"]).month == m]
        if mets:
            y -= 1.5 * mm; head(lx["met"])
            for mt in mets:
                a = date.fromisoformat(mt["night_start"]); b = a + timedelta(days=1)
                line(lx["meteors"][mt["code"]], (0.65, 0.85, 1.0))
                if mt["radiant_never_rises"]:
                    line(lx["never"], MUTE, 7.6)
                else:
                    mm_ = lx["months"][b.month - 1]
                    line(f"{lx['night'].format(a=a.day, b=b.day, m=mm_)} · {lx['zhr'].format(z=mt['zhr'])} · "
                         f"{lx['moonlit'].format(p=round(mt['moon_frac'] * 100))}", MUTE, 7.6)
        es = [e for e in ecl if int(e["local_date"][5:7]) == m]
        if es:
            y -= 1.5 * mm; head(lx["ecl"])
            for e in es:
                line(f"{int(e['local_date'][8:10])} {lx['months'][m - 1][:3]}  {e['title']}", RED)
                line(e["detail"], MUTE, 7.6)
        y -= 1.5 * mm; head(lx["pl"])
        pl = data["planets"][str(m)]
        if pl["light_nights"]:
            line(lx["light_nights"], MUTE, 7.6)
        for p in PLANETS:
            ks = pl["visible"][p]
            if not ks:
                word = lx["vis"]["none"]
            elif ks == ["E", "N", "M"]:
                word = lx["vis"]["all"]
            else:
                word = ", ".join(lx["vis"][k] for k in ks)
            if ks:
                az = pl["instants"][ks[0]]["planets"][p][1]
                word += f" ({F.DIRS[lang][F.compass8(az)]})"
            line(f"{lx['planets'][p]}: {word}", INK if ks else MUTE, 7.8)
        # dagsljus
        first, last = data["days"][date(YEAR, m, 1).timetuple().tm_yday - 1], data["days"][date(YEAR, m, calendar.monthrange(YEAR, m)[1]).timetuple().tm_yday - 1]

        def dlen(r):
            if r["rise"] and r["set"]:
                return hm_len((datetime.fromisoformat(r["set"]) - datetime.fromisoformat(r["rise"])).total_seconds(), lang)
            return lx["polar_day"] if r.get("polar") == "day" else lx["polar_night"] if r.get("polar") == "night" else "—"
        y -= 1.5 * mm; head(lx["daylight"])
        line(lx["dl"].format(d1=f"1/{m}", h1=dlen(first), d2=f"{calendar.monthrange(YEAR, m)[1]}/{m}", h2=dlen(last)), INK, 7.8)
        # förklaring
        tzn = sorted({datetime(YEAR, m, 1, 12, tzinfo=tz).tzname(), datetime(YEAR, m, 28, 12, tzinfo=tz).tzname()})
        c.setFont("Sans", 6.8); c.setFillColorRGB(*MUTE)
        c.drawString(M, gbot - 4 * mm, lx["legend"].format(tz="/".join(tzn)))
        credit(); c.showPage()
    c.save()
    return geo


def _eclipse_lines(data, lx, tz, lang):
    out = []
    for e in data["lunar"]:
        d = datetime.fromisoformat(e["utc"]).astimezone(tz)
        t = fmt_hm(e["utc"], tz)
        out.append({"local_date": d.date().isoformat(), "visible": e["visible"], "title": lx["lunar"][e["kind"]],
                    "detail": (lx["lunar_vis"] if e["visible"] else lx["lunar_novis"]).format(t=t),
                    "cell": lx["ecl_cell_l"], "date_label": f"{d.day} {lx['months'][d.month - 1][:3]}"})
    for e in data["solar"]:
        if e["type"] == "none" or not e["max_utc"]:
            continue
        d = datetime.fromisoformat(e["max_utc"]).astimezone(tz)
        p = e["obscuration"] * 100
        ps = f"{p:.0f}" if p >= 99.95 or p < 10 else f"{p:.0f}"
        typ = e["type"]
        out.append({"local_date": d.date().isoformat(), "visible": e["visible"], "title": lx["solar"][typ],
                    "detail": lx["solar_vis"].format(t=fmt_hm(e["max_utc"], tz), p=ps),
                    "cell": lx["ecl_cell_s"], "date_label": f"{d.day} {lx['months'][d.month - 1][:3]}"})
    out.sort(key=lambda e: e["local_date"])
    return out


def generate(order_path):
    timings = {}
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    sky = F.Sky()
    timings["ladda_data_s"] = time.perf_counter() - t0
    t1 = time.perf_counter()
    data = compute(order, sky)
    timings["berakna_s"] = time.perf_counter() - t1
    out = Path(os.environ.get("STJARN_OUT", F.ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, geos = {}, {}
    t2 = time.perf_counter()
    for lang in order["languages"]:
        path = out / f"{order['id']}_{lang}.pdf"
        geos[lang] = render(order, data, lang, path)
        files[lang] = str(path)
    timings["rendera_s"] = time.perf_counter() - t2
    meta = {"generator": GENERATOR_VERSION, "product": "himmelskalender", "year": YEAR, "order": order, "files": files,
            "data": data, "geometry": geos, "timings": timings,
            "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"]}, indent=1))
