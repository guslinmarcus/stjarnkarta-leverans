"""Oberoende månberäkning för månfas-affischens grind – får ALDRIG importera Skyfield eller generatorns kod.

Metoder (annan teori och andra tabeller än generatorn, som använder Skyfield + JPL DE421):
  * Månens läge: Meeus, Astronomical Algorithms 2 uppl., kap. 47 (ELP-2000/82 trunkerad, tabell 47.A och 47.B,
    inkl. A1–A3-termerna och excentricitetsfaktorn E).
  * Solens läge och avstånd: Meeus kap. 25 (låg precision, ~0,01°).
  * Belysning: Meeus kap. 48, ekv. 48.2–48.4 (fasvinkeln ur elongation och avstånd, ej lågprecisionsvarianten).
  * Huvudfasernas tidpunkter: Meeus kap. 49 (oberoende.moon_phase_jde).
  * ΔT: Espenak & Meeus (2006), styckvisa polynom 1900–2150.
Kontroll mot Meeus räkneexempel 47.a och 48.a: se selftest() längst ned.
"""
import math
from datetime import datetime, timedelta, timezone

import oberoende as ob

D2R = math.pi / 180

# Tabell 47.A: D, M, M', F, Σl (1e-6 grad), Σr (1e-3 km)
_LR = [
    (0, 0, 1, 0, 6288774, -20905355), (2, 0, -1, 0, 1274027, -3699111), (2, 0, 0, 0, 658314, -2955968),
    (0, 0, 2, 0, 213618, -569925), (0, 1, 0, 0, -185116, 48888), (0, 0, 0, 2, -114332, -3149),
    (2, 0, -2, 0, 58793, 246158), (2, -1, -1, 0, 57066, -152138), (2, 0, 1, 0, 53322, -170733),
    (2, -1, 0, 0, 45758, -204586), (0, 1, -1, 0, -40923, -129620), (1, 0, 0, 0, -34720, 108743),
    (0, 1, 1, 0, -30383, 104755), (2, 0, 0, -2, 15327, 10321), (0, 0, 1, 2, -12528, 0),
    (0, 0, 1, -2, 10980, 79661), (4, 0, -1, 0, 10675, -34782), (0, 0, 3, 0, 10034, -23210),
    (4, 0, -2, 0, 8548, -21636), (2, 1, -1, 0, -7888, 24208), (2, 1, 0, 0, -6766, 30824),
    (1, 0, -1, 0, -5163, -8379), (1, 1, 0, 0, 4987, -16675), (2, -1, 1, 0, 4036, -12831),
    (2, 0, 2, 0, 3994, -10445), (4, 0, 0, 0, 3861, -11650), (2, 0, -3, 0, 3665, 14403),
    (0, 1, -2, 0, -2689, -7003), (2, 0, -1, 2, -2602, 0), (2, -1, -2, 0, 2390, 10056),
    (1, 0, 1, 0, -2348, 6322), (2, -2, 0, 0, 2236, -9884), (0, 1, 2, 0, -2120, 5751),
    (0, 2, 0, 0, -2069, 0), (2, -2, -1, 0, 2048, -4950), (2, 0, 1, -2, -1773, 4130),
    (2, 0, 0, 2, -1595, 0), (4, -1, -1, 0, 1215, -3958), (0, 0, 2, 2, -1110, 0),
    (3, 0, -1, 0, -892, 3258), (2, 1, 1, 0, -810, 2616), (4, -1, -2, 0, 759, -1897),
    (0, 2, -1, 0, -713, -2117), (2, 2, -1, 0, -700, 2354), (2, 1, -2, 0, 691, 0),
    (2, -1, 0, -2, 596, 0), (4, 0, 1, 0, 549, -1423), (0, 0, 4, 0, 537, -1117),
    (4, -1, 0, 0, 520, -1571), (1, 0, -2, 0, -487, -1739), (2, 1, 0, -2, -399, 0),
    (0, 0, 2, -2, -381, -4421), (1, 1, 1, 0, 351, 0), (3, 0, -2, 0, -340, 0),
    (4, 0, -3, 0, 330, 0), (2, -1, 2, 0, 327, 0), (0, 2, 1, 0, -323, 1165),
    (1, 1, -1, 0, 299, 0), (2, 0, 3, 0, 294, 0), (2, 0, -1, -2, 0, 8752),
]
# Tabell 47.B: D, M, M', F, Σb (1e-6 grad)
_B = [
    (0, 0, 0, 1, 5128122), (0, 0, 1, 1, 280602), (0, 0, 1, -1, 277693), (2, 0, 0, -1, 173237),
    (2, 0, -1, 1, 55413), (2, 0, -1, -1, 46271), (2, 0, 0, 1, 32573), (0, 0, 2, 1, 17198),
    (2, 0, 1, -1, 9266), (0, 0, 2, -1, 8822), (2, -1, 0, -1, 8216), (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200), (2, 1, 0, -1, -3359), (2, -1, -1, 1, 2463), (2, -1, 0, 1, 2211),
    (2, -1, -1, -1, 2065), (0, 1, -1, -1, -1870), (4, 0, -1, -1, 1828), (0, 1, 0, 1, -1794),
    (0, 0, 0, 3, -1749), (0, 1, -1, 1, -1565), (1, 0, 0, 1, -1491), (0, 1, 1, 1, -1475),
    (0, 1, 1, -1, -1410), (0, 1, 0, -1, -1344), (1, 0, 0, -1, -1335), (0, 0, 3, 1, 1107),
    (4, 0, 0, -1, 1021), (4, 0, -1, 1, 833), (0, 0, 1, -3, 777), (4, 0, -2, 1, 671),
    (2, 0, 0, -3, 607), (2, 0, 2, -1, 596), (2, -1, 1, -1, 491), (2, 0, -2, 1, -451),
    (0, 0, 3, -1, 439), (2, 0, 2, 1, 422), (2, 0, -3, -1, 421), (2, 1, -1, 1, -366),
    (2, 1, 0, 1, -351), (4, 0, 0, 1, 331), (2, -1, 1, 1, 315), (2, -2, 0, -1, 302),
    (0, 0, 1, 3, -283), (2, 1, 1, -1, -229), (1, 1, 0, -1, 223), (1, 1, 0, 1, 223),
    (0, 1, -2, -1, -220), (2, 1, -1, -1, -220), (1, 0, 1, 1, -185), (2, -1, -2, -1, 181),
    (0, 1, 2, 1, -177), (4, 0, -2, -1, 176), (4, -1, -1, -1, 166), (1, 0, 1, -1, -164),
    (4, 0, 1, -1, 132), (1, 0, -1, -1, -119), (4, -1, 0, -1, 115), (2, -2, 0, 1, 107),
]


