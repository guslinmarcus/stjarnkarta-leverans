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
"""
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
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import kartgeo as K
import kartlayout as KL
import lmkartor as L
import osmdata as O
import passning as PS

GENERATOR_VERSION = "historisk/0.1.0"
FEL = os.environ.get("FELINJEKTION", "")
ROOT = Path(__file__).parent
A3 = (297 * mm, 420 * mm)
A4 = (210 * mm, 297 * mm)
PANEL_MM = 124.0
OUT_DPI = 300
MIN_SRC_DPI = 200
DETAIL_M = 3000.0
DETAIL_MM = 170.0
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
        "sub": "Samma plats på fyra kartor · {a}",
        "scalebar": "{km} km", "north": "N",
        "credit_lm": "Historiska kartor: © Lantmäteriet, fria enligt CC0 (Lantmäteriets öppna data). {blad}.",
        "credit_osm": "Dagens karta: © OpenStreetMap contributors, data under Open Database License (ODbL) 1.0, openstreetmap.org/copyright. OSM-data per {ts}.",
        "credit_geo": "Adressen slogs upp i OpenStreetMaps adressdata (© OpenStreetMap contributors, ODbL, via Geofabrik), orten i GeoNames (CC BY 4.0).",
        "credit_fit": "Lantmäteriet anger att georeferensen för de gamla kartorna är ”inte exakta utan mer generella”. Vi har mätt passningen mot dagens stränder – se guiden.",
        "guide_title": "Så läser du dina kartor",
        "guide_intro": ("Affischen visar samma kvadrat, {km} × {km} km, på fyra kartor från olika tider. Den röda ringen är din adress: "
                        "{addr} ({lat:.5f}° N, {lon:.5f}° O; SWEREF 99 TM {e:.0f} / {n:.0f})."),
        "about": {
            "gsk": "Generalstabskartan är Sveriges första rikstäckande topografiska karta, ritad av arméns lantmätare i skala 1:100 000. Den visar vägar, gårdar, kyrkor, vatten och terrängen med streck (backar). Den användes för militär planering.",
            "hek": "Häradsekonomiska kartan ritades i skala 1:20 000 för att beskatta och planera jordbruket. Den visar varje gård, åker (gul), äng och betesmark, skog och ägogränser. Den är den mest detaljerade bilden av landsbygden före industrialiseringen.",
            "ek": "Ekonomiska kartan byggde på flygbilder och visar fastigheter, åkrar, skog och byggnader i skala 1:10 000 eller 1:20 000. Den gjordes för jord- och skogsbruk och visar landskapet just före och under efterkrigstidens stora förändringar.",
            "osm": "Dagens karta är ritad av oss ur OpenStreetMap, en fri världskarta som byggs av frivilliga. Den visar vägar, vatten och grönområden så som de ser ut i dag.",
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
        "sources": "Källor och licenser",
        "year_note": "Årtalen är kartbladens tryck- eller karteringsår enligt Lantmäteriets bladindex.",
        "scan_note": "Kartorna är skanningar av originalen: fläckar, veck och handskrivna tillägg kommer från originalbladen.",
    },
    "en": {
        "series": {"hek": "Economic map of the hundreds", "gsk": "General Staff map", "ek": "Economic map", "osm": "Today's map"},
        "today": "today", "sheet": "sheet", "sheets": "sheets", "scale": "scale",
        "missing": "The {s} does not exist for this place.",
        "excluded": "The {s} exists here, but the scan of the sheet does not match Lantmäteriet's sheet index and cannot be fitted reliably, so it is not shown.",
        "sub": "The same place on four maps · {a}",
        "scalebar": "{km} km", "north": "N",
        "credit_lm": "Historical maps: © Lantmäteriet (Swedish mapping authority), free under CC0 (open data). {blad}.",
        "credit_osm": "Today's map: © OpenStreetMap contributors, data under the Open Database License (ODbL) 1.0, openstreetmap.org/copyright. OSM data as of {ts}.",
        "credit_geo": "Address lookup: OpenStreetMap address data (© OpenStreetMap contributors, ODbL, via Geofabrik); town: GeoNames (CC BY 4.0).",
        "credit_fit": "Lantmäteriet states that the georeferencing of the old maps is “not exact but more general”. We measured the fit against today's shorelines – see the guide.",
        "guide_title": "How to read your maps",
        "guide_intro": ("The poster shows the same square, {km} × {km} km, on four maps from different times. The red ring is your address: "
                        "{addr} ({lat:.5f}° N, {lon:.5f}° E; SWEREF 99 TM {e:.0f} / {n:.0f})."),
        "about": {
            "gsk": "The General Staff map (Generalstabskartan) was Sweden's first nationwide topographic map, drawn by army surveyors at 1:100,000. It shows roads, farms, churches, water and the terrain as hachures. It was made for military planning.",
            "hek": "The Economic map of the hundreds (Häradsekonomiska kartan) was drawn at 1:20,000 to tax and plan farming. It shows every farm, field (yellow), meadow and pasture, forest and property boundary – the most detailed picture of the countryside before industrialisation.",
            "ek": "The Economic map (Ekonomiska kartan) was based on aerial photographs and shows properties, fields, forest and buildings at 1:10,000 or 1:20,000. It was made for farming and forestry and shows the landscape just before and during the great post-war changes.",
            "osm": "Today's map is drawn by us from OpenStreetMap, a free world map built by volunteers. It shows roads, water and green areas as they are today.",
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
    osm, ep = O.fetch(O.query_map(s_, w_, n_, e_, minor=True))
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
    return {"lat": lat, "lon": lon, "E": E, "N": N, "geokod": gi, "D": D, "square": [e0, n0, e1, n1],
            "mitt_flyttad": [round(best[1] * D, 1), round(best[2] * D, 1)], "uteslutna": excluded, "_img": {},
            "sheets": sheets, "center_sheets": {s: [b["blad"] for b in v] for s, v in center.items()},
            "fit": fit, "F": F, "osm_ts": osm.get("osm3s", {}).get("timestamp_osm_base", ""), "osm_endpoint": ep,
            "missing_on_ftp": cover_info, "timings": t}


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
    # paneler i årtalsordning
    items = []
    for s in ("gsk", "hek", "ek"):
        items.append((first_year(R["sheets"][s]) if R["sheets"][s] else {"gsk": 1860, "hek": 1880, "ek": 1950}[s], s))
    items.sort()
    items.append((9999, "osm"))
    pw = PANEL_MM * mm
    gap = 10 * mm
    x0 = (W - 2 * pw - gap) / 2
    tops = [H - 68 * mm, H - 68 * mm - pw - 24 * mm]
    npx = int(round(PANEL_MM / 25.4 * OUT_DPI))
    for k, (_, s) in enumerate(items):
        x = x0 + (k % 2) * (pw + gap); y = tops[k // 2] - pw
        rec = {"serie": s, "bbox_pt": [x, y, x + pw, y + pw], "npx": npx}
        if s == "osm":
            O.draw_features(c, R["F"], lambda lo, la: K.to_sweref(la, lo), (x, y, pw, pw), tuple(sq), "klassisk", pw / D)
            rec.update(kalla="OpenStreetMap", vektor=True)
            label, sub = f"{T['series']['osm']} · {T['today']}", f"OpenStreetMap {R['osm_ts'][:10]}"
        elif R["sheets"][s] and not (FEL == "tom_panel" and s == "ek"):
            img, cov, andel = panel_image(R["sheets"][s], sq, npx, R["_img"])
            draw_raster(c, img, x, y, pw, pw)
            src_px = D / min(sh.px for sh in R["sheets"][s])
            rec.update(kalla="Lantmäteriet", tackning=round(float(cov.mean()), 4), andel_per_blad=andel,
                       kall_dpi=round(src_px / (PANEL_MM / 25.4), 1),
                       blad=[sh.b["blad"] for sh in R["sheets"][s]])
            yrs = fmt_years(R["sheets"][s], lang)
            if FEL == "fel_artal" and s == "hek":
                yrs = "1799"
            names = ", ".join(dict.fromkeys(sh.b["namn"] for sh in R["sheets"][s]))
            label = f"{yrs} · {T['series'][s]}"
            sub = f"{T['sheets'] if len(R['sheets'][s]) > 1 else T['sheet']} {names} · {L.SERIES[s]['skala']}" if s != "ek" else \
                f"{T['sheets'] if len(R['sheets'][s]) > 1 else T['sheet']} {', '.join(sh.b['blad'] for sh in R['sheets'][s])} · {L.SERIES[s]['skala']}"
            rec["artal_tryckt"] = yrs
        else:
            why = "excluded" if R["uteslutna"].get(s) and not R["sheets"][s] else "missing"
            draw_missing(c, x, y, pw, pw, T[why].format(s=T["series"][s]))
            rec.update(kalla="saknas", tackning=0.0, orsak=why)
            label, sub = T["series"][s], ""
        c.setStrokeColorRGB(*INK); c.setLineWidth(0.6); c.rect(x, y, pw, pw, stroke=1, fill=0)
        mx, my = x + (R["E"] - sq[0]) / D * pw, y + (R["N"] - sq[1]) / D * pw
        draw_marker(c, mx, my)
        rec["markor_pt"] = [mx, my]
        c.setFillColorRGB(*INK); c.setFont("KSerif", 13); c.drawString(x, y - 7 * mm, label)
        c.setFillColorRGB(*MUTE); c.setFont("KSans", 7.5); c.drawString(x, y - 12 * mm, sub[:110])
        rec["etikett"] = label
        geo["panels"].append(rec)
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
        blad = "; ".join(f"{T['series'][s]} {', '.join(sh.b['blad'] for sh in R['sheets'][s])}" for s in ("gsk", "hek", "ek") if R["sheets"][s])
        y = yb - 8 * mm
        for key, kw in (("credit_lm", {"blad": blad}), ("credit_osm", {"ts": R["osm_ts"][:10]}), ("credit_geo", {}), ("credit_fit", {})):
            y = KL.para(c, T[key].format(**kw), 20 * mm, y, W - 40 * mm, "KSans", 6.6, color=MUTE) - 0.6 * mm
    c.setFont("KSans", 6.5); c.setFillColorRGB(*MUTE)
    c.drawRightString(W - 20 * mm, 8 * mm, "Moodly Sverige")
    c.showPage()
    geo["guide"] = render_guide(c, order, R, lang, items)
    c.save()
    return geo


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
    y = KL.para(c, T["guide_intro"].format(km=f"{D / 1000:g}".replace(".", "," if lang == "sv" else "."), addr=addr_line,
                                           lat=R["lat"], lon=R["lon"], e=R["E"], n=R["N"])
                + (T["street_only"] if R["geokod"].get("precision") == "gata" else ""),
                20 * mm, y, W - 40 * mm, "KSans", 10) - 4 * mm
    for _, s in items:
        head = T["series"][s]
        if s != "osm" and R["sheets"][s]:
            head += f" ({fmt_years(R['sheets'][s], lang)})"
        c.setFont("KSerif", 12.5); c.setFillColorRGB(*INK); c.drawString(20 * mm, y, head); y -= 5.5 * mm
        y = KL.para(c, T["about"][s], 20 * mm, y, W - 40 * mm, "KSans", 9.2) - 3 * mm
    c.setFont("KSerif", 12.5); c.setFillColorRGB(*INK); c.drawString(20 * mm, y, T["fit_head"]); y -= 5.5 * mm
    fit_lines = []
    for s in ("gsk", "hek", "ek"):
        if s not in R["fit"]:
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
    c.showPage()
    # sida 3–4: närbilder
    dm = DETAIL_M
    dsq = (R["E"] - dm / 2, R["N"] - dm / 2, R["E"] + dm / 2, R["N"] + dm / 2)
    dpx = int(round(DETAIL_MM / 25.4 * OUT_DPI))
    pw = DETAIL_MM * mm
    for s in ("hek", "ek", "osm"):
        page_bg()
        x, yb = 20 * mm, H - 28 * mm - pw
        rec = {"serie": s, "bbox_pt": [x, yb, x + pw, yb + pw], "npx": dpx, "D": dm}
        c.setFillColorRGB(*INK); c.setFont("KSerif", 18)
        head = T["series"][s] + (f" · {fmt_years(R['sheets'][s], lang)}" if s != "osm" and R["sheets"][s] else (f" · {T['today']}" if s == "osm" else ""))
        c.drawString(20 * mm, H - 20 * mm, head)
        if s == "osm":
            O.draw_features(c, R["F"], lambda lo, la: K.to_sweref(la, lo), (x, yb, pw, pw), dsq, "klassisk", pw / dm)
            rec["vektor"] = True
        elif R["sheets"][s]:
            img, cov, andel = panel_image(R["sheets"][s], dsq, dpx, R["_img"])
            draw_raster(c, img, x, yb, pw, pw)
            rec.update(tackning=round(float(cov.mean()), 4), kall_dpi=round(dm / min(sh.px for sh in R["sheets"][s]) / (DETAIL_MM / 25.4), 1))
        else:
            draw_missing(c, x, yb, pw, pw, T["missing"].format(s=T["series"][s]))
            rec["tackning"] = 0.0
        c.setStrokeColorRGB(*INK); c.setLineWidth(0.6); c.rect(x, yb, pw, pw, stroke=1, fill=0)
        draw_marker(c, x + pw / 2, yb + pw / 2)
        rec["markor_pt"] = [x + pw / 2, yb + pw / 2]
        c.setFont("KSans", 9); c.setFillColorRGB(*MUTE)
        c.drawString(x, yb - 6 * mm, T["detail"].format(km=f"{dm / 1000:g}"))
        y2 = KL.para(c, T["about"][s], x, yb - 13 * mm, pw, "KSans", 9, color=INK)
        if s == "osm" and FEL != "saknad_kallhanvisning":
            c.setFont("KSerif", 12); c.setFillColorRGB(*INK); c.drawString(x, y2 - 4 * mm, T["sources"])
            y3 = y2 - 10 * mm
            blad = "; ".join(f"{T['series'][s2]}: " + ", ".join(f"{sh.b['blad']} {sh.b['namn']} ({sh.b['ar']})" if s2 != "ek" else f"{sh.b['blad']} ({sh.b['ar']})"
                                                              for sh in R["sheets"][s2]) for s2 in ("gsk", "hek", "ek") if R["sheets"][s2])
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
            "blad": {s: [dict(sh.info, dE=sh.dE, dN=sh.dN, path=sh.b["path"], lokal=str(sh.path)) for sh in R["sheets"][s]]
                     for s in R["sheets"]},
            "mittblad": R["center_sheets"], "saknas_pa_ftp": R["missing_on_ftp"], "uteslutna_blad": R["uteslutna"],
            "mitt_flyttad_m": R["mitt_flyttad"], "passning": R["fit"],
            "osm": {"tidsstampel": R["osm_ts"], "instans": R["osm_endpoint"],
                    "fraga": O.query_map(*square_latlon(*R["square"]), minor=True)},
            "geometry": geos, "timings": {k: round(v, 2) for k, v in timings.items()},
            "sha256": {l: hashlib.sha256(open(p, "rb").read()).hexdigest() for l, p in files.items()}}
    json.dump(meta, open(out / f"{order['id']}_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    return meta


if __name__ == "__main__":
    m = generate(sys.argv[1])
    print(json.dumps({"files": m["files"], "timings": m["timings"]}, indent=1))
