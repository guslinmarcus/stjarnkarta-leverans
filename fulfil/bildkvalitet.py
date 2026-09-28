"""Automatisk bildkvalitet för annonsbilder och tryckfiler. Deterministiskt, ingen AI, ingen människa.

Kontroller (varje funktion returnerar (ok, detalj, extra)):
  tomma_rektanglar  stora enfärgade rektanglar inne i bildinnehållet (saknade kartplattor, tomma paneler):
                    enfärgade block (8×8 px på 800 px-skala) som hänger ihop i färg, fyller ≥ 92 % av sin
                    omslutande rektangel, täcker ≥ 0,4 % av bilden och har tät detalj (karta, stjärnhimmel) på ≥ 3
                    sidor (i kartrutor, karta=True: mönster på ≥ 1 sida, även mot rutans kant) – enfärgade möbler och väggar i rumsbilderna räknas därför inte.
                    Bakgrund (vägg, papper, textkort) rör bildkanten på ≥ 3 sidor eller omsluter innehåll
                    och fylls därför inte.
  skarvar           raka färgskarvar mellan två tätt mönstrade ytor (kartblad med olika tryck/skanning), se funktionen.
                    OBS: larmar också när kartans innehåll skiljer sig längs en rak linje – publiceringsgrinden
                    använder den därför som VARNING; kartbladsskarvar grindas exakt längs kända bladkanter i
                    produktgrinden (grind_historisk.seam_check).
  avklippt_text     OCR (tesseract, eng+swe) på bildens fyra kantremsor (15 %): ett ord med bokstäver som
                    rör bildkanten är avklippt (fyra anrop). Utan tesseract: kontrollen underkänns (hellre stopp än tyst godkänt).
  kontrast          luminansens 1–99-percentilspann ≥ 70 och standardavvikelse ≥ 18.
  skarpa            Laplace-varians i de 15 % mest mönstrade 64 px-blocken (full upplösning) ≥ SKARPA_MIN.
  motiv_fyller      huvudmotivets omslutande rektangel (allt som skiljer sig från kantens bakgrundsfärg)
                    ≥ 30 % av bilden (förstabilden ≥ 45 %).

Körning: python bildkvalitet.py bild.jpg [...]   (skriver resultat som JSON)
         python bildkvalitet.py --felinjektion     (planterar fel i riktiga bilder och mäter att de fångas)

OBS: vendorad kopia av agentbutik/etsy/api/bildkvalitet.py – grind_historisk.py körs i GitHub Actions där bara
den här git-repot (leverans) checkas ut, så modulen kan inte nås via sys.path över repogränsen. Håll de två
filerna i synk för hand vid ändring (publiceringsgrinden i etsy/api/ använder sin egen kopia, med OCR/tesseract).
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # numpy/scipy: annars ≈ 700 MB privat minne i trådbuffertar (12 kärnor)
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = None
TESS = Path(os.environ.get("TESSERACT_EXE") or r"C:\Program Files\Tesseract-OCR\tesseract.exe")  # Actions: /usr/bin/tesseract
TESSDATA = Path(os.environ.get("TESSDATA_DIR") or Path.home() / "tessdata")  # svenska paketet ligger här; utan TESSDATA_PREFIX blir svaret tomt (minnet)
VERSION = "2026-09-28.3"        # annonsbildernas kontroller – höj när en kontroll ändras (styr publiceringsgrindens cache)
VERSION_TRYCK = "2026-09-28.2"  # tryckfilens kontroller
DENS_MIN = 0.20      # tomma_rektanglar i annonsbilder: andel pixlar med tydlig gradient runt ytan (karta ≈ 0,3–0,6,
                     # tecknad vägg/möbel ≈ 0–0,05; kalibrerat 2026-09-28 mot 39 larm i kön, varav 38 var möbler/väggar)
SKARV_STEG = 34.0     # summa |ΔR|+|ΔG|+|ΔB| mellan medianfärgerna på var sida
SKARV_LANGD = 0.12    # fönstrets längd som andel av bildens höjd/bredd
SKARPA_MIN = 60.0
KONTRAST_SPANN, KONTRAST_STD = 70.0, 18.0
MOTIV_MIN, MOTIV_FORSTA = 0.30, 0.45


def _rgb(im, long_side=None):
    im = im.convert("RGB")
    if long_side and max(im.size) != long_side:
        f = long_side / max(im.size)
        im = im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.BOX)
    return np.asarray(im, np.float32)


# ------------------------------------------------------------------ 1. tomma rektanglar
def tomma_rektanglar(im, bs=8, scale=800, tol=7.0, std_max=2.2, min_area=0.004, min_fill=0.92, tex=8.0, min_side=0.03, karta=False):
    """karta=True: bilden ÄR en kartruta – då räknas också enfärgade ytor som når rutans kanter (en remsa utan
    kartblad längs kanten), och det räcker med mönster på en sida."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    a = _rgb(im, scale)
    H, W = a.shape[0] // bs * bs, a.shape[1] // bs * bs
    a = a[:H, :W]
    gh, gw = H // bs, W // bs
    blk = a.reshape(gh, bs, gw, bs, 3)
    mean = blk.mean((1, 3))
    lum = blk.mean(4)
    std = lum.std((1, 3))
    uni = std < std_max
    from scipy.ndimage import sobel
    Lg = a[..., :].mean(-1)
    dens = (np.hypot(sobel(Lg, 0), sobel(Lg, 1)) / 4 > 12).reshape(gh, bs, gw, bs).mean((1, 3))  # detaljtäthet per block
    idx = np.arange(gh * gw).reshape(gh, gw)
    rows, cols = [], []
    for (s0, s1) in (((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
                     ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))):
        ok = uni[s0] & uni[s1] & (np.abs(mean[s0] - mean[s1]).sum(-1) < tol)
        rows.append(idx[s0][ok]); cols.append(idx[s1][ok])
    r = np.concatenate(rows); c = np.concatenate(cols)
    n, lab = connected_components(coo_matrix((np.ones(len(r)), (r, c)), shape=(gh * gw, gw * gh)), directed=False)
    lab = lab.reshape(gh, gw)
    lab[~uni] = -1
    counts = np.bincount(lab[lab >= 0].ravel(), minlength=n)
    hits = []
    for k in np.where(counts >= min_area * gh * gw)[0]:
        ys, xs = np.where(lab == k)
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        box = (y1 - y0 + 1) * (x1 - x0 + 1)
        fill = counts[k] / box
        if fill < min_fill:
            continue
        edges = [y0 == 0, y1 == gh - 1, x0 == 0, x1 == gw - 1]
        if (sum(edges) >= 3 and not karta) or min(y1 - y0 + 1, x1 - x0 + 1) < min_side * min(gh, gw):
            continue
        # mönster utanför varje sida (1–2 block ut)
        sides = []
        for side, e in zip(("topp", "botten", "vänster", "höger"), edges):
            if e:
                continue
            src, lim = (std, tex) if karta else (dens, DENS_MIN)  # annonsbild: kräver kart-/himmelstät detalj runt om
            if side == "topp":
                strip = src[max(0, y0 - 3):y0, x0:x1 + 1]
            elif side == "botten":
                strip = src[y1 + 1:y1 + 4, x0:x1 + 1]
            elif side == "vänster":
                strip = src[y0:y1 + 1, max(0, x0 - 3):x0]
            else:
                strip = src[y0:y1 + 1, x1 + 1:x1 + 4]
            if strip.size and float(np.median(strip)) >= lim:
                sides.append(side)
        if len(sides) >= (1 if karta else 3):  # annonsbild: omgiven av tät detalj på ≥ 3 sidor (hål i en karta/himmel)
            f = im.width / W if im.width >= im.height else im.height / H
            hits.append({"ruta_px": [int(x0 * bs * f), int(y0 * bs * f), int((x1 + 1) * bs * f), int((y1 + 1) * bs * f)],
                         "andel": round(float(counts[k]) / (gh * gw), 4), "fyllnad": round(float(fill), 3),
                         "farg": [int(v) for v in mean[lab == k].mean(0)], "monster_pa": sides})
    return not hits, (f"{len(hits)} tom(ma) rektangel(ar): " + "; ".join(
        f"{h['andel'] * 100:.1f} % av bilden vid {h['ruta_px']}" for h in hits[:3])) if hits else "inga", hits


