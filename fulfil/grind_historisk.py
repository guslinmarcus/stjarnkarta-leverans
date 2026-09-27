"""Kvalitetsgrind för "Din adress genom tiden". Oberoende av generatorn (egen kod för allt som avgör rätt/fel):

  1. Projektion: egen Transversal Mercator (Snyder, USGS PP 1395, serieutveckling) mot generatorns
     Gauss-Krüger (Lantmäteriets formler) – SWEREF 99 TM och RT 90 ska stämma ≤ 0,5 m.
  2. Adress: grinden tolkar adressen själv och söker i OSM-adresserna (samma extrakt, egen kod); punkten ska
     vara en adresspunkt med rätt gata och nummer (eller ligga vid gatan när numret saknas i OSM) och ≤ 25 km
     från orten enligt grindens egen GeoNames-uppslagning. Utan adress = ortens koordinat.
  3. Kartblad: Lantmäteriets bladindex läses med grindens egen shapefil-läsare (hämtas från FTP:n om den
     saknas). Mittbladet ska täcka punkten, alla blad som täcker > 5 % av utsnittet ska vara med, årtalen
     på affischen ska vara indexets.
  4. Georeferering: grinden räknar själv (bilinjär interpolation mellan bladhörnen, egen GeoTIFF-tolkning
     för Ekonomiska kartan, kartramens linjer kontrolleras i skanningen för Generalstabskartan) fram vilken
     källpixel 400 slumpade affischpixlar ska komma från och jämför färgen med bilden i PDF:en.
  5. Passning: (a) bildens förskjutning mot Lantmäteriets georeferens = den deklarerade justeringen och
     inom taket, (b) PDF:en redovisar uppmätt passning eller säger att den inte kunde mätas och trycker
     osäkerheten, (c) egen mätning mot dagens vatten i OSM (k-means-färggrupper + FFT-korskorrelation, annan
     kod och annan klassning än generatorns): kartan ska ligga inom 60/150/200 m (Ekonomiska/Härads-
     ekonomiska/Generalstabs) när passningen är uppmätt, annars inom den tryckta osäkerheten.
  6. Upplösning: ≥ 200 dpi ur originalskanningen i varje historisk kartruta, inbäddade bilder ≥ 250 dpi.
  7. Källhänvisning och osäkerhetstext tryckt (Lantmäteriet, CC0, © OpenStreetMap contributors, ODbL,
     GeoNames, "inte exakta"/"not exact"), inga tomma rutor, markören på adressen, inga saknade tecken.

Körning: python grind_historisk.py <ut>/<id>_meta.json -> <ut>/<id>_qc.json, exitkod 0 = GODKÄND
"""
import gzip
import io
import json
import math
import struct
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
ROOT = Path(__file__).parent
IDX = ROOT / "data" / "lm" / "index"
FTP = "ftp://download-opendata.lantmateriet.se/"
SHP = {"gsk": [("gsk_norra", "Generalstabskartan/Generalstabskartan_Norra/Generalstabskartan_Norra"),
               ("gsk_sodra", "Generalstabskartan/Generalstabskartan_Södra/Generalstabskartan_Södra")],
       "hek": [("hek_norra", "Haradsekonomiska_kartan/Häradsekonomiska_Kartan_Norra/Häradsekonomiska_kartan_norra"),
               ("hek_sodra", "Haradsekonomiska_kartan/Häradsekonomiska_Kartan_Södra/Häradsekonomiska_kartan_södra")],
       "ek": [("ek_rutor", "Ekonomiska_kartan/Bladindelningskartor/Shape/EkoRutor")]}
MIN_SRC_DPI = 200
MIN_EMB_DPI = 250
PAPER = np.array([238, 233, 220])
MUST = {"sv": ["Lantmäteriet", "CC0", "© OpenStreetMap contributors", "ODbL", "GeoNames", "inte exakta"],
        "en": ["Lantmäteriet", "CC0", "© OpenStreetMap contributors", "ODbL", "GeoNames", "not exact"]}
NA_TEXT = {"sv": "kunde inte mätas", "en": "could not be measured"}
CLAIM_NA = {"hek": 500.0, "gsk": 400.0, "ek": 40.0}  # tryckt övre gräns när passningen inte kunde mätas
ADJ_CAP = {"hek": 600.0, "gsk": 500.0, "ek": 60.0}   # största tillåtna justering (samma som produktens regel, egen kopia)
OK_TEXT = {"sv": "kvarvarande avvikelse", "en": "remaining offset"}
CHECK_M = {"hek": 150.0, "gsk": 200.0, "ek": 60.0}

# ---------------------------------------------------------------- 1. egen projektion (Snyder)
A_GRS80, F_GRS80 = 6378137.0, 1 / 298.257222101
SWEREF = (15.0, 0.9996, 0.0, 500000.0)
RT90 = (15 + 48 / 60 + 22.624306 / 3600, 1.00000561024, -667.711, 1500064.274)


def tm_snyder(lat, lon, prm):
    lon0, k0, fn, fe = prm
    a, f = A_GRS80, F_GRS80
    e2 = f * (2 - f); ep2 = e2 / (1 - e2)
    phi = np.radians(lat)
    N = a / np.sqrt(1 - e2 * np.sin(phi) ** 2)
    T = np.tan(phi) ** 2; C = ep2 * np.cos(phi) ** 2
    A = np.radians(np.asarray(lon) - lon0) * np.cos(phi)
    M = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * np.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * np.sin(4 * phi) - (35 * e2 ** 3 / 3072) * np.sin(6 * phi))
    x = k0 * N * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T ** 2 + 72 * C - 58 * ep2) * A ** 5 / 120)
    y = k0 * (M + N * np.tan(phi) * (A ** 2 / 2 + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24
                                     + (61 - 58 * T + T ** 2 + 600 * C - 330 * ep2) * A ** 6 / 720))
    return x + fe, y + fn


