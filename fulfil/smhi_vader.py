"""SMHI:s öppna meteorologiska data (metobs), CC BY 4.0 SE ("Med våra öppna data följer licensvillkoren
Creative Commons Erkännande 4.0 SE", smhi.se/data/om-smhis-data/villkor-for-anvandning – A, DATAKALLOR_OCH_KOMBIPRODUKTER.md).
Bara Sverige. Bara dygnets: medeltemperatur (parameter 2), min/max temperatur (19/20), nederbörd (parameter 5)
och, där det finns, soltimmar (parameter 10, summerad från timvärden – bara 20 stationer i landet, alla från 1983).

Hårda täckningsregler (DATAKALLOR_OCH_KOMBIPRODUKTER.md, MVP-specifikation):
  * temperatur/nederbörd: stationens egen mätperiod (metadata "from"/"to") måste täcka datumet. Annars visas
    INGET värde för den kategorin – vi gissar aldrig och byter aldrig till en annan dag.
  * soltimmar: bara tillåtet om datumet är >= 1983-01-01 OCH stationen (en av de ~20) mäter parameter 10 den dagen.
  * närmaste station med täckning för DATUMET (inte närmaste station rakt av) väljs per parameter var för sig –
    olika parametrar kan alltså komma från olika stationer. Stationens namn och avstånd (km) följer med varje
    värde och ska tryckas med liten text på affischen.

API: opendata-download-metobs.smhi.se, mönster
  api/version/1.0/parameter/{p}/station/{s}/period/{period}/data.csv
Period väljs efter ålder: "corrected-archive" (kvalitetskontrollerat, utom senaste ~3 mån) i första hand,
"latest-months" (rullande ~4 mån) som fallback för färska datum.

Determinism: cachar station- och seriedata på disk (SMHI_CACHE, default <modulmapp>/data/smhi_cache) så att en
omkörning för samma order ger identiskt resultat även om SMHI hunnit lägga till senare dagar i samma fil.
"""
import json
import math
import os
import time
import urllib.request
from datetime import date as Date, datetime, timezone
from pathlib import Path

try:
    import truststore
    truststore.inject_into_ssl()  # Marcus dator: annars CERTIFICATE_VERIFY_FAILED (minnesregel)
except Exception:
    pass

ROOT = Path(__file__).parent
CACHE = Path(os.environ.get("SMHI_CACHE", ROOT / "data" / "smhi_cache"))
CACHE.mkdir(parents=True, exist_ok=True)
BASE = "https://opendata-download-metobs.smhi.se/api/version/1.0"
UA = "moodly-fodelsetavla/0.1 (+https://moodlyapp.se)"
LICENS = "Väderdata: SMHI"
LICENS_LONG_SV = "Väderdata: SMHI, CC BY 4.0"
FIRST_SUN_DATE = Date(1983, 1, 1)
MAX_STATION_KM = 150.0  # ingen station bortom detta accepteras (annars: ingen uppgift, inte en gissning)

PARAM = {"medel": "2", "min": "19", "max": "20", "nederbord": "5", "sol": "10"}
UNIT = {"medel": "°C", "min": "°C", "max": "°C", "nederbord": "mm", "sol": "h"}


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(1.0, h)))


def stations(param):
    """Alla stationer för en parameter (id, namn, lat, lon, aktiv-period). Cachad 30 dygn."""
    p = CACHE / f"stationer_{param}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 30 * 86400:
        return json.loads(p.read_text(encoding="utf-8"))
    d = json.loads(_get(f"{BASE}/parameter/{param}.json"))
    out = [{"id": s["id"], "name": s["name"], "lat": s["latitude"], "lon": s["longitude"],
            "from": s["from"], "to": s["to"], "active": s["active"]} for s in d["station"]]
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _covers(st, d: Date):
    ms = int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp() * 1000)
    return st["from"] <= ms <= st["to"]


def nearest_covering(param, lat, lon, d: Date, max_km=MAX_STATION_KM):
    """Närmaste station för parametern vars mätperiod täcker datumet d. None om ingen finns inom max_km."""
    cands = [dict(s, km=haversine_km(lat, lon, s["lat"], s["lon"])) for s in stations(param) if _covers(s, d)]
    if not cands:
        return None
    best = min(cands, key=lambda s: s["km"])
    return best if best["km"] <= max_km else None


def _period_for(d: Date):
    age_days = (Date.today() - d).days
    return "latest-months" if 0 <= age_days <= 110 else "corrected-archive"


def _series_path(param, station_id, period):
    return CACHE / f"serie_{param}_{station_id}_{period}.csv"