# ------------------------------------------------------------------ 2. skarvar
def skarvar(im, scale=1000, near=3, far=40, tex_min=0.22, run=None):
    """Rak skarv mellan två mönstrade ytor med olika färg (t.ex. två kartblad med olika tryck).
    Längs varje rak lodrät/vågrät linje och ett glidande fönster på SKARV_LANGD av bildens höjd/bredd:
      (1) färgskillnaden mellan sidorna har SAMMA riktning längs hela fönstret: medelvärdet av den tecken-
          behållna skillnaden (vänster − höger, per färgkanal) är ≥ SKARV_STEG. Kartans egna detaljer
          (vägar, text, stränder) byter riktning hela tiden och tar ut varandra; en bladskarv gör det inte.
      (2) samma sak exakt vid linjen (±3 px, medianfilter 5 px) med minst 60 % av styrkan – skarven är skarp och rak (en strand som slingrar sig ger inget stadigt språng i en enda kolumn),
          inte en mjuk ljusövergång;
      (3) båda sidorna är tätt mönstrade (≥ 22 % av pixlarna i fönstret 40 px ut har en tydlig gradient) i ≥ 90 %
          av fönstret – en ensam kant (ram, panelkant, möbel) ger ≈ 5 %, så de räknas inte."""
    from scipy.ndimage import median_filter, uniform_filter
    a = _rgb(im, scale)
    out = []
    for orient in ("lodrät", "vågrät"):
        b = a if orient == "lodrät" else a.transpose(1, 0, 2)
        H, W = b.shape[:2]
        if W <= 2 * far + 2:
            continue
        Lw = max(20, int((run or SKARV_LANGD) * H))
        M = np.stack([median_filter(b[..., ch], size=(9, 5)) for ch in range(3)], -1)
        U = np.stack([uniform_filter(b[..., ch], size=(9, 41)) for ch in range(3)], -1)
        L = b.mean(-1)
        from scipy.ndimage import sobel
        G = np.hypot(sobel(L, 0), sobel(L, 1)) / 4
        S = uniform_filter((G > 12).astype(np.float32), (9, 41))  # tätheten av detaljer: en ensam kant ger ≈ 0,05
        xs = np.arange(far, W - far)
        V2 = U[:, xs - far] - U[:, xs + far]
        V1 = M[:, xs - near] - M[:, xs + near]
        ok = (np.minimum(S[:, xs - far], S[:, xs + far]) >= tex_min).astype(np.float32)

        def win(A):  # glidande summa över Lw rader
            c = np.concatenate([np.zeros((1,) + A.shape[1:], A.dtype), np.cumsum(A, 0)])
            return c[Lw:] - c[:-Lw]
        n = win(ok)
        m2 = np.abs(win(V2 * ok[..., None]) / np.maximum(n, 1)[..., None]).sum(-1)
        m1 = np.abs(win(V1 * ok[..., None]) / np.maximum(n, 1)[..., None]).sum(-1)
        hit = (n >= 0.9 * Lw) & (m2 >= SKARV_STEG) & (m1 >= 0.6 * SKARV_STEG)
        if hit.any():
            j = np.argmax(np.where(hit, m2, 0).max(0))
            rows = hit[:, j]
            f = max(im.size) / max(a.shape[:2])
            out.append({"riktning": orient, "lage_px": int(xs[j] * f), "styrka": round(float(m2[:, j].max()), 1),
                        "rad_px": int(np.argmax(rows) * f), "langd_andel": round((rows.sum() + Lw) / H, 3)})
    return not out, ("; ".join(f"{s['riktning']} skarv vid {s['lage_px']} px (styrka {s['styrka']:.0f}, från {s['rad_px']} px)"
                               for s in out) if out else "inga"), out