def tm_inverse(E, Nn, prm, it=6):
    """Invers genom Newton-iteration på den egna framåtformeln (numerisk derivata)."""
    lat = 45.0 + 0 * np.asarray(E, float) + (np.asarray(Nn) - 5e6) / 111200.0
    lon = prm[0] + 0 * np.asarray(E, float)
    lat = np.clip(lat, 50, 70)
    for _ in range(it):
        x, y = tm_snyder(lat, lon, prm)
        x1, y1 = tm_snyder(lat + 1e-5, lon, prm)
        x2, y2 = tm_snyder(lat, lon + 1e-5, prm)
        j11, j12, j21, j22 = (x1 - x) / 1e-5, (x2 - x) / 1e-5, (y1 - y) / 1e-5, (y2 - y) / 1e-5
        dx, dy = E - x, Nn - y
        det = j11 * j22 - j12 * j21
        lat = lat + (j22 * dx - j12 * dy) / det
        lon = lon + (-j21 * dx + j11 * dy) / det
    return lat, lon


# ---------------------------------------------------------------- 3. egen shapefil-läsare
def _fetch(remote, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(FTP + urllib.parse.quote(remote), timeout=300) as r:
        dest.write_bytes(r.read())


def read_index(serie):
    out = []
    for name, remote in SHP[serie]:
        for ext in ("shp", "dbf"):
            p = IDX / f"{name}.{ext}"
            if not p.exists():
                _fetch(f"{remote}.{ext}", p)
        shp = (IDX / f"{name}.shp").read_bytes(); dbf = (IDX / f"{name}.dbf").read_bytes()
        nrec, hlen, rlen = struct.unpack("<4xIHH", dbf[:12])
        flds, off = [], 32
        while dbf[off] != 13:
            flds.append((dbf[off:off + 11].split(b"\x00")[0].decode("latin-1"), dbf[off + 16])); off += 32
        pos, k = 100, 0
        while pos < len(shp) and k < nrec:
            clen = struct.unpack(">i", shp[pos + 4:pos + 8])[0] * 2
            rec = shp[pos + 8:pos + 8 + clen]; pos += 8 + clen
            o = hlen + k * rlen + 1; row = {}
            for fn, fl in flds:
                row[fn] = dbf[o:o + fl].decode("latin-1").strip(); o += fl
            k += 1
            if struct.unpack("<i", rec[:4])[0] != 5:
                continue
            np_, npt = struct.unpack("<ii", rec[36:44])
            first = struct.unpack("<i", rec[44:48])[0]
            end = struct.unpack("<i", rec[48:52])[0] if np_ > 1 else npt
            pts = np.frombuffer(rec[44 + 4 * np_:44 + 4 * np_ + 16 * npt], "<f8").reshape(-1, 2)[first:end]
            key = row.get("ekoruta") if serie == "ek" else row.get("Bladnummer")
            yr = row.get("År", "")
            if serie == "ek" and len(yr) == 2:
                yr = "19" + yr
            out.append({"blad": key, "ar": yr, "namn": row.get("Bladnamn", key), "ring": pts})
    return out


def remote_tiff_size(path):
    """Bildens bredd/höjd direkt ur TIFF-huvudet på Lantmäteriets FTP (grindens egen läsning, två små hämtningar)."""
    import ftplib

    def rng(off, n):
        f = ftplib.FTP("download-opendata.lantmateriet.se", timeout=120); f.login(); f.voidcmd("TYPE I")
        conn = f.transfercmd("RETR " + path, rest=off); buf = b""
        while len(buf) < n:
            ch = conn.recv(n - len(buf))
            if not ch:
                break
            buf += ch
        conn.close()
        try:
            f.abort()
        except Exception:
            pass
        f.close()
        return buf
    off = struct.unpack("<I", rng(0, 8)[4:8])[0]
    n = struct.unpack("<H", rng(off, 2))[0]
    ifd = rng(off + 2, 12 * n)
    t = {}
    for i in range(n):
        tag, typ, cnt, val = struct.unpack("<HHII", ifd[12 * i:12 * i + 12])
        t[tag] = (val & 0xFFFF) if typ == 3 else val
    return t[256], t[257]


def winding_inside(x, y, ring):
    wn = 0
    for (x1, y1), (x2, y2) in zip(ring, np.roll(ring, -1, axis=0)):
        cross = (x2 - x1) * (y - y1) - (x - x1) * (y2 - y1)
        if y1 <= y < y2 and cross > 0:
            wn += 1
        elif y2 <= y < y1 and cross < 0:
            wn -= 1
    return wn != 0


def corners4(ring):
    pts = ring[:-1] if np.allclose(ring[0], ring[-1]) else ring
    c = pts.mean(0)
    q = {}
    for p in pts:
        k = (p[0] > c[0], p[1] > c[1])
        if k not in q or np.sum((p - c) ** 2) > np.sum((q[k] - c) ** 2):
            q[k] = p
    return [q[(False, True)], q[(True, True)], q[(True, False)], q[(False, False)]]  # NV NO SO SV


def bilinear_inverse(E, N, geo):
    """(E, N) -> (u, v) i [0,1]² för fyrhörningen NV, NO, SO, SV (Newton)."""
    P = [np.array(g, float) for g in geo]
    u = np.full(np.shape(E), 0.5); v = np.full(np.shape(E), 0.5)
    for _ in range(12):
        x = (1 - u) * (1 - v) * P[0][0] + u * (1 - v) * P[1][0] + u * v * P[2][0] + (1 - u) * v * P[3][0]
        y = (1 - u) * (1 - v) * P[0][1] + u * (1 - v) * P[1][1] + u * v * P[2][1] + (1 - u) * v * P[3][1]
        xu = -(1 - v) * P[0][0] + (1 - v) * P[1][0] + v * P[2][0] - v * P[3][0]
        xv = -(1 - u) * P[0][0] - u * P[1][0] + u * P[2][0] + (1 - u) * P[3][0]
        yu = -(1 - v) * P[0][1] + (1 - v) * P[1][1] + v * P[2][1] - v * P[3][1]
        yv = -(1 - u) * P[0][1] - u * P[1][1] + u * P[2][1] + (1 - u) * P[3][1]
        det = xu * yv - xv * yu
        du = (yv * (E - x) - xv * (N - y)) / det
        dv = (-yu * (E - x) + xu * (N - y)) / det
        u, v = u + du, v + dv
    return u, v


def _n(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(ch)).strip()


def geonames_se(place):
    q = _n(place); best = None
    with gzip.open(ROOT / "data" / "orter.tsv.gz", "rt", encoding="utf-8") as f:
        for line in f:
            name, lat, lon, cc, pop, tz, names = line.rstrip("\n").split("\t")
            if cc == "SE" and q in names.split("|"):
                r = (int(pop or 0), float(lat), float(lon))
                best = r if best is None or r > best else best
    return best


def hav(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- 5. egen vattentolkning + ömsesidig information
def osm_water_labels(osm, e0, n0, e1, n1, npx):
    """0 = övrigt, 1 = vatten (polygoner), 2 = kustlinje/strand (linje 3 px),
    3 = havssidan av kustlinjen (band 250 m, OSM-kusten har land till vänster), 4 = landsidan (band 250 m)."""
    from PIL import ImageDraw
    m = (e1 - e0) / npx
    im = Image.new("L", (npx, npx), 0); d = ImageDraw.Draw(im)

    def pix(lon, lat):
        E, N = tm_snyder(np.asarray(lat), np.asarray(lon), SWEREF)
        return list(zip(((E - e0) / m).tolist(), ((n1 - N) / m).tolist()))
    lines = []
    for el in osm.get("elements", []):
        t = el.get("tags", {})
        water = t.get("natural") == "water" or t.get("waterway") == "riverbank" or t.get("landuse") == "reservoir"
        if el["type"] == "way" and "geometry" in el:
            g = [p for p in el["geometry"] if p]
            lo = np.array([p["lon"] for p in g]); la = np.array([p["lat"] for p in g])
            if water and len(g) >= 4:
                d.polygon(pix(lo, la), fill=1)
            if t.get("natural") == "coastline" or water:
                lines.append(pix(lo, la))
        elif el["type"] == "relation" and water:
            for mb in el.get("members", []):
                if mb.get("type") == "way" and "geometry" in mb:
                    g = [p for p in mb["geometry"] if p]
                    lines.append(pix(np.array([p["lon"] for p in g]), np.array([p["lat"] for p in g])))
    # relationernas ytor: fyll slutna kedjor (enkel sammanfogning på ändpunkter)
    for el in osm.get("elements", []):
        t = el.get("tags", {})
        if el["type"] != "relation" or not (t.get("natural") == "water" or t.get("waterway") == "riverbank"):
            continue
        segs = [[(p["lon"], p["lat"]) for p in mb["geometry"] if p] for mb in el.get("members", [])
                if mb.get("type") == "way" and "geometry" in mb and mb.get("role") != "inner"]
        rings, cur = [], None
        segs = [s for s in segs if len(s) >= 2]
        while segs:
            cur = segs.pop(0)
            grow = True
            while grow and cur[0] != cur[-1]:
                grow = False
                for i, s in enumerate(segs):
                    if s[0] == cur[-1]:
                        cur += s[1:]
                    elif s[-1] == cur[-1]:
                        cur += s[::-1][1:]
                    else:
                        continue
                    segs.pop(i); grow = True; break
            if cur[0] == cur[-1] and len(cur) >= 4:
                rings.append(cur)
        for r in rings:
            a = np.array(r)
            d.polygon(pix(a[:, 0], a[:, 1]), fill=1)
    bw = max(3, int(250 / m))
    for el in osm.get("elements", []):
        if el["type"] == "way" and el.get("tags", {}).get("natural") == "coastline" and "geometry" in el:
            g = [p for p in el["geometry"] if p]
            P = np.array(pix(np.array([p["lon"] for p in g]), np.array([p["lat"] for p in g])))
            if len(P) < 2:
                continue
            t = np.gradient(P, axis=0)
            t /= (np.linalg.norm(t, axis=1, keepdims=True) + 1e-9)
            nrm = np.stack([-t[:, 1], t[:, 0]], 1)  # med y nedåt pekar denna normal åt havssidan (kontrollerat mot Ystad)
            for sign, val in ((1, 3), (-1, 4)):
                off = P + sign * nrm * (bw / 2 + 1.5)
                d.line([tuple(q) for q in off], fill=val, width=bw)
    for l in lines:
        if len(l) >= 2:
            d.line(l, fill=2, width=3)
    return np.asarray(im)


def mutual_info(q, lab, n_q):
    j = np.bincount(q * 5 + lab, minlength=n_q * 5).reshape(n_q, 5).astype(float)
    p = j / j.sum()
    pq, pl = p.sum(1, keepdims=True), p.sum(0, keepdims=True)
    nz = p > 0
    return float((p[nz] * np.log(p[nz] / (pq @ pl)[nz])).sum())


def shore_lines(osm, e0, n0, e1, n1, m_px):
    """Dagens strandlinjer (kust + sjöar/åar-ytor) i pixelkoordinater på rutnätet."""
    out = []

    def pix(g):
        E, N = tm_snyder(np.array([p["lat"] for p in g]), np.array([p["lon"] for p in g]), SWEREF)
        return np.stack([(E - e0) / m_px, (n1 - N) / m_px], 1)
    for el in osm.get("elements", []):
        t = el.get("tags", {})
        water = t.get("natural") in ("water", "coastline") or t.get("waterway") == "riverbank"
        if not water:
            continue
        if el["type"] == "way" and "geometry" in el:
            g = [p for p in el["geometry"] if p]
            if len(g) >= 2:
                out.append(pix(g))
        elif el["type"] == "relation":
            for mb in el.get("members", []):
                if mb.get("type") == "way" and "geometry" in mb:
                    g = [p for p in mb["geometry"] if p]
                    if len(g) >= 2:
                        out.append(pix(g))
    return out


def shore_offsets(img, cov, lines, m_px, T, step_m=150.0, win_m=90.0):
    """Lodräta avstånd mellan dagens strand och kartans strand: längs varje strandlinje tas en punkt var
    150:e meter; kartans färgprofil vinkelrätt mot stranden (±2,5·T) söks efter det starkaste färgsprånget
    (tvåsidig medelvärdesskillnad över 90 m). Rak strand ger bara avståndet vinkelrätt mot stranden – det är
    det som avgör om vatten och land hamnar rätt."""
    from scipy.ndimage import map_coordinates, uniform_filter
    x = np.stack([uniform_filter(img[..., c].astype(float), 3) for c in range(3)], 0)
    n = img.shape[0]
    L = int(math.ceil(2.5 * T / m_px)); w = max(2, int(round(win_m / m_px)))
    ts = np.arange(-L - w, L + w + 1)
    offs, contrasts = [], []
    for P in lines:
        seg = np.diff(P, axis=0); ln = np.hypot(seg[:, 0], seg[:, 1])
        cum = np.concatenate([[0], np.cumsum(ln)])
        if cum[-1] * m_px < step_m:
            continue
        for d in np.arange(step_m / m_px / 2, cum[-1], step_m / m_px):
            i = min(np.searchsorted(cum, d) - 1, len(seg) - 1)
            if ln[i] == 0:
                continue
            f = (d - cum[i]) / ln[i]
            c0 = P[i] + f * seg[i]
            tvec = seg[i] / ln[i]; nv = np.array([-tvec[1], tvec[0]])
            pts = c0[None, :] + ts[:, None] * nv[None, :]
            if (pts < 1).any() or (pts > n - 2).any():
                continue
            cv = map_coordinates(cov.astype(float), [pts[:, 1], pts[:, 0]], order=0)
            if cv.mean() < 0.95:
                continue
            prof = np.stack([map_coordinates(x[c], [pts[:, 1], pts[:, 0]], order=1) for c in range(3)], 1)
            cs = np.concatenate([np.zeros((1, 3)), np.cumsum(prof, 0)])
            best, bt = -1, 0
            for j in range(w, len(ts) - w):
                a_ = (cs[j] - cs[j - w]) / w; b_ = (cs[j + w] - cs[j]) / w
                v = float(np.abs(a_ - b_).sum())
                if v > best:
                    best, bt = v, ts[j]
            if best >= 40:
                offs.append(abs(bt) * m_px); contrasts.append(best)
    return np.array(offs), np.array(contrasts)


def fit_decide(img, cov, osm, sq, T, m_px=10.0):
    """Grindens egen passningsmätning: k-means (8 färggrupper) på kartan; de grupper som mest ligger i dagens vatten
    (OSM-polygoner + havsbandet innanför kusten) blir kartans vatten. Förskjutningen = toppen i korskorrelationen
    (FFT) mellan kartans vatten och dagens vatten. Bekräftad om bästa värdet inom T klart slår bästa värdet
    bortom 1,5·T, fälld om det omvända gäller, annars obestämd."""
    e0, n0, e1, n1 = sq
    n = int(round((e1 - e0) / m_px))
    small = np.asarray(Image.fromarray(img).resize((n, n), Image.BOX)).astype(float)
    cv = np.asarray(Image.fromarray(cov.astype(np.uint8) * 255).resize((n, n), Image.NEAREST)) > 0
    lab = osm_water_labels(osm, e0, n0, e1, n1, n)
    Wm = (lab == 1) | (lab == 3)
    known = Wm | (lab == 4) | ((lab == 0) & ~np.isin(lab, [3]))
    if cv.mean() < 0.3 or Wm[cv].mean() < 0.02:
        return "obestamd", "för lite vatten i utsnittet"
    X = small[cv]
    rng = np.random.default_rng(3)
    C = X[rng.choice(len(X), 8, replace=False)]
    for _ in range(12):
        d = ((X[:, None, :] - C[None]) ** 2).sum(-1); a_ = d.argmin(1)
        C = np.array([X[a_ == k].mean(0) if (a_ == k).any() else C[k] for k in range(8)])
    lab_img = np.full((n, n), -1); lab_img[cv] = a_
    wfrac = [Wm[cv][a_ == k].mean() if (a_ == k).any() else 0 for k in range(8)]
    base = Wm[cv].mean()
    H = np.isin(lab_img, [k for k in range(8) if wfrac[k] >= max(0.3, 2.5 * base)]) & cv
    if H.sum() < 50:
        return "obestamd", "kartans vatten gick inte att skilja ut"
    A = np.where(cv, H - H[cv].mean(), 0.0); B = np.where(cv, Wm - Wm[cv].mean(), 0.0)
    corr = np.fft.irfft2(np.fft.rfft2(A, s=(2 * n, 2 * n)) * np.conj(np.fft.rfft2(B, s=(2 * n, 2 * n))), s=(2 * n, 2 * n))
    k = int(math.ceil(2.5 * T / m_px))
    inn = out = -1e18; best = (-1e18, 0, 0); allv = []
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            r = math.hypot(dx, dy) * m_px
            if r > 2.5 * T:
                continue
            v = corr[dy % (2 * n), dx % (2 * n)]
            allv.append(v)
            if v > best[0]:
                best = (v, dx, dy)
            if r <= T:
                inn = max(inn, v)
            elif r > 1.5 * T:
                out = max(out, v)
    margin = 0.05 * (best[0] - float(np.median(allv)) + 1e-12)
    verdict = "bekraftad" if inn > out + margin else ("fald" if out > inn + margin else "obestamd")
    return verdict, f"kartans vatten ligger bäst {math.hypot(best[1], best[2]) * m_px:.0f} m från dagens (öst {best[1] * m_px:.0f}, nord {-best[2] * m_px:.0f})"


# ---------------------------------------------------------------- körning
def panel_array(page, bbox_pt, H):
    x0, y0, x1, y1 = bbox_pt
    r = fitz.Rect(x0, H - y1, x1, H - y0)
    for im in page.get_images(full=True):
        for rr in page.get_image_rects(im[0]):
            if abs(rr.x0 - r.x0) < 1 and abs(rr.y0 - r.y0) < 1 and abs(rr.width - r.width) < 1:
                pm = fitz.Pixmap(page.parent, im[0])
                if pm.n != 3:
                    pm = fitz.Pixmap(fitz.csRGB, pm)
                a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, 3)
                return a, im[2] / (rr.width / 72)
    return None, 0.0


def source_mapper(serie, info, ring_geo):
    """(E, N) SWEREF -> (kolumn, rad) i källbilden, grindens egen väg."""
    path = Path(info["lokal"])
    im = Image.open(path)
    W, H = im.size
    if serie == "ek":
        tp, sc = im.tag_v2[33922], im.tag_v2[33550]
        x0, y0, px = tp[3], tp[4], sc[0]

        def f(E, N):
            la, lo = tm_inverse(E, N, SWEREF)
            X, Y = tm_snyder(la, lo, RT90)
            return (X - x0) / px, (y0 - Y) / px
        return f, im, px
    geo = corners4(ring_geo)
    if serie == "hek":
        src = [(0, 0), (W, 0), (W, H), (0, H)]
    else:
        src = info["ram_horn_px"]
    src = np.array(src, float)

    def f(E, N):
        u, v = bilinear_inverse(E, N, geo)
        c = (1 - u) * (1 - v) * src[0] + (u * (1 - v))[..., None] * 0 if False else None
        C = (1 - u) * (1 - v) * src[0][0] + u * (1 - v) * src[1][0] + u * v * src[2][0] + (1 - u) * v * src[3][0]
        R = (1 - u) * (1 - v) * src[0][1] + u * (1 - v) * src[1][1] + u * v * src[2][1] + (1 - u) * v * src[3][1]
        return C, R
    w_m = np.hypot(*(np.array(geo[1]) - np.array(geo[0]))); w_px = np.hypot(*(src[1] - src[0]))
    return f, im, w_m / w_px


def frame_lines_dark(im, corners):
    g = np.asarray(im.convert("L"))
    ok = tot = 0
    for a, b in zip(corners, corners[1:] + corners[:1]):
        for t in np.linspace(0.05, 0.95, 60):
            x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
            xi, yi = int(round(x)), int(round(y))
            win = g[max(0, yi - 3):yi + 4, max(0, xi - 3):xi + 4]
            tot += 1
            ok += win.size and win.min() < 170  # kartytans innerlinje är tunn (1–2 px) men tydlig
    return ok / max(tot, 1)


def run(meta_path):
    import osmdata as O  # bara hämtning/cache av samma Overpass/Nominatim-svar
    meta = json.load(open(meta_path, encoding="utf-8"))
    o = meta["order"]; pt = meta["punkt"]; ut = meta["utsnitt"]
    checks = []

    def chk(name, ok, detail):
        checks.append({"grind": name, "ok": bool(ok), "detalj": detail})
    rng = np.random.default_rng(7)
    # 1
    e_s, n_s = tm_snyder(pt["lat"], pt["lon"], SWEREF)
    d1 = math.hypot(float(e_s) - pt["E"], float(n_s) - pt["N"])
    chk("projektion_mot_snyder", d1 <= 0.5, f"SWEREF 99 TM skiljer {d1:.3f} m (gräns 0,5 m)")
    # 2
    g = geonames_se(o["place"])
    if g is None:
        chk("ort_mot_geonames", False, "orten finns inte i GeoNames (SE)")
    else:
        dd = hav(g[1], g[2], pt["lat"], pt["lon"])
        chk("ort_mot_geonames", dd <= 25000, f"punkten ligger {dd / 1000:.1f} km från orten {o['place']} (gräns 25 km)")
    if (o.get("address") or "").strip():
        # egen tolkning av adressen och egen sökning i OSM-adresserna (samma data, annan kod)
        import re
        m = re.match(r"^(.*?)[\s,]+(\d+\s*[a-zA-Z]?)\s*$", o["address"].strip())
        street = " ".join(_n(m.group(1) if m else o["address"]).replace(".", " ").split())
        nr = m.group(2).replace(" ", "").lower() if m else ""
        gk = pt["geokod"]
        data = O.load_addresses(gk["adresskalla"]) if gk.get("adresskalla") else {"adresser": [], "gator": {}}
        same = lambda a: " ".join(_n(a).replace(".", " ").split()) == street
        pop = o.get("pop", 0)
        Rg = 20000 if pop >= 500000 else 10000 if pop >= 50000 else 4000  # egen kopia av regeln "adressen ska ligga i orten"
        inplace = lambda a: _n(a.get("ort", "")) == _n(o["place"]) or hav(o["lat"], o["lon"], a["lat"], a["lon"]) <= Rg
        chk("adress_i_orten", hav(o["lat"], o["lon"], pt["lat"], pt["lon"]) <= Rg or gk.get("precision") == "hus",
            f"punkten ligger {hav(o['lat'], o['lon'], pt['lat'], pt['lon']) / 1000:.1f} km från ortens mitt (gräns {Rg / 1000:.0f} km om postorten inte stämmer)")
        if gk.get("precision") == "hus":
            cand = [a for a in data["adresser"] if same(a["gata"]) and a["nr"].replace(" ", "").lower() == nr and inplace(a)]
            okh = any(hav(a["lat"], a["lon"], pt["lat"], pt["lon"]) <= 1.0 for a in cand)
            chk("adress_mot_osm_adresser", okh and nr != "", f"{len(cand)} adresspunkter '{street} {nr}' i OSM; punkten är en av dem: {okh}")
        else:
            cand = [x for name, lst in data["gator"].items() if same(name) for x in lst]
            has_nr = nr and any(same(a["gata"]) and a["nr"].replace(" ", "").lower() == nr and inplace(a) for a in data["adresser"])
            okg = bool(cand) and min(hav(x["lat"], x["lon"], pt["lat"], pt["lon"]) for x in cand) <= 1500 and not has_nr
            chk("adress_mot_osm_adresser", okg, f"gatunivå: {len(cand)} vägdelar med namnet; punkten ≤ 1,5 km från gatan; husnumret saknas i OSM: {not has_nr}")
    # 3
    E, N = pt["E"], pt["N"]
    e0, n0, e1, n1 = ut["square"]
    for s in ("gsk", "hek", "ek"):
        idx = read_index(s)
        if s == "ek":
            lat_, lon_ = pt["lat"], pt["lon"]
            X, Y = tm_snyder(lat_, lon_, RT90)
            px_, py_ = float(X), float(Y)
        else:
            px_, py_ = E, N
        center = sorted(b["blad"] for b in idx if b["ring"][:, 0].min() <= px_ <= b["ring"][:, 0].max()
                        and b["ring"][:, 1].min() <= py_ <= b["ring"][:, 1].max() and winding_inside(px_, py_, b["ring"]))
        claimed = meta["blad"].get(s, [])
        ok_c = sorted(meta["mittblad"].get(s, [])) == center and (not claimed or not center or claimed[0]["blad"] in center)
        chk(f"{s}_mittblad_tacker_punkten", ok_c, f"index: {center}, produkten: {[b['blad'] for b in claimed][:1]}")
        # täckning av utsnittet (provpunkter)
        es, ns = np.meshgrid(np.linspace(e0, e1, 13), np.linspace(n0, n1, 13))
        if s == "ek":
            la, lo = tm_inverse(es, ns, SWEREF); XX, YY = tm_snyder(la, lo, RT90)
        else:
            XX, YY = es, ns
        need = []
        for b in idx:
            bx0, by0 = b["ring"].min(0); bx1, by1 = b["ring"].max(0)
            if bx1 < XX.min() or bx0 > XX.max() or by1 < YY.min() or by0 > YY.max():
                continue
            fr = np.mean([winding_inside(x, y, b["ring"]) for x, y in zip(XX.ravel(), YY.ravel())])
            if fr > 0.05:
                need.append(b["blad"])
        excl = meta.get("uteslutna_blad", {}).get(s, [])
        have = {b["blad"] for b in claimed} | {m["blad"] for m in meta.get("saknas_pa_ftp", {}).get(s, [])} | {m["blad"] for m in excl}
        # uteslutna blad: kontrollera själv att skanningen verkligen inte stämmer med indexet
        byb = {b["blad"]: b for b in idx}
        for ex in excl:
            b = byb.get(ex["blad"])
            if b is None:
                chk(f"{s}_uteslutet_{ex['blad']}", False, "uteslutet blad finns inte i indexet"); continue
            if Path(ex["lokal"]).exists():
                W_, H_ = Image.open(ex["lokal"]).size
            else:
                try:
                    W_, H_ = remote_tiff_size(ex["path"])
                except Exception as e:
                    chk(f"{s}_uteslutet_{ex['blad']}", False, f"kunde inte läsa bladets storlek: {e!r}"[:200]); continue
            g4 = corners4(b["ring"])
            w_m = math.dist(g4[0], g4[1]); h_m = math.dist(g4[0], g4[3])
            asp = (w_m / h_m) / (W_ / H_) - 1
            ok_ex = (s == "hek" and (abs(asp) > 0.02 or not 1.6 <= w_m / W_ <= 2.4)) or (s == "gsk" and ("kartram" in ex["skal"] or "format" in ex["skal"] or not 8.5 <= w_m / W_ * 1.35 <= 16))
            chk(f"{s}_uteslutet_{ex['blad']}", ok_ex, f"generatorn: {ex['skal']}; grinden: format {100 * asp:.1f} %, {w_m / W_:.2f} m/px")
        miss = sorted(set(need) - have)
        chk(f"{s}_alla_blad_med", not miss, f"{len(need)} blad täcker > 5 % av utsnittet; saknas: {miss}")
        yr = {b["blad"]: b["ar"] for b in idx}
        bad_y = [b["blad"] for b in claimed if yr.get(b["blad"]) != b["ar"]]
        chk(f"{s}_artal_mot_index", not bad_y, "årtalen stämmer med Lantmäteriets bladindex" if not bad_y else f"fel årtal: {bad_y}")
    # 4–7 per språk
    osm, _ = O.fetch(meta["osm"]["fraga"])
    idx_all = {s: {b["blad"]: b for b in read_index(s)} for s in ("gsk", "hek", "ek")}
    for lang, pdf in meta["files"].items():
        doc = fitz.open(pdf)
        page = doc[0]; H = page.rect.height
        text = "".join(p.get_text() for p in doc)
        miss = [m for m in MUST[lang] if m not in text]
        chk(f"{lang}_kallhanvisning", not miss, "källor, licenser och osäkerhetstext finns" if not miss else f"saknas: {miss}")
        badch = text.count(chr(0)) + text.count(chr(0xFFFD))
        chk(f"{lang}_inga_saknade_tecken", badch == 0, f"{badch} tecken utan glyf")
        chk(f"{lang}_sidor", len(doc) == 5 and abs(doc[0].rect.width - 841.9) < 2 and abs(doc[1].rect.width - 595.3) < 2,
            f"{len(doc)} sidor, affisch {doc[0].rect.width:.0f}×{doc[0].rect.height:.0f} pt, guide {doc[1].rect.width:.0f} pt bred")
        ptext = page.get_text()
        for pan in meta["geometry"][lang]["panels"]:
            s = pan["serie"]
            if s == "osm":
                x0, y0, x1, y1 = pan["bbox_pt"]
                pix = page.get_pixmap(dpi=60, clip=fitz.Rect(x0, H - y1, x1, H - y0))
                a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3].astype(int)
                inkf = (np.abs(a - np.median(a.reshape(-1, 3), 0)).sum(-1) > 60).mean()
                chk(f"{lang}_osm_panel_har_innehall", inkf > 0.03 and "OpenStreetMap" in ptext, f"bläck {inkf * 100:.1f} % av rutan")
                continue
            claimed = meta["blad"].get(s, [])
            if pan.get("kalla") == "saknas":
                chk(f"{lang}_{s}_panel_ej_tom", not claimed, "rutan säger att kartan saknas" + (" – men blad finns!" if claimed else ""))
                continue
            arr, emb_dpi = panel_array(page, pan["bbox_pt"], H)
            if arr is None:
                chk(f"{lang}_{s}_panel_bild", False, "ingen bild hittades i rutan"); continue
            chk(f"{lang}_{s}_inbaddad_upplosning", emb_dpi >= MIN_EMB_DPI, f"{emb_dpi:.0f} dpi (gräns {MIN_EMB_DPI})")
            # årtal i etiketten
            yrs = [idx_all[s][b["blad"]]["ar"].replace("-", "–") for b in claimed if b["blad"] in idx_all[s]]
            chk(f"{lang}_{s}_artal_tryckt", all(y and y in ptext for y in yrs if y), f"indexets år {yrs} i affischtexten")
            # 4. georeferering: slumpade pixlar
            n = arr.shape[0]
            m_out = (e1 - e0) / n
            ii = rng.integers(0, n, 400); jj = rng.integers(0, n, 400)
            Eo = e0 + (jj + 0.5) * m_out; No = n1 - (ii + 0.5) * m_out
            got = arr[ii, jj].astype(int)
            exp = np.full((400, 3), -1)
            bestd = np.zeros(400)
            src_m = []
            for b in claimed:
                ring = idx_all[s][b["blad"]]["ring"]
                if s == "gsk":
                    im0 = Image.open(b["lokal"])
                    fr = frame_lines_dark(im0, [tuple(c) for c in b["ram_horn_px"]])
                    chk(f"{lang}_gsk_kartram_{b['blad']}", fr >= 0.85, f"{fr * 100:.0f} % av punkterna längs kartytans kantlinjer (innerlinjen) är mörka (gräns 85 %)")
                f, im, spx = source_mapper(s, b, ring)
                src_m.append(spx)
                C, R = f(Eo + b["dE"], No + b["dN"])
                Wi, Hi = im.size
                if s == "gsk":
                    q = np.array(b["ram_horn_px"]); x0_, y0_, x1_, y1_ = q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()
                else:
                    tr = b.get("skannerkant_px", [0, 0, 0, 0])  # topp, botten, vänster, höger
                    x0_, y0_, x1_, y1_ = tr[2], tr[0], Wi - tr[3], Hi - tr[1]
                depth = np.minimum.reduce([C - x0_, x1_ - C, R - y0_, y1_ - R]) * spx
                # samma regel som produkten ska följa: djupast inne i ett blad vinner; 8 m marginal mot kanten
                ok = (depth > bestd) & (C >= 0) & (C < Wi - 1) & (R >= 0) & (R < Hi - 1)
                bestd = np.where(ok, depth, bestd)
                rgb = im.convert("RGB")
                half = max(0, int(m_out / spx / 2))
                for k in np.where(ok)[0]:
                    c_, r_ = int(C[k]), int(R[k])
                    box = rgb.crop((c_ - half, r_ - half, c_ + half + 1, r_ + half + 1))
                    exp[k] = np.asarray(box).reshape(-1, 3).mean(0)
            exp[bestd < 3 * m_out] = -1  # nära en bladkant: för känsligt för avrundning, hoppa över
            val = exp[:, 0] >= 0
            if val.sum() < 100:
                chk(f"{lang}_{s}_georeferering", False, f"bara {val.sum()} provpunkter i källbladen"); continue
            dev = np.abs(got[val] - exp[val]).mean(1)
            med, p90 = float(np.median(dev)), float(np.percentile(dev, 90))
            chk(f"{lang}_{s}_georeferering", med <= 22 and p90 <= 60,
                f"{val.sum()} provpunkter: färgavvikelse median {med:.1f}, 90:e percentil {p90:.1f} (gränser 22 / 60)")
            # 6. upplösning ur originalet
            pin = (pan["bbox_pt"][2] - pan["bbox_pt"][0]) / 72
            sdpi = (e1 - e0) / max(src_m) / pin
            chk(f"{lang}_{s}_kall_upplosning", sdpi >= MIN_SRC_DPI, f"{sdpi:.0f} dpi ur originalskanningen (gräns {MIN_SRC_DPI})")
            # tom ruta?
            papf = (np.abs(arr.astype(int) - PAPER).sum(-1) < 10).mean()
            chk(f"{lang}_{s}_ej_tom", arr.std() > 12 and papf <= 1 - pan.get("tackning", 1) + 0.03,
                f"std {arr.std():.1f}, papper {papf * 100:.1f} %, täckning enligt generatorn {pan.get('tackning', 0) * 100:.1f} %")
            # 5. passning (bara första språket – samma bild)
            if lang == list(meta["files"])[0]:
                P5 = meta["passning"].get(s, {})
                gen_ok = bool(P5.get("efter", {}).get("matbar"))
                adj = P5.get("justering", {"ost_m": 0.0, "nord_m": 0.0})
                alltext = "".join(p.get_text() for p in doc)
                # a) bilden använder exakt Lantmäteriets georeferens + den deklarerade justeringen, inom taket
                cons = all(abs(b["dE"] - adj["ost_m"]) < 0.01 and abs(b["dN"] - adj["nord_m"]) < 0.01 for b in claimed)
                cap = ADJ_CAP[s]
                chk(f"passning_{s}_deklarerad", cons and math.hypot(adj["ost_m"], adj["nord_m"]) <= cap,
                    f"bladens förskjutning = deklarerad justering ({adj['ost_m']:.0f}, {adj['nord_m']:.0f}) m: {cons}; tak {cap:.0f} m")
                # b) PDF:en säger vad som gäller
                if gen_ok:
                    chk(f"passning_{s}_tryckt", OK_TEXT[lang] in alltext, "PDF:en redovisar uppmätt passning")
                else:
                    chk(f"passning_{s}_tryckt", NA_TEXT[lang] in alltext and f"–{int(CLAIM_NA[s])} m" in alltext,
                        f"PDF:en ska säga att passningen inte kunde mätas och ange osäkerhet upp till {CLAIM_NA[s]:.0f} m")
                # c) oberoende mätning mot dagens vatten (grindens egen metod): kartan ska ligga inom den gräns som
                #    PDF:en utlovar – uppmätt passning: CHECK_M, annars den tryckta osäkerheten
                T = CHECK_M[s] if gen_ok else max(CLAIM_NA[s], CHECK_M[s])
                cov_full = np.abs(arr.astype(int) - PAPER).sum(-1) >= 10
                verdict, det = fit_decide(arr, cov_full, osm, (e0, n0, e1, n1), T)
                chk(f"passning_{s}_mot_osm", verdict != "fald", f"{verdict} (gräns {T:.0f} m): {det}")
        # adressmarkören (röd ring) på adressens plats i varje affischruta
        red = [d for d in page.get_drawings() if d.get("color") and abs(d["color"][0] - 0.72) < 0.02 and abs(d["color"][1] - 0.12) < 0.02]
        miss_m = []
        for p in meta["geometry"][lang]["panels"]:
            bx0, by0, bx1, by1 = p["bbox_pt"]
            ex = bx0 + (E - e0) / (e1 - e0) * (bx1 - bx0); ey = by0 + (N - n0) / (n1 - n0) * (by1 - by0)
            if not any(abs((d["rect"].x0 + d["rect"].x1) / 2 - ex) < 1.5 and abs(H - (d["rect"].y0 + d["rect"].y1) / 2 - ey) < 1.5 for d in red):
                miss_m.append(p["serie"])
        chk(f"{lang}_adressmarkor", not miss_m, f"{len(red)} röda markörer; saknas på adressens plats i: {miss_m}")
    passed = all(c["ok"] for c in checks)
    res = {"order": o["id"], "product": "historisk", "godkand": passed, "antal_grindar": len(checks),
           "underkanda": [c for c in checks if not c["ok"]], "alla": checks}
    json.dump(res, open(Path(meta_path).with_name(f"{o['id']}_qc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    t = time.perf_counter()
    r = run(sys.argv[1])
    print(json.dumps({"order": r["order"], "godkand": r["godkand"], "grindar": r["antal_grindar"],
                      "underkanda": r["underkanda"], "tid_s": round(time.perf_counter() - t, 2)}, ensure_ascii=False, indent=1))
    sys.exit(0 if r["godkand"] else 1)
