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
    # Did we consume every word into the line set? Compare what we placed
    # against the original word list (more reliable than comparing pixel widths).
    placed_words = sum(len(l.split()) for l in lines)
    if len(lines) == max_lines and placed_words < len(words):
        last = lines[-1]
        # Don't double up "…" if the input already ended in one.
        if last.endswith('…'):
            return lines
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
    numerology: list,        # list of (num:int, label:str, trait_name:str, trait_desc:str)
    url: str,
    country_label: str,
) -> None:
    """Write the pin PNG for one name. Caller must ensure the directory exists.

    The 3-stat row (popularity/peak/sound) is dropped from the canvas because
    the same information already shows in the hero panel (origin) and the
    numerology block needs the room for trait descriptions. The popularity
    string is folded into the subtitle under the name instead.
    """
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

    # ─── Hero panel (110 → 620) ─────────────────────────────────────────
    panel_top, panel_bot = 110, 620
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
    name_y = panel_top + (panel_bot - panel_top - name_size) // 2 - 50
    d.text(((W - nw) // 2, name_y), name, font=name_font, fill=INK)

    # Subtitle row 1: gender · origin
    sub_font = _font(INTER, 32)
    sub = gender_label
    if origin_label:
        sub = f"{gender_label}  ·  {origin_label}"
    sw = d.textlength(sub, font=sub_font)
    d.text(((W - sw) // 2, panel_bot - 120), sub, font=sub_font, fill=MUTED)

    # Subtitle row 2: popularity + peak (folded together)
    pop_bits = [v for v in (popularity, peak_era, sound) if v]
    if pop_bits:
        pop_text = "  ·  ".join(pop_bits[:2])  # keep it tight, drop the third
        pop_font = _font(POPPINS, 28)
        pw = d.textlength(pop_text, font=pop_font)
        # shrink if too wide
        sz = 28
        while pw > W - 200 and sz > 18:
            sz -= 1
            pop_font = _font(POPPINS, sz)
            pw = d.textlength(pop_text, font=pop_font)
        d.text(((W - pw) // 2, panel_bot - 75), pop_text, font=pop_font, fill=INK)

    # ─── Meaning block (650 → 830) ─────────────────────────────────────
    y = 650
    if meaning:
        d.text((80, y), "MEANING", font=_font(INTER, 22), fill=TEAL)
        mean_font = _font(POPPINS, 36)
        lines = _wrap(d, meaning, mean_font, W - 160, max_lines=3)
        for i, line in enumerate(lines):
            d.text((80, y + 34 + i * 46), line, font=mean_font, fill=INK)

    # ─── Numerology card row ───────────────────────────────────────────
    # When the meaning block is empty (~30% of names — Wikipedia etymology
    # didn't parse), the 650–830 band would otherwise be blank canvas. In
    # that case, slide the numerology block up to occupy it, add a short
    # explainer headline, and let each card's description wrap to more
    # lines so the card fills its taller height.
    if numerology:
        full_height = not meaning
        nblock_top = 650 if full_height else 870
        nblock_bot = 1380
        d.rounded_rectangle([(60, nblock_top), (W - 60, nblock_bot)],
                            radius=32, fill=NUM_BG)
        d.text((90, nblock_top + 24), "NUMEROLOGY",
               font=_font(INTER, 22), fill=CORAL)
        # Explainer (only when we have the extra room).
        if full_height:
            blurb = "Each number maps a different facet of the name — the path it sets, the inner self it expresses, the face it shows the world."
            blurb_font = _font(INTER, 22)
            blurb_lines = _wrap(d, blurb, blurb_font, W - 180, max_lines=3)
            by = nblock_top + 60
            for line in blurb_lines:
                d.text((90, by), line, font=blurb_font, fill=MUTED)
                by += 30
        cards = numerology[:3]
        card_w = (W - 60 - 60 - 40) // 3       # 60px margins, 20px gap
        card_y = nblock_top + (170 if full_height else 70)
        x = 80
        for entry in cards:
            num, lbl, trait_name, trait_desc = entry
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
            # trait name — auto-shrink to fit
            sz = 24
            t_font = _font(POPPINS, sz)
            tw = d.textlength(trait_name, font=t_font)
            while tw > card_w - 12 and sz > 16:
                sz -= 1
                t_font = _font(POPPINS, sz)
                tw = d.textlength(trait_name, font=t_font)
            d.text((cx - tw / 2, card_y + 146), trait_name, font=t_font, fill=INK)
            # trait description — wrap up to 3 lines
            if trait_desc:
                desc_font = _font(INTER, 16)
                desc_lines = _wrap(d, trait_desc, desc_font, card_w - 10, max_lines=4)
                dy = card_y + 184
                for line in desc_lines:
                    lw2 = d.textlength(line, font=desc_font)
                    d.text((cx - lw2 / 2, dy), line, font=desc_font, fill=MUTED)
                    dy += 22
            x += card_w + 20

    # ─── Footer (1390 → 1500) ──────────────────────────────────────────
    d.rectangle([(0, H - 110), (W, H)], fill=INK)
    url_font = _font(POPPINS, 32)
    uw = d.textlength(url, font=url_font)
    d.text(((W - uw) // 2, H - 76), url, font=url_font, fill=WHITE)
    d.rectangle([(0, H - 12), (W, H)], fill=TEAL)

    # Palettize — cuts file ~3× for this flat-color design without visible loss.
    img.convert('P', palette=Image.ADAPTIVE, colors=96).save(out_path, 'PNG', optimize=True)
