"""Render Pinterest-friendly 1000x1500 PNG share cards.

Called from generate_site.generate_name_page for the top ~1000 names per
country. The output is a palettized PNG (~30 KB) — best size-quality
trade-off for the flat-color brand design. Renderer is idempotent: callers
skip when the file already exists, so the monthly cron stays incremental.
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 1500

INK     = (27, 36, 64)
TEAL    = (20, 158, 145)
TEAL_DK = (15, 124, 114)
CORAL   = (255, 107, 92)
CANVAS  = (247, 248, 250)
MUTED   = (91, 102, 120)
WHITE   = (255, 255, 255)
SOFT    = (232, 244, 242)
NUM_BG  = (245, 232, 230)  # warm wash for the numerology card row

_FONT_DIR = Path(__file__).parent / 'assets' / 'fonts'
POPPINS = str(_FONT_DIR / 'Poppins-Bold.ttf')
INTER   = str(_FONT_DIR / 'Inter-Regular.ttf')


@lru_cache(maxsize=64)
def _font(face: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(face, size)


def _wrap(d: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
          max_w: int, max_lines: int = 2) -> list[str]:
    """Greedy word wrap. Last line gets ellipsised if more would overflow."""
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    cur = ''
    for w in words:
        cand = f"{cur} {w}".strip()
        if d.textlength(cand, font=font) <= max_w:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and d.textlength(' '.join(words), font=font) > sum(
            d.textlength(l, font=font) for l in lines):
        # truncate last line + ellipsis
        last = lines[-1]
        while last and d.textlength(last + '…', font=font) > max_w:
            last = last.rsplit(' ', 1)[0] if ' ' in last else last[:-1]
        lines[-1] = (last + '…') if last else '…'
    return lines


def render_pin(
    out_path: Path,
    *,
    name: str,
    gender_label: str,
    origin_label: str,
    popularity: str,
    peak_era: str,
    sound: str,
    meaning: str,            # short meaning blurb (≤ ~80 chars) or ""
    numerology: list,        # list of (num:int, label:str, trait:str) — up to 3
    url: str,
    country_label: str,
) -> None:
    """Write the pin PNG for one name. Caller must ensure the directory exists."""
    img = Image.new('RGB', (W, H), CANVAS)
    d = ImageDraw.Draw(img)

    # ─── Top brand bar (80 px) ────────────────────────────────────────────
    d.rectangle([(0, 0), (W, 80)], fill=INK)
    bar = _font(POPPINS, 30)
    d.text((48, 22), "Name", font=bar, fill=WHITE)
    nw = d.textlength("Name", font=bar)
    d.text((48 + nw, 22), "Charted", font=bar, fill=TEAL)
    chip = _font(INTER, 22)
    cw = d.textlength(country_label, font=chip)
    d.text((W - cw - 48, 26), country_label, font=chip, fill=WHITE)

    # ─── Hero panel (110 → 600) ─────────────────────────────────────────
    panel_top, panel_bot = 110, 600
    d.rounded_rectangle([(60, panel_top), (W - 60, panel_bot)],
                        radius=40, fill=SOFT)
    d.ellipse([(W - 170, panel_top + 50), (W - 100, panel_top + 120)], fill=CORAL)

    # Name — auto-shrink to fit
    name_size = 280
    name_font = _font(POPPINS, name_size)
    nw = d.textlength(name, font=name_font)
    while nw > W - 200 and name_size > 100:
        name_size -= 10
        name_font = _font(POPPINS, name_size)
        nw = d.textlength(name, font=name_font)
    name_y = panel_top + (panel_bot - panel_top - name_size) // 2 - 30
    d.text(((W - nw) // 2, name_y), name, font=name_font, fill=INK)

    sub_font = _font(INTER, 34)
    sub = gender_label
    if origin_label:
        sub = f"{gender_label}  ·  {origin_label}"
    sw = d.textlength(sub, font=sub_font)
    d.text(((W - sw) // 2, panel_bot - 80), sub, font=sub_font, fill=MUTED)

    # ─── Meaning block (620 → 760) ─────────────────────────────────────
    y = 630
    if meaning:
        d.text((80, y), "MEANING", font=_font(INTER, 22), fill=TEAL)
        mean_font = _font(POPPINS, 38)
        lines = _wrap(d, meaning, mean_font, W - 160, max_lines=2)
        for i, line in enumerate(lines):
            d.text((80, y + 32 + i * 48), line, font=mean_font, fill=INK)

    # ─── Stat rows (790 → 1060) ─────────────────────────────────────────
    rows = [("POPULARITY", popularity), ("PEAK ERA", peak_era), ("SOUND", sound)]
    rows = [(l, v) for l, v in rows if v]
    label_font = _font(INTER, 22)
    val_font   = _font(POPPINS, 32)
    y = 790
    for lbl, val in rows:
        d.rectangle([(80, y + 12), (90, y + 56)], fill=TEAL)
        d.text((114, y), lbl, font=label_font, fill=MUTED)
        d.text((114, y + 28), val, font=val_font, fill=INK)
        y += 90

    # ─── Numerology card row (1080 → 1370) ─────────────────────────────
    if numerology:
        nblock_top = 1080
        d.rounded_rectangle([(60, nblock_top), (W - 60, nblock_top + 290)],
                            radius=32, fill=NUM_BG)
        d.text((90, nblock_top + 24), "NUMEROLOGY",
               font=_font(INTER, 22), fill=CORAL)
        cards = numerology[:3]
        card_w = (W - 60 - 60 - 40) // 3       # 60px margins, 20px gap
        card_h = 200
        card_y = nblock_top + 70
        x = 80
        for num, lbl, trait in cards:
            # number circle
            cx = x + card_w // 2
            cy = card_y + 56
            r  = 46
            d.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=INK)
            nfont = _font(POPPINS, 56)
            ntext = str(num)
            nw2 = d.textlength(ntext, font=nfont)
            d.text((cx - nw2 / 2, cy - 38), ntext, font=nfont, fill=WHITE)
            # label
            lbl_font = _font(INTER, 18)
            lw = d.textlength(lbl, font=lbl_font)
            d.text((cx - lw / 2, card_y + 118), lbl, font=lbl_font, fill=MUTED)
            # trait
            t_font = _font(POPPINS, 24)
            tw = d.textlength(trait, font=t_font)
            # shrink if too wide
            sz = 24
            while tw > card_w - 12 and sz > 16:
                sz -= 1
                t_font = _font(POPPINS, sz)
                tw = d.textlength(trait, font=t_font)
            d.text((cx - tw / 2, card_y + 146), trait, font=t_font, fill=INK)
            x += card_w + 20

    # ─── Footer (1390 → 1500) ──────────────────────────────────────────
    d.rectangle([(0, H - 110), (W, H)], fill=INK)
    url_font = _font(POPPINS, 32)
    uw = d.textlength(url, font=url_font)
    d.text(((W - uw) // 2, H - 76), url, font=url_font, fill=WHITE)
    d.rectangle([(0, H - 12), (W, H)], fill=TEAL)

    # Palettize — cuts file ~3× for this flat-color design without visible loss.
    img.convert('P', palette=Image.ADAPTIVE, colors=96).save(out_path, 'PNG', optimize=True)
