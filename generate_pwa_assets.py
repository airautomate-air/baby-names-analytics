"""Render the PNG app icons we need for the PWA install + iOS home screen.

Outputs 192x192 + 512x512 (standard manifest) and 180x180 (Apple touch icon)
PNGs to docs/, all painted from the same brand mark used in favicon.svg.
"""
from pathlib import Path
from PIL import Image, ImageDraw

INK   = (27, 36, 64)
TEAL  = (20, 158, 145)
CORAL = (255, 107, 92)
WHITE = (255, 255, 255)

OUT = Path('docs')


def render_icon(size: int, out_path: Path, *, padded: bool = False) -> None:
    """Render the brand mark at `size`px. If padded, leaves transparent
    margin (handy for non-square crops); otherwise fills the canvas."""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    inset = int(size * 0.06) if padded else 0
    s = size - 2 * inset
    radius = int(s * 0.22)
    d.rounded_rectangle(
        [(inset, inset), (size - inset, size - inset)],
        radius=radius, fill=TEAL,
    )
    # Trend polyline (matches favicon's 32×32 coords, scaled).
    def pt(x, y):
        return (inset + int(x / 32 * s), inset + int(y / 32 * s))
    pts = [pt(6, 22), pt(12, 17), pt(17, 20), pt(24, 10)]
    line_w = max(2, int(s * 0.08))
    d.line(pts, fill=WHITE, width=line_w, joint='curve')
    for p in pts:
        r = max(2, int(s * 0.025))
        d.ellipse([(p[0] - r, p[1] - r), (p[0] + r, p[1] + r)], fill=WHITE)
    # Coral accent dot (matches the favicon).
    cx, cy = pt(24, 10)
    cr = int(s * 0.09)
    d.ellipse([(cx - cr, cy - cr), (cx + cr, cy + cr)], fill=CORAL)
    img.save(out_path, 'PNG', optimize=True)


def render_maskable(size: int, out_path: Path) -> None:
    """Maskable icon: keep the mark inside the safe 80% area so Android can
    crop to any shape (circle, squircle, etc.) without clipping content."""
    img = Image.new('RGB', (size, size), TEAL)
    d = ImageDraw.Draw(img)
    inset = int(size * 0.15)
    s = size - 2 * inset
    def pt(x, y):
        return (inset + int(x / 32 * s), inset + int(y / 32 * s))
    pts = [pt(6, 22), pt(12, 17), pt(17, 20), pt(24, 10)]
    line_w = max(2, int(s * 0.09))
    d.line(pts, fill=WHITE, width=line_w, joint='curve')
    for p in pts:
        r = max(2, int(s * 0.028))
        d.ellipse([(p[0] - r, p[1] - r), (p[0] + r, p[1] + r)], fill=WHITE)
    cx, cy = pt(24, 10)
    cr = int(s * 0.10)
    d.ellipse([(cx - cr, cy - cr), (cx + cr, cy + cr)], fill=CORAL)
    img.save(out_path, 'PNG', optimize=True)


for size in (192, 512):
    p = OUT / f'icon-{size}.png'
    render_icon(size, p)
    print(f"  {p}  ({p.stat().st_size // 1024} KB)")

render_icon(180, OUT / 'apple-touch-icon.png')
print(f"  {OUT / 'apple-touch-icon.png'}")

render_maskable(512, OUT / 'icon-maskable-512.png')
print(f"  {OUT / 'icon-maskable-512.png'}")
