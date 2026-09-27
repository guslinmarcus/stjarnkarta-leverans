"""Geodesi och kartbladsindex för de historiska kartorna – utan GDAL/pyproj (ren Python + numpy).

* Gauss-Krüger fram och tillbaka enligt Lantmäteriets formler ("Gauss Konforma projektion", GRS 80):
  SWEREF 99 TM och RT 90 2,5 gon V (Lantmäteriets direkta parametrar från SWEREF 99-latitud/longitud,
  noggrannhet ≈ 1 m – långt under de historiska kartornas egen osäkerhet).
* Läsare för ESRI shapefile (polygoner) och dBase – Lantmäteriets bladindex ligger som shapefiler på FTP:n.
* Web Mercator (OSM-plattor).

Ingen nätverkstrafik här; se historisk.py för hämtningen.
"""
import math
import struct

import numpy as np
from pathlib import Path

GRS80 = (6378137.0, 1 / 298.257222101)
SWEREF99TM = dict(lon0=15.0, k0=0.9996, fn=0.0, fe=500000.0)
RT90 = dict(lon0=15 + 48 / 60 + 22.624306 / 3600, k0=1.00000561024, fn=-667.711, fe=1500064.274)


def _consts(a, f):
    e2 = f * (2 - f)
    n = f / (2 - f)
    ar = a / (1 + n) * (1 + n * n / 4 + n ** 4 / 64)
    return e2, n, ar


def geo_to_grid(lat, lon, p, ell=GRS80):
    a, f = ell
    e2, n, ar = _consts(a, f)
    A = e2
    B = (5 * e2 ** 2 - e2 ** 3) / 6
    C = (104 * e2 ** 3 - 45 * e2 ** 4) / 120
    D = (1237 * e2 ** 4) / 1260
    b1 = n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16 + 41 * n ** 4 / 180
    b2 = 13 * n ** 2 / 48 - 3 * n ** 3 / 5 + 557 * n ** 4 / 1440
    b3 = 61 * n ** 3 / 240 - 103 * n ** 4 / 140
    b4 = 49561 * n ** 4 / 161280
    phi, lam = np.radians(lat), np.radians(lon)
    s = np.sin(phi)
    phis = phi - s * np.cos(phi) * (A + B * s ** 2 + C * s ** 4 + D * s ** 6)
    dl = lam - np.radians(p["lon0"])
    xi = np.arctan(np.tan(phis) / np.cos(dl))
    eta = np.arctanh(np.cos(phis) * np.sin(dl))
    x = p["k0"] * ar * (xi + b1 * np.sin(2 * xi) * np.cosh(2 * eta) + b2 * np.sin(4 * xi) * np.cosh(4 * eta)
                        + b3 * np.sin(6 * xi) * np.cosh(6 * eta) + b4 * np.sin(8 * xi) * np.cosh(8 * eta)) + p["fn"]
    y = p["k0"] * ar * (eta + b1 * np.cos(2 * xi) * np.sinh(2 * eta) + b2 * np.cos(4 * xi) * np.sinh(4 * eta)
                        + b3 * np.cos(6 * xi) * np.sinh(6 * eta) + b4 * np.cos(8 * xi) * np.sinh(8 * eta)) + p["fe"]
    return y, x  # (östlig, nordlig)


def grid_to_geo(e, nn, p, ell=GRS80):
    a, f = ell
    e2, n, ar = _consts(a, f)
    d1 = n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96 - n ** 4 / 360
    d2 = n ** 2 / 48 + n ** 3 / 15 - 437 * n ** 4 / 1440
    d3 = 17 * n ** 3 / 480 - 37 * n ** 4 / 840
    d4 = 4397 * n ** 4 / 161280
    As = e2 + e2 ** 2 + e2 ** 3 + e2 ** 4
    Bs = -(7 * e2 ** 2 + 17 * e2 ** 3 + 30 * e2 ** 4) / 6
    Cs = (224 * e2 ** 3 + 889 * e2 ** 4) / 120
    Ds = -(4279 * e2 ** 4) / 1260
    xi = (nn - p["fn"]) / (p["k0"] * ar)
    eta = (e - p["fe"]) / (p["k0"] * ar)
    xp = xi - d1 * np.sin(2 * xi) * np.cosh(2 * eta) - d2 * np.sin(4 * xi) * np.cosh(4 * eta) \
        - d3 * np.sin(6 * xi) * np.cosh(6 * eta) - d4 * np.sin(8 * xi) * np.cosh(8 * eta)
    ep = eta - d1 * np.cos(2 * xi) * np.sinh(2 * eta) - d2 * np.cos(4 * xi) * np.sinh(4 * eta) \
        - d3 * np.cos(6 * xi) * np.sinh(6 * eta) - d4 * np.cos(8 * xi) * np.sinh(8 * eta)
    phis = np.arcsin(np.sin(xp) / np.cosh(ep))
    dl = np.arctan(np.sinh(ep) / np.cos(xp))
    s = np.sin(phis)
    phi = phis + s * np.cos(phis) * (As + Bs * s ** 2 + Cs * s ** 4 + Ds * s ** 6)
    return np.degrees(phi), p["lon0"] + np.degrees(dl)


def to_sweref(lat, lon):
    return geo_to_grid(lat, lon, SWEREF99TM)


def to_rt90(lat, lon):
    return geo_to_grid(lat, lon, RT90)


def from_sweref(e, n):
    return grid_to_geo(e, n, SWEREF99TM)


def from_rt90(e, n):
    return grid_to_geo(e, n, RT90)


# ---- Web Mercator (OSM)
def merc_px(lat, lon, z):
    """Globala pixelkoordinater (256-plattor) på zoomnivå z."""
    s = 256 * 2 ** z
    x = (lon + 180) / 360 * s
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * s
    return x, y


def merc_geo(x, y, z):
    s = 256 * 2 ** z
    lon = x / s * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / s))))
    return lat, lon


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ---- shapefile + dBase
def read_dbf(path, enc="latin-1"):
    b = Path(path).read_bytes()
    n, hl, rl = struct.unpack("<xxxxIHH", b[:12])
    fields, i = [], 32
    while b[i] != 0x0D:
        fields.append((b[i:i + 11].split(b"\0")[0].decode(enc), b[i + 16])); i += 32
    out = []
    for r in range(n):
        o, row = hl + r * rl + 1, {}
        for name, ln in fields:
            row[name] = b[o:o + ln].decode(enc).strip(); o += ln
        out.append(row)
    return out


def read_shp_polygons(path):
    """Lista av polygoner; varje polygon = lista av ringar [(x, y), …]."""
    b = Path(path).read_bytes()
    pos, out = 100, []
    while pos < len(b):
        _, clen = struct.unpack(">ii", b[pos:pos + 8]); pos += 8
        rec = b[pos:pos + clen * 2]; pos += clen * 2
        st = struct.unpack("<i", rec[:4])[0]
        if st == 0:
            out.append([]); continue
        nparts, npts = struct.unpack("<ii", rec[36:44])
        parts = list(struct.unpack(f"<{nparts}i", rec[44:44 + 4 * nparts])) + [npts]
        pts = struct.unpack(f"<{2 * npts}d", rec[44 + 4 * nparts:44 + 4 * nparts + 16 * npts])
        rings = [[(pts[2 * k], pts[2 * k + 1]) for k in range(parts[j], parts[j + 1])] for j in range(nparts)]
        out.append(rings)
    return out


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]; xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-300) + xi:
            inside = not inside
        j = i
    return inside


def point_in_polygon(x, y, rings):
    return sum(point_in_ring(x, y, r) for r in rings) % 2 == 1
