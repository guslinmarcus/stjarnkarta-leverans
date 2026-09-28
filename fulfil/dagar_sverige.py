"""Svenska helgdagar och namnsdagar 2027 – datamodul för föreningskalendern (och andra svenska kalenderprodukter).

Källor (se data/svenska_dagar_2027.json -> "meta"):
  * Helgdagar: Lag (1989:253) om allmänna helgdagar 1 §, hämtad från data.riksdagen.se 2026-09-28 (KLASS A,
    primärkälla). Oberoende verifierad mot Meeus/Jones/Butcher-påskformeln (matematisk beräkning, ingen
    upphovsrätt) – alla 13 lagreglerade helgdagar 2027 matchar exakt, se verifiera_oberoende() nedan och
    BESLUTSLOGG.md 2026-09-28. Almanacksmonopolet (Kungl. Vetenskapsakademiens ensamrätt att ge ut almanackor)
    upphörde 1973 (sv.wikipedia.org/wiki/Almanacka) – vem som helst får publicera helgdagar/namnsdagar.
  * Namnsdagar: den svenska namnlängden, fritt använd av alla kalenderutgivare sedan 1973. Datan är hämtad
    (bekvämlighetsspegling, KLASS B) från api.dryg.net/dagar/v2.1/2027. OSÄKERT: den tjänstens egen drifts-
    licens är inte dokumenterad – dubbelkolla mot en andra publik källa (t.ex. Institutet för språk och
    folkminnen) före skarp drift i stor skala (se LUCKOR.md).

Användning:
    import dagar_sverige as DS
    DS.DAGAR["2027-06-26"] -> {"veckodag": "Lördag", "rod_dag": True, "helgdag": "Midsommardagen", "namnsdag": []}
    DS.rod_dag("2027-06-26") -> True
    DS.namnsdag("2027-01-02") -> ["Svea"]
"""
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
_FIL = ROOT / "data" / "svenska_dagar_2027.json"
_RAW = json.loads(_FIL.read_text(encoding="utf-8"))
META = _RAW["meta"]
DAGAR = _RAW["dagar"]  # {"YYYY-MM-DD": {"veckodag","rod_dag","helgdag","namnsdag":[...]}}
AR = META["ar"]

assert len(DAGAR) in (365, 366), f"fel antal dagar i datafilen: {len(DAGAR)}"


def rod_dag(iso):
    return bool(DAGAR.get(iso, {}).get("rod_dag"))


def helgdag(iso):
    return DAGAR.get(iso, {}).get("helgdag")


def namnsdag(iso):
    return DAGAR.get(iso, {}).get("namnsdag") or []


def veckodag(iso):
    return DAGAR.get(iso, {}).get("veckodag")


# ------------------------------------------------------------------ oberoende kontroll (används av grinden)
def _pask(year):
    """Meeus/Jones/Butcher – gregoriansk påskformel. Matematisk algoritm, ingen upphovsrätt."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _lordag_i_intervall(year, month, d0, d1):
    for dd in range(d0, d1 + 1):
        dt = date(year, month, dd)
        if dt.weekday() == 5:
            return dt
    return None


def helgdagar_oberoende(year=AR):
    """Räknar fram de 13 datumen i Lag (1989:253) 1 § oberoende av datafilen. Returnerar {iso: namn}.
    Sön-, påsk- och pingstdagen räknas inte in separat (de faller alltid på en söndag som redan är helgdag)."""
    p = _pask(year)
    alla_helgons = _lordag_i_intervall(year, 10, 31, 31) or _lordag_i_intervall(year, 11, 1, 6)
    return {
        date(year, 1, 1).isoformat(): "Nyårsdagen",
        date(year, 1, 6).isoformat(): "Trettondedag jul",
        (p - timedelta(days=2)).isoformat(): "Långfredagen",
        p.isoformat(): "Påskdagen",
        (p + timedelta(days=1)).isoformat(): "Annandag påsk",
        date(year, 5, 1).isoformat(): "Första maj",
        (p + timedelta(days=39)).isoformat(): "Kristi himmelsfärds dag",
        (p + timedelta(days=49)).isoformat(): "Pingstdagen",
        date(year, 6, 6).isoformat(): "Nationaldagen",
        _lordag_i_intervall(year, 6, 20, 26).isoformat(): "Midsommardagen",
        alla_helgons.isoformat(): "Alla helgons dag",
        date(year, 12, 25).isoformat(): "Juldagen",
        date(year, 12, 26).isoformat(): "Annandag jul",
    }


def verifiera_oberoende():
    """Jämför datafilens röda dagar mot den oberoende beräkningen. Kastar AssertionError vid avvikelse.
    Körs av grind_foreningskalender.py (klass C-krav: felinjektionstest om kod, BESLUTSSYSTEM §2)."""
    egna = helgdagar_oberoende()
    fel = []
    for iso, namn in egna.items():
        rec = DAGAR.get(iso)
        if not rec or not rec.get("rod_dag"):
            fel.append(f"{iso} ({namn}) saknas som röd dag i datafilen")
    if fel:
        raise AssertionError("helgdagar_oberoende avviker: " + "; ".join(fel))
    return True


if __name__ == "__main__":
    verifiera_oberoende()
    print(f"OK – {len(DAGAR)} dagar {AR}, {sum(1 for d in DAGAR.values() if d['rod_dag'])} röda dagar, "
          f"{len(helgdagar_oberoende())} lagreglerade helgdagar verifierade oberoende.")