def _fetch_series(param, station_id, period):
    p = _series_path(param, station_id, period)
    max_age = 3600 if period == "latest-months" else 30 * 86400
    if p.exists() and time.time() - p.stat().st_mtime < max_age:
        return p.read_text(encoding="utf-8", errors="replace")
    url = f"{BASE}/parameter/{param}/station/{station_id}/period/{period}/data.csv"
    txt = _get(url).decode("utf-8", errors="replace")
    p.write_text(txt, encoding="utf-8")
    return txt


def _parse_day_rows(csv_text):
    """{datumsträng: (värde, kvalitet)} – kolumn 2 = representativt dygn, kolumn 3 = värde, kolumn 4 = kvalitet."""
    lines = csv_text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("Från Datum") or ln.startswith("Fr�n Datum")
                  or ln.lower().startswith("fr") and "datum" in ln.lower() and "till datum" in ln.lower()), None)
    out = {}
    if start is None:
        return out
    for ln in lines[start + 1:]:
        if not ln.strip():
            continue
        cols = ln.split(";")
        if len(cols) < 4:
            continue
        try:
            out[cols[2].strip()] = (float(cols[3].strip().replace(",", ".")), cols[4].strip() if len(cols) > 4 else "")
        except ValueError:
            continue
    return out


def day_value(param_key, station_id, d: Date):
    param = PARAM[param_key]
    period = _period_for(d)
    txt = _fetch_series(param, station_id, period)
    rows = _parse_day_rows(txt)
    key = d.isoformat()
    if key not in rows and period == "latest-months":
        rows = _parse_day_rows(_fetch_series(param, station_id, "corrected-archive"))
    if key not in rows:
        return None
    val, qual = rows[key]
    return val


def _sun_seconds_from(txt, d: Date):
    """Timvärden (format 'Datum;Tid;Solskenstid[sekund];Kvalitet'): summan för dygnet + antal timmar med värde."""
    key = d.isoformat()
    tot, n = 0.0, 0
    for ln in txt.splitlines():
        cols = ln.split(";")
        if len(cols) < 4 or cols[0].strip() != key:
            continue
        try:
            tot += float(cols[2].strip().replace(",", "."))
            n += 1
        except ValueError:
            continue
    return tot, n


def _sun_hours_for_day(station_id, d: Date):
    """Soltimmar = summan av timvärdena (parameter 10, mätt i sekunder) för dygnet, omvandlat till timmar.
    Kräver nästan hela dygnet mätt (>=20 av 24 timmar) för att räknas som ett komplett dygnsvärde."""
    period = _period_for(d)
    tot, n = _sun_seconds_from(_fetch_series(PARAM["sol"], station_id, period), d)
    if n < 20 and period == "latest-months":
        tot, n = _sun_seconds_from(_fetch_series(PARAM["sol"], station_id, "corrected-archive"), d)
    return round(tot / 3600.0, 1) if n >= 20 else None


def weather_for(lat, lon, d: Date):
    """Dygnets väder för en svensk ort/datum. Returnerar None per kategori som saknar täckning – ALDRIG en gissning.

    {"medel": {"varde","enhet","station","avstand_km"} | None, "min": ..., "max": ..., "nederbord": ...,
     "sol": ... | None (även om täckt av min/max/nederbörd; kräver egen 1983-gräns + egen stationslista)}
    """
    out = {}
    for key in ("medel", "min", "max", "nederbord"):
        st = nearest_covering(PARAM[key], lat, lon, d)
        if st is None:
            out[key] = None
            continue
        v = day_value(key, st["id"], d)
        out[key] = None if v is None else {"varde": v, "enhet": UNIT[key], "station": st["name"],
                                            "avstand_km": round(st["km"], 1), "station_id": st["id"]}
    if d >= FIRST_SUN_DATE:
        st = nearest_covering(PARAM["sol"], lat, lon, d)
        if st is not None:
            v = _sun_hours_for_day(st["id"], d)
            out["sol"] = None if v is None else {"varde": v, "enhet": UNIT["sol"], "station": st["name"],
                                                  "avstand_km": round(st["km"], 1), "station_id": st["id"]}
        else:
            out["sol"] = None
    else:
        out["sol"] = None
    out["har_data"] = any(out[k] for k in ("medel", "min", "max", "nederbord", "sol"))
    return out


if __name__ == "__main__":
    import sys
    lat, lon, ds = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
    print(json.dumps(weather_for(lat, lon, Date.fromisoformat(ds)), ensure_ascii=False, indent=1))
