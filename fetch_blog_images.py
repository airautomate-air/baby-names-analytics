#!/usr/bin/env python3
"""
fetch_blog_images.py — Download stock photos for each blog post from Pexels.

Usage:
    PEXELS_API_KEY=your_key_here python3 fetch_blog_images.py

Or put the key in a .env file:
    echo "PEXELS_API_KEY=your_key_here" > .env
    python3 fetch_blog_images.py

Get a free API key at: https://www.pexels.com/api/
Free tier: 200 req/hour, 20,000/month. This script makes one request per post.

Images are saved to docs/blog/images/{slug}.jpg at 1200×630 (social/OG size).
Re-run any time to pick up new posts; existing images are skipped.
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path

try:
    from PIL import Image
    import io
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False
    print("Warning: Pillow not installed. Images will be saved without resizing.")

# ---------------------------------------------------------------------------
# API key — env var or .env file
# ---------------------------------------------------------------------------
API_KEY = os.environ.get('PEXELS_API_KEY', '')
if not API_KEY:
    env_file = Path('.env')
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith('PEXELS_API_KEY='):
                API_KEY = line.split('=', 1)[1].strip().strip('"\'')
                break

if not API_KEY:
    print("ERROR: No Pexels API key found.")
    print("  Set env var:  PEXELS_API_KEY=your_key python3 fetch_blog_images.py")
    print("  Or create .env file with:  PEXELS_API_KEY=your_key")
    print("  Get a free key at: https://www.pexels.com/api/")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Per-slug search queries — curated for best results
# ---------------------------------------------------------------------------
SLUG_QUERIES = {
    # Decade flashbacks
    'names-of-1914': 'vintage 1910s portrait family sepia',
    'names-of-1924': 'roaring twenties vintage 1920s portrait',
    'names-of-1934': 'great depression 1930s vintage family',
    'names-of-1944': 'world war two 1940s vintage family',
    'names-of-1954': '1950s vintage family americana',
    'names-of-1964': '1960s retro family vintage',
    'names-of-1974': '1970s retro family portrait',
    'names-of-1984': '1980s retro family childhood',
    'names-of-1994': '1990s childhood retro family',
    'names-of-2004': '2000s family childhood nostalgic',
    'names-of-2014': 'modern family baby nursery',
    'names-100-years-ago': 'antique vintage 1920s photograph family',

    # Origin / heritage posts
    'african-origin-names': 'african family celebration cultural',
    'arabic-persian-origin-names': 'middle eastern architecture ornate mosaic',
    'english-origin-names': 'english countryside village cottage',
    'french-origin-names': 'paris france eiffel tower romantic',
    'german-origin-names': 'germany bavaria forest castle',
    'greek-origin-names': 'greece santorini ancient ruins blue',
    'hebrew-origin-names': 'jerusalem israel ancient stone architecture',
    'irish-origin-names': 'ireland green landscape cliffs coast',
    'italian-origin-names': 'italy rome colosseum historic',
    'italian-names-america': 'little italy new york street festival',
    'japanese-origin-names': 'japan cherry blossom temple traditional',
    'latin-origin-names': 'roman ruins ancient marble columns',
    'scandinavian-origin-names': 'scandinavia norway fjord landscape',
    'scottish-origin-names': 'scotland highland castle bagpipe',
    'slavic-origin-names': 'eastern europe architecture cathedral',
    'spanish-origin-names': 'spain barcelona architecture sunlight',
    'welsh-origin-names': 'wales castle green hills landscape',
    'french-us-names-both': 'france america friendship cultural',
    'french-names-not-in-us': 'paris cafe culture french style',

    # Pop culture / celebrity
    'bridgerton-names': 'regency era ball gown dress elegant',
    'disney-names': 'fairy tale castle magic kingdom',
    'game-of-thrones-names': 'medieval castle stone fortress dramatic',
    'harry-potter-names': 'english countryside castle library books',
    'taylor-swift-names': 'concert stadium lights crowd music',
    'pop-culture-names': 'entertainment hollywood celebrity red carpet',
    'friends-names': 'coffee shop cafe friends laughing table',
    'music-names': 'concert music performance stage light',
    'literary-names': 'library books reading vintage classic',
    'sports-names': 'sports stadium athlete achievement',
    'presidential-names': 'american flag white house patriotic',
    'royal-baby-names': 'royal palace crown elegant british',

    # Nature / botanical
    'flower-names': 'wildflowers meadow colorful blooming',
    'nature-names': 'forest nature landscape serene green',
    'celestial-names': 'stars night sky milky way galaxy',
    'season-names': 'four seasons autumn spring bloom',
    'color-names': 'colorful rainbow vibrant abstract art',
    'gemstone-names': 'gemstones jewels colorful sparkling',
    'place-names': 'world map travel landscape geography',

    # Trends / analysis
    'rising-girl-names-2024': 'baby girl newborn nursery pink',
    'rising-boy-names-2024': 'baby boy newborn nursery blue',
    'falling-girl-names-2024': 'nostalgic retro childhood girl',
    'falling-boy-names-2024': 'nostalgic retro childhood boy',
    'fastest-rising-2024': 'arrow upward growth success chart',
    'three-letter-names': 'baby name alphabet wooden letters toy',
    'er-ending-names': 'baby boy nursery wooden toy blocks',
    'short-baby-names': 'minimalist nursery simple clean modern',
    'long-baby-names': 'calligraphy elegant writing pen script',
    'one-syllable-names': 'simple clean minimal modern design',
    'aiden-names': 'baby boy playing smiling happy',

    # Name patterns
    'names-ending-in-a': 'graceful elegant flower feminine bloom',
    'gender-neutral-names': 'neutral modern nursery scandi design',
    'surname-names': 'old leather bound book heritage family',
    'nickname-names': 'friendly casual happy family laughing',
    'occupational-names': 'craftsman workshop tools handmade',
    'skip-generation-names': 'grandparent grandchild generations family',
    'generational-names': 'three generations family portrait',
    'gender-shift-names': 'balance balance symmetry yin yang',
    'twin-names': 'twins baby matching cute siblings',
    'sibling-names': 'siblings children playing together',

    # Vintage / comeback
    'vintage-girl-names-comeback': 'vintage girl portrait 1920s elegant',
    'vintage-boy-names-comeback': 'vintage boy portrait 1920s classic',
    'timeless-names': 'classic antique elegant timeless',
    'fallen-names': 'vintage abandoned old forgotten classic',
    'hidden-gem-names': 'hidden treasure gem discovery unique',

    # Meaning posts
    'names-meaning-light': 'golden sunrise light rays warm glow',
    'names-meaning-strength': 'strong mountain powerful bold dramatic',
    'names-meaning-love': 'heart love romantic roses red',
    'names-meaning-grace': 'elegant ballet dancer graceful movement',
    'names-meaning-warrior': 'warrior strong bold dramatic epic',
    'names-meaning-peace': 'peaceful calm serene lake nature',

    # Data / history
    'us-number-one-boys-history': 'american history timeline classic',
    'us-number-one-girls-history': 'american history timeline classic',
    'gender-shift-names': 'change transformation modern gender',
    'country-western-names': 'cowboy country western barn rural',
    'biblical-names-2024': 'ancient bible scripture stone church',
    'mythological-names': 'ancient greek roman sculpture marble',

    # Misc
    'virtue-names': 'elegant graceful virtue calm meditation',
    'hidden-gem-names': 'treasure unique rare jewel gem discovery',
}

TAG_QUERIES = {
    'vintage': 'vintage nursery antique baby classic',
    'pop-culture': 'entertainment television cinema retro',
    'tv': 'television screen living room vintage',
    'origin': 'world map heritage culture family',
    'nature': 'flowers garden botanical wildflower',
    'biblical': 'ancient stone church architecture',
    'trends': 'baby nursery newborn modern',
    'meaning': 'meaningful elegant symbolic calm',
    'celebrity': 'celebrity fame entertainment glamour',
    'literary': 'books library reading vintage',
    'music': 'concert music performance stage',
    'sports': 'sports athlete competition stadium',
    'history': 'history vintage archive antique',
    'analysis': 'baby nursery soft pastel newborn',
}


def get_query(slug: str, tags: list) -> str:
    if slug in SLUG_QUERIES:
        return SLUG_QUERIES[slug]
    for tag in tags:
        q = TAG_QUERIES.get(tag.lower())
        if q:
            return q
    return 'baby nursery newborn soft pastel'


# ---------------------------------------------------------------------------
# Frontmatter parser (same logic as generate_site.py)
# ---------------------------------------------------------------------------
def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith('---'):
        return {}, text
    end = text.find('\n---', 4)
    if end < 0:
        return {}, text
    front = text[3:end].strip()
    meta: dict = {}
    for line in front.splitlines():
        if ':' not in line:
            continue
        k, v = line.split(':', 1)
        meta[k.strip()] = v.strip().strip('"\'')
    if 'tags' in meta:
        meta['tags'] = [t.strip() for t in meta['tags'].strip('[]').split(',') if t.strip()]
    return meta, text[end + 4:]


# ---------------------------------------------------------------------------
# Pexels API
# ---------------------------------------------------------------------------
def search_pexels(query: str):
    """Return the URL of the best landscape photo for query, or None."""
    params = urllib.parse.urlencode({
        'query': query,
        'per_page': 5,
        'orientation': 'landscape',
        'size': 'large',
    })
    req = urllib.request.Request(
        f'https://api.pexels.com/v1/search?{params}',
        headers={'Authorization': API_KEY, 'User-Agent': 'Mozilla/5.0'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f'    API error: {e}')
        return None
    photos = data.get('photos', [])
    if not photos:
        return None
    # Prefer the first result; use 'large2x' (1880px wide) or 'large' (940px)
    src = photos[0].get('src', {})
    return src.get('large2x') or src.get('large') or src.get('original')


def download_and_save(url: str, dest: Path) -> bool:
    """Download image, resize to 1200×630, save as JPEG."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'NameCharted/1.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as e:
        print(f'    Download error: {e}')
        return False

    if HAS_PILLOW:
        try:
            img = Image.open(io.BytesIO(data)).convert('RGB')
            # Smart crop to 1200×630 (16:8.4) — center crop
            target_w, target_h = 1200, 630
            orig_w, orig_h = img.size
            scale = max(target_w / orig_w, target_h / orig_h)
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            img = img.crop((left, top, left + target_w, top + target_h))
            img.save(dest, 'JPEG', quality=85, optimize=True)
        except Exception as e:
            print(f'    Pillow error ({e}), saving raw bytes')
            dest.write_bytes(data)
    else:
        dest.write_bytes(data)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    blog_dir = Path('data/blog')
    img_dir = Path('docs/blog/images')
    img_dir.mkdir(parents=True, exist_ok=True)

    posts = sorted(blog_dir.glob('*.md'))
    print(f"Found {len(posts)} blog posts. Checking for missing images...\n")

    skipped = 0
    fetched = 0
    failed = 0

    for md_path in posts:
        meta, _ = parse_frontmatter(md_path.read_text(encoding='utf-8'))
        slug = meta.get('slug', '')
        if not slug:
            continue
        country = meta.get('country', 'US')
        if country != 'US':
            continue  # images only for US posts

        dest = img_dir / f'{slug}.jpg'
        if dest.exists():
            skipped += 1
            continue

        tags = meta.get('tags', [])
        query = get_query(slug, tags)
        print(f"  [{slug}]")
        print(f"    query: {query!r}")

        url = search_pexels(query)
        if not url:
            print(f"    No results — skipping")
            failed += 1
            continue

        ok = download_and_save(url, dest)
        if ok:
            size_kb = dest.stat().st_size // 1024
            print(f"    saved {dest.name} ({size_kb}KB)")
            fetched += 1
        else:
            failed += 1

    print(f"\nDone: {fetched} downloaded, {skipped} already existed, {failed} failed.")
    if fetched > 0:
        print(f"\nNext step: run  python3 generate_site.py  to rebuild with images.")


if __name__ == '__main__':
    main()
