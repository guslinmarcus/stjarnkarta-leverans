"""Hämtar väntande beställningar från leveransportalen, tillverkar produkten, kör dess kvalitetsgrind
och laddar upp PDF:en. Körs av GitHub Actions (schemalagt) eller lokalt:

    PORTAL_URL=https://... FULFIL_SECRET=... python fulfil.py

Ingen människa i loopen: en order som inte klarar grinden levereras aldrig, den markeras som misslyckad
med ett begripligt meddelande till köparen (t.ex. "orten hittades inte – kontrollera stavningen").

Jobbets fält "product" väljer generator + grind (saknas fältet = stjärnkarta, som före 2026-09-27):
    stjarnkarta      stjarnkarta.py      + kvalitetsgrind.py        (text, ort, land, datum, tid, språk en/sv/de)
    formorkelse      formorkelse.py      + grind_formorkelse.py     (text, ort, land, språk en/sv/de/es)
    himmelskalender  himmelskalender.py  + grind_himmelskalender.py (text, ort, land, språk en/sv/de/es)
    historisk        historisk.py        + grind_historisk.py       (text, adress, ort i Sverige, språk sv/en)
    stadskarta       stadskarta.py       + grind_stadskarta.py      (text, ort, land, stil, radie, språk en/sv/de)
    karlekskarta     karlekskarta.py     + grind_karlekskarta.py    (text, 2–5 platser med datum/etikett, stil, språk en/sv/de)
    manfas           manfas.py           + grind_manfas.py          (text, datum, tid valfri, ort, land, stil, rad, rubrik,
                                                                     familjeläge 2–6 personer, språk en/sv/de/fr/es)

Kartprodukterna hämtar data över nätet (Lantmäteriets FTP, Overpass, Nominatim). Ett tillfälligt nätfel
lämnar jobbet i kön (nytt försök nästa körning, högst ett dygn). Körningen har en tidsbudget så att
GitHub Actions-jobbet (15 min) aldrig avbryts mitt i en order.
"""
import gzip, json, os, sys, tempfile, time, unicodedata, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
PORTAL = os.environ.get("PORTAL_URL", "").rstrip("/")
SECRET = os.environ.get("FULFIL_SECRET", "")
MAX_PER_RUN = 40
PRODUCTS = {  # produkt: (generator, grind, tillåtna språk)
    "stjarnkarta": ("stjarnkarta", "kvalitetsgrind", ("en", "sv", "de", "fr")),
    "formorkelse": ("formorkelse", "grind_formorkelse", ("en", "sv", "de", "es", "fr")),
    "himmelskalender": ("himmelskalender", "grind_himmelskalender", ("en", "sv", "de", "es")),
    "historisk": ("historisk", "grind_historisk", ("sv", "en")),
    "stadskarta": ("stadskarta", "grind_stadskarta", ("en", "sv", "de")),
    "karlekskarta": ("karlekskarta", "grind_karlekskarta", ("en", "sv", "de")),
    "manfas": ("manfas", "grind_manfas", ("en", "sv", "de", "fr", "es")),
}
MANFAS_STYLES = ("mork", "ljus", "akvarell", "barnrum")
RUN_BUDGET_S = int(os.environ.get("FULFIL_BUDGET_S", str(11 * 60)))
EST_S = {"historisk": 900, "stadskarta": 420}  # uppskattad längsta tid per order (övriga ≈ 60 s)
TEMPORARY = ("overpass misslyckades", "FTP-hämtning misslyckades")
# Delning mellan arbetsflöden: FULFIL_ONLY=historisk (eget jobb, 45 min, cache) och FULFIL_SKIP=historisk (ordinarie 15-min-jobb)
ONLY = {x for x in os.environ.get("FULFIL_ONLY", "").split(",") if x}
SKIP = {x for x in os.environ.get("FULFIL_SKIP", "").split(",") if x}

try:
    import truststore; truststore.inject_into_ssl()  # behövs på Marcus dator (SSL), ofarligt i CI
except Exception:
    pass


def norm(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)).strip()


_ORTER = None
_LANDER = None


def load_geo():
    global _ORTER, _LANDER
    if _ORTER is None:
        _LANDER = {}
        for line in gzip.open(ROOT / "data" / "lander.tsv.gz", "rt", encoding="utf-8"):
            iso, name = line.rstrip("\n").split("\t")
            _LANDER[norm(name)] = iso
            _LANDER[norm(iso)] = iso
        # vanliga lokala namn
        for k, v in {"sverige": "SE", "tyskland": "DE", "deutschland": "DE", "norge": "NO", "danmark": "DK",
                     "finland": "FI", "suomi": "FI", "usa": "US", "united states of america": "US", "uk": "GB",
                     "england": "GB", "storbritannien": "GB", "nederlanderna": "NL", "holland": "NL",
                     "osterrike": "AT", "osterreich": "AT", "schweiz": "CH", "frankrike": "FR", "spanien": "ES",
                     "italien": "IT", "polen": "PL", "island": "IS"}.items():
            _LANDER[k] = v
        _ORTER = {}
        for line in gzip.open(ROOT / "data" / "orter.tsv.gz", "rt", encoding="utf-8"):
            name, lat, lon, cc, pop, tz, names = line.rstrip("\n").split("\t")
            rec = (name, float(lat), float(lon), cc, int(pop or 0), tz)
            for n in names.split("|"):
                _ORTER.setdefault(n, []).append(rec)
    return _ORTER, _LANDER


