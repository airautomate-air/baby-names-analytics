"""Render Pinterest-friendly 1000x1500 PNG share cards.

Called from generate_site.generate_name_page for the top ~1000 names per
country. The output is a palettized PNG (~25 KB) — best size-quality
trade-off for the flat-color brand design. Renderer is idempotent: callers
skip when the file already exists, so the monthly cron stays incremental.
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 1500

INK    = (27, 36, 64)
TEAL   = (20, 158, 145)
CORAL  = (255, 107, 92)
CANVAS = (247, 248, 250)
MUTED  = (91, 102, 120)
WHITE  = (255, 255, 255)
SOFT   = (232, 244, 242)

_FONT_DIR = Path(__file__).parent / 'assets' / 'fonts'
POPPINS = str(_FONT_DIR / 'Poppins-Bold.ttf')
INTER   = str(_FONT_DIR / 'Inter-Regular.ttf')


@lru_cache(maxsize=64)
def _font(face: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(face, size)


def render_pin(
    out_path: Path,
    *,
    name: str,
    gender_label: str,
    origin_label: str,
    popularity: str,
    peak_era: str,
    sound: str,
    url: str,
    country_label: str,
) -> None:
    """Write the pin PNG for one name. Caller must ensure the directory exists."""
    img = Image.new('RGB', (W, H), CANVAS)
    d = ImageDraw.Draw(img)

    # Top brand bar
    d.rectangle([(0, 0), (W, 80)], fill=INK)
    bar = _font(POPPINS, 30)
    d.text((48, 22), "Name", font=bar, fill=WHITE)
    nw = d.textlength("Name", font=bar)
    d.text((48 + nw, 22), "Charted", font=bar, fill=TEAL)
    chip = _font(INTER, 22)
    cw = d.textlength(country_label, font=chip)
    d.text((W - cw - 48, 26), country_label, font=chip, fill=WHITE)

    # Hero panel
    panel_top, panel_bot = 180, 760
    d.rounded_rectangle([(60, panel_top), (W - 60, panel_bot)],
                        radius=40, fill=SOFT)
    d.ellipse([(W - 180, panel_top + 60), (W - 100, panel_top + 140)], fill=CORAL)

    # Name — auto-shrink to fit
    name_size = 320
    name_font = _font(POPPINS, name_size)
    nw = d.textlength(name, font=name_font)
    while nw > W - 200 and name_size > 100:
        name_size -= 10
        name_font = _font(POPPINS, name_size)
        nw = d.textlength(name, font=name_font)
    name_y = panel_top + (panel_bot - panel_top - name_size) // 2 - 30
    d.text(((W - nw) // 2, name_y), name, font=name_font, fill=INK)

    # Subtitle under name
    sub_font = _font(INTER, 36)
    sub = gender_label
    if origin_label:
        sub = f"{gender_label}  ·  {origin_label}"
    sw = d.textlength(sub, font=sub_font)
    d.text(((W - sw) // 2, panel_bot - 90), sub, font=sub_font, fill=MUTED)

    # Stat rows
    rows = [("POPULARITY", popularity), ("PEAK ERA", peak_era), ("SOUND", sound)]
    rows = [(l, v) for l, v in rows if v]
    block_h = len(rows) * 100
    y = 820 + (560 - 110 - block_h) // 2  # roughly centered between panel and footer
    label_font = _font(INTER, 24)
    val_font   = _font(POPPINS, 40)
    for lbl, val in rows:
        d.rectangle([(80, y + 14), (90, y + 64)], fill=TEAL)
        d.text((116, y), lbl, font=label_font, fill=MUTED)
        d.text((116, y + 32), val, font=val_font, fill=INK)
        y += 100

    # Footer
    d.rectangle([(0, H - 110), (W, H)], fill=INK)
    url_font = _font(POPPINS, 34)
    uw = d.textlength(url, font=url_font)
    d.text(((W - uw) // 2, H - 78), url, font=url_font, fill=WHITE)
    d.rectangle([(0, H - 12), (W, H)], fill=TEAL)

    # Palettize — cuts the file ~3× for this flat-color design without visible loss.
    img.convert('P', palette=Image.ADAPTIVE, colors=64).save(out_path, 'PNG', optimize=True)
