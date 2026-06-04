"""Render three sample Pinterest pin cards for the name 'Olivia' so we can
compare PNG vs JPEG vs SVG before committing to a format for the full build.

Pinterest's preferred pin ratio is 2:3 — we use 1000x1500.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# --- brand tokens (mirror generate_og_image.py) ---
INK     = (27, 36, 64)
TEAL    = (20, 158, 145)
TEAL_DK = (15, 124, 114)
CORAL   = (255, 107, 92)
CANVAS  = (247, 248, 250)
MUTED   = (91, 102, 120)
WHITE   = (255, 255, 255)
SOFT    = (232, 244, 242)  # tinted bg behind the name

W, H = 1000, 1500

FONT_DIR = Path(__file__).parent / 'assets' / 'fonts'
POPPINS  = str(FONT_DIR / 'Poppins-Bold.ttf')
INTER    = str(FONT_DIR / 'Inter-Regular.ttf')

OUT = Path(__file__).parent / 'pin-samples'
OUT.mkdir(exist_ok=True)

# --- sample data — Olivia (US #1, 2024) ---
NAME       = "Olivia"
GENDER     = "Girl name"
ORIGIN     = "Greek"
MEANING    = "olive tree · peace"
RANK_LINE  = "#1 in the US, 2024"
PEAK_LINE  = "Peaked in the 2010s"
SYLL_LINE  = "3 syllables"
URL        = "namecharted.com/name/olivia"
FLAG_EMOJI = "US"  # rendered as a small chip rather than emoji for font safety


def draw_pin(img: Image.Image):
    """Paint the shared pin design onto `img`. Used by both PNG + JPEG."""
    d = ImageDraw.Draw(img)
    d.rectangle([(0, 0), (W, H)], fill=CANVAS)

    # top brand bar
    d.rectangle([(0, 0), (W, 80)], fill=INK)
    bar_font = ImageFont.truetype(POPPINS, 30)
    d.text((48, 22), "Name", font=bar_font, fill=WHITE)
    nw = d.textlength("Name", font=bar_font)
    d.text((48 + nw, 22), "Charted", font=bar_font, fill=TEAL)
    # country chip
    chip_font = ImageFont.truetype(INTER, 22)
    chip_text = "🇺🇸 United States"  # may fall back; we also draw a colored dot
    cw = d.textlength(chip_text, font=chip_font)
    d.text((W - cw - 48, 26), chip_text, font=chip_font, fill=WHITE)

    # soft hero panel behind the name
    panel_top, panel_bot = 180, 760
    d.rounded_rectangle([(60, panel_top), (W - 60, panel_bot)],
                        radius=40, fill=SOFT)
    # accent dot
    d.ellipse([(W - 180, panel_top + 60), (W - 100, panel_top + 140)], fill=CORAL)

    # huge name
    # Pick a size that fits inside the panel with margin
    name_size = 320
    name_font = ImageFont.truetype(POPPINS, name_size)
    nw = d.textlength(NAME, font=name_font)
    while nw > W - 200 and name_size > 120:
        name_size -= 10
        name_font = ImageFont.truetype(POPPINS, name_size)
        nw = d.textlength(NAME, font=name_font)
    name_y = panel_top + (panel_bot - panel_top - name_size) // 2 - 30
    d.text(((W - nw) // 2, name_y), NAME, font=name_font, fill=INK)

    # gender + origin chip row under name (inside panel)
    sub_font = ImageFont.truetype(INTER, 36)
    sub_text = f"{GENDER}  ·  {ORIGIN}"
    sw = d.textlength(sub_text, font=sub_font)
    d.text(((W - sw) // 2, panel_bot - 90), sub_text, font=sub_font, fill=MUTED)

    # meaning callout
    mean_label = ImageFont.truetype(INTER, 24)
    mean_val   = ImageFont.truetype(POPPINS, 44)
    d.text((80, 820), "MEANING", font=mean_label, fill=TEAL)
    d.text((80, 858), MEANING, font=mean_val, fill=INK)

    # three stat rows
    stat_label = ImageFont.truetype(INTER, 22)
    stat_val   = ImageFont.truetype(POPPINS, 36)
    rows = [
        ("POPULARITY", RANK_LINE),
        ("PEAK ERA",   PEAK_LINE),
        ("SOUND",      SYLL_LINE),
    ]
    y = 970
    for label, val in rows:
        # left rule
        d.rectangle([(80, y + 14), (88, y + 58)], fill=TEAL)
        d.text((112, y), label, font=stat_label, fill=MUTED)
        d.text((112, y + 28), val, font=stat_val, fill=INK)
        y += 100

    # footer URL band
    d.rectangle([(0, H - 110), (W, H)], fill=INK)
    url_font = ImageFont.truetype(POPPINS, 34)
    uw = d.textlength(URL, font=url_font)
    d.text(((W - uw) // 2, H - 78), URL, font=url_font, fill=WHITE)
    d.rectangle([(0, H - 12), (W, H)], fill=TEAL)


# --- 1a. PNG (truecolor, max optimize) ---
png_img = Image.new('RGB', (W, H), CANVAS)
draw_pin(png_img)
png_path = OUT / 'olivia.png'
png_img.save(png_path, 'PNG', optimize=True)

# --- 1b. PNG palettized (best for flat-color designs like this) ---
png8_path = OUT / 'olivia.palette.png'
png_img.convert('P', palette=Image.ADAPTIVE, colors=64).save(png8_path, 'PNG', optimize=True)

# --- 2a. JPEG q82 ---
jpg_img = Image.new('RGB', (W, H), CANVAS)
draw_pin(jpg_img)
jpg_path = OUT / 'olivia.jpg'
jpg_img.save(jpg_path, 'JPEG', quality=82, optimize=True, progressive=True)

# --- 2b. JPEG q70 (more aggressive) ---
jpg70_path = OUT / 'olivia.q70.jpg'
jpg_img.save(jpg70_path, 'JPEG', quality=70, optimize=True, progressive=True)

# --- 2c. WebP (modern, often best ratio) ---
webp_path = OUT / 'olivia.webp'
jpg_img.save(webp_path, 'WEBP', quality=80, method=6)

# --- 3. SVG (text-based, smallest, but Pinterest pin button requires raster) ---
svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Inter, system-ui, sans-serif">
  <rect width="100%" height="100%" fill="rgb{CANVAS}"/>

  <!-- top brand bar -->
  <rect x="0" y="0" width="{W}" height="80" fill="rgb{INK}"/>
  <text x="48" y="52" font-family="Poppins, sans-serif" font-weight="700" font-size="30" fill="white">Name<tspan fill="rgb{TEAL}">Charted</tspan></text>
  <text x="{W-48}" y="50" text-anchor="end" font-size="22" fill="white">🇺🇸 United States</text>

  <!-- hero panel -->
  <rect x="60" y="180" width="{W-120}" height="580" rx="40" ry="40" fill="rgb{SOFT}"/>
  <circle cx="{W-140}" cy="280" r="40" fill="rgb{CORAL}"/>

  <!-- name -->
  <text x="{W/2}" y="540" text-anchor="middle" font-family="Poppins, sans-serif" font-weight="700" font-size="320" fill="rgb{INK}">{NAME}</text>

  <!-- gender + origin -->
  <text x="{W/2}" y="700" text-anchor="middle" font-size="36" fill="rgb{MUTED}">{GENDER}  ·  {ORIGIN}</text>

  <!-- meaning -->
  <text x="80" y="844" font-size="24" fill="rgb{TEAL}" letter-spacing="2">MEANING</text>
  <text x="80" y="898" font-family="Poppins, sans-serif" font-weight="700" font-size="44" fill="rgb{INK}">{MEANING}</text>

  <!-- stat rows -->
  <g transform="translate(80, 970)">
"""
rows = [("POPULARITY", RANK_LINE), ("PEAK ERA", PEAK_LINE), ("SOUND", SYLL_LINE)]
for i, (lbl, val) in enumerate(rows):
    y = i * 100
    svg += f'''    <rect x="0" y="{y+14}" width="8" height="44" fill="rgb{TEAL}"/>
    <text x="32" y="{y+24}" font-size="22" fill="rgb{MUTED}" letter-spacing="2">{lbl}</text>
    <text x="32" y="{y+66}" font-family="Poppins, sans-serif" font-weight="700" font-size="36" fill="rgb{INK}">{val}</text>
'''
svg += f"""  </g>

  <!-- footer -->
  <rect x="0" y="{H-110}" width="{W}" height="110" fill="rgb{INK}"/>
  <text x="{W/2}" y="{H-66}" text-anchor="middle" font-family="Poppins, sans-serif" font-weight="700" font-size="34" fill="white">{URL}</text>
  <rect x="0" y="{H-12}" width="{W}" height="12" fill="rgb{TEAL}"/>
</svg>
"""
svg_path = OUT / 'olivia.svg'
svg_path.write_text(svg, encoding='utf-8')

# --- report ---
variants = [
    (png_path,  'PNG truecolor'),
    (png8_path, 'PNG 64-color'),
    (jpg_path,  'JPEG q82'),
    (jpg70_path,'JPEG q70'),
    (webp_path, 'WebP q80'),
    (svg_path,  'SVG'),
]
for p, label in variants:
    kb = p.stat().st_size / 1024
    print(f"  {label:14s}  {p.name:22s}  {kb:7.1f} KB")

print()
print("Extrapolated repo footprint:")
print(f"  {'':14s}  {'30K names':>10s}  {'top 1K/cc (5K)':>16s}")
for p, label in variants:
    sz = p.stat().st_size
    all_mb  = (sz * 30000) / (1024 * 1024)
    top_mb  = (sz *  5000) / (1024 * 1024)
    print(f"  {label:14s}  {all_mb:7.0f} MB  {top_mb:13.0f} MB")