# ------------------------------------------------------------------ 3. avklippt text
def tesseract_ok():
    return TESS.exists() and (TESSDATA / "eng.traineddata").exists()


def _ocr_words(img):
    env = dict(os.environ, TESSDATA_PREFIX=str(TESSDATA))
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "k.png"
        img.save(p)
        langs = "eng+swe" if (TESSDATA / "swe.traineddata").exists() else "eng"
        r = subprocess.run([str(TESS), str(p), "stdout", "-l", langs, "--psm", "11", "tsv"], env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"tesseract returkod {r.returncode}: {r.stderr[:200]}")
    words = []
    for line in r.stdout.splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 12 or not f[11].strip():
            continue
        try:
            conf = float(f[10])
        except ValueError:
            continue
        words.append({"text": f[11].strip(), "conf": conf, "x": int(f[6]), "y": int(f[7]), "w": int(f[8]), "h": int(f[9])})
    return words


def avklippt_text(im, strip=0.15, min_conf=60, touch=8, scale=0.7):
    """OCR på bildens fyra kantremsor (15 % av bredden/höjden, var för sig med egen bakgrund – en sammanlagd bild
    gav sämre träff i felinjiceringen). Ett ord (≥ 3 bokstäver, säkerhet ≥ 60) vars ruta når remsans yttre kant
    (≤ 8 px vid 1400 px) = avklippt text."""
    if not tesseract_ok():
        return False, "tesseract saknas – kan inte kontrollera avklippt text", []
    g = im.convert("L")
    W, H = g.size
    f = min(1.0, 2000 / max(W, H)) * scale
    g = g.resize((round(W * f), round(H * f)), Image.LANCZOS); W, H = g.size
    s = int(strip * min(W, H)); pad = 40
    cut = []
    for side, box in (("topp", (0, 0, W, s)), ("botten", (0, H - s, W, H)), ("vänster", (0, 0, s, H)), ("höger", (W - s, 0, W, H))):
        crop = g.crop(box)
        canvas = Image.new("L", (crop.width + 2 * pad, crop.height + 2 * pad), 255)
        canvas.paste(crop, (pad, pad))
        words = _ocr_words(canvas)
        if np.median(np.asarray(crop)) < 128:  # mörk remsa: läs också inverterad (ljus text på mörkt)
            from PIL import ImageOps
            words += _ocr_words(ImageOps.invert(canvas))
        for w_ in words:
            if w_["conf"] < min_conf or not re.search(r"[A-Za-zÅÄÖåäöÉéÜü]{3,}", w_["text"]):
                continue
            x0, y0 = w_["x"] - pad, w_["y"] - pad
            x1, y1 = x0 + w_["w"], y0 + w_["h"]
            edge = {"topp": y0 <= touch, "botten": y1 >= crop.height - touch,
                    "vänster": x0 <= touch, "höger": x1 >= crop.width - touch}[side]
            if edge:
                cut.append({"sida": side, "ord": w_["text"], "sakerhet": w_["conf"]})
    return not cut, (f"{len(cut)} avklippta ord: " + ", ".join(f"'{c['ord']}' ({c['sida']})" for c in cut[:6])) if cut else "inga", cut


