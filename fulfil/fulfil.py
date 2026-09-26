"""Hämtar väntande beställningar från leveransportalen, tillverkar stjärnkartan, kör kvalitetsgrinden
och laddar upp PDF:en. Körs av GitHub Actions (schemalagt) eller lokalt:

    PORTAL_URL=https://... FULFIL_SECRET=... python fulfil.py

Ingen människa i loopen: en order som inte klarar grinden levereras aldrig, den markeras som misslyckad
med ett begripligt meddelande till köparen (t.ex. "orten hittades inte – kontrollera stavningen").
"""
import gzip, json, os, sys, tempfile, unicodedata, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
PORTAL = os.environ.get("PORTAL_URL", "").rstrip("/")
SECRET = os.environ.get("FULFIL_SECRET", "")
MAX_PER_RUN = 40

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
    g = geocode(job["city"], job.get("country", ""))
    if not g:
        return None, "city_not_found"
    name, lat, lon, cc, pop, tz = g
    hh, mm = (job.get("time") or "21:00").split(":")[:2]
    order = {"id": job["id"], "name": job["text"].strip()[:60], "place": job["city"].strip()[:40],
             "lat": round(lat, 4), "lon": round(lon, 4), "timezone": tz,
             "datetime_local": f"{job['date']}T{int(hh):02d}:{int(mm):02d}", "languages": [job.get("lang", "en")]}
    return order, None


def produce(order, workdir):
    os.environ["STJARN_OUT"] = str(workdir)
    sys.path.insert(0, str(ROOT))
    import stjarnkarta, kvalitetsgrind
    op = Path(workdir) / f"{order['id']}.json"
    op.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    stjarnkarta.generate(op)
    r = kvalitetsgrind.run(Path(workdir) / f"{order['id']}_meta.json")
    pdf = Path(workdir) / f"{order['id']}_{order['languages'][0]}.pdf"
    return r, pdf


def main():
    if not PORTAL or not SECRET:
        sys.exit("PORTAL_URL och FULFIL_SECRET måste vara satta")
    jobs = api("GET", "/api/queue").get("jobs", [])[:MAX_PER_RUN]
    print(f"{len(jobs)} väntande")
    for job in jobs:
        try:
            order, err = make_order(job)
            if err:
                api("POST", f"/api/fail/{job['id']}", {"reason": err}); print(job["id"], err); continue
            with tempfile.TemporaryDirectory() as wd:
                r, pdf = produce(order, wd)
                if not r["godkand"]:
                    api("POST", f"/api/fail/{job['id']}", {"reason": "quality_gate", "detail": r["underkanda"]})
                    print(job["id"], "grind underkänd"); continue
                api("POST", f"/api/done/{job['id']}?place={urllib.request.quote(order['place'])}",
                    pdf.read_bytes(), "application/pdf")
                print(job["id"], "levererad")
        except Exception as e:  # en trasig order får aldrig stoppa de andra
            print(job.get("id"), "FEL", repr(e))
            try:
                api("POST", f"/api/fail/{job['id']}", {"reason": "internal"})
            except Exception:
                pass


if __name__ == "__main__":
    main()
