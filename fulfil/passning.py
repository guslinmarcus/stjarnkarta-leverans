"""Passningsmätning: hur väl en historisk karta ligger på dagens karta, mätt mot vattnet i OpenStreetMap.

Metod (generatorns): OSM-vattnet rastras på samma rutnät. En linjär diskriminant (LDA på RGB) lär sig
kartans vattenfärg ur de pixlar som OSM säger är vatten/land, och ger en "vattenlikhet" per pixel.
Förskjutningen = toppen i korskorrelationen (FFT) mellan vattenlikheten och OSM-vattnet inom ±max_m.
Grinden gör en egen, oberoende mätning (grind_historisk.py: annan klassning, brute-force-sökning).

Mätningen säger något bara där det finns både vatten och land i utsnittet. Annars: None (ej mätbar).
Obs: strandlinjer har ändrats sedan 1800-talet (landhöjning, utfyllnad), så en rest på 20–60 m är väntad
även för en perfekt passad karta.
"""
import numpy as np
from PIL import Image, ImageDraw


def rasterize_water(F, to_grid, e0, n0, e1, n1, npx):
    """OSM-lager (osmdata.parse_features) → bool-mask (True = vatten) på rutnätet (rad 0 = norr)."""
    import osmdata as O
    m_px = (e1 - e0) / npx

    def pix(x, y):
        return ((x - e0) / m_px, (n1 - y) / m_px)
    coast = [O.project_line(to_grid, l) for l in F["coast"]]
    im = Image.new("L", (npx, npx), 0)
    d = ImageDraw.Draw(im)
    if coast:
        mx = (e1 - e0) * 0.02
        land, lakes, has_coast = O.coast_land_polygons(coast, e0 - mx, n0 - mx, e1 + mx, n1 + mx)
        if has_coast:
            d.rectangle([0, 0, npx, npx], fill=1)
            for p in land:
                d.polygon([pix(*q) for q in p], fill=0)
            for p in lakes:
                d.polygon([pix(*q) for q in p], fill=1)
    for rings in F["water"]:
        # jämn-udda: rita yttre som 1, inre som 0 (inre = öar i sjön)
        area = [(abs(_area(r)), r) for r in rings]
        area.sort(key=lambda a: -a[0])
        for k, (_, r) in enumerate(area):
            pts = [pix(*q) for q in O.project_line(to_grid, r)]
            if len(pts) >= 3:
                d.polygon(pts, fill=1 if k == 0 or not _inside_any(r, [a[1] for a in area[:k]]) else 0)
    for kind, pts in F["rivers"]:
        w = max(1, int(round((25 if kind == "river" else 10) / m_px)))
        d.line([pix(*q) for q in O.project_line(to_grid, pts)], fill=1, width=w)
    return np.asarray(im, bool)


def _area(r):
    a = 0.0
    for i in range(len(r) - 1):
        a += r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1]
    return a / 2


