"""Gemensamma typsnitt och texthjälp för kartprodukterna (historisk, stadskarta, kärlekskarta)."""
from pathlib import Path

from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = Path(__file__).parent
FONTS = ROOT / "fonts"
for _n, _f in (("KSerif", "NotoSerif-Regular.ttf"), ("KSerifIt", "NotoSerif-Italic.ttf"),
               ("KSans", "NotoSans-Regular.ttf"), ("KScript", "GreatVibes-Regular.ttf"),
               ("KRound", "VarelaRound-Regular.ttf")):
    try:
        pdfmetrics.getFont(_n)
    except KeyError:
        pdfmetrics.registerFont(TTFont(_n, str(FONTS / _f)))


def fit_size(text, font, size, max_w, min_size=8):
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_w:
        size -= 0.5
    return size


def para(c, text, x, y, w, font="KSans", size=9, leading=None, color=(0.1, 0.1, 0.1)):
    """Skriv ett stycke med radbrytning; returnerar y under stycket."""
    leading = leading or size * 1.35
    c.setFont(font, size); c.setFillColorRGB(*color)
    for line in simpleSplit(text, font, size, w):
        c.drawString(x, y, line); y -= leading
    return y


def covers_glyphs(text, font):
    f = pdfmetrics.getFont(font)
    cmap = getattr(f.face, "charToGlyph", None)
    return all(ord(ch) in cmap for ch in text if not ch.isspace()) if cmap else True
