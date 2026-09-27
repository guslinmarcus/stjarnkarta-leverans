"""Lantmäteriets historiska kartor från den öppna FTP:n: kartbladsindex, hämtning (cache), georeferering
och omsampling till ett gemensamt SWEREF 99 TM-rutnät.

Källa: ftp://download-opendata.lantmateriet.se/ (utan konto). Licens enligt Lantmäteriet: CC0
("Produkten är avgiftsfri och får användas och publiceras fritt, enligt Creative Commons, CC0"); kartorna är
dessutom äldre än 70 år (Ekonomiska kartan: tryckår 1935–1978, där CC0 gäller). FTP-filerna innehåller ingen
licenstext – licensen är dokumenterad på Geotorget (se LUCKOR L21) och trycks på produkten.

Serier (bladindex = Lantmäteriets egna shapefiler på FTP:n):
  hek  Häradsekonomiska kartan 1859–1934, 1:20 000, skannad utan marginal, ingen georeferens i filen
       → bildens hörn = bladpolygonens hörn (Lantmäteriets bladindex, SWEREF 99 TM)
  gsk  Generalstabskartan 1827–1971, 1:100 000, skannad MED pappersmarginal → kartramen hittas i bilden
       (fyra linjer anpassas robust) och ramens hörn = bladpolygonens hörn
  ek   Ekonomiska kartan 1935–1978, 1:10 000/1:20 000, GeoTIFF i RT 90 2,5 gon V, 1 m/pixel

Lantmäteriet skriver själva att georeferensen för de äldre kartorna är "inte exakta utan mer generella".
Därför mäts passningen i varje order mot dagens vatten i OpenStreetMap (historisk.py/grinden) och trycks.
"""
import gzip
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

import kartgeo as K

Image.MAX_IMAGE_PIXELS = None
ROOT = Path(__file__).parent
LM = ROOT / "data" / "lm"
CACHE = Path(os.environ.get("LM_CACHE", LM / "cache"))
INDEX = LM / "kartblad_index.json.gz"
FTP = "ftp://download-opendata.lantmateriet.se"
CACHE_MAX_BYTES = int(os.environ.get("LM_CACHE_MAX_GB", "8")) * 1024 ** 3

SERIES = {
    "hek": dict(namn="Häradsekonomiska kartan", skala="1:20 000", period="1859–1934", crs="sweref"),
    "gsk": dict(namn="Generalstabskartan", skala="1:100 000", period="1827–1971", crs="sweref"),
    "ek": dict(namn="Ekonomiska kartan", skala="1:10 000 / 1:20 000", period="1935–1978", crs="rt90"),
}
_SHP = {  # indexfil på FTP:n -> (serie, katalog för bladen)
    "gsk_norra": ("gsk", "Generalstabskartan/Generalstabskartan_Norra",
                  "Generalstabskartan/Generalstabskartan_Norra/Generalstabskartan_Norra"),
    "gsk_sodra": ("gsk", "Generalstabskartan/Generalstabskartan_Södra",
                  "Generalstabskartan/Generalstabskartan_Södra/Generalstabskartan_Södra"),
    "hek_norra": ("hek", "Haradsekonomiska_kartan/Häradsekonomiska_Kartan_Norra",
                  "Haradsekonomiska_kartan/Häradsekonomiska_Kartan_Norra/Häradsekonomiska_kartan_norra"),
    "hek_sodra": ("hek", "Haradsekonomiska_kartan/Häradsekonomiska_Kartan_Södra",
                  "Haradsekonomiska_kartan/Häradsekonomiska_Kartan_Södra/Häradsekonomiska_kartan_södra"),
    "ek_rutor": ("ek", "Ekonomiska_kartan", "Ekonomiska_kartan/Bladindelningskartor/Shape/EkoRutor"),
}


def ftp_url(path):
    return FTP + "/" + urllib.parse.quote(path)