def _inside_any(r, others):
    x, y = r[0]
    for o in others:
        ins = False
        j = len(o) - 1
        for i in range(len(o)):
            xi, yi = o[i]; xj, yj = o[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi:
                ins = not ins
            j = i
        if ins:
            return True
    return False


def _measure_one(img, cov, water, m_px, max_m=600.0, min_frac=0.03, min_sharp=0.0):
    """Returnerar dict med förskjutning (m, öst/nord), korrelation, toppens skärpa och vattenandel."""
    cov = cov & np.isfinite(img[..., 0])
    frac = water[cov].mean() if cov.any() else 0
    if cov.mean() < 0.3 or frac < min_frac or frac > 0.97:
        return {"matbar": False, "vattenandel": round(float(frac), 3), "skal": "för lite vatten/land i utsnittet"}
    from scipy.ndimage import gaussian_filter
    sm = np.stack([gaussian_filter(img[..., k].astype(float), 1.5) for k in range(3)], -1)
    X = sm.reshape(-1, 3)
    c, y = cov.ravel(), water.ravel()
    # QDA: log-kvot mellan två gaussiska färgmodeller (vatten / land) – kartans vattenfärg lärs per order
    def logpdf(Xs, mu, S):
        Si = np.linalg.inv(S); d = Xs - mu
        return -0.5 * np.einsum("ij,jk,ik->i", d, Si, d) - 0.5 * np.log(np.linalg.det(S))
    mu1, mu0 = X[c & y].mean(0), X[c & ~y].mean(0)
    S1, S0 = np.cov(X[c & y].T) + np.eye(3) * 4, np.cov(X[c & ~y].T) + np.eye(3) * 4
    s = np.clip(logpdf(X, mu1, S1) - logpdf(X, mu0, S0), -20, 20)
    s = (s - s[c].mean()) / (s[c].std() + 1e-9)
    s[~c] = 0
    P = s.reshape(cov.shape)
    M = np.where(cov, water.astype(float) - frac, 0.0)
    n = cov.shape[0]
    Fp = np.fft.rfft2(P, s=(2 * n, 2 * n)); Fm = np.fft.rfft2(M, s=(2 * n, 2 * n))
    corr = np.fft.irfft2(Fp * np.conj(Fm), s=(2 * n, 2 * n))
    k = int(max_m / m_px)
    best, bd = -1e18, (0, 0)
    for dy in range(-k, k + 1):
        row = corr[dy % (2 * n)]
        for dx in range(-k, k + 1):
            v = row[dx % (2 * n)]
            if v > best:
                best, bd = v, (dx, dy)
    dx, dy = bd
    # toppens skärpa: hur mycket korrelationen faller 100 m bort i åtta riktningar (rak strand = tvetydig längs stranden)
    win = np.array([[corr[(yy) % (2 * n), (xx) % (2 * n)] for xx in range(-k, k + 1)] for yy in range(-k, k + 1)])
    base = float(np.median(win))
    st = max(3, int(round(100 / m_px)))
    drops = []
    for ux, uy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        v = corr[(dy + uy * st) % (2 * n), (dx + ux * st) % (2 * n)]
        drops.append((best - v) / (best - base + 1e-12))
    sharp = float(min(drops) / (max(drops) + 1e-12)) if max(drops) > 0 else 0.0  # isotropi: 1 = rund topp, 0 = ränna

    def pearson(dx, dy):
        a = np.roll(np.roll(P, -dy, 0), -dx, 1)
        mm = np.roll(np.roll(cov, -dy, 0), -dx, 1) & cov
        return float(np.corrcoef(a[mm], water[mm])[0, 1])
    r0, r1 = pearson(0, 0), pearson(dx, dy)
    edge = abs(dx) == k or abs(dy) == k
    ok = not edge and sharp >= min_sharp
    return {"matbar": ok, "ost_m": round(dx * m_px, 1), "nord_m": round(-dy * m_px, 1),
            "forskjutning_m": round(float(np.hypot(dx, dy) * m_px), 1), "r_vid_topp": round(r1, 3),
            "r_utan_flytt": round(r0, 3), "skarpa": round(sharp, 3), "vattenandel": round(float(frac), 3),
            "upplosning_m": round(m_px, 2),
            "skal": "toppen ligger på sökgränsen" if edge else ("tvetydig topp (rak strand?)" if sharp < min_sharp else "ok")}


def measure(img, cov, water, m_px, max_m=600.0):
    """Två oberoende mål (hela vattnet / 60 m-bandet innanför stranden) – mätningen godtas bara om de
    två skattningarna stämmer (≤ max(60 m, 3 px)). Returnerar medelvärdet."""
    from scipy.ndimage import distance_transform_edt
    a = _measure_one(img, cov, water, m_px, max_m)
    band = water & (distance_transform_edt(water) * m_px <= 60)
    b = _measure_one(img, cov, band, m_px, max_m, min_frac=0.004, min_sharp=0.15) if band.any() else {"matbar": False}
    if "ost_m" not in a or "ost_m" not in b:
        a["matbar"] = False
        a["skal"] = a.get("skal", "") if "ost_m" not in a else "för lite strand i utsnittet"
        return a
    diff = float(np.hypot(a["ost_m"] - b["ost_m"], a["nord_m"] - b["nord_m"]))
    ost, nord = (a["ost_m"] + b["ost_m"]) / 2, (a["nord_m"] + b["nord_m"]) / 2
    ok = diff <= max(60.0, 3 * m_px) and a["matbar"] and b["matbar"] and max(a["r_vid_topp"], b["r_vid_topp"]) > 0.15
    return {"matbar": bool(ok), "ost_m": round(ost, 1), "nord_m": round(nord, 1),
            "forskjutning_m": round(float(np.hypot(ost, nord)), 1), "skillnad_mellan_metoder_m": round(diff, 1),
            "r_vatten": a["r_vid_topp"], "r_strandband": b["r_vid_topp"], "skarpa": [a["skarpa"], b["skarpa"]],
            "vattenandel": a["vattenandel"],
            "upplosning_m": round(m_px, 2), "skal": "ok" if ok else (a["skal"] if a["skal"] != "ok" else (b["skal"] if b["skal"] != "ok" else f"metoderna skiljer {diff:.0f} m"))}