def geocode(city, country):
    orter, lander = load_geo()
    cands = orter.get(norm(city), [])
    cc = lander.get(norm(country)) if country else None
    if cc:
        cands = [c for c in cands if c[3] == cc]
    if not cands:
        return None
    return max(cands, key=lambda c: c[4])


def api(method, path, data=None, ctype="application/json"):
    body = json.dumps(data).encode() if isinstance(data, (dict, list)) else data
    req = urllib.request.Request(PORTAL + path, data=body, method=method,
                                 headers={"Authorization": f"Bearer {SECRET}", "Content-Type": ctype,
                                          "User-Agent": "moodly-leverans/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def make_order(job):
    product = job.get("product") or "stjarnkarta"
    if product not in PRODUCTS:
        return None, "internal"
    langs = PRODUCTS[product][2]
    lang = job.get("lang") if job.get("lang") in langs else langs[0] if product == "historisk" else "en"
    if product == "karlekskarta":
        places = []
        _, lander = load_geo()
        for p in (job.get("places") or [])[:5]:
            if norm(p.get("country", "")) not in lander:  # okänt land = fel ort hellre än en gissning
                return None, "city_not_found"
            g = geocode(p.get("city", ""), p.get("country", ""))
            if not g:
                return None, "city_not_found"
            places.append({"place": p["city"].strip()[:40], "country": p.get("country", "").strip()[:40], "cc": g[3],
                           "date": p["date"], "label": (p.get("label") or "").strip()[:40],
                           "lat": round(g[1], 4), "lon": round(g[2], 4)})
        if len(places) < 2:
            return None, "places_count"
        return {"id": job["id"], "product": product, "text": (job.get("text") or "").strip()[:40], "places": places,
                "style": job.get("style") or "ljus", "place": places[0]["place"], "languages": [lang]}, None
    if product == "manfas":
        return make_manfas(job, lang)
    g = geocode(job["city"], "SE" if product == "historisk" else job.get("country", ""))
    if not g:
        return None, "city_not_found"
    name, lat, lon, cc, pop, tz = g
    if product == "historisk":
        return {"id": job["id"], "product": product, "text": (job.get("text") or "").strip()[:60],
                "address": (job.get("address") or "").strip()[:80], "place": job["city"].strip()[:40],
                "lat": round(lat, 4), "lon": round(lon, 4), "pop": pop, "timezone": tz, "languages": [lang]}, None
    if product == "stadskarta":
        try:
            r = float(job.get("radius_km") or 3)
        except ValueError:
            r = 3.0
        return {"id": job["id"], "product": product, "text": (job.get("text") or "").strip()[:40],
                "place": job["city"].strip()[:40], "country": (job.get("country") or "").strip()[:40], "cc": cc,
                "lat": round(lat, 4), "lon": round(lon, 4), "timezone": tz, "style": job.get("style") or "klassisk",
                "radius_km": min(max(r, 1.0), 8.0), "languages": [lang]}, None
    if product == "stjarnkarta":
        hh, mm = (job.get("time") or "21:00").split(":")[:2]
        order = {"id": job["id"], "name": job["text"].strip()[:60], "place": job["city"].strip()[:40],
                 "lat": round(lat, 4), "lon": round(lon, 4), "timezone": tz,
                 "datetime_local": f"{job['date']}T{int(hh):02d}:{int(mm):02d}", "languages": [lang]}
        for k in ("style", "palette", "frame", "font"):  # stjärnkartans stilval från portalen (tomt = standard)
            if job.get(k):
                order[k] = job[k]
    else:
        order = {"id": job["id"], "product": product, "text": (job.get("text") or "").strip()[:60],
                 "place": job["city"].strip()[:40], "lat": round(lat, 4), "lon": round(lon, 4), "timezone": tz,
                 "languages": [lang]}
    return order, None


def make_manfas(job, lang):
    """Månfas-affischen: en person (text = namn) eller familj (people, 2–6). Tom ort/land hos en person = familjens."""
    import re
    _, lander = load_geo()
    mode = "family" if job.get("mode") == "family" else "single"
    if mode == "family":
        people = [p for p in (job.get("people") or [])[:6] if (p.get("name") or "").strip() or p.get("date")]
    else:
        people = [{"name": job.get("text", ""), "date": job.get("date", ""), "time": job.get("time") or "",
                   "city": job.get("city", ""), "country": job.get("country", "")}]
    if (mode == "family" and not 2 <= len(people) <= 6) or not people:
        return None, "people_count"
    moons = []
    for p in people:
        city = (p.get("city") or "").strip() or (job.get("city") or "").strip()
        country = (p.get("country") or "").strip() or (job.get("country") or "").strip()
        if norm(country) not in lander:  # okänt land = fel ort hellre än en gissning
            return None, "city_not_found"
        g = geocode(city, country)
        if not g:
            return None, "city_not_found"
        d = str(p.get("date") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or not ("1900-01-01" <= d <= "2050-12-31"):
            return None, "date_out_of_range"
        t = str(p.get("time") or "")
        t = t[:5] if re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", t) and int(t[:2]) < 24 and int(t[3:5]) < 60 else None
        name = (p.get("name") or "").strip()[:40]
        if not name:
            return None, "people_count"
        moons.append({"name": name, "date": d, "time": t, "place": city[:40], "country": country[:40],
                      "lat": round(g[1], 4), "lon": round(g[2], 4), "timezone": g[5]})
    return {"id": job["id"], "product": "manfas", "mode": mode, "text": (job.get("text") or "").strip()[:40] if mode == "family" else "",
            "heading": job.get("heading") if job.get("heading") in ("born", "wedding", "met", "none") else "born",
            "style": job.get("style") if job.get("style") in MANFAS_STYLES else "mork",
            "row": job.get("row") if mode == "single" and job.get("row") in ("none", "month", "week") else "none",
            "place": moons[0]["place"], "moons": moons, "languages": [lang]}, None


def produce(order, workdir):
    """Kör generator + grind för orderns produkt. Returnerar (grindresultat, pdf) eller (None, felorsak)."""
    import importlib
    os.environ["STJARN_OUT"] = str(workdir)
    sys.path.insert(0, str(ROOT))
    gen_name, gate_name, _ = PRODUCTS[order.get("product", "stjarnkarta")]
    gen, gate = importlib.import_module(gen_name), importlib.import_module(gate_name)
    op = Path(workdir) / f"{order['id']}.json"
    op.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    try:
        gen.generate(op)
    except SystemExit as e:  # generatorn vägrar med en begriplig orsak (t.ex. förmörkelsen syns inte från orten)
        return None, str(e.code or "internal")
    r = gate.run(Path(workdir) / f"{order['id']}_meta.json")
    pdf = Path(workdir) / f"{order['id']}_{order['languages'][0]}.pdf"
    return r, pdf


def main():
    if not PORTAL or not SECRET:
        sys.exit("PORTAL_URL och FULFIL_SECRET måste vara satta")
    t_start = time.time()
    jobs = api("GET", "/api/queue").get("jobs", [])[:MAX_PER_RUN]
    print(f"{len(jobs)} väntande")
    for job in jobs:
        prod = job.get("product") or "stjarnkarta"
        if (ONLY and prod not in ONLY) or prod in SKIP:
            continue
        left = RUN_BUDGET_S - (time.time() - t_start)
        if left < EST_S.get(job.get("product") or "", 60):
            print(job["id"], "väntar till nästa körning (tidsbudget)"); continue
        try:
            order, err = make_order(job)
            if err:
                api("POST", f"/api/fail/{job['id']}", {"reason": err}); print(job["id"], err); continue
            with tempfile.TemporaryDirectory() as wd:
                r, pdf = produce(order, wd)
                if r is None:
                    api("POST", f"/api/fail/{job['id']}", {"reason": pdf}); print(job["id"], pdf); continue
                if not r["godkand"]:
                    api("POST", f"/api/fail/{job['id']}", {"reason": "quality_gate", "detail": r["underkanda"]})
                    print(job["id"], "grind underkänd"); continue
                api("POST", f"/api/done/{job['id']}?place={urllib.request.quote(order['place'])}",
                    pdf.read_bytes(), "application/pdf")
                print(job["id"], order.get("product", "stjarnkarta"), "levererad")
        except Exception as e:  # en trasig order får aldrig stoppa de andra
            if isinstance(e, RuntimeError) and str(e).startswith(TEMPORARY):
                try:
                    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(job["created"].replace("Z", "+00:00"))).total_seconds() / 3600
                except Exception:
                    age_h = 0
                if age_h < 24:
                    print(job.get("id"), "tillfälligt nätfel – nytt försök nästa körning:", str(e)[:160]); continue
            print(job.get("id"), "FEL", repr(e))
            try:
                api("POST", f"/api/fail/{job['id']}", {"reason": "internal"})
            except Exception:
                pass


if __name__ == "__main__":
    main()