def delta_t(year):
    """ΔT i sekunder, Espenak & Meeus (2006) för 1900–2150 (decimalår)."""
    y = year
    if y < 1920:
        t = y - 1900; return -2.79 + 1.494119 * t - 0.0598939 * t ** 2 + 0.0061966 * t ** 3 - 0.000197 * t ** 4
    if y < 1941:
        t = y - 1920; return 21.20 + 0.84493 * t - 0.076100 * t ** 2 + 0.0020936 * t ** 3
    if y < 1961:
        t = y - 1950; return 29.07 + 0.407 * t - t ** 2 / 233 + t ** 3 / 2547
    if y < 1986:
        t = y - 1975; return 45.45 + 1.067 * t - t ** 2 / 260 - t ** 3 / 718
    if y < 2005:
        t = y - 2000
        return 63.86 + 0.3345 * t - 0.060374 * t ** 2 + 0.0017275 * t ** 3 + 0.000651814 * t ** 4 + 0.00002373599 * t ** 5
    if y < 2050:
        t = y - 2000; return 62.92 + 0.32217 * t + 0.005589 * t ** 2
    u = (y - 1820) / 100
    return -20 + 32 * u * u - 0.5628 * (2150 - y)


def jde_from_utc(dt):
    """datetime (aware) -> JDE (TT) med egen ΔT."""
    jd = ob.jd_from_dt(dt)
    yr = dt.year + (dt.timetuple().tm_yday - 0.5) / 365.25
    return jd + delta_t(yr) / 86400