# ------------------------------------------------------------------ 4–6. kontrast, skärpa, motiv
def kontrast_tryck(im):
    """Tryckfil: skillnaden mellan bläck och papper (0,2- och 99,8-percentilen i 1500 px). En minimalistisk affisch
    är mest papper, så bildens spridning (std) säger inget om läsbarheten där."""
    g = im.convert("L"); f = 1500 / max(g.size)
    L = np.asarray(g.resize((round(g.width * f), round(g.height * f)), Image.LANCZOS), np.float32)
    span = float(np.percentile(L, 99.8) - np.percentile(L, 0.2))
    return span >= 100, f"bläck mot papper {span:.0f} nivåer (≥ 100)", {"spann": span}


def kontrast(im):
    L = np.asarray(im.convert("L").resize((500, 500), Image.BOX), np.float32)
    span = float(np.percentile(L, 99) - np.percentile(L, 1)); sd = float(L.std())
    return span >= KONTRAST_SPANN and sd >= KONTRAST_STD, f"spann {span:.0f} (≥ {KONTRAST_SPANN:.0f}), std {sd:.1f} (≥ {KONTRAST_STD:.0f})", {"spann": span, "std": sd}


def skarpa(im, bs=64, top=0.15):
    from scipy.ndimage import laplace
    g = np.asarray(im.convert("L"), np.float32)
    if max(g.shape) > 4000:  # tryckfiler: ett mitt-utsnitt i full upplösning räcker
        h, w = g.shape; g = g[h // 4:3 * h // 4, w // 4:3 * w // 4]
    lap = laplace(g)
    H, W = g.shape[0] // bs * bs, g.shape[1] // bs * bs
    lb = lap[:H, :W].reshape(H // bs, bs, W // bs, bs).var((1, 3)).ravel()
    tb = g[:H, :W].reshape(H // bs, bs, W // bs, bs).std((1, 3)).ravel()
    k = max(1, int(top * len(lb)))
    v = float(np.median(lb[np.argsort(tb)[-k:]]))
    return v >= SKARPA_MIN, f"Laplace-varians {v:.0f} i de mest mönstrade blocken (≥ {SKARPA_MIN:.0f})", {"laplace": v}


def motiv_fyller(im, forsta=False):
    a = _rgb(im, 400)
    H, W = a.shape[:2]
    border = np.concatenate([a[:3].reshape(-1, 3), a[-3:].reshape(-1, 3), a[:, :3].reshape(-1, 3), a[:, -3:].reshape(-1, 3)])
    bg = np.median(border, 0)
    fg = np.abs(a - bg).sum(-1) > 30
    if fg.mean() < 0.002:
        frac = 0.0
    else:
        ys, xs = np.where(fg)
        y0, y1 = np.percentile(ys, [0.5, 99.5]); x0, x1 = np.percentile(xs, [0.5, 99.5])
        frac = float((y1 - y0 + 1) * (x1 - x0 + 1) / (H * W))
    lim = MOTIV_FORSTA if forsta else MOTIV_MIN
    return frac >= lim, f"motivet fyller {frac * 100:.0f} % (≥ {lim * 100:.0f} %)", {"andel": frac}


def analysera(im, forsta=False, ocr=True, typ="annonsbild"):
    """Alla kontroller på en bild. typ='tryckfil' hoppar över OCR (tryckfilens text kontrolleras i PDF:en)
    och motiv-kravet (en tryckfil är hela sidan)."""
    if isinstance(im, (str, Path)):
        im = Image.open(im)
    res = {}
    for name, fn in (("tomma_rektanglar", tomma_rektanglar), ("skarvar", skarvar),
                     ("kontrast", kontrast if typ == "annonsbild" else kontrast_tryck), ("skarpa", skarpa)):
        ok, det, extra = fn(im)
        res[name] = {"ok": bool(ok), "detalj": det}
    if typ == "annonsbild":
        ok, det, _ = motiv_fyller(im, forsta)
        res["motiv_fyller"] = {"ok": bool(ok), "detalj": det}
        if ocr:
            try:
                ok, det, _ = avklippt_text(im)
            except Exception as e:
                ok, det = False, f"OCR misslyckades: {e!r}"[:200]
            res["avklippt_text"] = {"ok": bool(ok), "detalj": det}
    return res


def pdf_text_inom_sida(pdf, marg_mm=2.0):
    """Tryckfilens text: varje textspann ska ligga helt inom sidan (minus marginal). Returnerar (ok, detalj)."""
    import fitz
    doc = fitz.open(pdf)
    bad = []
    m = marg_mm / 25.4 * 72
    for i, p in enumerate(doc):
        R = p.rect
        for b in p.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    if not s["text"].strip():
                        continue
                    x0, y0, x1, y1 = s["bbox"]
                    if x0 < R.x0 + m or y0 < R.y0 + m or x1 > R.x1 - m or y1 > R.y1 - m:
                        bad.append(f"s{i + 1}: '{s['text'][:30]}'")
    return not bad, (f"{len(bad)} textrader utanför sidan: " + "; ".join(bad[:4])) if bad else "all text inom sidan"


# ------------------------------------------------------------------ felinjektion
def felinjektion(bilder, ut=None):
    """Planterar fel i riktiga, godkända bilder. Varje fel ska fångas av sin kontroll."""
    rng = np.random.default_rng(1)
    rows = []
    for p in bilder:
        base = Image.open(p).convert("RGB")
        a = np.asarray(base).copy()
        H, W = a.shape[:2]
        inj = {}
        # felen planteras där bilden är mest mönstrad (där en karta eller stjärnhimmel finns) – det är där en saknad
        # kartplatta eller en bladskarv uppstår i verkligheten
        Lf = np.asarray(base.convert("L").resize((200, 200)), float)
        from scipy.ndimage import uniform_filter, sobel
        G = np.hypot(sobel(Lf, 0), sobel(Lf, 1))
        D = uniform_filter(G, 60)
        cy, cx = np.unravel_index(np.argmax(D[30:170, 30:170]), (140, 140)); cy, cx = (cy + 30) / 200, (cx + 30) / 200
        # (a) tom kartplatta: 16 % × 16 % enfärgad ruta i det mönstrade området
        b = a.copy(); y, x = int(H * (cy - 0.08)), int(W * (cx - 0.08)); b[y:y + int(H * 0.16), x:x + int(W * 0.16)] = (238, 233, 220)
        inj["tomma_rektanglar"] = Image.fromarray(b)
        # (b) skarv: högra halvan av det mönstrade området (30 % × 30 %) färgförskjuten, som ett annat kartblad
        b = a.astype(np.int16).copy(); y, x = int(H * (cy - 0.15)), int(W * cx)
        b[max(0, y):y + int(H * 0.30), x:x + int(W * 0.15)] += np.array([-45, -10, 25], np.int16)
        inj["skarvar"] = Image.fromarray(np.clip(b, 0, 255).astype(np.uint8))
        # (c) oskärpa
        inj["skarpa"] = base.filter(ImageFilter.GaussianBlur(4))
        # (d) låg kontrast (urblekt)
        inj["kontrast"] = Image.fromarray((a.astype(np.float32) * 0.25 + 170).astype(np.uint8))
        # (e) motivet för litet: bilden krympt till 35 % på väggfärg
        c = Image.new("RGB", (W, H), (236, 231, 222)); c.paste(base.resize((int(W * 0.35), int(H * 0.35))), (int(W * 0.32), int(H * 0.32)))
        inj["motiv_fyller"] = c
        # (f) avklippt text: stor text som går ut över högerkanten
        from PIL import ImageDraw, ImageFont
        c = base.copy(); d = ImageDraw.Draw(c)
        try:
            fnt = ImageFont.truetype(str(Path(__file__).resolve().parent / "fonts" / "NotoSans-Regular.ttf"), int(H * 0.05))
        except Exception:
            fnt = ImageFont.load_default()
        tw = d.textlength("Generalstabskartan", font=fnt)
        d.rectangle((W - tw * 0.6 - 20, int(H * 0.8) - 10, W, int(H * 0.8) + int(H * 0.07)), fill=(250, 248, 240))
        d.text((W - tw * 0.6, int(H * 0.8)), "Generalstabskartan", fill=(30, 25, 20), font=fnt)
        inj["avklippt_text"] = c
        base_res = analysera(base, forsta=False)
        for fel, img in inj.items():
            r = analysera(img, forsta=False)
            rows.append({"bild": Path(p).name, "fel": fel, "fangad": not r[fel]["ok"], "detalj": r[fel]["detalj"],
                         "grund_ok": base_res[fel]["ok"]})
    return rows


if __name__ == "__main__":
    if sys.argv[1:2] == ["--felinjektion"]:
        rows = felinjektion(sys.argv[2:])
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        ok = [r for r in rows if r["grund_ok"]]
        print(f"fångade {sum(r['fangad'] for r in ok)}/{len(ok)} (där originalbilden var godkänd i samma kontroll)")
    else:
        for p in sys.argv[1:]:
            print(p, json.dumps(analysera(p, forsta="01" in Path(p).name), ensure_ascii=False, indent=1))