def fetch(path, dest, tries=3):
    """Hämta en fil från Lantmäteriets FTP till dest (atomiskt). Returnerar sekunder."""
    dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
    t = time.perf_counter()
    tmp = dest.with_suffix(dest.suffix + ".part")
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(ftp_url(path), timeout=300) as r, open(tmp, "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            os.replace(tmp, dest)
            return time.perf_counter() - t
        except Exception as e:
            err = e; time.sleep(5 * (i + 1))
    raise RuntimeError(f"FTP-hämtning misslyckades {path}: {err!r}")


def ensure_index_shapefiles():
    d = LM / "index"
    for name, (_, _, remote) in _SHP.items():
        for ext in ("shp", "dbf", "prj"):
            p = d / f"{name}.{ext}"
            if not p.exists():
                fetch(f"{remote}.{ext}", p)
    return d


def build_index():
    """Bygg kartblad_index.json.gz ur Lantmäteriets bladindex (körs en gång, resultatet checkas in)."""
    d = ensure_index_shapefiles()
    out = []
    for name, (serie, folder, _) in _SHP.items():
        polys = K.read_shp_polygons(d / f"{name}.shp")
        rows = K.read_dbf(d / f"{name}.dbf")
        for poly, row in zip(polys, rows):
            if not poly:
                continue
            ring = poly[0]
            if serie == "ek":
                path = f"{folder}/{row['location']}"
                blad, bnamn, ar = row["ekoruta"], row["ekoruta"], row["År"]
                ar = f"19{ar}" if len(ar) == 2 else ar
            else:
                b = row["Bladnummer"]
                num = b[1:].split("-", 1)
                path = f"{folder}/{b}/{num[0]}_{num[1]}_0.tif"
                blad, bnamn, ar = b, row["Bladnamn"], row["År"]
            xs, ys = [p[0] for p in ring], [p[1] for p in ring]
            out.append({"serie": serie, "blad": blad, "namn": bnamn, "ar": ar, "path": path,
                        "ring": [[round(x, 2), round(y, 2)] for x, y in ring],
                        "bbox": [min(xs), min(ys), max(xs), max(ys)]})
    json.dump({"kalla": "Lantmäteriets bladindex på ftp://download-opendata.lantmateriet.se/ (hämtat 2026-09-27)",
               "blad": out}, gzip.open(INDEX, "wt", encoding="utf-8"), ensure_ascii=False)
    return out


_IDX = None


def index():
    global _IDX
    if _IDX is None:
        if not INDEX.exists():
            build_index()
        _IDX = json.load(gzip.open(INDEX, "rt", encoding="utf-8"))["blad"]
    return _IDX


def sheets_for_square(e0, n0, e1, n1, serie, samples=11):
    """Blad i serien som täcker någon del av kvadraten (SWEREF 99 TM). Returnerar lista med
    (blad, andel av provpunkterna som bladet täcker)."""
    es = np.linspace(e0, e1, samples); ns = np.linspace(n0, n1, samples)
    EE, NN = np.meshgrid(es, ns)
    if SERIES[serie]["crs"] == "rt90":
        la, lo = K.from_sweref(EE, NN)
        XX, YY = K.to_rt90(la, lo)
    else:
        XX, YY = EE, NN
    x0, y0, x1, y1 = XX.min(), YY.min(), XX.max(), YY.max()
    out = []
    for b in index():
        if b["serie"] != serie:
            continue
        bx0, by0, bx1, by1 = b["bbox"]
        if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
            continue
        hit = sum(K.point_in_ring(x, y, b["ring"]) for x, y in zip(XX.ravel(), YY.ravel()))
        if hit:
            out.append((b, hit / XX.size))
    return out


def sheet_for_point(e, n, serie):
    if SERIES[serie]["crs"] == "rt90":
        la, lo = K.from_sweref(e, n); x, y = K.to_rt90(la, lo)
    else:
        x, y = e, n
    return [b for b in index() if b["serie"] == serie and b["bbox"][0] <= x <= b["bbox"][2]
            and b["bbox"][1] <= y <= b["bbox"][3] and K.point_in_ring(x, y, b["ring"])]


SIZE_CACHE = LM / "bladstorlek.json"


def _ftp_range(path, offset, n):
    """Läs n byte från offset ur en FTP-fil (REST + avbruten RETR)."""
    import ftplib
    ftp = ftplib.FTP("download-opendata.lantmateriet.se", timeout=120)
    ftp.login()
    ftp.voidcmd("TYPE I")
    buf = bytearray()
    conn = ftp.transfercmd("RETR " + path, rest=offset)
    try:
        while len(buf) < n:
            chunk = conn.recv(min(65536, n - len(buf)))
            if not chunk:
                break
            buf += chunk
    finally:
        conn.close()
        try:
            ftp.abort()
        except Exception:
            pass
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    return bytes(buf)


def remote_size(b):
    """(bredd, höjd) i pixlar för ett blad utan att hämta hela filen (TIFF-huvudet via två små läsningar).
    Cachas i data/lm/bladstorlek.json."""
    import struct
    cache = json.loads(SIZE_CACHE.read_text(encoding="utf-8")) if SIZE_CACHE.exists() else {}
    if b["path"] in cache:
        return tuple(cache[b["path"]])
    lp = local_file(b)
    if lp.exists():
        wh = Image.open(lp).size
    else:
        head = _ftp_range(b["path"], 0, 16)
        off = struct.unpack("<I", head[4:8])[0]
        n = struct.unpack("<H", _ftp_range(b["path"], off, 2))[0]
        ifd = _ftp_range(b["path"], off + 2, 12 * n)
        tags = {}
        for i in range(n):
            tag, typ, cnt, val = struct.unpack("<HHII", ifd[12 * i:12 * i + 12])
            tags[tag] = (val & 0xFFFF) if typ == 3 else val
        wh = (tags[256], tags[257])
    cache[b["path"]] = list(wh)
    SIZE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    return tuple(wh)


def precheck(b):
    """Stoppa blad vars skanning uppenbart inte motsvarar bladindexets polygon – innan de hämtas.
    Returnerar None om bladet kan användas, annars skälet."""
    if b["serie"] != "hek":
        return None
    W, H = remote_size(b)
    geo = ring_corners(b["ring"])
    w_m = float(np.hypot(geo[1][0] - geo[0][0], geo[1][1] - geo[0][1]))
    h_m = float(np.hypot(geo[3][0] - geo[0][0], geo[3][1] - geo[0][1]))
    asp = (w_m / h_m) / (W / H) - 1
    mpx = (w_m / W + h_m / H) / 2
    bad = []
    if abs(asp) > 0.02:
        bad.append(f"skanningens format avviker {100 * asp:.1f} % från bladindexets")
    if not 1.6 <= mpx <= 2.4:
        bad.append(f"{mpx:.2f} m/pixel ligger utanför 1.6–2.4")
    return "; ".join(bad) or None


def local_file(b):
    return CACHE / b["serie"] / Path(b["path"]).name


def get_sheet(b):
    """Returnerar (lokal fil, hämtningstid i s – 0 om cache)."""
    p = local_file(b)
    if p.exists():
        os.utime(p)
        return p, 0.0
    prune_cache()
    return p, fetch(b["path"], p)


def prune_cache():
    files = sorted((f for f in CACHE.rglob("*.tif")), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    while files and total > CACHE_MAX_BYTES:
        f = files.pop(0); total -= f.stat().st_size; f.unlink()


# ------------------------------------------------------------------ georeferering
def ring_corners(ring):
    """Bladpolygonens fyra hörn i ordningen NV, NO, SO, SV."""
    pts = [tuple(p) for p in ring[:-1]] if tuple(ring[0]) == tuple(ring[-1]) else [tuple(p) for p in ring]
    if len(pts) > 4:  # ta de fyra punkter som ligger längst från centrum i varje kvadrant
        cx, cy = np.mean([p[0] for p in pts]), np.mean([p[1] for p in pts])
        q = {}
        for p in pts:
            k = (p[0] > cx, p[1] > cy)
            if k not in q or (p[0] - cx) ** 2 + (p[1] - cy) ** 2 > (q[k][0] - cx) ** 2 + (q[k][1] - cy) ** 2:
                q[k] = p
        pts = list(q.values())
    s = sorted(pts, key=lambda p: -p[1])
    top, bot = sorted(s[:2]), sorted(s[2:])
    return [top[0], top[1], bot[1], bot[0]]


def homography(src, dst):
    """3×3 H så att dst ~ H·src (fyra punktpar)."""
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    _, _, vt = np.linalg.svd(np.array(A, float))
    H = vt[-1].reshape(3, 3)
    return H / H[2, 2]


def apply_h(H, x, y):
    w = H[2, 0] * x + H[2, 1] * y + H[2, 2]
    return (H[0, 0] * x + H[0, 1] * y + H[0, 2]) / w, (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / w


def _robust_line(ts, vs):
    """Anpassa v = a·t + b robust (iterativt bort med avvikare). Returnerar (a, b, rms, antal)."""
    ts, vs = np.asarray(ts, float), np.asarray(vs, float)
    keep = np.ones(len(ts), bool)
    a, b = 0.0, float(np.median(vs))
    for _ in range(6):
        if keep.sum() < 4:
            break
        a, b = np.polyfit(ts[keep], vs[keep], 1)
        r = np.abs(vs - (a * ts + b))
        thr = max(3.0, 3 * np.median(r[keep]))
        keep = r < thr
    r = np.abs(vs - (a * ts + b))[keep]
    return a, b, float(np.sqrt((r ** 2).mean())) if len(r) else 99.0, int(keep.sum())


def find_frame(gray):
    """Hitta Generalstabskartans tjocka kartram. Returnerar hörn (NV, NO, SO, SV) i pixlar + kvalitet."""
    H, W = gray.shape
    dark = gray < 115

    def first_thick(line, vals):
        # första mörka följd på 3–30 px (i halverad upplösning) räknat från papperskanten, med medelvärde < 90
        # (kartramen är en tjock, nästan svart linje; text och blyerts är tunnare eller ljusare)
        run = 0
        for i, d in enumerate(line):
            if d:
                run += 1
            else:
                if 3 <= run <= 30 and vals[i - run:i].mean() < 90:
                    return i - run / 2
                run = 0
        return None

    fits = {}
    for side in ("top", "bottom", "left", "right"):
        ts, vs = [], []
        span = np.linspace(0.12, 0.88, 61)
        for f in span:
            if side in ("top", "bottom"):
                x = int(f * W); lim = int(H * 0.35)
                sl = slice(None, lim) if side == "top" else slice(H - 1, H - 1 - lim, -1)
                v = first_thick(dark[sl, x], gray[sl, x])
                if v is not None:
                    ts.append(x + 0.5); vs.append(v if side == "top" else H - v)
            else:
                y = int(f * H); lim = int(W * 0.35)
                sl = slice(None, lim) if side == "left" else slice(W - 1, W - 1 - lim, -1)
                v = first_thick(dark[y, sl], gray[y, sl])
                if v is not None:
                    ts.append(y + 0.5); vs.append(v if side == "left" else W - v)
        fits[side] = _robust_line(ts, vs)

    def inter(h, v):  # h: y = a x + b ; v: x = c y + d
        a, b = h[0], h[1]; c, d = v[0], v[1]
        y = (a * d + b) / (1 - a * c); x = c * y + d
        return (x, y)
    # Innanför den tjocka ramen ligger en marginal med ortnamn; sedan kommer minutskalan: två tunna linjer
    # (≈ 60 och ≈ 78 px in i full upplösning) med streck emellan. Kartytan börjar vid den inre av dem, och
    # bladindexets polygon motsvarar kartytan (med ytterramen blev felet 300–850 m i testerna).
    inner, qi = {}, {}
    for side, (a, b, rms, npt) in fits.items():
        horiz = side in ("top", "bottom")
        sign = 1 if side in ("top", "left") else -1
        ts = np.linspace(0.1, 0.9, 400) * (W if horiz else H)
        ti = np.clip(np.round(ts).astype(int), 0, (W if horiz else H) - 1)
        fr = np.zeros(80)
        for d in range(80):
            vals = []
            for dd in (-1, 0, 1):
                vi = np.clip(np.round(a * ts + b + sign * (d + dd)).astype(int), 0, (H if horiz else W) - 1)
                vals.append(gray[vi, ti] if horiz else gray[ti, vi])
            fr[d] = float((np.min(vals, axis=0) < 175).mean())
        peaks = [d for d in range(12, 70) if fr[d] >= 0.35 and fr[d] == fr[max(0, d - 2):d + 3].max()
                 and fr[d] - fr[max(0, d - 6):d].min() >= 0.25]
        # slå ihop toppar som ligger intill varandra
        pk = []
        for d in peaks:
            if not pk or d - pk[-1] > 3:
                pk.append(d)
        choice = pk[1] if len(pk) >= 2 and 4 <= pk[1] - pk[0] <= 14 else None
        if choice is None:  # bara den inre linjen syns: godta en ensam topp där innerlinjen brukar ligga (68–92 px)
            lone = [d for d in pk if 34 <= d <= 46]
            choice = lone[0] if len(lone) == 1 else None
        qi[side] = {"toppar_px": [2 * d for d in pk], "forskjutning_px": 2 * choice if choice else None, "hittad": choice is not None}
        inner[side] = (a, b + sign * choice if choice else b)
    t, bm, l, r = fits["top"], fits["bottom"], fits["left"], fits["right"]
    corners_outer = [inter(t, l), inter(t, r), inter(bm, r), inter(bm, l)]
    t, bm, l, r = inner["top"], inner["bottom"], inner["left"], inner["right"]
    corners = [inter(t, l), inter(t, r), inter(bm, r), inter(bm, l)]
    q = {s: {"rms_px": round(f[2], 2), "punkter": f[3], "innerlinje": qi[s]} for s, f in fits.items()}
    q["ytterram_horn_px"] = [[round(x * 2, 1), round(y * 2, 1)] for x, y in corners_outer]
    return corners, q


class SheetInvalid(Exception):
    """Bladet går inte att georeferera automatiskt (t.ex. skanningen motsvarar inte bladindexets polygon)."""


def _dark_trim(gray, thr=100, maxfrac=0.04):
    """Antal pixlar mörk skannerkant att skala bort per sida (topp, botten, vänster, höger)."""
    H, W = gray.shape
    out = []
    for arr in (gray, gray[::-1], gray.T, gray.T[::-1]):
        k = 0
        lim = int(maxfrac * arr.shape[0])
        while k < lim and arr[k].mean() < thr:
            k += 1
        out.append(k)
    return out


class Sheet:
    """Ett georefererat blad: fil + funktion (E, N) i SWEREF 99 TM -> (kolumn, rad) i bilden."""

    def __init__(self, b, path):
        self.b, self.path = b, Path(path)
        self.serie = b["serie"]
        im = Image.open(self.path)
        self.W, self.H = im.size
        self.info = {"blad": b["blad"], "namn": b["namn"], "ar": b["ar"], "fil": self.path.name,
                     "pixlar": [self.W, self.H]}
        self.dE = self.dN = 0.0  # passningsjustering (m): kartans innehåll vid (E+dE, N+dN) hör till (E, N)
        self.lut = None  # färgjustering mot grannbladen (3×256, uint8), sätts av harmonize(); None = originalfärger
        self.frame_px = None
        self.valid = (0, 0, self.W, self.H)
        if self.serie == "ek":
            tp = im.tag_v2.get(33922); sc = im.tag_v2.get(33550)
            if not tp or not sc:
                raise RuntimeError(f"GeoTIFF-taggar saknas i {self.path.name}")
            self.x0, self.y0, self.px = tp[3], tp[4], sc[0]
            self.info.update(georef="GeoTIFF (RT 90 2,5 gon V)", m_per_px=round(sc[0], 3))
        else:
            src_px = [(0, 0), (self.W, 0), (self.W, self.H), (0, self.H)]
            if self.serie == "gsk":
                g = np.asarray(im.convert("L").reduce(2)) if max(self.W, self.H) > 2000 else np.asarray(im.convert("L"))
                corners, q = find_frame(g)
                src_px = [(x * 2, y * 2) for x, y in corners]
                self.frame_px = src_px
                self.info["ram"] = q
                self.info["ram_horn_px"] = [[round(x, 1), round(y, 1)] for x, y in src_px]
            if self.serie == "hek":
                g4 = np.asarray(im.convert("L").reduce(4))
                t, bt, l, r = (4 * v for v in _dark_trim(g4))
                self.valid = (l, t, self.W - r, self.H - bt)
                self.info["skannerkant_px"] = [t, bt, l, r]
            geo = ring_corners(b["ring"])
            self.H_geo2px = homography(geo, src_px)
            w_m = np.hypot(geo[1][0] - geo[0][0], geo[1][1] - geo[0][1])
            w_px = np.hypot(src_px[1][0] - src_px[0][0], src_px[1][1] - src_px[0][1])
            h_m = np.hypot(geo[3][0] - geo[0][0], geo[3][1] - geo[0][1])
            h_px = np.hypot(src_px[3][0] - src_px[0][0], src_px[3][1] - src_px[0][1])
            self.px = float((w_m / w_px + h_m / h_px) / 2)
            self.info.update(georef="bladindexets hörn" if self.serie == "hek" else "kartramen i skanningen + bladindexets hörn",
                             m_per_px=round(self.px, 3), skalskillnad_bredd_hojd_pct=round(100 * ((w_m / w_px) / (h_m / h_px) - 1), 2),
                             horn_sweref=[[round(x, 1), round(y, 1)] for x, y in geo])
            asp = (w_m / h_m) / (w_px / h_px) - 1
            self.info["bildformat_mot_bladindex_pct"] = round(100 * asp, 2)
            lo, hi = {"hek": (1.6, 2.4), "gsk": (8.5, 11.5)}[self.serie]
            bad = []
            # HEK: bilden antas vara bladet → formatet måste stämma. GSK: ramen hittas i bilden, så ojämn
            # papperskrympning (några %) rättas av hörnpassningen – bara grova avvikelser stoppar.
            if abs(asp) > (0.02 if self.serie == "hek" else 0.06):
                bad.append(f"skanningens format avviker {100 * asp:.1f} % från bladindexets")
            if not lo <= self.px <= hi:
                bad.append(f"{self.px:.2f} m/pixel ligger utanför {lo}–{hi}")
            if self.serie == "gsk" and any(q["rms_px"] > 3 or q["punkter"] < 20 or not q["innerlinje"]["hittad"]
                                           for k, q in self.info["ram"].items() if k != "ytterram_horn_px"):
                bad.append("kartramen hittades inte säkert")
            if bad:
                im.close()
                raise SheetInvalid("; ".join(bad))
        im.close()

    def to_px(self, E, N):
        E, N = E + self.dE, N + self.dN
        if self.serie == "ek":
            la, lo = K.from_sweref(E, N)
            X, Y = K.to_rt90(la, lo)
            return (X - self.x0) / self.px, (self.y0 - Y) / self.px
        return apply_h(self.H_geo2px, E, N)


def resample(sheets, e0, n0, e1, n1, npx, paper=(238, 233, 220), with_who=False, raw=False):
    """Bygg en bild npx×npx över kvadraten ur ett eller flera blad. Där blad överlappar vinner det blad där
    punkten ligger längst in från bladets kant (ramens marginal och skannerkanter hamnar då under grannbladet).
    Returnerar (RGB-array, täckningsmask, per-blad-andel)."""
    from scipy.ndimage import map_coordinates
    m_out = (e1 - e0) / npx
    es = e0 + (np.arange(npx) + 0.5) * m_out
    ns = n1 - (np.arange(npx) + 0.5) * m_out
    EE, NN = np.meshgrid(es, ns)
    out = np.empty((npx, npx, 3), np.uint8); out[:] = paper
    cov = np.zeros((npx, npx), bool)
    best = np.zeros((npx, npx))
    who = np.full((npx, npx), -1)
    andel = {}
    for si, s in enumerate(sheets):
        C, R = s.to_px(EE, NN)
        v0, v1, v2, v3 = s.valid
        m = (C >= v0) & (C < v2 - 1) & (R >= v1) & (R < v3 - 1)
        if s.frame_px:  # bara innanför kartramen
            ring = list(s.frame_px) + [s.frame_px[0]]
            m &= _in_quad(C, R, ring)
            fx = [p[0] for p in s.frame_px]; fy = [p[1] for p in s.frame_px]
            v0, v1, v2, v3 = min(fx), min(fy), max(fx), max(fy)
        depth = np.minimum.reduce([C - v0, v2 - C, R - v1, v3 - R]) * s.px
        m &= depth > best
        best = np.where(m, depth, best)
        who[m] = si
        if not m.any():
            continue
        c0, c1 = int(max(0, np.floor(C[m].min()) - 2)), int(min(s.W, np.ceil(C[m].max()) + 3))
        r0, r1 = int(max(0, np.floor(R[m].min()) - 2)), int(min(s.H, np.ceil(R[m].max()) + 3))
        im = Image.open(s.path).crop((c0, r0, c1, r1)).convert("RGB")
        f = max(1, int(m_out / s.px))  # förminska först (lådfilter) för att undvika vikning
        if f > 1:
            im = im.reduce(f)
        a = np.asarray(im, np.float32)
        cc, rr = (C[m] - c0) / f - 0.5, (R[m] - r0) / f - 0.5  # kantkoordinat -> index för pixelcentrum
        for ch in range(3):
            v = np.clip(map_coordinates(a[..., ch], [rr, cc], order=1, mode="nearest"), 0, 255).astype(np.uint8)
            if s.lut is not None and not raw:
                v = s.lut[ch][v]
            out[..., ch][m] = v
        cov |= m
    for si, s in enumerate(sheets):  # andel per blad räknas efter att alla blad lagts (senare blad kan ta över)
        andel[s.b["blad"]] = float((who == si).mean())
    if with_who:
        return out, cov, andel, who
    return out, cov, andel


def _quantile_lut(src, dst, nq=33):
    """Monoton färgkurva per kanal som flyttar src-pixlarnas fördelning till dst:s (kvantilmatchning)."""
    q = np.linspace(0.03, 0.97, nq)
    lut = np.zeros((3, 256), np.uint8)
    x = np.arange(256, dtype=float)
    for ch in range(3):
        a = np.quantile(src[:, ch], q); b = np.quantile(dst[:, ch], q)
        a = np.maximum.accumulate(a + np.arange(nq) * 1e-3)  # strikt växande
        y = np.interp(x, a, b)
        y[x < a[0]] = b[0] - (a[0] - x[x < a[0]])     # utanför: parallellförskjutning
        y[x > a[-1]] = b[-1] + (x[x > a[-1]] - a[-1])
        lut[ch] = np.clip(np.maximum.accumulate(y), 0, 255).astype(np.uint8)
    return lut


def harmonize(sheets, e0, n0, e1, n1, npx=600, band_m=500.0, seam_m=60.0):
    """Färgjustera bladen i ett utsnitt så att skarvarna inte syns. Bladet med störst andel är referens; övriga
    justeras i tur och ordning (grannar till redan justerade först) med kvantilmatchning av pixlarna i ett band
    (band_m) på var sida om den gemensamma kanten. Kurvorna sparas i sh.lut och används av resample().
    Returnerar mätningen per skarv: medelfärgsskillnad (|ΔR|+|ΔG|+|ΔB|) i ett smalt band (seam_m) på var
    sida om kanten, före och efter justeringen."""
    from scipy.ndimage import binary_dilation, distance_transform_edt
    for s in sheets:
        s.lut = None
    if len(sheets) < 2:
        return {"blad": len(sheets), "skarvar": []}
    img, cov, andel, who = resample(sheets, e0, n0, e1, n1, npx, with_who=True, raw=True)
    m_px = (e1 - e0) / npx
    bw = max(2, int(round(band_m / m_px))); sw = max(1, int(round(seam_m / m_px)))
    present = [i for i in range(len(sheets)) if (who == i).any()]
    if len(present) < 2:
        return {"blad": len(present), "skarvar": []}
    dist = {i: distance_transform_edt(who != i) for i in present}  # avstånd (px) till bladets yta
    adj = {i: {j for j in present if j != i and ((who == i) & (dist[j] <= 1.5)).sum() >= 5} for i in present}
    ref = max(present, key=lambda i: (who == i).sum())
    done, order = {ref}, []
    cur = img.astype(np.float32)
    while len(done) < len(present):
        cand = [i for i in present if i not in done and adj[i] & done]
        if not cand:  # ej sammanhängande: justera mot referensen via hela utsnittet
            cand = [i for i in present if i not in done]
        i = max(cand, key=lambda k: ((who == k) & (np.minimum.reduce([dist[j] for j in done]) <= bw)).sum())
        near_done = np.minimum.reduce([dist[j] for j in done])
        src = cur[(who == i) & (near_done <= bw)]
        dst = cur[np.isin(who, list(done)) & (dist[i] <= bw)]
        if len(src) < 200 or len(dst) < 200:
            src, dst = cur[who == i], cur[np.isin(who, list(done))]
        lut = _quantile_lut(src, dst)
        sheets[i].lut = lut
        m = who == i
        for ch in range(3):
            cur[..., ch][m] = lut[ch][img[..., ch][m]]
        done.add(i); order.append(i)

    def seam_diff(arr):
        res = []
        for i in present:
            for j in adj[i]:
                if j <= i:
                    continue
                a_ = arr[(who == i) & (dist[j] <= sw)]; b_ = arr[(who == j) & (dist[i] <= sw)]
                if len(a_) < 20 or len(b_) < 20:
                    continue
                res.append(((sheets[i].b["blad"], sheets[j].b["blad"]), float(np.abs(a_.mean(0) - b_.mean(0)).sum())))
        return res
    before = dict(seam_diff(img.astype(np.float32))); after = dict(seam_diff(cur))
    return {"blad": len(present), "referens": sheets[ref].b["blad"],
            "skarvar": [{"blad": list(k), "fore": round(before[k], 1), "efter": round(after[k], 1)} for k in after],
            "max_efter": round(max(after.values()), 1) if after else 0.0,
            "lut_max_andring": {sheets[i].b["blad"]: int(np.abs(sheets[i].lut.astype(int) - np.arange(256)).max()) for i in order}}


def _in_quad(E, N, ring):
    inside = np.zeros(E.shape, bool)
    j = len(ring) - 2
    for i in range(len(ring) - 1):
        xi, yi = ring[i]; xj, yj = ring[j]
        cond = ((yi > N) != (yj > N)) & (E < (xj - xi) * (N - yi) / ((yj - yi) or 1e-300) + xi)
        inside ^= cond
        j = i
    return inside