def moon_geo(jde):
    """Månens geocentriska ekliptiska longitud λ (grad, medelekvinoktium för datum, utan nutation),
    latitud β (grad) och avstånd Δ (km). Meeus kap. 47."""
    T = (jde - 2451545.0) / 36525
    Lp = 218.3164477 + 481267.88123421 * T - 0.0015786 * T ** 2 + T ** 3 / 538841 - T ** 4 / 65194000
    D = 297.8501921 + 445267.1114034 * T - 0.0018819 * T ** 2 + T ** 3 / 545868 - T ** 4 / 113065000
    M = 357.5291092 + 35999.0502909 * T - 0.0001536 * T ** 2 + T ** 3 / 24490000
    Mp = 134.9633964 + 477198.8675055 * T + 0.0087414 * T ** 2 + T ** 3 / 69699 - T ** 4 / 14712000
    F = 93.2720950 + 483202.0175233 * T - 0.0036539 * T ** 2 - T ** 3 / 3526000 + T ** 4 / 863310000
    A1 = 119.75 + 131.849 * T
    A2 = 53.09 + 479264.290 * T
    A3 = 313.45 + 481266.484 * T
    E = 1 - 0.002516 * T - 0.0000074 * T ** 2
    Ef = {0: 1.0, 1: E, 2: E * E}
    sl = sr = sb = 0.0
    for d, m, mp, f, cl, cr in _LR:
        arg = (d * D + m * M + mp * Mp + f * F) * D2R
        e = Ef[abs(m)]
        sl += cl * e * math.sin(arg)
        sr += cr * e * math.cos(arg)
    for d, m, mp, f, cb in _B:
        arg = (d * D + m * M + mp * Mp + f * F) * D2R
        sb += cb * Ef[abs(m)] * math.sin(arg)
    sl += 3958 * math.sin(A1 * D2R) + 1962 * math.sin((Lp - F) * D2R) + 318 * math.sin(A2 * D2R)
    sb += (-2235 * math.sin(Lp * D2R) + 382 * math.sin(A3 * D2R) + 175 * math.sin((A1 - F) * D2R)
           + 175 * math.sin((A1 + F) * D2R) + 127 * math.sin((Lp - Mp) * D2R) - 115 * math.sin((Lp + Mp) * D2R))
    lam = (Lp + sl / 1e6) % 360
    beta = sb / 1e6
    dist = 385000.56 + sr / 1000
    return lam, beta, dist


def sun_geo(jde):
    """Solens geometriska longitud (grad, medelekvinoktium för datum) och avstånd (km). Meeus kap. 25."""
    T = (jde - 2451545.0) / 36525
    L0 = 280.46646 + 36000.76983 * T + 0.0003032 * T ** 2
    M = 357.52911 + 35999.05029 * T - 0.0001537 * T ** 2
    e = 0.016708634 - 0.000042037 * T - 0.0000001267 * T ** 2
    Mr = M * D2R
    C = ((1.914602 - 0.004817 * T - 0.000014 * T ** 2) * math.sin(Mr) + (0.019993 - 0.000101 * T) * math.sin(2 * Mr)
         + 0.000289 * math.sin(3 * Mr))
    lon = (L0 + C) % 360
    v = M + C
    R_au = 1.000001018 * (1 - e * e) / (1 + e * math.cos(v * D2R))
    lon_app = lon - 0.00569  # aberration (nutationen tas ut mot månens, som saknar nutation här)
    return lon_app % 360, R_au * 149597870.7


def moon_state_utc(dt):
    """Belyst andel k (0..1), elongation i ekliptisk longitud (0..360, <180 = tilltagande), fasvinkel i (grad)."""
    jde = jde_from_utc(dt)
    lam, beta, dist = moon_geo(jde)
    lam0, R = sun_geo(jde)
    # månens ljustid ~1,3 s försummas; solens ljustid ingår i aberrationen
    dl = (lam - lam0) % 360
    cpsi = math.cos(beta * D2R) * math.cos(dl * D2R)
    psi = math.acos(max(-1.0, min(1.0, cpsi)))
    i = math.atan2(R * math.sin(psi), dist - R * math.cos(psi))
    k = (1 + math.cos(i)) / 2
    return k, dl, i / D2R


def principal_phases_between(t0, t1):
    """Huvudfaser (0 ny, 1 första kv., 2 full, 3 sista kv.) med UTC-tid i [t0, t1). Meeus kap. 49."""
    out = []
    y = t0.year + (t0.timetuple().tm_yday - 0.5) / 365.25
    k0 = math.floor((y - 2000) * 12.3685) - 1
    for kk in range(k0 * 4, (k0 + 3) * 4):
        k = kk / 4
        jde = ob.moon_phase_jde(k)
        utc = ob.dt_from_jd(jde - delta_t(y) / 86400)
        if t0 <= utc < t1:
            out.append((kk % 4, utc))
    return out


def selftest():
    """Meeus räkneexempel 47.a och 48.a (1992 april 12, 0h TD)."""
    jde = 2448724.5
    lam, beta, dist = moon_geo(jde)
    assert abs(lam - 133.162655) < 0.0005, lam  # 47.a: λ utan nutation 133,162655
    assert abs(beta - -3.229126) < 0.0005, beta
    assert abs(dist - 368409.7) < 1.0, dist
    lam0, R = sun_geo(jde)
    dl = (lam - lam0) % 360
    cpsi = math.cos(beta * D2R) * math.cos(dl * D2R)
    psi = math.acos(cpsi)
    i = math.atan2(R * math.sin(psi), dist - R * math.cos(psi))
    k = (1 + math.cos(i)) / 2
    assert abs(k - 0.6786) < 0.0006, k  # 48.a: k = 0,6786
    return {"lambda": lam, "beta": beta, "dist_km": dist, "k": k}


if __name__ == "__main__":
    print(selftest())
