"""Oberoende astronomi för kvalitetsgrindarna – får ALDRIG importera Skyfield eller generatorernas kod.

Metoder (andra formler och andra efemerider än generatorerna, som använder Skyfield + JPL DE421):
  * Solförmörkelser: Besselska element från NASA (Espenak & Meeus, VSOP87/ELP2000), lokal beräkning enligt
    Explanatory Supplement to the Astronomical Almanac (kap. 8) – egen implementation, egen rotsökning.
  * Månfaser: Meeus, Astronomical Algorithms 2 uppl., kap. 49 (med de 14 planetära termerna).
  * Sol upp/ned: NOAA:s solkalkylator (Meeus kap. 25 låg precision), två iterationer, standardrefraktion 50'.
  * Planeter: JPL "Approximate Positions of the Planets" (E. M. Standish), Keplerelement 1800–2050.
  * Månens belysning: Meeus kap. 48 (låg precision).
  * ΔT: Espenak & Meeus (2006) polynom för 2005–2050.
"""
import math
from datetime import datetime, timedelta, timezone

D2R = math.pi / 180
R2D = 180 / math.pi


# ---------------------------------------------------------------- tid
def jd_from_dt(dt):
    """datetime (UTC, aware eller naiv=UTC) -> juliansk dag."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return (dt - datetime(2000, 1, 1, 12)).total_seconds() / 86400 + 2451545.0


def dt_from_jd(jd):
    return (datetime(2000, 1, 1, 12) + timedelta(days=jd - 2451545.0)).replace(tzinfo=timezone.utc)


def delta_t(year):
    t = year - 2000
    return 62.92 + 0.32217 * t + 0.005589 * t * t  # sekunder (Espenak & Meeus 2006)


# ---------------------------------------------------------------- solförmörkelse (Besselska element)
class Bessel:
    def __init__(self, el):
        self.el = el
        self.t0 = el["t0_tdt_h"]
        self.dT = el["deltaT_s"]
        self.jd0 = math.floor(el["jd_greatest"] - el["t0_tdt_h"] / 24 + 0.5) - 0.5 + el["t0_tdt_h"] / 24  # JD (TDT) vid t0

    @staticmethod
    def _poly(c, t):
        return sum(ci * t ** i for i, ci in enumerate(c))

    @staticmethod
    def _dpoly(c, t):
        return sum(i * ci * t ** (i - 1) for i, ci in enumerate(c) if i)

    def state(self, t, lat, lon, h_m=0.0):
        """Allt i fundamentalplanet för tiden t (timmar från t0, TDT) och observatören."""
        e = self.el
        x, y = self._poly(e["x"], t), self._poly(e["y"], t)
        dx, dy = self._dpoly(e["x"], t), self._dpoly(e["y"], t)
        d = self._poly(e["d"], t) * D2R
        dd = self._dpoly(e["d"], t) * D2R
        mu = self._poly(e["mu"], t)
        dmu = self._dpoly(e["mu"], t) * D2R
        l1, l2 = self._poly(e["l1"], t), self._poly(e["l2"], t)
        phi = lat * D2R
        u = math.atan(0.99664719 * math.tan(phi))
        rs = 0.99664719 * math.sin(u) + h_m / 6378140.0 * math.sin(phi)
        rc = math.cos(u) + h_m / 6378140.0 * math.cos(phi)
        H = (mu + lon - 0.00417807 * self.dT) * D2R  # timvinkel; ΔT-korrektion för jordrotationen
        xi = rc * math.sin(H)
        eta = rs * math.cos(d) - rc * math.cos(H) * math.sin(d)
        zeta = rs * math.sin(d) + rc * math.cos(H) * math.cos(d)
        dxi = dmu * rc * math.cos(H)
        deta = dmu * xi * math.sin(d) - zeta * dd
        L1 = l1 - zeta * e["tanf1"]
        L2 = l2 - zeta * e["tanf2"]
        U, V = x - xi, y - eta
        A, B = dx - dxi, dy - deta
        # solens höjd och azimut (geodetisk latitud, som i Explanatory Supplement)
        sinalt = math.sin(d) * math.sin(phi) + math.cos(d) * math.cos(phi) * math.cos(H)
        alt = math.asin(max(-1, min(1, sinalt))) * R2D
        az = math.atan2(-math.cos(d) * math.sin(H),
                        math.sin(d) * math.cos(phi) - math.cos(d) * math.cos(H) * math.sin(phi)) * R2D % 360
        return dict(U=U, V=V, A=A, B=B, L1=L1, L2=L2, m=math.hypot(U, V), alt=alt, az=az)

    def ut(self, t):
        return dt_from_jd(self.jd0 + t / 24 - self.dT / 86400)

    def local(self, lat, lon, h_m=0.0):
        """Lokala omständigheter. Rotsökning med bisektion (inte JSEX:s iteration)."""
        # 1) maximum: minimera m(t) (gyllene snittet) i elementens giltighetsintervall
        lo, hi = self.el["tmin_h"] - 1, self.el["tmax_h"] + 1
        f = lambda t: self.state(t, lat, lon, h_m)["m"]
        g = (math.sqrt(5) - 1) / 2
        a, b = lo, hi
        # grovsökning först (m kan ha flera lokala minima utanför intervallet)
        ts = [lo + i * (hi - lo) / 400 for i in range(401)]
        k = min(range(len(ts)), key=lambda i: f(ts[i]))
        a, b = ts[max(0, k - 1)], ts[min(400, k + 1)]
        c, dd_ = b - g * (b - a), a + g * (b - a)
        for _ in range(80):
            if f(c) < f(dd_):
                b = dd_
            else:
                a = c
            c, dd_ = b - g * (b - a), a + g * (b - a)
        tm = (a + b) / 2
        s = self.state(tm, lat, lon, h_m)
        res = {"t_max": tm, "state_max": s}
        mag = (s["L1"] - s["m"]) / (s["L1"] + s["L2"])
        if mag <= 0:
            res["type"] = "none"; res["magnitude"] = mag; res["obscuration"] = 0.0
            return res

        def root(fun, t_in, t_out):
            fa = fun(t_in)
            for _ in range(60):
                mid = (t_in + t_out) / 2
                if (fun(mid) < 0) == (fa < 0):
                    t_in = mid
                else:
                    t_out = mid
            return (t_in + t_out) / 2

        pen = lambda t: (lambda q: q["m"] - q["L1"])(self.state(t, lat, lon, h_m))
        res["t_c1"] = root(pen, tm, tm - 4)
        res["t_c4"] = root(pen, tm, tm + 4)
        central = s["m"] < abs(s["L2"])
        if central:
            umb = lambda t: (lambda q: q["m"] - abs(q["L2"]))(self.state(t, lat, lon, h_m))
            res["t_c2"] = root(umb, tm, tm - 0.2)
            res["t_c3"] = root(umb, tm, tm + 0.2)
            res["type"] = "total" if s["L2"] < 0 else "annular"
        else:
            res["type"] = "partial"
        # radier i solradier: sol = 1, måne = k
        k = (s["L1"] - s["L2"]) / (s["L1"] + s["L2"])
        sep = 2 * s["m"] / (s["L1"] + s["L2"])
        res["magnitude"] = mag if not central else k
        res["moon_sun_ratio"] = k
        res["obscuration"] = overlap_fraction(1.0, k, sep)
        for key in ("c1", "c2", "c3", "c4"):
            if f"t_{key}" in res:
                q = self.state(res[f"t_{key}"], lat, lon, h_m)
                res[f"alt_{key}"] = q["alt"]
        res["alt_max"], res["az_max"] = s["alt"], s["az"]
        return res


def overlap_fraction(r_sun, r_moon, d):
    """Andel av solskivans yta som täcks av månskivan (cirkel–cirkel-skärning)."""
    if d >= r_sun + r_moon:
        return 0.0
    if d <= abs(r_moon - r_sun):
        return 1.0 if r_moon >= r_sun else (r_moon / r_sun) ** 2
    a1 = math.acos((d * d + r_sun * r_sun - r_moon * r_moon) / (2 * d * r_sun))
    a2 = math.acos((d * d + r_moon * r_moon - r_sun * r_sun) / (2 * d * r_moon))
    area = r_sun ** 2 * (a1 - math.sin(2 * a1) / 2) + r_moon ** 2 * (a2 - math.sin(2 * a2) / 2)
    return area / (math.pi * r_sun ** 2)


# ---------------------------------------------------------------- månfaser (Meeus kap. 49)
def moon_phase_jde(k):
    """k heltal + 0 / 0.25 / 0.5 / 0.75. Returnerar JDE (TT)."""
    T = k / 1236.85
    jde = 2451550.09766 + 29.530588861 * k + 0.00015437 * T ** 2 - 0.000000150 * T ** 3 + 0.00000000073 * T ** 4
    E = 1 - 0.002516 * T - 0.0000074 * T ** 2
    M = (2.5534 + 29.10535670 * k - 0.0000014 * T ** 2 - 0.00000011 * T ** 3) * D2R
    Mp = (201.5643 + 385.81693528 * k + 0.0107582 * T ** 2 + 0.00001238 * T ** 3 - 0.000000058 * T ** 4) * D2R
    F = (160.7108 + 390.67050284 * k - 0.0016118 * T ** 2 - 0.00000227 * T ** 3 + 0.000000011 * T ** 4) * D2R
    Om = (124.7746 - 1.56375588 * k + 0.0020672 * T ** 2 + 0.00000215 * T ** 3) * D2R
    s = math.sin
    frac = round((k - math.floor(k)) * 4) % 4
    if frac in (0, 2):
        if frac == 0:
            c = [-0.40720 * s(Mp), 0.17241 * E * s(M), 0.01608 * s(2 * Mp), 0.01039 * s(2 * F),
                 0.00739 * E * s(Mp - M), -0.00514 * E * s(Mp + M), 0.00208 * E * E * s(2 * M)]
        else:
            c = [-0.40614 * s(Mp), 0.17302 * E * s(M), 0.01614 * s(2 * Mp), 0.01043 * s(2 * F),
                 0.00734 * E * s(Mp - M), -0.00515 * E * s(Mp + M), 0.00209 * E * E * s(2 * M)]
        c += [-0.00111 * s(Mp - 2 * F), -0.00057 * s(Mp + 2 * F), 0.00056 * E * s(2 * Mp + M),
              -0.00042 * s(3 * Mp), 0.00042 * E * s(M + 2 * F), 0.00038 * E * s(M - 2 * F),
              -0.00024 * E * s(2 * Mp - M), -0.00017 * s(Om), -0.00007 * s(Mp + 2 * M),
              0.00004 * s(2 * Mp - 2 * F), 0.00004 * s(3 * M), 0.00003 * s(Mp + M - 2 * F),
              0.00003 * s(2 * Mp + 2 * F), -0.00003 * s(Mp + M + 2 * F), 0.00003 * s(Mp - M + 2 * F),
              -0.00002 * s(Mp - M - 2 * F), -0.00002 * s(3 * Mp + M), 0.00002 * s(4 * Mp)]
        corr = sum(c)
    else:
        c = [-0.62801 * s(Mp), 0.17172 * E * s(M), -0.01183 * E * s(Mp + M), 0.00862 * s(2 * Mp),
             0.00804 * s(2 * F), 0.00454 * E * s(Mp - M), 0.00204 * E * E * s(2 * M), -0.00180 * s(Mp - 2 * F),
             -0.00070 * s(Mp + 2 * F), -0.00040 * s(3 * Mp), -0.00034 * E * s(2 * Mp - M),
             0.00032 * E * s(M + 2 * F), 0.00032 * E * s(M - 2 * F), -0.00028 * E * E * s(Mp + 2 * M),
             0.00027 * E * s(2 * Mp + M), -0.00017 * s(Om), -0.00005 * s(Mp - M - 2 * F),
             0.00004 * s(2 * Mp + 2 * F), -0.00004 * s(Mp + M + 2 * F), 0.00004 * s(Mp - 2 * M),
             0.00003 * s(Mp + M - 2 * F), 0.00003 * s(3 * M), 0.00002 * s(2 * Mp - 2 * F),
             0.00002 * s(Mp - M + 2 * F), -0.00002 * s(3 * Mp + M)]
        corr = sum(c)
        W = 0.00306 - 0.00038 * E * math.cos(M) + 0.00026 * math.cos(Mp) - 0.00002 * math.cos(Mp - M) \
            + 0.00002 * math.cos(Mp + M) + 0.00002 * math.cos(2 * F)
        corr += W if frac == 1 else -W
    A = [299.77 + 0.107408 * k - 0.009173 * T ** 2, 251.88 + 0.016321 * k, 251.83 + 26.651886 * k,
         349.42 + 36.412478 * k, 84.66 + 18.206239 * k, 141.74 + 53.303771 * k, 207.14 + 2.453732 * k,
         154.84 + 7.306860 * k, 34.52 + 27.261239 * k, 207.19 + 0.121824 * k, 291.34 + 1.844379 * k,
         161.72 + 24.198154 * k, 239.56 + 25.513099 * k, 331.55 + 3.592518 * k]
    Ac = [0.000325, 0.000165, 0.000164, 0.000126, 0.000110, 0.000062, 0.000060, 0.000056, 0.000047,
          0.000042, 0.000040, 0.000037, 0.000035, 0.000023]
    corr += sum(c_ * math.sin(a * D2R) for c_, a in zip(Ac, A))
    return jde + corr


def moon_phases_year(year):
    """[(fas 0..3, datetime UTC)] för alla faser vars UTC-tid ligger i [year-01-01 - 2 d, year+1-01-01 + 2 d]."""
    out = []
    k0 = math.floor((year - 2000) * 12.3685) - 2
    for kk in range(k0 * 4, (k0 + 16) * 4):
        k = kk / 4
        jde = moon_phase_jde(k)
        dt = dt_from_jd(jde - delta_t(year) / 86400)
        if datetime(year - 1, 12, 30, tzinfo=timezone.utc) <= dt <= datetime(year + 1, 1, 2, tzinfo=timezone.utc):
            out.append((kk % 4, dt))
    return out


def moon_illum(jd):
    """Belyst andel (Meeus kap. 48, låg precision)."""
    T = (jd - 2451545.0) / 36525
    D = math.radians((297.8501921 + 445267.1114034 * T) % 360)
    M = math.radians((357.5291092 + 35999.0502909 * T) % 360)
    Mp = math.radians((134.9633964 + 477198.8675055 * T) % 360)
    i = 180 - math.degrees(D) - 6.289 * math.sin(Mp) + 2.100 * math.sin(M) - 1.274 * math.sin(2 * D - Mp) \
        - 0.658 * math.sin(2 * D) - 0.214 * math.sin(2 * Mp) - 0.110 * math.sin(D)
    return (1 + math.cos(math.radians(i))) / 2


# ---------------------------------------------------------------- solen (NOAA / Meeus kap. 25)
def sun_eq(jd):
    """Solens skenbara RA/Dec (grader), ekvationen för tid (minuter), skenbar longitud (grader, datumets ekvinoktium)."""
    T = (jd - 2451545.0) / 36525
    L0 = (280.46646 + T * (36000.76983 + 0.0003032 * T)) % 360
    M = 357.52911 + T * (35999.05029 - 0.0001537 * T)
    e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)
    C = math.sin(M * D2R) * (1.914602 - T * (0.004817 + 0.000014 * T)) + math.sin(2 * M * D2R) * (0.019993 - 0.000101 * T) \
        + math.sin(3 * M * D2R) * 0.000289
    true_long = L0 + C
    omega = 125.04 - 1934.136 * T
    lam = true_long - 0.00569 - 0.00478 * math.sin(omega * D2R)
    eps0 = 23 + (26 + ((21.448 - T * (46.815 + T * (0.00059 - T * 0.001813)))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(omega * D2R)
    ra = math.atan2(math.cos(eps * D2R) * math.sin(lam * D2R), math.cos(lam * D2R)) * R2D % 360
    dec = math.asin(math.sin(eps * D2R) * math.sin(lam * D2R)) * R2D
    y = math.tan(eps * D2R / 2) ** 2
    eot = 4 * R2D * (y * math.sin(2 * L0 * D2R) - 2 * e * math.sin(M * D2R) + 4 * e * y * math.sin(M * D2R) * math.cos(2 * L0 * D2R)
                     - 0.5 * y * y * math.sin(4 * L0 * D2R) - 1.25 * e * e * math.sin(2 * M * D2R))
    return ra, dec, eot, lam % 360


def sun_lon_j2000(jd):
    """Solens skenbara ekliptiska longitud omräknad till J2000-ekvinoktiet (för meteorsvärmar, IMO:s λ☉)."""
    lam = sun_eq(jd)[3]
    T = (jd - 2451545.0) / 36525
    return (lam - 1.397 * T) % 360  # allmän precession ≈ 1,397°/sekel


def sun_event_utc(date, lat, lon, rising=True, zenith=90.833):
    """NOAA-metoden. date = datetime.date (lokalt kalenderdatum approximeras med UTC-datum + longitud).
    Returnerar datetime UTC eller None (midnattssol / polarnatt)."""
    jd_noon = jd_from_dt(datetime(date.year, date.month, date.day, 12)) - lon / 360
    t_guess = jd_noon
    for _ in range(3):
        _, dec, eot, _ = sun_eq(t_guess)
        cosH = (math.cos(zenith * D2R) / (math.cos(lat * D2R) * math.cos(dec * D2R))
                - math.tan(lat * D2R) * math.tan(dec * D2R))
        if cosH > 1 or cosH < -1:
            return None, ("polarnatt" if cosH > 1 else "midnattssol")
        H = math.acos(cosH) * R2D
        minutes = 720 - 4 * (lon + (H if rising else -H)) - eot
        t_guess = jd_from_dt(datetime(date.year, date.month, date.day)) + minutes / 1440
    return dt_from_jd(t_guess), None


def sun_altaz(jd, lat, lon):
    ra, dec, _, _ = sun_eq(jd)
    return radec_to_altaz(ra, dec, lat, lon, jd)


def gmst_deg(jd):
    T = (jd - 2451545.0) / 36525
    return (280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * T * T) % 360


def radec_to_altaz(ra, dec, lat, lon, jd):
    H = (gmst_deg(jd) + lon - ra) * D2R
    phi, de = lat * D2R, dec * D2R
    alt = math.asin(math.sin(phi) * math.sin(de) + math.cos(phi) * math.cos(de) * math.cos(H))
    az = math.atan2(math.sin(H), math.cos(H) * math.sin(phi) - math.tan(de) * math.cos(phi))
    return alt * R2D, (az * R2D + 180) % 360


# ---------------------------------------------------------------- planeter (Standish, JPL)
_KEP = {  # a, e, I, L, long.peri, long.node  och ändring per sekel
    "mercury": ([0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593],
                [0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081]),
    "venus": ([0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255],
              [0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418]),
    "earth": ([1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0],
              [0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0]),
    "mars": ([1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891],
             [0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343]),
    "jupiter": ([5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909],
                [-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106]),
    "saturn": ([9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448],
               [-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794]),
}


def _helio(name, T):
    el, rate = _KEP[name]
    a, e, I, L, wbar, node = [x + r * T for x, r in zip(el, rate)]
    w = wbar - node
    M = ((L - wbar + 180) % 360 - 180) * D2R
    E = M + e * math.sin(M)
    for _ in range(30):
        E -= (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
    xp, yp = a * (math.cos(E) - e), a * math.sqrt(1 - e * e) * math.sin(E)
    w, node, I = w * D2R, node * D2R, I * D2R
    x = (math.cos(w) * math.cos(node) - math.sin(w) * math.sin(node) * math.cos(I)) * xp + \
        (-math.sin(w) * math.cos(node) - math.cos(w) * math.sin(node) * math.cos(I)) * yp
    y = (math.cos(w) * math.sin(node) + math.sin(w) * math.cos(node) * math.cos(I)) * xp + \
        (-math.sin(w) * math.sin(node) + math.cos(w) * math.cos(node) * math.cos(I)) * yp
    z = math.sin(w) * math.sin(I) * xp + math.cos(w) * math.sin(I) * yp
    return x, y, z


def planet_radec(name, jd):
    """Geocentrisk RA/Dec (J2000, grader) med ljustidskorrektion en gång."""
    T = (jd - 2451545.0) / 36525
    ex, ey, ez = _helio("earth", T)
    px, py, pz = _helio(name, T)
    dist = math.sqrt((px - ex) ** 2 + (py - ey) ** 2 + (pz - ez) ** 2)
    px, py, pz = _helio(name, T - dist * 0.0057755183 / 36525)
    gx, gy, gz = px - ex, py - ey, pz - ez
    eps = 23.43928 * D2R
    xq, yq, zq = gx, gy * math.cos(eps) - gz * math.sin(eps), gy * math.sin(eps) + gz * math.cos(eps)
    ra = math.atan2(yq, xq) * R2D % 360
    dec = math.atan2(zq, math.hypot(xq, yq)) * R2D
    # precession J2000 -> datum (låg precision, Meeus 21.1 förenklad) så att höjden blir rätt
    years = (jd - 2451545.0) / 365.25
    ra_r, de_r = ra * D2R, dec * D2R
    ra += (3.075 + 1.336 * math.sin(ra_r) * math.tan(de_r)) * years * 15 / 3600
    dec += 20.04 * math.cos(ra_r) * years / 3600
    return ra % 360, dec


def angsep(ra1, de1, ra2, de2):
    a = math.sin(de1 * D2R) * math.sin(de2 * D2R) + math.cos(de1 * D2R) * math.cos(de2 * D2R) * math.cos((ra1 - ra2) * D2R)
    return math.acos(max(-1, min(1, a))) * R2D


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = lat1 * D2R, lat2 * D2R
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((lon2 - lon1) * D2R / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))
