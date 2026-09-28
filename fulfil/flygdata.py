# -*- coding: utf-8 -*-
"""flygdata.py - flygplatsuppslag + storcirkelberäkning för flygresekartan. Egen kopia (inte en import över
mappgränsen) av data/flyg/flygplatser.py + storcirkel.py i agentbutik-fabrik-repot, eftersom leverans/fulfil/
synkas som en egen, självständig mapp till leveransrepot (se fabrik/synka.py) - datafilen ligger därför lokalt
här som data/flyg_airports.dat (samma rader som OpenFlights airports.dat, hämtat 2026-09-28).

Källa och licens: OpenFlights (github.com/jpatokal/openflights / openflights.org), Open Database License (ODbL
1.0). Källangivelse krävs alltid på varje renderad karta: "Flygplatsdata: © OpenFlights (openflights.org)".
Se data/flyg/LICENS.md i huvudrepot för den fullständiga analysen. En RENDERAD karta är ett "Produced Work"
(bara källangivelse krävs, ingen share-alike) - ingen kopia av databasen publiceras här.
"""
from __future__ import annotations

import csv
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AIRPORTS_PATH = ROOT / "data" / "flyg_airports.dat"
JORDRADIE_KM = 6371.0088  # medelradie (IUGG)

KOLUMNER = ["id", "namn", "stad", "land", "iata", "icao", "lat", "lon", "hojd_fot",
            "utc_offset", "dst", "tz", "typ", "kalla"]


def _null(v):
    return None if v in ("\\N", "", None) else v


def _norm(s):
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c)).strip()


@dataclass(frozen=True)
class Flygplats:
    iata: str | None
    icao: str | None
    namn: str
    stad: str
    land: str
    lat: float
    lon: float


_ALLA: list[Flygplats] | None = None
_IATA: dict[str, Flygplats] | None = None
_STAD: dict[str, list[Flygplats]] | None = None


def _load():
    global _ALLA, _IATA, _STAD
    if _ALLA is not None:
        return
    _ALLA, _IATA, _STAD = [], {}, {}
    with AIRPORTS_PATH.open(encoding="utf-8") as f:
        for rad in csv.reader(f):
            if len(rad) < 14:
                continue
            d = dict(zip(KOLUMNER, rad))
            try:
                lat, lon = float(d["lat"]), float(d["lon"])
            except ValueError:
                continue
            if d.get("typ") not in (None, "", "airport"):
                continue  # bara flygplatser (inte station/port/etc - OpenFlights blandar typer i nyare rader)
            fp = Flygplats(iata=_null(d["iata"]), icao=_null(d["icao"]), namn=d["namn"], stad=d["stad"],
                            land=d["land"], lat=lat, lon=lon)
            _ALLA.append(fp)
            if fp.iata:
                _IATA[fp.iata.upper()] = fp
            _STAD.setdefault(_norm(fp.stad), []).append(fp)


def hitta(kod_eller_stad: str, land: str = "") -> Flygplats | None:
    """IATA-kod (3 bokstäver) i första hand, annars bästa träff på ortnamn (flest flygplatser i orten
    filtreras på land om angivet; av flera väljs den med IATA-kod - en riktig kommersiell flygplats)."""
    _load()
    s = (kod_eller_stad or "").strip()
    if len(s) == 3 and s.isalpha():
        fp = _IATA.get(s.upper())
        if fp:
            return fp
    kand = _STAD.get(_norm(s), [])
    if land:
        filtrerad = [fp for fp in kand if _norm(fp.land) == _norm(land) or _norm(fp.land).startswith(_norm(land))]
        if filtrerad:
            kand = filtrerad
    if not kand:
        return None
    kand = sorted(kand, key=lambda fp: (fp.iata is None, fp.namn))
    return kand[0]


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * JORDRADIE_KM * math.asin(math.sqrt(min(1.0, h)))


def storcirkelbage(lat1, lon1, lat2, lon2, n: int = 80) -> list[tuple[float, float]]:
    """Sfärisk linjär interpolation (slerp) - se data/flyg/storcirkel.py för fullständig dokumentation."""
    phi1, lam1 = math.radians(lat1), math.radians(lon1)
    phi2, lam2 = math.radians(lat2), math.radians(lon2)
    x1, y1, z1 = math.cos(phi1) * math.cos(lam1), math.cos(phi1) * math.sin(lam1), math.sin(phi1)
    x2, y2, z2 = math.cos(phi2) * math.cos(lam2), math.cos(phi2) * math.sin(lam2), math.sin(phi2)
    d = math.acos(max(-1.0, min(1.0, x1 * x2 + y1 * y2 + z1 * z2)))
    if d < 1e-12:
        return [(lat1, lon1)] * (n + 1)
    out = []
    for i in range(n + 1):
        f = i / n
        A_ = math.sin((1 - f) * d) / math.sin(d)
        B_ = math.sin(f * d) / math.sin(d)
        x = A_ * x1 + B_ * x2
        y = A_ * y1 + B_ * y2
        z = A_ * z1 + B_ * z2
        out.append((math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))))
    return out
