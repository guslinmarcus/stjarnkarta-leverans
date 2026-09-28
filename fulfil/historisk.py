"""Generator: "Din adress genom tiden" – samma utsnitt ur tre historiska kartserier och dagens karta.

PDF: sida 1 = affisch A3 (fyra kartor i årtalsordning), sida 2–5 = guide A4 (så läser du kartorna,
närbilder ur Häradsekonomiska och Ekonomiska kartan, dagens karta, källor och passning).

Data (se lmkartor.py och osmdata.py för villkoren):
  * Lantmäteriets öppna FTP: Häradsekonomiska kartan, Generalstabskartan, Ekonomiska kartan (CC0).
  * OpenStreetMap ur Geofabrik-extrakt, bearbetat lokalt med pyosmium (ODbL, "© OpenStreetMap contributors") –
    dagens karta och vattnet för passningen.
  * Adress → koordinat: OSM-adresserna (addr:*) i samma Geofabrik-extrakt, lokalt; orten ur GeoNames (fulfil.py).
Utsnittets storlek bestäms av Generalstabskartans upplösning: kartrutan ska få ≥ 200 dpi ur originalskanningen.

Körning: python historisk.py order.json -> <ut>/<id>_<språk>.pdf + <ut>/<id>_meta.json
Felinjektion (bara tester): FELINJEKTION=fel_blad|forskjutning|lag_upplosning|saknad_kallhanvisning|fel_artal|tom_panel|fel_adress
                             |skarv|tom_yta|avklippt_etikett

Version 0.2 (2026-09-28):
  * Kartbladen i varje serie färgjusteras mot varandra (lmkartor.harmonize, kvantilmatchning i ett band längs
    skarven). Kurvorna sparas i meta så att grinden kan kontrollera georefereringen mot originalet.
  * Ekonomiska kartan används på affischen bara om utsnittet täcks helt och skarvarna efter justeringen är
    små (ett blad, eller kvarvarande färgskillnad ≤ EK_SEAM_MAX längs varje skarv). Annars visas en andra, större
    närbild av Häradsekonomiska kartan (HEK_CLOSE_M) i den rutan, och Ekonomiska kartan visas i guiden som
    närbild ur ETT blad (fönstret flyttas/krymps så att det ryms i bladet), eller inte alls.
  * Dagens karta ritas i de gamla kartornas färgvärld: vatten med strandlinje, åker, äng, skog, bebyggelse,
    byggnader, vägar i tre nivåer med kant och ortnamn (etiketter som inte får plats utelämnas, aldrig klipps).
  * Etiketter och bildtexter anpassas till rutans bredd (mindre text, kortare årtalsformat) – aldrig avhuggna.
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # numpy/scipy: annars ≈ 700 MB privat minne i trådbuffertar (12 kärnor)
import hashlib
import io
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import kartgeo as K
import kartlayout as KL
import lmkartor as L
import osmdata as O
import passning as PS

GENERATOR_VERSION = "historisk/0.2.0"
FEL = os.environ.get("FELINJEKTION", "")
ROOT = Path(__file__).parent
A3 = (297 * mm, 420 * mm)
A4 = (210 * mm, 297 * mm)
PANEL_MM = 124.0
OUT_DPI = 300
MIN_SRC_DPI = 200
DETAIL_M = 3000.0
DETAIL_MM = 170.0
HEK_CLOSE_M = 2500.0  # affischens närbild av Häradsekonomiska kartan när Ekonomiska kartan inte kan användas
GSK_CLOSE_M = 12000.0  # affischens närbild av Generalstabskartan: bladen är ≈ 10 m/px i skanningen (1:100 000), så fönstret
# måste vara stort nog för ≥ 200 dpi källupplösning vid tryck (se MIN_SRC_DPI) – ett GSK-blad täcker ≈ 60×46 km, gott om marginal
EK_SEAM_MAX = 25.0    # största kvarvarande färgskarv (papperston, lmkartor.seam_steps) längs en skarv i Ekonomiska kartan
EK_COVER_MIN = 0.995
HEK_SEAM_MAX = 30.0   # Häradsekonomiska kartans huvudruta: större kvarvarande skarv → affischen visar närbilder i stället
GSK_SEAM_MAX = 30.0   # Generalstabskartans huvudruta: större kvarvarande skarv (enstaka bladpar med stor tonskillnad) → närbild
EK_WIN = (3000.0, 2500.0, 2000.0, 1500.0)  # guidens närbild ur ett EK-blad (≥ 1500 m ger ≥ 200 dpi på 170 mm)
PASS_CAP = {"hek": 600.0, "gsk": 500.0, "ek": 60.0}  # största justering vi tillåter per serie
# Osäkerhet som trycks när passningen inte kunde mätas. Uppmätt i testordrarna: Häradsekonomiska 30–55 m på
# de flesta blad men ≈ 500 m på ett (Ystad); Generalstabskartan ≈ 60 m efter att kartytans innerlinje används
# (300–850 m med ytterramen). Övre gränsen kontrolleras av grinden (deklaration + tryckt text).
TYPICAL = {"hek": "±50–500 m", "gsk": "±50–400 m", "ek": "±5–40 m"}  # visas bara när passningen inte gick att mäta
PAPER = (0.972, 0.955, 0.915)
INK = (0.16, 0.13, 0.10)
MUTE = (0.42, 0.38, 0.33)
RED = (0.72, 0.12, 0.10)

TXT = {
    "sv": {
        "series": {"hek": "Häradsekonomiska kartan", "gsk": "Generalstabskartan", "ek": "Ekonomiska kartan", "osm": "Dagens karta"},
        "today": "idag", "sheet": "blad", "sheets": "blad", "scale": "skala",
        "missing": "{s} finns inte för den här platsen.",
        "excluded": "{s} finns här, men skanningen av bladet stämmer inte med Lantmäteriets bladindex och kan inte passas in säkert. Därför visas den inte.",
        "sub": "Samma plats genom tiden · {a}",
        "closeup_label": "{y} · Häradsekonomiska kartan, närbild",
        "closeup_label_gsk": "{y} · Generalstabskartan, närbild",
        "closeup_sub": "{km} × {km} km kring adressen · {sk}",
        "ek_note": ("Ekonomiska kartan finns här, men utsnittet består av {n} kartblad från olika år som inte går att foga "
                    "ihop utan synliga skarvar{tom}. Därför visar affischen i stället en närbild av Häradsekonomiska kartan{g}."),
        "ek_note_tom": " och med en tom yta där ett blad saknas",
        "hek_note": ("Häradsekonomiska kartan finns här, men utsnittet består av {n} kartblad i olika utföranden som inte går att "
                     "foga ihop utan synliga skarvar. Därför visar affischen den som närbild kring adressen."),
        "gsk_note": ("Generalstabskartan finns här, men utsnittet består av {n} kartblad med en tonskillnad som inte går att "
                     "justera bort. Därför visar affischen den som närbild kring adressen."),
        "ek_note_guide": ", och Ekonomiska kartan visas som närbild ur ett enda kartblad i guiden",
        "ek_note_none": "",
        "scalebar": "{km} km", "north": "N",
        "credit_lm": "Historiska kartor: © Lantmäteriet, fria enligt CC0 (Lantmäteriets öppna data). {blad}.",
        "credit_osm": "Dagens karta: © OpenStreetMap contributors, data under Open Database License (ODbL) 1.0, openstreetmap.org/copyright. OSM-data per {ts}.",
        "credit_geo": "Adressen slogs upp i OpenStreetMaps adressdata (© OpenStreetMap contributors, ODbL, via Geofabrik), orten i GeoNames (CC BY 4.0).",
        "credit_fit": "Lantmäteriet anger att georeferensen för de gamla kartorna är ”inte exakta utan mer generella”. Vi har mätt passningen mot dagens stränder – se guiden.",
        "guide_title": "Så läser du dina kartor",
        "guide_intro": ("Affischen visar samma kvadrat, {km} × {km} km, på kartor från olika tider{extra}. Den röda ringen är din adress: "
                        "{addr} ({lat:.5f}° N, {lon:.5f}° O; SWEREF 99 TM {e:.0f} / {n:.0f})."),
        "about": {
            "gsk": "Generalstabskartan är Sveriges första rikstäckande topografiska karta, ritad av arméns lantmätare i skala 1:100 000. Den visar vägar, gårdar, kyrkor, vatten och terrängen med streck (backar). Den användes för militär planering.",
            "hek": "Häradsekonomiska kartan ritades i skala 1:20 000 för att beskatta och planera jordbruket. Den visar varje gård, åker (gul), äng och betesmark, skog och ägogränser. Den är den mest detaljerade bilden av landsbygden före industrialiseringen.",
            "ek": "Ekonomiska kartan byggde på flygbilder och visar fastigheter, åkrar, skog och byggnader i skala 1:10 000 eller 1:20 000. Den gjordes för jord- och skogsbruk och visar landskapet just före och under efterkrigstidens stora förändringar.",
            "osm": "Dagens karta är ritad av oss ur OpenStreetMap, en fri världskarta som byggs av frivilliga, i de gamla kartornas färger: vatten, åkrar, skog, bebyggelse, byggnader, vägar och ortnamn så som de ser ut i dag.",
        },
        "street_only": " Husnumret finns inte i OpenStreetMap, så ringen visar gatans mitt.",
        "fit_head": "Passning – hur väl kartorna ligger på varandra",
        "fit_ok": "{s}: justerad {d:.0f} m mot dagens stränder; kvarvarande avvikelse {r:.0f} m.",
        "fit_ok_small": "{s}: låg rätt från början (kvarvarande avvikelse {r:.0f} m).",
        "fit_na": "{s}: kunde inte mätas här ({why}). Räkna med en osäkerhet på {typ}.",
        "fit_note": ("Mätt så här: dagens vatten i OpenStreetMap jämfördes med vattnet på varje gammal karta, och kartan flyttades "
                     "tills stränderna låg på varandra. Stränder har ändrats sedan 1800-talet (landhöjning, utfyllnad), så små "
                     "skillnader är väntade. Mätningen görs automatiskt och kontrolleras av ett separat program. På de gamla "
                     "kartorna kan den röda ringen därför ligga så långt från den verkliga platsen som osäkerheten ovan anger."),
        "detail": "Närbild {km} × {km} km",
        "extra_close": " och en närbild, {km} × {km} km, ur Häradsekonomiska kartan",
        "extra_close_gsk": " och en närbild, {km} × {km} km, ur Generalstabskartan",
        "sources": "Källor och licenser",
        "year_note": "Årtalen är kartbladens tryck- eller karteringsår enligt Lantmäteriets bladindex.",
        "scan_note": "Kartorna är skanningar av originalen: fläckar, veck och handskrivna tillägg kommer från originalbladen.",
    },
    "en": {
        "series": {"hek": "Economic map of the hundreds", "gsk": "General Staff map", "ek": "Economic map", "osm": "Today's map"},
        "today": "today", "sheet": "sheet", "sheets": "sheets", "scale": "scale",
        "missing": "The {s} does not exist for this place.",
        "excluded": "The {s} exists here, but the scan of the sheet does not match Lantmäteriet's sheet index and cannot be fitted reliably, so it is not shown.",
        "sub": "The same place through time · {a}",
        "closeup_label": "{y} · Economic map of the hundreds, close-up",
        "closeup_label_gsk": "{y} · General Staff map, close-up",
        "closeup_sub": "{km} × {km} km around the address · {sk}",
        "ek_note": ("The Economic map exists here, but the view is made of {n} sheets from different years that cannot be joined "
                    "without visible seams{tom}. The poster therefore shows a close-up of the Economic map of the hundreds instead{g}."),
        "ek_note_tom": " and has an empty area where a sheet is missing",
        "hek_note": ("The Economic map of the hundreds exists here, but the view is made of {n} sheets in different styles that cannot "
                     "be joined without visible seams. The poster therefore shows it as a close-up around the address."),
        "gsk_note": ("The General Staff map exists here, but the view is made of {n} sheets with a tone difference that cannot be "
                     "adjusted away. The poster therefore shows it as a close-up around the address."),
        "ek_note_guide": ", and the Economic map is shown as a close-up from a single sheet in the guide",
        "ek_note_none": "",
        "scalebar": "{km} km", "north": "N",
        "credit_lm": "Historical maps: © Lantmäteriet (Swedish mapping authority), free under CC0 (open data). {blad}.",
        "credit_osm": "Today's map: © OpenStreetMap contributors, data under the Open Database License (ODbL) 1.0, openstreetmap.org/copyright. OSM data as of {ts}.",
        "credit_geo": "Address lookup: OpenStreetMap address data (© OpenStreetMap contributors, ODbL, via Geofabrik); town: GeoNames (CC BY 4.0).",
        "credit_fit": "Lantmäteriet states that the georeferencing of the old maps is “not exact but more general”. We measured the fit against today's shorelines – see the guide.",
        "guide_title": "How to read your maps",
        "guide_intro": ("The poster shows the same square, {km} × {km} km, on maps from different times{extra}. The red ring is your address: "
                        "{addr} ({lat:.5f}° N, {lon:.5f}° E; SWEREF 99 TM {e:.0f} / {n:.0f})."),
        "about": {
            "gsk": "The General Staff map (Generalstabskartan) was Sweden's first nationwide topographic map, drawn by army surveyors at 1:100,000. It shows roads, farms, churches, water and the terrain as hachures. It was made for military planning.",
            "hek": "The Economic map of the hundreds (Häradsekonomiska kartan) was drawn at 1:20,000 to tax and plan farming. It shows every farm, field (yellow), meadow and pasture, forest and property boundary – the most detailed picture of the countryside before industrialisation.",
            "ek": "The Economic map (Ekonomiska kartan) was based on aerial photographs and shows properties, fields, forest and buildings at 1:10,000 or 1:20,000. It was made for farming and forestry and shows the landscape just before and during the great post-war changes.",
            "osm": "Today's map is drawn by us from OpenStreetMap, a free world map built by volunteers, in the colours of the old maps: water, fields, forest, built-up areas, buildings, roads and place names as they are today.",
        },
        "street_only": " The house number is not in OpenStreetMap, so the ring shows the middle of the street.",
        "fit_head": "Fit – how well the maps line up",
        "fit_ok": "{s}: adjusted {d:.0f} m to today's shorelines; remaining offset {r:.0f} m.",
        "fit_ok_small": "{s}: was in place from the start (remaining offset {r:.0f} m).",
        "fit_na": "{s}: could not be measured here ({why}). Expect an uncertainty of {typ}.",
        "fit_note": ("How we measured: today's water in OpenStreetMap was compared with the water on each old map, and the map was moved "
                     "until the shorelines matched. Shorelines have changed since the 1800s (land uplift, infilling), so small "
                     "differences are expected. The measurement is automatic and checked by a separate program. On the old "
                     "maps the red ring can therefore be as far from the true spot as the uncertainty above."),
        "detail": "Close-up {km} × {km} km",
        "extra_close": " and a close-up, {km} × {km} km, of the Economic map of the hundreds",
        "extra_close_gsk": " and a close-up, {km} × {km} km, of the General Staff map",
        "sources": "Sources and licences",
        "year_note": "Years are the sheets' survey or print years according to Lantmäteriet's sheet index.",
        "scan_note": "The maps are scans of the originals: stains, folds and handwritten additions come from the original sheets.",
    },
}
WHY = {"sv": {"few": "för lite vatten i utsnittet", "disagree": "mätmetoderna gav olika svar"},
       "en": {"few": "too little water in the view", "disagree": "the measuring methods disagreed"}}


class OrderError(SystemExit):
    pass


def _norm(s):
    import unicodedata
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)).replace(".", " ").split())


def split_address(addr):
    """'Stora Östergatan 20 B' -> ('stora ostergatan', '20b'); utan nummer -> (gata, '')."""
    import re
    m = re.match(r"^(.*?)[\s,]+(\d+\s*[a-zA-Z]?)\s*$", addr.strip())
    if m:
        return _norm(m.group(1)), m.group(2).replace(" ", "").lower()
    return _norm(addr), ""


def local_radius_m(pop):
    return 20000 if pop >= 500000 else 10000 if pop >= 50000 else 4000


def geocode(order):
    """(lat, lon, info). Adressen slås upp i OpenStreetMap-adresserna i Geofabrik-extraktet (lokalt, ingen
    extern tjänst). Utan adress = ortens koordinat (GeoNames, satt av fulfil.py)."""
    lat0, lon0 = order["lat"], order["lon"]
    addr = (order.get("address") or "").strip()
    if not addr:
        return lat0, lon0, {"metod": "GeoNames ort", "text": order["place"], "avstand_till_ort_m": 0.0, "precision": "ort"}
    data, key = O.addresses(lat0, lon0)
    street, nr = split_address(addr)
    # bara adresser i orten: adressens postort = orten, eller inom ortens radie (större ort → större radie).
    # Annars kan samma gatunamn i en grannkommun väljas (test: "Hamngatan 5, Vaxholm" blev Sundbyberg).
    R = local_radius_m(order.get("pop", 0))
    place = _norm(order["place"])
    local = lambda a: _norm(a.get("ort", "")) == place or K.haversine_m(lat0, lon0, a["lat"], a["lon"]) <= R
    hits = [a for a in data["adresser"] if _norm(a["gata"]) == street and (not nr or a["nr"].replace(" ", "").lower() == nr) and local(a)]
    precision = "hus" if nr and hits else None
    if not hits:
        hits = [dict(g, gata=name, nr="") for name, lst in data["gator"].items() if _norm(name) == street for g in lst
                if K.haversine_m(lat0, lon0, g["lat"], g["lon"]) <= R]
        precision = "gata" if hits else None
    if not hits:
        raise OrderError("address_not_found")
    best = min(hits, key=lambda a: K.haversine_m(lat0, lon0, a["lat"], a["lon"]))
    if precision == "gata":  # gatans mitt: medelpunkten av gatans delar närmast orten (inom 3 km från den närmaste)
        near = [a for a in hits if K.haversine_m(best["lat"], best["lon"], a["lat"], a["lon"]) < 3000]
        best = dict(best, lat=sum(a["lat"] for a in near) / len(near), lon=sum(a["lon"] for a in near) / len(near))
    d = K.haversine_m(lat0, lon0, best["lat"], best["lon"])
    return best["lat"], best["lon"], {"ortradie_m": R, "metod": "OpenStreetMap-adresser (Geofabrik-extrakt)", "precision": precision,
                                      "text": f"{best['gata']} {best['nr']}".strip() + (f", {best['ort']}" if best.get("ort") else ""),
                                      "osm": best.get("osm"), "adresskalla": key, "avstand_till_ort_m": round(d, 1)}


def fmt_years(sheets, lang="sv"):
    ys = []
    for s in sheets:
        y = s.b["ar"].replace("-", "–") or ("år okänt" if lang == "sv" else "year unknown")
        if y not in ys:
            ys.append(y)
    return " / ".join(ys)


def first_year(sheets):
    try:
        return min(int(s.b["ar"][:4]) for s in sheets)
    except ValueError:
        return 9999


def square_latlon(e0, n0, e1, n1, margin=0.03):
    m = (e1 - e0) * margin
    es, ns = np.array([e0 - m, e1 + m, e0 - m, e1 + m]), np.array([n0 - m, n0 - m, n1 + m, n1 + m])
    la, lo = K.from_sweref(es, ns)
    return float(la.min()), float(lo.min()), float(la.max()), float(lo.max())


def compute(order):
    t = {}
    t0 = time.perf_counter()
    lat, lon, gi = geocode(order)
    if FEL == "fel_adress":
        lat += 0.027  # ≈ 3 km norrut
    E, N = map(float, K.to_sweref(lat, lon))
    t["geokodning_s"] = time.perf_counter() - t0
    if not (250000 < E < 1000000 and 6100000 < N < 7700000):
        raise OrderError("not_covered")
    center = {s: L.sheet_for_point(E, N, s) for s in ("gsk", "hek", "ek")}
    if not center["gsk"] or sum(bool(center[s]) for s in ("hek", "ek")) < 1:
        raise OrderError("not_covered")
    # utsnittet: Generalstabskartans upplösning styr (≥ 200 dpi ur skanningen i kartrutan)
    dl = {"s": 0.0}
    sheets = {}

    def load(b):
        p, dt = L.get_sheet(b)
        dl["s"] += dt
        return L.Sheet(b, p)
    t1 = time.perf_counter()
    try:
        g0 = load(center["gsk"][0])
    except L.SheetInvalid:  # Generalstabskartans blad går inte att passa in → hela produkten saknar sin äldsta karta
        raise OrderError("not_covered")
    need_px = PANEL_MM / 25.4 * MIN_SRC_DPI
    D = math.ceil(g0.px * need_px * 1.03 / 100) * 100
    D = min(max(D, 4000), 14000)
    if FEL == "lag_upplosning":
        D = 3000
    # utsnittets mitt: adressen, men flytta upp till 30 % av sidan om de historiska kartorna då täcker mer
    # (t.ex. vid kusten, där bladen slutar vid strandlinjen). Adressen ligger alltid inom de mittersta 60 %.
    def score(ce, cn):
        tot = 0.0
        for s in ("hek", "ek"):
            tot += min(1.0, sum(f for _, f in L.sheets_for_square(ce - D / 2, cn - D / 2, ce + D / 2, cn + D / 2, s, samples=9)))
        return tot
    base = score(E, N)
    best = (base, 0.0, 0.0)
    for fx in (-0.3, -0.15, 0.0, 0.15, 0.3):
        for fy in (-0.3, -0.15, 0.0, 0.15, 0.3):
            sc = score(E + fx * D, N + fy * D) if (fx or fy) else base
            # flytta bara om täckningen blir klart bättre; små flyttar före stora
            if sc > base + 0.05 and sc - 0.1 * math.hypot(fx, fy) > best[0] - 0.1 * math.hypot(best[1], best[2]):
                best = (sc, fx, fy)
    CE, CN = E + best[1] * D, N + best[2] * D
    e0, n0, e1, n1 = CE - D / 2, CN - D / 2, CE + D / 2, CN + D / 2
    cover_info = {}
    excluded = {}
    for s in ("gsk", "hek", "ek"):
        found = L.sheets_for_square(e0, n0, e1, n1, s)
        # blad som täcker mittpunkten först
        found.sort(key=lambda bf: (not any(bf[0]["blad"] == c["blad"] for c in center[s]), -bf[1]))
        if FEL == "fel_blad" and s == "hek" and found:
            others = [b for b in L.index() if b["serie"] == "hek" and b["blad"] not in {f[0]["blad"] for f in found}]
            bx = found[0][0]["bbox"]
            cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
            others.sort(key=lambda b: ((b["bbox"][0] + b["bbox"][2]) / 2 - cx) ** 2 + ((b["bbox"][1] + b["bbox"][3]) / 2 - cy) ** 2)
            found = [(others[0], found[0][1])]
        lst = []
        for b, frac in found:
            if s == "gsk" and b["blad"] == g0.b["blad"]:
                lst.append(g0); continue
            try:
                why = L.precheck(b)
                if why:
                    raise L.SheetInvalid(why)
                lst.append(load(b))
            except L.SheetInvalid as e:  # skanningen stämmer inte med bladindexet – används inte
                excluded.setdefault(s, []).append({"blad": b["blad"], "skal": str(e), "lokal": str(L.local_file(b)), "path": b["path"]})
            except RuntimeError as e:  # bladet saknas på FTP:n
                cover_info.setdefault(s, []).append({"blad": b["blad"], "fel": str(e)[:200]})
        sheets[s] = lst
    t["hamta_och_georeferera_s"] = time.perf_counter() - t1
    t["varav_ftp_s"] = dl["s"]
    # OSM
    t2 = time.perf_counter()
    s_, w_, n_, e_ = square_latlon(e0, n0, e1, n1)
    osm, ep = O.fetch(O.query_map(s_, w_, n_, e_, minor=True, rich=True))
    F = O.parse_features(osm)
    t["osm_s"] = time.perf_counter() - t2
    to_grid = lambda lon_, lat_: K.to_sweref(lat_, lon_)
    # passning (mätrutnät ~10 m)
    t3 = time.perf_counter()
    nm = int(round(D / 10))
    water = PS.rasterize_water(F, to_grid, e0, n0, e1, n1, nm)
    fit = {}
    for s in ("gsk", "hek", "ek"):
        if not sheets[s]:
            continue
        img, cov, _ = L.resample(sheets[s], e0, n0, e1, n1, nm)
        m1 = PS.measure(img, cov, water, D / nm, max_m=min(1000.0, PASS_CAP[s] * 1.25))
        adj = {"ost_m": 0.0, "nord_m": 0.0}
        if m1.get("matbar") and m1["forskjutning_m"] <= PASS_CAP[s] and m1["forskjutning_m"] > 1.5 * D / nm:
            adj = {"ost_m": m1["ost_m"], "nord_m": m1["nord_m"]}
            for sh in sheets[s]:
                sh.dE, sh.dN = adj["ost_m"], adj["nord_m"]
            img, cov, _ = L.resample(sheets[s], e0, n0, e1, n1, nm)
            m2 = PS.measure(img, cov, water, D / nm, max_m=min(1000.0, PASS_CAP[s] * 1.25))
        else:
            m2 = m1
        fit[s] = {"forsta_matning": m1, "justering": adj, "efter": m2}
    if FEL == "forskjutning":
        for sh in sheets.get("hek") or sheets.get("ek"):
            sh.dE += 1200.0
    t["passning_s"] = time.perf_counter() - t3
    if not any(sheets[s] for s in ("hek", "ek")) or not sheets["gsk"]:
        raise OrderError("not_covered")
    # färgjustering mellan kartbladen + beslut om Ekonomiska kartan (se versionsnoten överst)
    t4 = time.perf_counter()
    harm = {}
    for s in ("gsk", "hek", "ek"):
        if sheets[s] and FEL != "skarv":
            harm[s] = L.harmonize(sheets[s], e0, n0, e1, n1)
    ek_cov = 0.0
    if sheets["ek"]:
        _, cv, _ = L.resample(sheets["ek"], e0, n0, e1, n1, 300)
        ek_cov = float(cv.mean())
    ek_seam = harm.get("ek", {}).get("max_efter", 0.0)
    ek_ok = bool(sheets["ek"]) and ek_cov >= EK_COVER_MIN and (len(sheets["ek"]) == 1 or ek_seam <= EK_SEAM_MAX)
    if FEL == "skarv" and sheets["ek"]:
        ek_ok = True
    win = ek_window(sheets["ek"], E, N) if sheets["ek"] else None
    hek_seam = harm.get("hek", {}).get("max_efter", 0.0)
    hek_ok = bool(sheets["hek"]) and (len(sheets["hek"]) == 1 or hek_seam <= HEK_SEAM_MAX)
    gsk_seam = harm.get("gsk", {}).get("max_efter", 0.0)
    gsk_ok = len(sheets["gsk"]) == 1 or gsk_seam <= GSK_SEAM_MAX  # sheets["gsk"] är alltid icke-tom här (se ovan)
    if FEL == "skarv":
        hek_ok = bool(sheets["hek"])
        gsk_ok = True
    # affischens fyra rutor: Generalstabskartan, Häradsekonomiska och Ekonomiska kartan i hela utsnittet om de går
    # att visa utan synliga skarvar och tomma ytor, annars närbilder ur ett blad; sist dagens karta
    slots = ["gsk"] if gsk_ok else ["gsk_narbild"]  # enstaka bladpar kan ha en tonskillnad kvar som inte går att justera bort
    if not sheets["hek"] or hek_ok:
        slots.append("hek")  # saknas bladet helt ritas en ruta som säger det
    if ek_ok:
        slots.append("ek")
    for cand in (["hek_narbild"] if sheets["hek"] else []) + (["ek_narbild"] if win else []):
        if len(slots) < 3:
            slots.append(cand)
    if len(slots) < 3:
        slots.append("ek")  # ingen annan källa: visas som den är – grindens bildkontroller avgör om den duger
    poster_ek = "ek" if "ek" in slots else ("ek_narbild" if "ek_narbild" in slots else "hek_narbild")
    guide_ek = win or ((E - DETAIL_M / 2, N - DETAIL_M / 2, E + DETAIL_M / 2, N + DETAIL_M / 2) if ek_ok else None)
    ek_beslut = {"tackning": round(ek_cov, 4), "blad": len(sheets["ek"]), "max_skarv_efter_justering": ek_seam,
                 "gransvarden": {"tackning_min": EK_COVER_MIN, "skarv_max": EK_SEAM_MAX}, "anvands_pa_affischen": ek_ok,
                 "affischens_ruta": poster_ek, "affischens_rutor": slots, "guidens_narbild": list(guide_ek) if guide_ek else None,
                 "hek_huvudruta": {"anvands": "hek" in slots and bool(sheets["hek"]), "max_skarv_efter_justering": hek_seam,
                                   "gransvarde": HEK_SEAM_MAX},
                 "gsk_huvudruta": {"anvands": gsk_ok, "max_skarv_efter_justering": gsk_seam, "gransvarde": GSK_SEAM_MAX},
                 "guidens_narbild_ett_blad": bool(win)}
    gsk_f = {"affisch": gsk_window(sheets["gsk"], E, N) or close_window(sheets["gsk"], E, N, GSK_CLOSE_M, "gsk")} if not gsk_ok else None
    hek_f = {"affisch": close_window(sheets["hek"], E, N, HEK_CLOSE_M, "hek"),
             "guide": close_window(sheets["hek"], E, N, DETAIL_M, "hek")} if sheets["hek"] else None
    t["farg_och_ek_s"] = time.perf_counter() - t4
    return {"lat": lat, "lon": lon, "E": E, "N": N, "geokod": gi, "D": D, "square": [e0, n0, e1, n1],
            "mitt_flyttad": [round(best[1] * D, 1), round(best[2] * D, 1)], "uteslutna": excluded, "_img": {},
            "sheets": sheets, "center_sheets": {s: [b["blad"] for b in v] for s, v in center.items()},
            "fit": fit, "harm": harm, "ek_beslut": ek_beslut, "hek_fonster": hek_f, "gsk_fonster": gsk_f, "F": F, "osm_ts": osm.get("osm3s", {}).get("timestamp_osm_base", ""), "osm_endpoint": ep,
            "missing_on_ftp": cover_info, "timings": t}


def ek_window(ek_sheets, E, N):
    """Guidens närbild ur ETT blad av Ekonomiska kartan: största fönster (EK_WIN) som ligger helt i ett av de
    hämtade bladen, med adressen inom fönstrets mittersta 60 %. Kontrolleras först mot bladindexet (snabbt),
    sedan mot den verkliga omsamplingen (täckning och ett enda blad)."""
    names = {sh.b["blad"] for sh in ek_sheets}
    for Dw in EK_WIN:
        for fx in (0.0, 0.2, -0.2, 0.3, -0.3):
            for fy in (0.0, 0.2, -0.2, 0.3, -0.3):
                sq = (E - fx * Dw - Dw / 2, N - fy * Dw - Dw / 2, E - fx * Dw + Dw / 2, N - fy * Dw + Dw / 2)
                f = L.sheets_for_square(*sq, "ek", samples=9)
                if len(f) != 1 or f[0][1] < 1.0 or f[0][0]["blad"] not in names:
                    continue
                one = [sh for sh in ek_sheets if sh.b["blad"] == f[0][0]["blad"]]
                _, cov, andel = L.resample(one, *sq, 150)
                if cov.mean() >= 0.999:
                    return tuple(sq)
    return None


def gsk_window(gsk_sheets, E, N):
    """Affischens närbild ur ETT blad av Generalstabskartan, när huvudutsnittets skarv mot grannbladet inte går
    att justera bort (se GSK_SEAM_MAX): fönstret (GSK_CLOSE_M) ligger helt i ett enda hämtat blad. Bladen är
    stora (≈ 60×46 km) så fönstret får vid behov flyttas långt från adressen för att komma undan skarven –
    annars, om ingen ren placering hittas (adressen nära ett hörn där flera blad möts), används close_window
    (bästa täckning; kan då fortfarande innehålla skarven, som grinden i så fall fäller)."""
    names = {sh.b["blad"] for sh in gsk_sheets}
    D = GSK_CLOSE_M
    offs = (0.0, 0.25, -0.25, 0.5, -0.5, 0.75, -0.75, 1.0, -1.0, 1.5, -1.5)
    for fx in offs:
        for fy in offs:
            sq = (E - fx * D - D / 2, N - fy * D - D / 2, E - fx * D + D / 2, N - fy * D + D / 2)
            f = L.sheets_for_square(*sq, "gsk", samples=9)
            if len(f) != 1 or f[0][1] < 1.0 or f[0][0]["blad"] not in names:
                continue
            one = [sh for sh in gsk_sheets if sh.b["blad"] == f[0][0]["blad"]]
            _, cov, andel = L.resample(one, *sq, 150)
            if cov.mean() >= 0.999:
                return tuple(sq)
    return None


def close_window(sheets, E, N, D, serie):
    """Närbildens ruta (D m) för en serie: kring adressen, men flyttad upp till 30 % av sidan om bladen då täcker
    mer (vid kusten slutar Häradsekonomiska bladen ofta vid stranden och lämnar en tom remsa). Adressen ligger
    alltid inom rutans mittersta 60 %. Små flyttar före stora."""
    names = {sh.b["blad"] for sh in sheets}
    best = None
    for fx in (0.0, 0.15, -0.15, 0.3, -0.3):
        for fy in (0.0, 0.15, -0.15, 0.3, -0.3):
            sq = (E - fx * D - D / 2, N - fy * D - D / 2, E - fx * D + D / 2, N - fy * D + D / 2)
            cov = min(1.0, sum(f for b, f in L.sheets_for_square(*sq, serie, samples=13) if b["blad"] in names))
            sc = cov - 0.02 * math.hypot(fx, fy)
            if best is None or sc > best[0] + 1e-9:
                best = (sc, sq, cov)
    return list(best[1])


def panel_image(sheets, sq, npx, cache=None):
    key = (tuple(sh.b["blad"] for sh in sheets), tuple(sq), npx)
    if cache is not None and key in cache:
        return cache[key]
    r = L.resample(sheets, *sq, npx)
    if cache is not None:
        cache[key] = r
    return r


def draw_marker(c, x, y, r=3.2 * mm):
    c.setStrokeColorRGB(1, 1, 1); c.setLineWidth(2.6); c.circle(x, y, r, stroke=1, fill=0)
    c.setStrokeColorRGB(*RED); c.setLineWidth(1.4); c.circle(x, y, r, stroke=1, fill=0)
    c.setFillColorRGB(*RED); c.circle(x, y, 0.7 * mm, stroke=0, fill=1)


def draw_raster(c, img, x, y, w, h, quality=90):
    bio = io.BytesIO(); Image.fromarray(img).save(bio, "JPEG", quality=quality, subsampling=0, dpi=(OUT_DPI, OUT_DPI))
    bio.seek(0)
    c.drawImage(ImageReader(bio), x, y, w, h)


def draw_missing(c, x, y, w, h, text):
    c.setFillColorRGB(0.93, 0.91, 0.86); c.rect(x, y, w, h, stroke=0, fill=1)
    c.setStrokeColorRGB(0.82, 0.79, 0.72); c.setLineWidth(0.4)
    for k in range(-int(h), int(w), 9):
        c.line(max(x, x + k), y + max(0, -k), min(x + w, x + k + h), y + min(h, w - k))
    c.setFillColorRGB(*MUTE)
    KL.para(c, text, x + 10 * mm, y + h / 2 + 4 * mm, w - 20 * mm, "KSerifIt", 11)


# ------------------------------------------------------------------ dagens karta i de gamla kartornas färgvärld
TODAY = dict(
    land=(0.957, 0.937, 0.890),          # samma papperston som affischen, lite mörkare
    water=(0.745, 0.839, 0.855),         # Häradsekonomiska kartans ljusblå
    shore_halo=(0.600, 0.745, 0.792),    # strandens skuggband (som de handkolorerade kartornas blå kant)
    shore=(0.345, 0.522, 0.604),
    forest=(0.855, 0.886, 0.792), fields=(0.957, 0.890, 0.690), meadow=(0.898, 0.910, 0.780),
    urban=(0.930, 0.862, 0.820), building=(0.690, 0.345, 0.275),
    road_case=(0.420, 0.255, 0.180), road_major=(0.890, 0.580, 0.420), road_mid=(0.975, 0.870, 0.650),
    road_minor=(0.470, 0.380, 0.320), rail=(0.18, 0.15, 0.13),
    label=(0.235, 0.172, 0.118), label_water=(0.200, 0.370, 0.480), halo=(0.965, 0.950, 0.905),
)
PLACE_RANK = {"city": (0, 13.0, True), "town": (1, 10.5, True), "suburb": (2, 8.0, True), "village": (3, 8.0, False),
              "quarter": (4, 7.0, False), "hamlet": (5, 6.8, False), "island": (5, 7.0, False), "neighbourhood": (6, 6.4, False),
              "locality": (7, 6.2, False), "islet": (8, 6.0, False), "farm": (9, 5.8, False), "isolated_dwelling": (9, 5.8, False)}


def draw_today(c, F, frame, sq, scale_pt_per_m, lang="sv", detail=False, mark=None):
    """Dagens karta ur OpenStreetMap: vatten med strandband, åker/äng/skog/bebyggelse, byggnader, vägar i tre nivåer
    med kant, järnväg och ortnamn. Etiketter placeras girigt efter rang; en etikett som krockar med en annan, med
    adressmarkören eller når utanför rutan utelämnas (klipps aldrig). Returnerar en sammanfattning."""
    S = TODAY
    px, py, pw, ph = frame
    x0, y0, x1, y1 = sq
    sx = pw / (x1 - x0)
    proj = lambda lo, la: K.to_sweref(la, lo)
    tp = lambda x, y: (px + (x - x0) * sx, py + (y - y0) * sx)

    def ring_path(rings):
        p = c.beginPath()
        for r in rings:
            if len(r) < 2:
                continue
            q = O.project_line(proj, r)
            p.moveTo(*tp(*q[0]))
            for pt in q[1:]:
                p.lineTo(*tp(*pt))
            p.close()
        return p

    def line_path(pts):
        q = O.project_line(proj, pts)
        p = c.beginPath(); p.moveTo(*tp(*q[0]))
        for pt in q[1:]:
            p.lineTo(*tp(*pt))
        return p

    c.saveState()
    cl = c.beginPath(); cl.rect(px, py, pw, ph); c.clipPath(cl, stroke=0, fill=0)
    coast = [O.project_line(proj, l) for l in F["coast"]]
    mx = (x1 - x0) * 0.02
    land, lakes, has_coast = O.coast_land_polygons(coast, x0 - mx, y0 - mx, x1 + mx, y1 + mx) if coast else ([], [], False)
    c.setFillColorRGB(*(S["water"] if has_coast else S["land"])); c.rect(px, py, pw, ph, stroke=0, fill=1)

    def xy_path(polys):
        p = c.beginPath()
        for poly in polys:
            p.moveTo(*tp(*poly[0]))
            for q in poly[1:]:
                p.lineTo(*tp(*q))
            p.close()
        return p
    if has_coast:
        c.setFillColorRGB(*S["land"])
        for poly in land:
            c.drawPath(xy_path([poly]), stroke=0, fill=1)
        c.setFillColorRGB(*S["water"])
        for poly in lakes:
            c.drawPath(xy_path([poly]), stroke=0, fill=1)
    # markanvändning (under vattnet, så att stränderna alltid ligger överst)
    for key, col in (("forest", "forest"), ("meadow", "meadow"), ("fields", "fields"), ("urban", "urban")):
        c.setFillColorRGB(*S[col])
        for rings in (F["green"] if key == "forest" else F.get(key, [])):
            c.drawPath(ring_path(rings), stroke=0, fill=1, fillMode=0)
    # vatten: skuggband, yta, strandlinje
    c.setLineJoin(1); c.setLineCap(1)
    wpaths = [ring_path(r) for r in F["water"]]
    lw_halo = max(1.2, min(3.2, 55 * scale_pt_per_m * (1 if not detail else 0.6)))
    c.setStrokeColorRGB(*S["shore_halo"]); c.setLineWidth(lw_halo)
    for poly in land if has_coast else []:
        c.drawPath(xy_path([poly]), stroke=1, fill=0)
    for wp in wpaths:
        c.drawPath(wp, stroke=1, fill=0)
    c.setFillColorRGB(*S["water"])
    for wp in wpaths:
        c.drawPath(wp, stroke=0, fill=1, fillMode=0)
    for kind, pts in F["rivers"]:
        c.setStrokeColorRGB(*S["water"]); c.setLineWidth(max(0.5, (14 if kind == "river" else 7) * scale_pt_per_m))
        c.drawPath(line_path(pts), stroke=1, fill=0)
    c.setStrokeColorRGB(*S["shore"]); c.setLineWidth(0.35)
    for poly in land if has_coast else []:
        c.drawPath(xy_path([poly]), stroke=1, fill=0)
    for wp in wpaths:
        c.drawPath(wp, stroke=1, fill=0)
    # byggnader
    c.setFillColorRGB(*S["building"])
    bp = c.beginPath()
    for rings in F.get("buildings", []):
        q = O.project_line(proj, rings[0])
        bp.moveTo(*tp(*q[0]))
        for pt in q[1:]:
            bp.lineTo(*tp(*pt))
        bp.close()
    c.drawPath(bp, stroke=0, fill=1)
    # järnväg
    c.setStrokeColorRGB(*S["rail"]); c.setLineWidth(0.9)
    for pts in F["rail"]:
        c.drawPath(line_path(pts), stroke=1, fill=0)
    c.setStrokeColorRGB(1, 1, 1); c.setLineWidth(0.45); c.setDash(2.2, 2.2)
    for pts in F["rail"]:
        c.drawPath(line_path(pts), stroke=1, fill=0)
    c.setDash()
    # vägar: småvägar som tunn linje, större med kant och fyllning (kant först för alla, sedan fyllning)
    k = (1.35 if detail else 1.0)
    W_ = {0: 1.55 * k, 1: 1.05 * k}
    c.setStrokeColorRGB(*S["road_minor"])
    for cls, pts in F["roads"]:
        if O.road_level(cls) == 2:
            c.setLineWidth((0.42 if cls in ("unclassified", "road") else 0.3) * k)
            c.drawPath(line_path(pts), stroke=1, fill=0)
    for lvl in (1, 0):
        c.setStrokeColorRGB(*S["road_case"]); c.setLineWidth(W_[lvl] + 0.55)
        for cls, pts in F["roads"]:
            if O.road_level(cls) == lvl:
                c.drawPath(line_path(pts), stroke=1, fill=0)
        c.setStrokeColorRGB(*(S["road_major"] if lvl == 0 else S["road_mid"])); c.setLineWidth(W_[lvl])
        for cls, pts in F["roads"]:
            if O.road_level(cls) == lvl:
                c.drawPath(line_path(pts), stroke=1, fill=0)
    c.restoreState()
    # ortnamn och vattennamn
    placed = []
    marg = 1.6 * mm
    area_m2 = (x1 - x0) ** 2
    cand = []
    for kind, name, lo, la, pop in F.get("places", []):
        if kind not in PLACE_RANK:
            continue
        rank, size, caps = PLACE_RANK[kind]
        if not detail and rank >= 8:
            continue
        E_, N_ = K.to_sweref(la, lo)
        cand.append((rank, -pop, name, float(E_), float(N_), size * (1.25 if detail else 1.0), caps, "KSerif" if caps else "KSerifIt", S["label"]))
    for name, ring in F.get("water_names", []):
        q = O.project_line(proj, ring)
        xs_, ys_ = [a for a, _ in q], [b for _, b in q]
        a2 = abs(sum(xs_[i] * ys_[i + 1] - xs_[i + 1] * ys_[i] for i in range(len(q) - 1))) / 2
        if a2 < area_m2 * 0.004:
            continue
        cand.append((4.5, -a2, name, sum(xs_) / len(xs_), sum(ys_) / len(ys_), 7.0 * (1.2 if detail else 1.0), False, "KSerifIt", S["label_water"]))
    cand.sort(key=lambda t: (t[0], t[1]))
    for rank, _, name, E_, N_, size, caps, font, col in cand:
        if len(placed) >= (70 if detail else 45):
            break
        if not (x0 <= E_ <= x1 and y0 <= N_ <= y1):
            continue
        text = name.upper() if caps and rank <= 1 else name
        sp = 1.2 if caps and rank <= 1 else 0.0
        tw = pdfmetrics.stringWidth(text, font, size) + sp * max(0, len(text) - 1)
        X, Y = tp(E_, N_)
        bx = (X - tw / 2 - 1, Y - size * 0.3 - 1, X + tw / 2 + 1, Y + size * 0.85 + 1)
        if bx[0] < px + marg or bx[2] > px + pw - marg or bx[1] < py + marg or bx[3] > py + ph - marg:
            continue
        if any(not (bx[2] < b[0] or bx[0] > b[2] or bx[3] < b[1] or bx[1] > b[3]) for b in placed):
            continue
        if mark is not None:
            MX, MY = tp(*mark)
            if bx[0] - 4.5 * mm < MX < bx[2] + 4.5 * mm and bx[1] - 4.5 * mm < MY < bx[3] + 4.5 * mm:
                continue  # aldrig text över adressmarkören
        placed.append(bx)
        c.saveState()
        t = c.beginText(); t.setFont(font, size); t.setCharSpace(sp); t.setTextOrigin(X - tw / 2, Y)
        t.setTextRenderMode(1); c.setStrokeColorRGB(*S["halo"]); c.setLineWidth(max(0.8, size * 0.13)); c.setLineJoin(1)
        t.textOut(text); c.drawText(t)
        c.restoreState(); c.saveState()
        t = c.beginText(); t.setFont(font, size); t.setCharSpace(sp); t.setTextOrigin(X - tw / 2, Y)
        t.setTextRenderMode(0); c.setFillColorRGB(*col); t.textOut(text); c.drawText(t)
        c.restoreState()
    return {"har_kust": has_coast, "byggnader": len(F.get("buildings", [])), "vagar": len(F["roads"]), "etiketter": len(placed)}



def render(order, R, lang, path):
    T = TXT[lang]
    D = R["D"]; sq = R["square"]
    c = canvas.Canvas(str(path), pagesize=A3, pageCompression=1)
    c.setTitle(f"{order.get('text') or order['place']} – {T['sub'].split(' · ')[0]}")
    c.setAuthor("Moodly Sverige"); c.setCreator(GENERATOR_VERSION)
    W, H = A3
    c.setFillColorRGB(*PAPER); c.rect(0, 0, W, H, stroke=0, fill=1)
    geo = {"page_size_pt": [W, H], "panels": []}
    title = (order.get("text") or "").strip() or (order.get("address") or order["place"])
    addr_line = ", ".join(x for x in (order.get("address", "").strip(), order["place"]) if x)
    size = KL.fit_size(title, "KSerif", 34, W - 40 * mm)
    c.setFillColorRGB(*INK); c.setFont("KSerif", size); c.drawCentredString(W / 2, H - 42 * mm, title)
    c.setFont("KSans", 11); c.setFillColorRGB(*MUTE)
    c.drawCentredString(W / 2, H - 52 * mm, T["sub"].format(a=addr_line))
    c.setFont("KSans", 9)
    c.drawCentredString(W / 2, H - 59 * mm, f"{R['lat']:.5f}° N  {R['lon']:.5f}° {'O' if lang == 'sv' else 'E'}")
    # paneler i årtalsordning; Ekonomiska kartans ruta kan vara ersatt av en närbild (R["ek_beslut"])
    pek = R["ek_beslut"]["affischens_ruta"]
    items = []
    for s in R["ek_beslut"]["affischens_rutor"]:
        b_ = {"hek_narbild": "hek", "ek_narbild": "ek", "gsk_narbild": "gsk"}.get(s, s)
        fy = first_year(R["sheets"][b_]) if R["sheets"][b_] else 9999
        items.append((fy if fy < 9999 else {"gsk": 1860, "hek": 1880, "ek": 1950}[b_], int(s != b_), s))  # okänt år: seriens typiska
    items.sort()
    items = [(y, s) for y, _, s in items]
    items.append((9999, "osm"))
    pw = PANEL_MM * mm
    gap = 10 * mm
    x0 = (W - 2 * pw - gap) / 2
    tops = [H - 68 * mm, H - 68 * mm - pw - 24 * mm]
    npx = int(round(PANEL_MM / 25.4 * OUT_DPI))
    for k, (_, s) in enumerate(items):
        x = x0 + (k % 2) * (pw + gap); y = tops[k // 2] - pw
        base = {"hek_narbild": "hek", "ek_narbild": "ek", "gsk_narbild": "gsk"}.get(s, s)
        psq = sq
        if s == "hek_narbild":
            psq = R["hek_fonster"]["affisch"]
        elif s == "ek_narbild":
            psq = list(R["ek_beslut"]["guidens_narbild"])
        elif s == "gsk_narbild":
            psq = R["gsk_fonster"]["affisch"]
        pD = psq[2] - psq[0]
        rec = {"serie": base, "ruta": s, "bbox_pt": [x, y, x + pw, y + pw], "npx": npx, "square": list(psq)}
        if s == "osm":
            draw_today(c, R["F"], (x, y, pw, pw), tuple(sq), pw / D, lang, mark=(R["E"], R["N"]))
            rec.update(kalla="OpenStreetMap", vektor=True)
            label, sub = f"{T['series']['osm']} · {T['today']}", f"OpenStreetMap {R['osm_ts'][:10]}"
        elif R["sheets"][base] and not (FEL == "tom_panel" and s in ("ek", "hek_narbild", "ek_narbild")):
            img, cov, andel = panel_image(R["sheets"][base], psq, npx, R["_img"])
            if FEL == "tom_yta":
                img = img.copy(); img[-int(npx * 0.3):, -int(npx * 0.3):] = (238, 233, 220)
            draw_raster(c, img, x, y, pw, pw)
            src_px = pD / min(sh.px for sh in R["sheets"][base])
            rec.update(kalla="Lantmäteriet", tackning=round(float(cov.mean()), 4), andel_per_blad=andel,
                       kall_dpi=round(src_px / (PANEL_MM / 25.4), 1),
                       blad=[sh.b["blad"] for sh in R["sheets"][base]])
            yrs = fmt_years(R["sheets"][base], lang)
            if FEL == "fel_artal" and base == "hek":
                yrs = "1799"
            used = [sh for sh in R["sheets"][base] if andel.get(sh.b["blad"], 0) > 0] or R["sheets"][base]
            if s in ("hek_narbild", "ek_narbild", "gsk_narbild"):
                yrs_u = fmt_years(used, lang)
                if FEL == "fel_artal" and base == "hek":
                    yrs_u = "1799"
                label = (T["closeup_label"].format(y=yrs_u) if s == "hek_narbild" else
                         T["closeup_label_gsk"].format(y=yrs_u) if s == "gsk_narbild" else
                         f"{yrs_u} · {T['series']['ek']} · {T['detail'].format(km=f'{pD / 1000:g}')}")
                sub = T["closeup_sub"].format(km=f"{pD / 1000:g}".replace(".", "," if lang == "sv" else "."), sk=L.SERIES[base]["skala"])
                rec["artal_tryckt"] = yrs_u
            else:
                names = ", ".join(dict.fromkeys(sh.b["namn"] for sh in R["sheets"][base])) if base != "ek" else                     ", ".join(sh.b["blad"] for sh in R["sheets"][base])
                label = f"{yrs} · {T['series'][base]}"
                sub = f"{T['sheets'] if len(R['sheets'][base]) > 1 else T['sheet']} {names} · {L.SERIES[base]['skala']}"
                rec["artal_tryckt"] = yrs
        else:
            why = "excluded" if R["uteslutna"].get(base) and not R["sheets"][base] else "missing"
            draw_missing(c, x, y, pw, pw, T[why].format(s=T["series"][base]))
            rec.update(kalla="saknas", tackning=0.0, orsak=why)
            label, sub = T["series"][base], ""
        c.setStrokeColorRGB(*INK); c.setLineWidth(0.6); c.rect(x, y, pw, pw, stroke=1, fill=0)
        mx, my = x + (R["E"] - psq[0]) / pD * pw, y + (R["N"] - psq[1]) / pD * pw
        draw_marker(c, mx, my)
        rec["markor_pt"] = [mx, my]
        # etikett och undertext: anpassas till rutans bredd – aldrig avhuggna eller utanför rutan
        lw = pw
        if FEL == "avklippt_etikett":  # felinjektion: lång etikett som inte anpassas till rutan
            label, lw = label + " · " + sub, pw * 3
        ls = KL.fit_size(label, "KSerif", 13, lw, min_size=9)
        if pdfmetrics.stringWidth(label, "KSerif", ls) > lw and " · " in label and "/" in label.split(" · ")[0]:
            ys_ = [int(v[:4]) for v in label.split(" · ")[0].replace("–", "/").split("/") if v.strip()[:4].isdigit()]
            label = f"{min(ys_)}–{max(ys_)} · " + label.split(" · ", 1)[1]
            ls = KL.fit_size(label, "KSerif", 13, lw, min_size=9)
        c.setFillColorRGB(*INK); c.setFont("KSerif", ls); c.drawString(x, y - 7 * mm, label)
        ss = KL.fit_size(sub, "KSans", 7.5, lw, min_size=6)
        lines = [sub] if pdfmetrics.stringWidth(sub, "KSans", ss) <= lw else simpleSplit(sub, "KSans", 6.5, lw)[:2]
        if len(lines) > 1:
            ss = 6.5
        c.setFillColorRGB(*MUTE); c.setFont("KSans", ss)
        for i_, ln in enumerate(lines):
            c.drawString(x, y - 12 * mm - i_ * 2.8 * mm, ln)
        rec.update(etikett=label, undertext=" ".join(lines), etikett_pt=ls, undertext_rader=len(lines))
        geo["panels"].append(rec)
    geo["ek_not"] = notes(R, T)
    # skalstock + norrpil
    yb = tops[1] - pw - 20 * mm
    km = 1 if D <= 6000 else 2
    bar = km * 1000 / D * pw
    c.setStrokeColorRGB(*INK); c.setLineWidth(1.2)
    c.line(W / 2 - bar / 2, yb, W / 2 + bar / 2, yb)
    for xx in (W / 2 - bar / 2, W / 2 + bar / 2):
        c.line(xx, yb - 1.5 * mm, xx, yb + 1.5 * mm)
    c.setFont("KSans", 8); c.setFillColorRGB(*INK); c.drawCentredString(W / 2, yb + 3 * mm, T["scalebar"].format(km=km))
    nx = W / 2 + bar / 2 + 12 * mm  # norrpil som vektor (typsnittet saknar pilglyf)
    c.line(nx, yb - 2 * mm, nx, yb + 4 * mm); c.line(nx - 1.2 * mm, yb + 2.4 * mm, nx, yb + 4 * mm); c.line(nx + 1.2 * mm, yb + 2.4 * mm, nx, yb + 4 * mm)
    c.drawString(nx + 2 * mm, yb - 1 * mm, T["north"])
    # källor
    if FEL != "saknad_kallhanvisning":
        blad = "; ".join(f"{T['series'][s]} {', '.join(sh.b['blad'] for sh in shown_sheets(R, s))}" for s in ("gsk", "hek", "ek") if shown_sheets(R, s))
        y = yb - 8 * mm
        for key, kw in (("credit_lm", {"blad": blad}), ("credit_osm", {"ts": R["osm_ts"][:10]}), ("credit_geo", {}), ("credit_fit", {})):
            y = KL.para(c, T[key].format(**kw), 20 * mm, y, W - 40 * mm, "KSans", 6.6, color=MUTE) - 0.6 * mm
        if geo["ek_not"]:
            KL.para(c, geo["ek_not"], 20 * mm, y - 0.6 * mm, W - 40 * mm, "KSans", 6.6, color=MUTE)
    c.setFont("KSans", 6.5); c.setFillColorRGB(*MUTE)
    c.drawRightString(W - 20 * mm, 8 * mm, "Moodly Sverige")
    c.showPage()
    geo["guide"] = render_guide(c, order, R, lang, items)
    c.save()
    return geo


def shown_sheets(R, s):
    """Blad som faktiskt syns i produkten (affisch eller guide)."""
    eb = R["ek_beslut"]
    slots = eb.get("affischens_rutor", [])
    if s in slots:
        return R["sheets"][s]
    sqs = []
    if s == "hek":
        sqs = [R["hek_fonster"]["affisch"], R["hek_fonster"]["guide"]] if R["hek_fonster"] else []
    elif s == "gsk":
        sqs = [R["gsk_fonster"]["affisch"]] if R["gsk_fonster"] else []
    elif eb["guidens_narbild"]:
        sqs = [eb["guidens_narbild"]]
    names = {b["blad"] for sq_ in sqs for b, _ in L.sheets_for_square(*sq_, s, samples=9)}
    return [sh for sh in R["sheets"][s] if sh.b["blad"] in names]


def notes(R, T):
    """Förklaring på affischen och i guiden när en karta visas som närbild i stället för i hela utsnittet."""
    eb = R["ek_beslut"]; slots = eb.get("affischens_rutor", [])
    out = []
    if "gsk" not in slots:
        out.append(T["gsk_note"].format(n=len(R["sheets"]["gsk"])))
    if R["sheets"]["hek"] and "hek" not in slots:
        out.append(T["hek_note"].format(n=len(R["sheets"]["hek"])))
    if R["sheets"]["ek"] and "ek" not in slots:
        out.append(T["ek_note"].format(n=eb["blad"], tom=T["ek_note_tom"] if eb["tackning"] < EK_COVER_MIN else "",
                                       g=T["ek_note_guide"] if eb["guidens_narbild"] else T["ek_note_none"]))
    return " ".join(out) or None


def render_guide(c, order, R, lang, items):
    T = TXT[lang]
    W, H = A4
    D = R["D"]; sq = R["square"]
    out = {"pages": []}
    addr_line = ", ".join(x for x in (order.get("address", "").strip(), order["place"]) if x)

    def page_bg():
        c.setPageSize(A4)
        c.setFillColorRGB(*PAPER); c.rect(0, 0, W, H, stroke=0, fill=1)

    # sida 2: introduktion + om kartorna + passning
    page_bg()
    y = H - 25 * mm
    c.setFillColorRGB(*INK); c.setFont("KSerif", 22); c.drawString(20 * mm, y, T["guide_title"]); y -= 10 * mm
    pek = R["ek_beslut"]["affischens_ruta"]
    rutor_ = R["ek_beslut"].get("affischens_rutor", [])
    extra = ("".join([T["extra_close"].format(km=f"{HEK_CLOSE_M / 1000:g}".replace(".", "," if lang == "sv" else ".")) if "hek_narbild" in rutor_ else "",
                      T["extra_close_gsk"].format(km=f"{GSK_CLOSE_M / 1000:g}".replace(".", "," if lang == "sv" else ".")) if "gsk_narbild" in rutor_ else ""]))
    y = KL.para(c, T["guide_intro"].format(km=f"{D / 1000:g}".replace(".", "," if lang == "sv" else "."), addr=addr_line,
                                           lat=R["lat"], lon=R["lon"], e=R["E"], n=R["N"], extra=extra)
                + (T["street_only"] if R["geokod"].get("precision") == "gata" else ""),
                20 * mm, y, W - 40 * mm, "KSans", 10) - 4 * mm
    series = []
    for _, s in items:
        b_ = {"hek_narbild": "hek", "ek_narbild": "ek", "gsk_narbild": "gsk"}.get(s, s)
        if b_ not in series:
            series.append(b_)
    if shown_sheets(R, "ek") and "ek" not in series:
        series.insert(len(series) - 1, "ek")
    for s in series:
        head = T["series"][s]
        if s != "osm" and shown_sheets(R, s):
            head += f" ({fmt_years(shown_sheets(R, s), lang)})"
        c.setFont("KSerif", 12.5); c.setFillColorRGB(*INK); c.drawString(20 * mm, y, head); y -= 5.5 * mm
        y = KL.para(c, T["about"][s], 20 * mm, y, W - 40 * mm, "KSans", 9.2) - 3 * mm
    c.setFont("KSerif", 12.5); c.setFillColorRGB(*INK); c.drawString(20 * mm, y, T["fit_head"]); y -= 5.5 * mm
    fit_lines = []
    for s in ("gsk", "hek", "ek"):
        if s not in R["fit"] or not shown_sheets(R, s):
            continue
        f = R["fit"][s]
        a, e2 = f["justering"], f["efter"]
        d = math.hypot(a["ost_m"], a["nord_m"])
        if e2.get("matbar"):
            line = (T["fit_ok"] if d > 0 else T["fit_ok_small"]).format(s=T["series"][s], d=d, r=e2["forskjutning_m"])
        else:
            why = WHY[lang]["few"] if "vatten" in e2.get("skal", "") else WHY[lang]["disagree"]
            line = T["fit_na"].format(s=T["series"][s], why=why, typ=TYPICAL[s])
        fit_lines.append(line)
        y = KL.para(c, "• " + line, 22 * mm, y, W - 44 * mm, "KSans", 9.2)
    y = KL.para(c, T["fit_note"], 20 * mm, y - 2 * mm, W - 40 * mm, "KSans", 8.2, color=MUTE)
    out["fit_lines"] = fit_lines
    if notes(R, T):
        KL.para(c, notes(R, T), 20 * mm, y - 3 * mm, W - 40 * mm, "KSans", 8.2, color=MUTE)
    c.showPage()
    # sida 3–4: närbilder
    dm = DETAIL_M
    dsq = (R["E"] - dm / 2, R["N"] - dm / 2, R["E"] + dm / 2, R["N"] + dm / 2)
    dpx = int(round(DETAIL_MM / 25.4 * OUT_DPI))
    pw = DETAIL_MM * mm
    gek = R["ek_beslut"]["guidens_narbild"]
    for s in ("hek", "ek", "osm"):
        if s == "ek" and not gek:
            continue  # ingen skarvfri närbild ur Ekonomiska kartan – sidan utgår (grinden vet det via meta)
        psq = tuple(gek) if s == "ek" else (tuple(R["hek_fonster"]["guide"]) if s == "hek" and R["hek_fonster"] else dsq)
        pdm = psq[2] - psq[0]
        shs = shown_sheets(R, s) if s != "osm" else []
        page_bg()
        x, yb = 20 * mm, H - 28 * mm - pw
        rec = {"serie": s, "bbox_pt": [x, yb, x + pw, yb + pw], "npx": dpx, "D": pdm, "square": list(psq)}
        c.setFillColorRGB(*INK)
        head = T["series"][s] + (f" · {fmt_years(shs, lang)}" if s != "osm" and shs else (f" · {T['today']}" if s == "osm" else ""))
        c.setFont("KSerif", KL.fit_size(head, "KSerif", 18, pw, min_size=11)); c.drawString(20 * mm, H - 20 * mm, head)
        if s == "osm":
            draw_today(c, R["F"], (x, yb, pw, pw), dsq, pw / dm, lang, detail=True, mark=(R["E"], R["N"]))
            rec["vektor"] = True
        elif shs:
            img, cov, andel = panel_image(shs, psq, dpx, R["_img"])
            draw_raster(c, img, x, yb, pw, pw)
            rec.update(tackning=round(float(cov.mean()), 4), kall_dpi=round(pdm / min(sh.px for sh in shs) / (DETAIL_MM / 25.4), 1),
                       blad=[sh.b["blad"] for sh in shs])
        else:
            draw_missing(c, x, yb, pw, pw, T["missing"].format(s=T["series"][s]))
            rec["tackning"] = 0.0
        c.setStrokeColorRGB(*INK); c.setLineWidth(0.6); c.rect(x, yb, pw, pw, stroke=1, fill=0)
        mxy = (x + (R["E"] - psq[0]) / pdm * pw, yb + (R["N"] - psq[1]) / pdm * pw)
        draw_marker(c, *mxy)
        rec["markor_pt"] = list(mxy)
        c.setFont("KSans", 9); c.setFillColorRGB(*MUTE)
        c.drawString(x, yb - 6 * mm, T["detail"].format(km=f"{pdm / 1000:g}".replace(".", "," if lang == "sv" else ".")))
        y2 = KL.para(c, T["about"][s], x, yb - 13 * mm, pw, "KSans", 9, color=INK)
        if s == "osm" and FEL != "saknad_kallhanvisning":
            c.setFont("KSerif", 12); c.setFillColorRGB(*INK); c.drawString(x, y2 - 4 * mm, T["sources"])
            y3 = y2 - 10 * mm
            blad = "; ".join(f"{T['series'][s2]}: " + ", ".join(f"{sh.b['blad']} {sh.b['namn']} ({sh.b['ar']})" if s2 != "ek" else f"{sh.b['blad']} ({sh.b['ar']})"
                                                              for sh in shown_sheets(R, s2)) for s2 in ("gsk", "hek", "ek") if shown_sheets(R, s2))
            for key, kw in (("credit_lm", {"blad": blad}), ("credit_osm", {"ts": R["osm_ts"][:10]}), ("credit_geo", {})):
                y3 = KL.para(c, T[key].format(**kw), x, y3, pw, "KSans", 7.4, color=MUTE) - 0.8 * mm
            y3 = KL.para(c, T["year_note"] + " " + T["scan_note"], x, y3, pw, "KSans", 7.4, color=MUTE)
        out["pages"].append(rec)
        c.showPage()
    return out


def generate(order_path):
    timings = {}
    t0 = time.perf_counter()
    order = json.load(open(order_path, encoding="utf-8"))
    R = compute(order)
    timings.update(R["timings"])
    out = Path(os.environ.get("STJARN_OUT", ROOT / "ut")); out.mkdir(parents=True, exist_ok=True)
    files, geos = {}, {}
    t2 = time.perf_counter()
    for lang in order["languages"]:
        path = out / f"{order['id']}_{lang}.pdf"
        geos[lang] = render(order, R, lang, path)
        files[lang] = str(path)
    timings["rendera_s"] = time.perf_counter() - t2
    timings["totalt_s"] = time.perf_counter() - t0
    meta = {"generator": GENERATOR_VERSION, "product": "historisk", "order": order, "files": files,
            "punkt": {"lat": R["lat"], "lon": R["lon"], "E": R["E"], "N": R["N"], "geokod": R["geokod"]},
            "utsnitt": {"D_m": R["D"], "square": R["square"], "panel_mm": PANEL_MM, "detalj_m": DETAIL_M, "detalj_mm": DETAIL_MM},
            "blad": {s: [dict(sh.info, dE=sh.dE, dN=sh.dN, path=sh.b["path"], lokal=str(sh.path),
                              lut=(sh.lut.tolist() if sh.lut is not None else None)) for sh in R["sheets"][s]]
                     for s in R["sheets"]},
            "fargjustering": R["harm"], "ek_beslut": R["ek_beslut"], "hek_narbildsrutor": R["hek_fonster"],
            "visade_blad": {s: [sh.b["blad"] for sh in shown_sheets(R, s)] for s in ("gsk", "hek", "ek")},
            "mittblad": R["center_sheets"], "saknas_pa_ftp": R["missing_on_ftp"], "uteslutna_blad": R["uteslutna"],
            "mitt_flyttad_m": R["mitt_flyttad"], "passning": R["fit"],
            "osm": {"tidsstampel": R["osm_ts"], "instans": R["osm_endpoint"],
                    "fraga": O.query_map(*square_latlon(*R["square"]), minor=True, rich=True)},
            "geometry": geos, "timings": {k: round(v, 2) for k, v in timings.items()},
            "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"]}, indent=1))
