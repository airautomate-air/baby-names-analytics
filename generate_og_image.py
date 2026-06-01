"""Generate the default 1200x630 OG image for NameCharted."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
INK = (27, 36, 64)
TEAL = (20, 158, 145)
CORAL = (255, 107, 92)
CANVAS = (247, 248, 250)
MUTED = (91, 102, 120)
WHITE = (255, 255, 255)

FONT_DIR = Path(__file__).parent / 'assets' / 'fonts'
POPPINS = str(FONT_DIR / 'Poppins-Bold.ttf')
INTER = str(FONT_DIR / 'Inter-Regular.ttf')

img = Image.new('RGB', (W, H), CANVAS)
d = ImageDraw.Draw(img)

d.rectangle([(0, 0), (W, 90)], fill=INK)

logo_size = 180
logo_x, logo_y = 130, 200
d.rounded_rectangle([(logo_x, logo_y), (logo_x + logo_size, logo_y + logo_size)],
                    radius=36, fill=TEAL)
pts = [
    (logo_x + 35, logo_y + 135),
    (logo_x + 70, logo_y + 100),
    (logo_x + 100, logo_y + 118),
    (logo_x + 145, logo_y + 55),
]
d.line(pts, fill=WHITE, width=14, joint='curve')
for p in pts:
    d.ellipse([(p[0]-7, p[1]-7), (p[0]+7, p[1]+7)], fill=WHITE)
d.ellipse([(logo_x + 125, logo_y + 35), (logo_x + 165, logo_y + 75)], fill=CORAL)

wordmark_x = 360
title_font = ImageFont.truetype(POPPINS, 110)
d.text((wordmark_x, 195), "Name", font=title_font, fill=INK)
name_w = d.textlength("Name", font=title_font)
d.text((wordmark_x + name_w, 195), "Charted", font=title_font, fill=TEAL)

tag_font = ImageFont.truetype(INTER, 42)
d.text((wordmark_x, 340), "Names, charted.", font=tag_font, fill=MUTED)

sub_font = ImageFont.truetype(INTER, 28)
d.text((wordmark_x, 410), "U.S. baby name popularity & trends, 1880–2024.",
       font=sub_font, fill=MUTED)
d.text((wordmark_x, 450), "104,000+ names · interactive charts · SSA data.",
       font=sub_font, fill=MUTED)

d.rectangle([(0, H - 12), (W, H)], fill=TEAL)

out = Path(__file__).parent / 'docs' / 'og-default.png'
img.save(out, 'PNG', optimize=True)
print(f"Wrote {out} ({out.stat().st_size // 1024} KB)")
