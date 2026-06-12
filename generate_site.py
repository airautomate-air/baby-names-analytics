#!/usr/bin/env python3
"""
Generate static site for baby names analytics.

Reads normalized per-country CSVs from data/normalized/<cc>.csv with schema
(country, year, sex, name, count). Each country gets its own URL tree:
US lives at the root (preserving every legacy URL); FR/GB/AU live at
/fr/, /uk/, /au/. A single sitemap.xml and robots.txt are emitted at the root.
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from pin_renderer import render_pin

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
COUNTRIES = ["US", "FR", "GB", "AU", "CA", "ES", "IT", "NL"]
# URL slug per country. US is empty (root). GB exposes /uk/ for branding.
COUNTRY_SLUG = {"US": "", "FR": "fr", "GB": "uk", "AU": "au", "CA": "ca", "ES": "es", "IT": "it", "NL": "nl"}
COUNTRY_LABEL = {"US": "US", "FR": "FR", "GB": "UK", "AU": "AU", "CA": "CA", "ES": "ES", "IT": "IT", "NL": "NL"}
COUNTRY_NAME = {"US": "United States", "FR": "France", "GB": "United Kingdom",
                "AU": "Australia", "CA": "Canada", "ES": "Spain", "IT": "Italy", "NL": "Netherlands"}
FLAG = {"US": "🇺🇸", "FR": "🇫🇷", "GB": "🇬🇧", "AU": "🇦🇺", "CA": "🇨🇦", "ES": "🇪🇸", "IT": "🇮🇹", "NL": "🇳🇱"}
# Country names rendered in each UI language (for the homepage cross-country callout).
COUNTRY_NAMES_EN = {"US": "United States", "FR": "France", "GB": "UK",
                    "AU": "Australia", "CA": "Canada", "ES": "Spain", "IT": "Italy", "NL": "Netherlands"}
COUNTRY_NAMES_FR = {"US": "États-Unis", "FR": "France", "GB": "Royaume-Uni",
                    "AU": "Australie", "CA": "Canada", "ES": "Espagne", "IT": "Italie", "NL": "Pays-Bas"}
COUNTRY_NAMES_IN_UI = {"US": COUNTRY_NAMES_EN, "FR": COUNTRY_NAMES_FR,
                       "GB": COUNTRY_NAMES_EN, "AU": COUNTRY_NAMES_EN,
                       "CA": COUNTRY_NAMES_EN, "ES": COUNTRY_NAMES_EN,
                       "IT": COUNTRY_NAMES_EN, "NL": COUNTRY_NAMES_EN}
DATA_SOURCE_FULL = {
    "US": "U.S. Social Security Administration",
    "FR": "INSEE (France)",
    "GB": "UK Office for National Statistics",
    "AU": "NSW BDM + VIC BDM (Australia)",
    "CA": "Statistics Canada (Canadian Vital Statistics)",
    "ES": "INE Padrón (Spain) — decadal totals",
    "IT": "ISTAT Contanomi (Italy)",
    "NL": "Meertens Voornamenbank (Netherlands)",
}
DATA_SOURCE_SHORT = {
    "US": "official SSA",
    "FR": "official INSEE",
    "GB": "official ONS",
    "AU": "official NSW & VIC BDM",
    "CA": "official StatCan",
    "ES": "official INE",
    "IT": "official ISTAT",
    "NL": "Meertens NVB",
}

DATA_DIR = Path('data/normalized')
OUTPUT_DIR = Path('docs')
TOP_N_NAMES = 1000
BASE_URL = "https://namecharted.com"
PAGE_MIN_TOTAL = 500

OUTPUT_DIR.mkdir(exist_ok=True)


def count_syllables(name: str) -> int:
    """Cheap heuristic: count vowel groups in the diacritic-folded name,
    drop a trailing silent 'e'. Good enough for rhythm scoring."""
    folded = unicodedata.normalize('NFD', name.lower())
    folded = ''.join(c for c in folded if unicodedata.category(c) != 'Mn')
    folded = re.sub(r'[^a-z]', '', folded)
    if not folded:
        return 1
    n = 0
    prev_vowel = False
    for c in folded:
        is_vowel = c in 'aeiouy'
        if is_vowel and not prev_vowel:
            n += 1
        prev_vowel = is_vowel
    if folded.endswith('e') and n > 1 and not folded.endswith('le'):
        n -= 1
    return max(1, n)


# Pythagorean numerology — letter-to-number map, A=1 … I=9, repeating.
_NUMEROLOGY_MAP = {c: (i % 9) + 1 for i, c in enumerate('abcdefghijklmnopqrstuvwxyz')}
_VOWELS = set('aeiouy')


def _reduce_numerology(n: int) -> int:
    """Reduce a sum to a single digit (1-9), preserving the 'master numbers'
    11, 22, 33 which numerology treats as standalone meanings."""
    while n > 9 and n not in (11, 22, 33):
        n = sum(int(d) for d in str(n))
    return n


def numerology_numbers(name: str) -> tuple[int, int, int]:
    """Returns (destiny, soul_urge, personality):
      destiny     — sum of ALL letters (life path / expression number)
      soul_urge   — sum of VOWELS only
      personality — sum of CONSONANTS only
    Diacritics are folded; non-letters are ignored."""
    s = unicodedata.normalize('NFD', name.lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    letters = [c for c in s if 'a' <= c <= 'z']
    if not letters:
        return (0, 0, 0)
    destiny = _reduce_numerology(sum(_NUMEROLOGY_MAP[c] for c in letters))
    soul = _reduce_numerology(sum(_NUMEROLOGY_MAP[c] for c in letters if c in _VOWELS))
    pers = _reduce_numerology(sum(_NUMEROLOGY_MAP[c] for c in letters if c not in _VOWELS))
    return (destiny, soul, pers)


def filter_famous_for(famous: list, given_name: str) -> list:
    """Keep only bearers whose display label actually leads with this given
    name. The raw Wikidata P735 list includes anyone for whom this is *any*
    of their given names — so 'Mary' was pulling Agatha (Mary) Christie,
    Meryl (Mary Louise) Streep, Amelia (Mary) Earhart. They use a different
    public name; we don't want them in the 'Famous people named Mary' list.
    """
    target = unicodedata.normalize('NFD', given_name.lower())
    target = ''.join(c for c in target if unicodedata.category(c) != 'Mn')
    target = re.sub(r'[^a-z]', '', target)
    if not target:
        return famous
    out = []
    for p in famous:
        label = p.get('name', '')
        folded = unicodedata.normalize('NFD', label.lower())
        folded = ''.join(c for c in folded if unicodedata.category(c) != 'Mn')
        # Treat hyphens/apostrophes as word boundaries (Jean-Pierre, O'Connor)
        tokens = re.split(r"[^a-z]+", folded)
        if tokens and tokens[0] == target:
            out.append(p)
    return out


def phonetic_key(name: str) -> str:
    """Metaphone-flavoured phonetic skeleton tuned for given names.

    Folds accents, applies the substitutions that catch the variants we
    actually see (ph→f, ck→k, qu→k, ch/sh/th → consonant codes), reduces
    c→k/s and g→j by the next-letter rule, then keeps the leading letter
    and strips the rest of the vowels. The result is a short consonant
    spine that's nearly identical for sound-alike names while still
    differing for genuinely distinct ones:

        Catherine/Katherine/Kathryn → ktrn
        Aiden/Aidan/Ayden            → adn
        Sophia/Sofia                 → sf
        Steven/Stephen               → stvn / stfn (different — by design,
                                                   they're a Wikipedia
                                                   merge but not phonetic
                                                   twins)
    """
    s = unicodedata.normalize('NFD', name.lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = re.sub(r'[^a-z]', '', s)
    if not s:
        return ''
    # Silent / awkward starting clusters
    for pref in ('kn', 'gn', 'pn', 'ps', 'wr', 'mn'):
        if s.startswith(pref):
            s = s[1:]
            break
    if s.startswith('x'):
        s = 's' + s[1:]
    # Multi-char substitutions, longest first
    for a, b in (('sch', 'X'), ('tch', 'X'), ('dge', 'j'), ('dg', 'j'),
                  ('ph', 'f'), ('gh', ''), ('ck', 'k'), ('qu', 'k'),
                  ('ch', 'X'), ('sh', 'X'), ('th', 't'), ('wh', 'w')):
        s = s.replace(a, b)
    # Single-char rules (left-to-right with single-char lookahead)
    out = []
    for i, c in enumerate(s):
        nxt = s[i + 1] if i + 1 < len(s) else ''
        if c == 'c':
            c = 's' if nxt in 'eiy' else 'k'
        elif c == 'g' and nxt in 'eiy':
            c = 'j'
        elif c == 'q':
            c = 'k'
        elif c == 'z':
            c = 's'
        elif c == 'y':
            c = 'i'
        elif c == 'x':
            out.append('k')
            c = 's'
        out.append(c)
    s = ''.join(out).replace('X', 'k')
    # Collapse runs of the same letter
    if s:
        collapsed = [s[0]]
        for c in s[1:]:
            if c != collapsed[-1]:
                collapsed.append(c)
        s = ''.join(collapsed)
    # Keep the leading letter then drop the rest of the vowels (the
    # consonant skeleton is what makes Catherine/Kathryn collapse).
    if s:
        s = s[0] + re.sub(r'[aeiou]', '', s[1:])
    return s[:6]


def slugify(name: str) -> str:
    """Consistent URL slug used for every internal link and file name.
    Strips diacritics so 'Léa' → 'lea' (not 'l-a') — keeps URLs ASCII-clean
    and avoids accented-vs-unaccented variants overwriting each other."""
    folded = unicodedata.normalize('NFD', name.lower())
    folded = ''.join(c for c in folded if unicodedata.category(c) != 'Mn')
    s = re.sub(r'[^a-z0-9]+', '-', folded).strip('-')
    return s or 'name'


# ---------------------------------------------------------------------------
# Country-scoped data structures.
# ---------------------------------------------------------------------------
years_by_country: dict[str, list[int]] = {}
counts_by_country: dict[str, dict] = {}
rank_by_year_sex_by_country: dict[str, dict] = {}
name_sex_total_by_country: dict[str, dict] = {}
name_total_by_country: dict[str, dict] = {}
pages_to_generate_by_country: dict[str, list] = {}
has_page_by_country: dict[str, set] = {}
same_sex_ranked_by_country: dict[str, dict] = {}
same_sex_index_by_country: dict[str, dict] = {}
latest_year_ranked_by_country: dict[str, dict] = {}
latest_year_index_by_country: dict[str, dict] = {}
by_initial_by_country: dict[str, dict] = {}
letter_names_by_country: dict[str, dict] = {}
name_meta_by_country: dict[str, dict] = {}
phonetic_by_sex_by_country: dict[str, dict] = {}
decades_by_country: dict[str, list[int]] = {}
decade_sex_counts_by_country: dict[str, dict] = {}
variants_of_by_country: dict[str, dict] = {}   # canonical_name -> [(variant, total), ...]
canonical_of_by_country: dict[str, dict] = {}  # variant_name -> canonical_name
# Top-10 names per year per sex for the homepage animated chart.
# Shape: cc -> {'years': [int, ...], 'M': {year: [[name, count], ...10]}, 'F': same}
top_race_by_country: dict[str, dict] = {}

# Global enrichment data shared across all countries. Origin + famous bearers
# keyed by name slug. Loaded once from data/normalized/name_enrichment.json if
# present — generator gracefully no-ops when file is missing or has no entry.
ENRICHMENT: dict[str, dict] = {}
# slug -> {origin: 'irish', famous: [...]}
ORIGIN_TO_NAMES_BY_CC: dict[str, dict[str, list[str]]] = {}
# Per-country: origin_slug -> sorted list of full names with that origin
# (so we can render /origin/<slug>.html with country-appropriate name lists)


def load_enrichment() -> None:
    global ENRICHMENT
    p = Path('data/normalized/name_enrichment.json')
    if not p.exists():
        print('  (no enrichment data yet — origins/famous sections will be empty)')
        return
    with p.open() as f:
        ENRICHMENT = json.load(f)
    print(f'  enrichment: {len(ENRICHMENT):,} names have origin or famous data')

    # Curated overrides — patches gaps in Wikidata P735 (Roman names where
    # the structured given name is a praenomen but the figure is known by
    # their nomen/cognomen, e.g. Julius Caesar's P735 is 'Gaius').
    op = Path('data/famous_overrides.json')
    if op.exists():
        with op.open() as f:
            overrides = json.load(f)
        n_patched = 0
        for slug, extras in overrides.items():
            if not isinstance(extras, list):  # skip _comment etc.
                continue
            entry = ENRICHMENT.setdefault(slug, {})
            existing = entry.get('famous', []) or []
            seen_urls = {p.get('url') for p in existing if p.get('url')}
            prepend = [p for p in extras if p.get('url') not in seen_urls]
            if prepend:
                entry['famous'] = prepend + existing
                n_patched += 1
        print(f'  famous overrides: patched {n_patched} names')

    # Curated short meanings — patches gaps in the Wikipedia auto-extractor.
    # Maps name slug -> short blurb (used by the pin renderer when extraction
    # fails). Override always wins over the auto-extracted meaning.
    mp = Path('data/name_meanings.json')
    if mp.exists():
        with mp.open() as f:
            mover = json.load(f)
        n_meanings = 0
        for slug, blurb in mover.items():
            if not isinstance(blurb, str) or slug.startswith('_'):
                continue
            entry = ENRICHMENT.setdefault(slug, {})
            entry['meaning_pin_override'] = blurb
            n_meanings += 1
        print(f'  meaning overrides: {n_meanings} names')


# Fiction data (Phase 6h). Shared across countries — each country's
# /fiction/<slug>.html links to its own /name/<slug>.html when the name exists.
FICTION: dict = {"franchises": []}
# Reverse index: name slug -> list of franchise dicts that include that name
FICTION_BY_NAME: dict[str, list[dict]] = {}


def load_fiction() -> None:
    global FICTION, FICTION_BY_NAME
    p = Path('data/fiction.json')
    if not p.exists():
        return
    with p.open() as f:
        FICTION = json.load(f)
    for fr in FICTION.get('franchises', []):
        for entry in fr.get('names', []):
            slug = slugify(entry['name'])
            FICTION_BY_NAME.setdefault(slug, []).append({
                'slug': fr['slug'], 'title': fr['title'], 'role': entry.get('role', '')
            })
    print(f'  fiction: {len(FICTION.get("franchises", []))} franchises, '
          f'{sum(len(f.get("names", [])) for f in FICTION.get("franchises", []))} characters')


# Blog posts (Phase 19). Loaded from data/blog/*.md, served per-country
# under /blog/. Each post has YAML-style frontmatter and a Markdown body.
BLOG_POSTS_BY_CC: dict[str, list[dict]] = {cc: [] for cc in COUNTRIES}


def _parse_blog_frontmatter(text: str) -> tuple[dict, str]:
    """Split a post into (frontmatter dict, body markdown). Frontmatter is
    a minimal YAML subset: key: value lines between leading `---` markers."""
    if not text.startswith('---'):
        return {}, text
    end = text.find('\n---', 4)
    if end < 0:
        return {}, text
    front = text[3:end].strip()
    body = text[end + 4:].lstrip('\n')
    meta: dict = {}
    for line in front.splitlines():
        if ':' not in line:
            continue
        k, v = line.split(':', 1)
        meta[k.strip()] = v.strip().strip('"\'')
    if 'tags' in meta:
        meta['tags'] = [t.strip() for t in meta['tags'].strip('[]').split(',') if t.strip()]
    return meta, body


_MD_LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
_MD_BOLD = re.compile(r'\*\*([^*]+)\*\*')
_MD_ITAL = re.compile(r'(?<!\*)\*([^*]+)\*(?!\*)')
_MD_CODE = re.compile(r'`([^`]+)`')


def _md_inline(s: str) -> str:
    """Escape HTML then apply inline markdown (links, bold, italic, code)."""
    s = (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
    s = _MD_LINK.sub(r'<a href="\2">\1</a>', s)
    s = _MD_BOLD.sub(r'<strong>\1</strong>', s)
    s = _MD_ITAL.sub(r'<em>\1</em>', s)
    s = _MD_CODE.sub(r'<code>\1</code>', s)
    return s


def md_to_html(body: str) -> str:
    """Tiny Markdown → HTML renderer. Supports headings (## / ###), bullet
    and numbered lists, blockquotes (>), horizontal rules (---), and
    paragraphs. Inline: [link](href), **bold**, *italic*, `code`."""
    out: list[str] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith('### '):
            out.append(f'<h3>{_md_inline(stripped[4:])}</h3>')
            i += 1
        elif stripped.startswith('## '):
            out.append(f'<h2>{_md_inline(stripped[3:])}</h2>')
            i += 1
        elif stripped.startswith('# '):
            out.append(f'<h1>{_md_inline(stripped[2:])}</h1>')
            i += 1
        elif stripped in ('---', '***'):
            out.append('<hr>')
            i += 1
        elif stripped.startswith('> '):
            block = []
            while i < len(lines) and lines[i].strip().startswith('> '):
                block.append(_md_inline(lines[i].strip()[2:]))
                i += 1
            out.append(f'<blockquote><p>{" ".join(block)}</p></blockquote>')
        elif stripped.startswith(('- ', '* ')):
            items = []
            while i < len(lines) and lines[i].strip().startswith(('- ', '* ')):
                items.append(f'<li>{_md_inline(lines[i].strip()[2:])}</li>')
                i += 1
            out.append('<ul>' + ''.join(items) + '</ul>')
        elif (stripped.startswith('|') and stripped.endswith('|')
              and i + 1 < len(lines)
              and re.match(r'^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$', lines[i + 1])):
            # Markdown table: header row, separator, then body rows.
            def cells(row):
                return [c.strip() for c in row.strip().strip('|').split('|')]
            header = cells(lines[i])
            i += 2  # skip header + separator
            body_rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                body_rows.append(cells(lines[i]))
                i += 1
            head_html = '<tr>' + ''.join(f'<th>{_md_inline(c)}</th>' for c in header) + '</tr>'
            body_html = ''.join(
                '<tr>' + ''.join(f'<td>{_md_inline(c)}</td>' for c in row) + '</tr>'
                for row in body_rows
            )
            out.append(f'<table class="blog-table"><thead>{head_html}</thead><tbody>{body_html}</tbody></table>')
        elif re.match(r'^\d+\.\s', stripped):
            items = []
            while i < len(lines) and re.match(r'^\s*\d+\.\s', lines[i]):
                content = re.sub(r'^\s*\d+\.\s', '', lines[i])
                items.append(f'<li>{_md_inline(content)}</li>')
                i += 1
            out.append('<ol>' + ''.join(items) + '</ol>')
        else:
            para = [stripped]
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(
                    ('#', '>', '- ', '* ', '---', '***')) and not re.match(
                        r'^\s*\d+\.\s', lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(f'<p>{_md_inline(" ".join(para))}</p>')
    return '\n'.join(out)


def load_blog() -> None:
    """Read data/blog/*.md, parse frontmatter + body, sort by date desc per
    country. Each post needs: title, slug, date (YYYY-MM-DD), description,
    country (US|FR|GB|AU|CA). Optional: tags."""
    d = Path('data/blog')
    if not d.is_dir():
        return
    n_total = 0
    for f in sorted(d.glob('*.md')):
        meta, body = _parse_blog_frontmatter(f.read_text(encoding='utf-8'))
        cc = meta.get('country')
        if cc not in BLOG_POSTS_BY_CC:
            print(f'  blog: skip {f.name} (country={cc!r} unknown)')
            continue
        for required in ('title', 'slug', 'date', 'description'):
            if not meta.get(required):
                print(f'  blog: skip {f.name} (missing {required})')
                break
        else:
            meta['body'] = body
            meta['html'] = md_to_html(body)
            BLOG_POSTS_BY_CC[cc].append(meta)
            n_total += 1
    for cc in COUNTRIES:
        BLOG_POSTS_BY_CC[cc].sort(key=lambda p: p['date'], reverse=True)
    print(f'  blog: {n_total} posts across {sum(1 for cc in COUNTRIES if BLOG_POSTS_BY_CC[cc])} countries')


# Saint-of-the-day calendar (Phase 6i FR-only, Phase 34 expanded to ES + IT).
# Each country has its own MM-DD → saint-name map and reverse slug → [dates] index.
SAINTS_BY_CC: dict[str, dict[str, str]] = {}
SAINT_TO_DATES_BY_CC: dict[str, dict[str, list[str]]] = {}

# Module-level shorthands for the active country. Repointed by build_country()
# at the start of each tree generation; kept for code that still reads SAINTS_FR
# directly (legacy callers).
SAINTS_FR: dict[str, str] = {}
SAINT_TO_DATES: dict[str, list[str]] = {}


def load_saints_all() -> None:
    """Load every saints_<cc>.json we have. Sets SAINTS_BY_CC and the per-CC
    reverse indexes. Idempotent — safe to call once per build."""
    global SAINTS_BY_CC, SAINT_TO_DATES_BY_CC
    for cc, fname in (('FR', 'saints_fr.json'), ('ES', 'saints_es.json'),
                      ('IT', 'saints_it.json')):
        p = Path('data') / fname
        if not p.exists():
            continue
        with p.open() as f:
            cal = json.load(f).get('calendar', {})
        SAINTS_BY_CC[cc] = cal
        rev: dict[str, list[str]] = {}
        for date, saint in cal.items():
            slug = slugify(saint)
            rev.setdefault(slug, []).append(date)
        SAINT_TO_DATES_BY_CC[cc] = rev
        print(f'  saints ({cc}): {len(cal)} calendar days, {len(rev)} unique slugs')


def _activate_saints_for(cc: str) -> None:
    """Point the legacy SAINTS_FR/SAINT_TO_DATES globals at the active CC's
    data so existing render code keeps working without per-call passing."""
    global SAINTS_FR, SAINT_TO_DATES
    SAINTS_FR = SAINTS_BY_CC.get(cc, {})
    SAINT_TO_DATES = SAINT_TO_DATES_BY_CC.get(cc, {})


# Back-compat shim — old code still calls load_saints_fr().
def load_saints_fr() -> None:
    load_saints_all()
    _activate_saints_for('FR')


def build_country(cc: str) -> None:
    """Load data/normalized/<cc>.csv and populate every *_by_country dict for cc."""
    csv_path = DATA_DIR / f'{cc.lower()}.csv'
    print(f"Reading {csv_path}...")

    counts = defaultdict(lambda: {'F': {}, 'M': {}})
    per_year_rows: dict[tuple[int, str], list] = defaultdict(list)
    years_seen: set[int] = set()

    with csv_path.open(encoding='utf-8') as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            _country, year_s, sex, name, count_s = row
            if sex not in ('F', 'M'):
                continue
            year = int(year_s)
            count = int(count_s)
            counts[name][sex][year] = counts[name][sex].get(year, 0) + count
            per_year_rows[(year, sex)].append((name, count))
            years_seen.add(year)

    years = sorted(years_seen)
    latest_year = years[-1]

    rank_by_year_sex: dict = {}
    for (year, sex), rows in per_year_rows.items():
        rows.sort(key=lambda x: (-x[1], x[0]))
        rank_by_year_sex[(year, sex)] = {n: i + 1 for i, (n, _) in enumerate(rows)}

    name_sex_total: dict = {}
    name_total: dict = {}
    for name, d in counts.items():
        ft = sum(d['F'].values())
        mt = sum(d['M'].values())
        name_sex_total[(name, 'F')] = ft
        name_sex_total[(name, 'M')] = mt
        name_total[name] = ft + mt

    def _dom(n: str) -> str:
        return 'F' if name_sex_total[(n, 'F')] >= name_sex_total[(n, 'M')] else 'M'

    top_names_local = sorted(name_total.items(), key=lambda x: (-x[1], x[0]))[:TOP_N_NAMES]
    pages = {n for n, _ in top_names_local}
    for (_y, _s), ranks in rank_by_year_sex.items():
        for n, rank in ranks.items():
            if rank <= 50:
                pages.add(n)
    for n, total in name_total.items():
        if total >= PAGE_MIN_TOTAL:
            pages.add(n)
    pages_to_generate = sorted(pages, key=lambda n: (-name_total[n], n))

    # Slug-collision handling. Multiple names (e.g. Emile vs Émile) can slugify
    # to the same URL after diacritic stripping. The most popular variant wins
    # the canonical page; the rest are recorded as variants for surfacing on
    # the canonical page and in the rare-names index.
    all_by_slug: dict[str, list[str]] = defaultdict(list)
    for n in name_total:
        all_by_slug[slugify(n)].append(n)
    for sl in all_by_slug:
        all_by_slug[sl].sort(key=lambda x: -name_total[x])

    variants_of: dict[str, list[tuple[str, int]]] = {}
    canonical_of: dict[str, str] = {}
    for sl, names_in_slug in all_by_slug.items():
        if len(names_in_slug) > 1:
            canon = names_in_slug[0]
            variants_of[canon] = [(n, name_total[n]) for n in names_in_slug[1:]]
            for n in names_in_slug[1:]:
                canonical_of[n] = canon

    # Dedup pages_to_generate by slug — the first occurrence (highest total)
    # wins. This also flips the previous bug where iteration order let the
    # less-popular variant overwrite the canonical's page.
    seen_slugs: set[str] = set()
    deduped: list[str] = []
    for n in pages_to_generate:
        sl = slugify(n)
        if sl in seen_slugs:
            continue
        seen_slugs.add(sl)
        deduped.append(n)
    pages_to_generate = deduped
    has_page = set(pages_to_generate)

    print(f"  [{cc}] Unique names: {len(name_total):,}  Pages: {len(pages_to_generate):,}  Years: {years[0]}–{latest_year}")

    same_sex_ranked = {'F': [], 'M': []}
    for n in pages_to_generate:
        same_sex_ranked[_dom(n)].append(n)
    for sex in ('F', 'M'):
        same_sex_ranked[sex].sort(key=lambda n: (-name_sex_total[(n, sex)], n))
    same_sex_index = {
        sex: {n: i for i, n in enumerate(lst)} for sex, lst in same_sex_ranked.items()
    }

    latest_year_ranked = {'F': [], 'M': []}
    for sex in ('F', 'M'):
        ranks = rank_by_year_sex.get((latest_year, sex), {})
        have = [(n, r) for n, r in ranks.items() if n in has_page]
        have.sort(key=lambda x: x[1])
        latest_year_ranked[sex] = have
    latest_year_index = {
        sex: {n: i for i, (n, _) in enumerate(lst)} for sex, lst in latest_year_ranked.items()
    }

    by_initial = defaultdict(list)
    for n in pages_to_generate:
        by_initial[n[0].upper()].append(n)
    for letter in by_initial:
        by_initial[letter].sort(key=lambda n: (-name_total[n], n))

    decades = list(range(years[0] - (years[0] % 10), latest_year + 1, 10))
    decade_sex_counts = defaultdict(lambda: defaultdict(int))
    for _n, _d in counts.items():
        for _sex in ('F', 'M'):
            for _y, _c in _d[_sex].items():
                decade_sex_counts[((_y // 10) * 10, _sex)][_n] += _c

    letter_names = {'F': defaultdict(list), 'M': defaultdict(list)}
    for n in pages_to_generate:
        letter_names[_dom(n)][n[0].upper()].append(n)
    for sex in ('F', 'M'):
        for letter in letter_names[sex]:
            letter_names[sex][letter].sort(key=lambda n: (-name_sex_total[(n, sex)], n))

    name_meta = {}
    phonetic_by_sex: dict[str, dict[str, list[str]]] = {'F': {}, 'M': {}}
    for n in pages_to_generate:
        dom = _dom(n)
        series = counts[n][dom]
        peak_year = max(series, key=series.get)
        low = n.lower()
        pkey = phonetic_key(n)
        name_meta[n] = {
            'dom': dom,
            'first': low[0],
            'last': low[-1],
            'last2': low[-2:],
            'len': len(n),
            'syll': count_syllables(n),
            'peak_dec': (peak_year // 10) * 10,
            'latest_rank': rank_by_year_sex.get((latest_year, dom), {}).get(n),
            'pkey': pkey,
        }
        phonetic_by_sex[dom].setdefault(pkey, []).append(n)

    # Top-5 names per year per sex for the homepage animated race chart.
    # Walked from per_year_rows which is already sorted in the rank step above.
    # Top-5 (not top-10) keeps the chart light: fewer line redraws + smaller
    # JSON, important because the client renders one SVG path per unique name
    # that ever made the cut.
    race_M: dict[int, list] = {}
    race_F: dict[int, list] = {}
    for (year, sex), rows in per_year_rows.items():
        top5 = sorted(rows, key=lambda x: (-x[1], x[0]))[:5]
        target = race_M if sex == 'M' else race_F
        target[year] = [[n, c] for n, c in top5]
    top_race_by_country[cc] = {
        'years': years,
        'M': race_M,
        'F': race_F,
    }

    years_by_country[cc] = years
    counts_by_country[cc] = counts
    rank_by_year_sex_by_country[cc] = rank_by_year_sex
    name_sex_total_by_country[cc] = name_sex_total
    name_total_by_country[cc] = name_total
    pages_to_generate_by_country[cc] = pages_to_generate
    has_page_by_country[cc] = has_page
    same_sex_ranked_by_country[cc] = same_sex_ranked
    same_sex_index_by_country[cc] = same_sex_index
    latest_year_ranked_by_country[cc] = latest_year_ranked
    latest_year_index_by_country[cc] = latest_year_index
    by_initial_by_country[cc] = by_initial
    letter_names_by_country[cc] = letter_names
    name_meta_by_country[cc] = name_meta
    phonetic_by_sex_by_country[cc] = phonetic_by_sex
    decades_by_country[cc] = decades
    decade_sex_counts_by_country[cc] = decade_sex_counts
    variants_of_by_country[cc] = variants_of
    canonical_of_by_country[cc] = canonical_of


# ---------------------------------------------------------------------------
# Active country state. set_active(cc) rebinds module-level aliases that all
# generator functions read, plus the URL PREFIX and OUTPUT path.
# ---------------------------------------------------------------------------
ACTIVE_CC = "US"
PREFIX = ""        # e.g. "" for US, "/fr" for France
OUT_DIR = OUTPUT_DIR

# placeholders so static analyzers don't complain — set_active populates them
YEARS: list = []
YEARS_SET: set = set()
DATA_RANGE = ""
LATEST_YEAR = 2024
DECADES: list = []
counts: dict = {}
rank_by_year_sex: dict = {}
name_sex_total: dict = {}
name_total: dict = {}
pages_to_generate: list = []
HAS_PAGE: set = set()
same_sex_ranked: dict = {}
same_sex_index: dict = {}
latest_year_ranked: dict = {}
latest_year_index: dict = {}
by_initial: dict = {}
letter_names: dict = {}
name_meta: dict = {}
phonetic_by_sex: dict = {}
decade_sex_counts: dict = {}
top_names: list = []
top_pin_set: set = set()
VARIANTS_OF: dict = {}
CANONICAL_OF: dict = {}

ALL_SITEMAP_URLS: list[str] = []

# Presence indices populated once after all build_country calls. Used by
# hreflang helpers to decide which countries to cross-link from a page.
SLUGS_WITH_PAGE_BY_CC: dict[str, set[str]] = {}
YEARS_SET_BY_CC: dict[str, set[int]] = {}
DECADES_SET_BY_CC: dict[str, set[int]] = {}
LETTERS_BY_CC: dict[str, set[tuple[str, str]]] = {}  # (sex_code, uppercase_letter)

HREFLANG = {"US": "en-US", "FR": "fr-FR", "GB": "en-GB", "AU": "en-AU", "CA": "en-CA", "ES": "es-ES", "IT": "it-IT", "NL": "nl-NL"}


def build_presence_indices() -> None:
    for cc in COUNTRIES:
        SLUGS_WITH_PAGE_BY_CC[cc] = {slugify(n) for n in pages_to_generate_by_country[cc]}
        YEARS_SET_BY_CC[cc] = set(years_by_country[cc])
        DECADES_SET_BY_CC[cc] = set(decades_by_country[cc])
        letters: set[tuple[str, str]] = set()
        for sex in ('F', 'M'):
            for letter in letter_names_by_country[cc][sex].keys():
                letters.add((sex, letter))
        LETTERS_BY_CC[cc] = letters


def _country_prefix(cc: str) -> str:
    slug = COUNTRY_SLUG[cc]
    return "" if not slug else f"/{slug}"


def hreflang_block(path_per_cc: dict[str, str]) -> str:
    """Render <link rel="alternate"> tags for every country in path_per_cc,
    plus an x-default pointing at US (or the first available country)."""
    if not path_per_cc:
        return ""
    parts = []
    for cc in COUNTRIES:
        if cc in path_per_cc:
            parts.append(
                f'\n    <link rel="alternate" hreflang="{HREFLANG[cc]}" '
                f'href="{BASE_URL}{path_per_cc[cc]}">'
            )
    default_cc = "US" if "US" in path_per_cc else next(iter(path_per_cc))
    parts.append(
        f'\n    <link rel="alternate" hreflang="x-default" '
        f'href="{BASE_URL}{path_per_cc[default_cc]}">'
    )
    return "".join(parts)


def hreflang_for_hub(rel: str) -> str:
    """Pages that exist on every country (homepage if rel='', else <prefix>/<rel>)."""
    paths = {}
    for cc in COUNTRIES:
        p = _country_prefix(cc)
        paths[cc] = f"{p}/" if not rel else f"{p}/{rel}"
    return hreflang_block(paths)


def hreflang_for_name(slug: str) -> str:
    paths = {}
    for cc in COUNTRIES:
        if slug in SLUGS_WITH_PAGE_BY_CC[cc]:
            paths[cc] = f"{_country_prefix(cc)}/name/{slug}.html"
    return hreflang_block(paths)


def hreflang_for_similar(slug: str) -> str:
    paths = {}
    for cc in COUNTRIES:
        if slug in SLUGS_WITH_PAGE_BY_CC[cc]:
            paths[cc] = f"{_country_prefix(cc)}/similar/{slug}.html"
    return hreflang_block(paths)


def hreflang_for_year(year: int) -> str:
    paths = {}
    for cc in COUNTRIES:
        if year in YEARS_SET_BY_CC[cc]:
            paths[cc] = f"{_country_prefix(cc)}/year/{year}.html"
    return hreflang_block(paths)


def hreflang_for_decade(d: int) -> str:
    paths = {}
    for cc in COUNTRIES:
        if d in DECADES_SET_BY_CC[cc]:
            paths[cc] = f"{_country_prefix(cc)}/decade/{d}s.html"
    return hreflang_block(paths)


def hreflang_for_origin(origin: str) -> str:
    paths = {}
    for cc in COUNTRIES:
        if origin in ORIGIN_TO_NAMES_BY_CC.get(cc, {}):
            paths[cc] = f"{_country_prefix(cc)}/origin/{origin}.html"
    return hreflang_block(paths)


def hreflang_for_letter(sex: str, letter: str) -> str:
    paths = {}
    for cc in COUNTRIES:
        if (sex, letter) in LETTERS_BY_CC[cc]:
            paths[cc] = f"{_country_prefix(cc)}/letter/{sex_label(sex)}-{letter.lower()}.html"
    return hreflang_block(paths)


def set_active(cc: str) -> None:
    g = globals()
    g['ACTIVE_CC'] = cc
    slug = COUNTRY_SLUG[cc]
    g['PREFIX'] = '' if not slug else f'/{slug}'
    g['OUT_DIR'] = OUTPUT_DIR if not slug else (OUTPUT_DIR / slug)
    yrs = years_by_country[cc]
    g['YEARS'] = yrs
    g['YEARS_SET'] = set(yrs)
    g['DATA_RANGE'] = f"{yrs[0]}–{yrs[-1]}" if yrs else ""
    g['LATEST_YEAR'] = yrs[-1] if yrs else 2024
    g['DECADES'] = decades_by_country[cc]
    g['counts'] = counts_by_country[cc]
    g['rank_by_year_sex'] = rank_by_year_sex_by_country[cc]
    g['name_sex_total'] = name_sex_total_by_country[cc]
    g['name_total'] = name_total_by_country[cc]
    g['pages_to_generate'] = pages_to_generate_by_country[cc]
    g['HAS_PAGE'] = has_page_by_country[cc]
    g['same_sex_ranked'] = same_sex_ranked_by_country[cc]
    g['same_sex_index'] = same_sex_index_by_country[cc]
    g['latest_year_ranked'] = latest_year_ranked_by_country[cc]
    g['latest_year_index'] = latest_year_index_by_country[cc]
    g['by_initial'] = by_initial_by_country[cc]
    g['letter_names'] = letter_names_by_country[cc]
    g['name_meta'] = name_meta_by_country[cc]
    g['phonetic_by_sex'] = phonetic_by_sex_by_country[cc]
    g['decade_sex_counts'] = decade_sex_counts_by_country[cc]
    g['VARIANTS_OF'] = variants_of_by_country[cc]
    g['CANONICAL_OF'] = canonical_of_by_country[cc]
    g['top_names'] = sorted(
        g['name_total'].items(), key=lambda x: (-x[1], x[0]))[:TOP_N_NAMES]
    # Pinterest pins: every name that has its own page gets a custom
    # 1000x1500 PNG card. (Was previously gated to top 1000, which left
    # ranks 1001+ — e.g. Serene at 5,686 in the US — falling back to
    # the generic og-default.png with no Share / Download buttons.)
    g['top_pin_set'] = set(g['HAS_PAGE'])

    out = g['OUT_DIR']
    for sub in ('name', 'year', 'similar', 'decade', 'letter', 'trends', 'pin'):
        (out / sub).mkdir(parents=True, exist_ok=True)
    if cc == 'US':
        (out / 'compare').mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers (read active-country globals)
# ---------------------------------------------------------------------------
def dominant_sex(name: str) -> str:
    return 'F' if name_sex_total[(name, 'F')] >= name_sex_total[(name, 'M')] else 'M'


def similar_names(name, k=24):
    """Phonetic-first 'names that sound like X'.

    Primary signal: shared phonetic key prefix (Catherine/Katherine/Kathryn
    all collapse to 'ktrn'). Secondary signals — length, era, last 2 — are
    smaller nudges that order candidates within a phonetic cluster.
    """
    m = name_meta[name]
    dom = m['dom']
    target = m['pkey']
    # Build a candidate pool that's likely to share phonetics: all names
    # whose pkey shares a prefix of >= 2 chars with this one. Iterating
    # phonetic_by_sex buckets is cheaper than scanning every name.
    candidates: set[str] = set()
    for pk, ns in phonetic_by_sex.get(dom, {}).items():
        common = 0
        for a, b in zip(target, pk):
            if a == b:
                common += 1
            else:
                break
        if common >= 2:
            candidates.update(ns)
    candidates.discard(name)

    scored = []
    for other in candidates:
        o = name_meta[other]
        other_key = o['pkey']
        # Shared phonetic-key prefix: the dominant signal.
        common = 0
        for a, b in zip(target, other_key):
            if a == b:
                common += 1
            else:
                break
        s = common * 5  # 10-30 for typical 2-6-char overlap
        # Exact phonetic match — clear cluster: bonus
        if target == other_key and target:
            s += 6
        # Same starting letter — softens cross-cluster jumps
        if o['first'] == m['first']:
            s += 2
        # Same last 2 letters — rhyming endings (Aiden/Hayden)
        if o['last2'] == m['last2']:
            s += 2
        # Length proximity
        ldiff = abs(o['len'] - m['len'])
        if ldiff <= 1:
            s += 2
        elif ldiff <= 2:
            s += 1
        # Era proximity (soft)
        if abs(o['peak_dec'] - m['peak_dec']) <= 10:
            s += 1
        if s >= 8:
            popdiff = abs(name_total[other] - name_total[name])
            scored.append((-s, popdiff, other))
    scored.sort()
    return [o for _, _, o in scored[:k]]


def sex_label(sex: str) -> str:
    return 'girls' if sex == 'F' else 'boys'


def home_url(cc: str | None = None) -> str:
    """Absolute home URL for a country (used in canonicals/breadcrumbs)."""
    c = cc or ACTIVE_CC
    slug = COUNTRY_SLUG[c]
    return f"{BASE_URL}/" if not slug else f"{BASE_URL}/{slug}/"


def home_path(cc: str | None = None) -> str:
    """Relative homepage href."""
    c = cc or ACTIVE_CC
    slug = COUNTRY_SLUG[c]
    return "/" if not slug else f"/{slug}/"


def data_source_label() -> str:
    return DATA_SOURCE_SHORT[ACTIVE_CC]


def data_source_full() -> str:
    return DATA_SOURCE_FULL[ACTIVE_CC]


# ---------------------------------------------------------------------------
# Localization. STRINGS holds UI copy per language; FR overrides English.
# Gendered nouns ('girls'/'filles', 'boy's'/'de garçon') come from GENDERED.
# fmt() handles per-country number formatting (FR uses thin spaces).
# URL slugs stay English ('girls-a', 'name', etc.) — only visible text changes.
# ---------------------------------------------------------------------------
STRINGS_EN: dict[str, str] = {
    "nav_home": "Home",
    "nav_browse": "Browse A–Z",
    "nav_trends": "Trends",
    "nav_decades": "Decades",
    "nav_rankings": "{year} Rankings",
    "nav_favorites": "Favorites",
    "nav_tools": "Tools",
    "nav_compare": "Compare",
    "compare_title": "Compare Two Names — NameCharted",
    "compare_h1": "Compare any two names",
    "compare_intro": "See two names' popularity side by side. Pick any two and we'll chart them together with stats and ranks.",
    "compare_desc": "Compare any two baby names side-by-side: yearly births, peak year, current rank, and a combined trend chart.",
    "compare_input_a": "First name",
    "compare_input_b": "Second name",
    "compare_go": "Compare",
    "compare_loading": "Loading…",
    "compare_not_found": "Couldn't find one of those names. Try the search again.",
    "compare_chart_h2": "Popularity over time",
    "compare_stat_total": "Total births",
    "compare_stat_peak": "Peak year",
    "compare_stat_latest_rank": "{year} rank",
    "compare_with_link": "Compare {name} with another name →",
    "fav_add_tip": "Save to your favorites",
    "fav_remove_tip": "Remove from favorites",
    "pin_share_tip": "Save to Pinterest",
    "pin_share_label": "Save to Pinterest",
    "share_btn_tip": "Share or save to Photos",
    "share_btn_label": "Share",
    "share_copied": "Link copied!",
    "download_btn_tip": "Download the card (PNG)",
    "download_btn_label": "Download",
    "tg_share_tip": "Share on Telegram",
    "tg_share_label": "Telegram",
    "blog_h1": "Stories & lists",
    "blog_title": "Baby name stories & lists — NameCharted",
    "blog_intro": "Trends, vintage comebacks, and curated lists from the NameCharted data.",
    "blog_desc": "Editorial posts on baby name trends — rising names, vintage comebacks, decade lookbacks — built from official rankings.",
    "blog_read_more": "Read",
    "blog_back": "Back to all posts",
    "nav_blog": "Blog",
    "fav_h1": "Your saved names",
    "fav_title": "Your saved names — NameCharted",
    "fav_desc": "Your personal shortlist of saved names.",
    "fav_intro": "Names you've saved. Stored only in your browser — clearing your site data removes them.",
    "fav_empty": "No saved names yet. Tap the heart on any name page to add it here.",
    "fav_share_btn": "Copy shareable link",
    "fav_share_done": "Link copied!",
    "fav_print_btn": "Print / Save as PDF",
    "fav_print_h1": "Your saved baby names",
    "fav_print_foot": "Saved from namecharted.com",
    "fav_meta_peak": "Peak {d}s",
    "fav_meta_unranked": "unranked",
    "fav_remove": "Remove",
    "footer_data": "Data: {source} ({range})",

    "crumb_home": "Home",
    "crumb_names": "Names",
    "crumb_browse": "Browse A–Z",
    "crumb_decades": "Decades",
    "crumb_trends": "Trends",
    "crumb_rising": "Rising",
    "crumb_falling": "Falling",
    "crumb_similar": "Similar names",
    "crumb_rare": "Rare names",

    # Homepage
    "home_tagline": "Names, charted.",
    "home_intro": ("Explore the popularity and trends of names from {range}. "
                   "Search any name to see its yearly counts, popularity rank, "
                   "gender split, and an interactive trend chart."),
    "home_search_placeholder": "Enter a name to explore...",
    "home_try": "Try names like {samples} &middot; or",
    "home_browse_link": "browse all {n} names A–Z",
    "home_top_h2": "Top Names of All Time (by total usage)",
    "home_cc_callout": "Also available for:",
    "home_total_babies": "{n} total babies",
    "home_mostly": "mostly {label}",
    "home_title": "NameCharted — Every name, charted.",
    "home_desc": ("Every name, charted. Yearly counts, rankings, gender split "
                  "and interactive trends for {n}+ names across 8 countries, "
                  "from {range}."),
    # Redesigned hero (Phase 1)
    "home_h1": "Every name, <span class=\"accent\">charted.</span>",
    "home_subhead": ("Yearly counts, rankings, gender split and interactive trends "
                     "— for every name across 8 countries, from {range_short}."),
    "home_search_placeholder_v2": "Search a name — try {samples}…",
    "home_search_cta": "Explore",
    "home_popular_label": "Popular right now:",
    "home_browse_chip": "or browse all {n} names A–Z →",
    "home_stats_years": "{n} years",
    "home_stats_years_sub": "of name data",
    "home_stats_names": "{n}",
    "home_stats_names_sub": "names tracked",
    "home_stats_countries": "{n} countries",
    "home_stats_countries_sub": "covered",
    "home_tools_label": "Or try one of our tools",
    "home_range_short": "{start} to {end}",
    "home_race_h2": "The race for #1",
    "home_race_sub": "Top 5 names per year — {range_short}",
    "home_race_pause": "Pause",
    "home_race_play": "Play",
    "home_race_restart": "Restart",
    "home_race_boys": "Boys",
    "home_race_girls": "Girls",
    "home_race_no1": "#1 · {name}",
    "home_race_foot": "Watch how a generation's favourites rise and fall.",
    "home_race_foot_link": "See full data →",
    "nav_explore": "Explore",
    "nav_search_aria": "Search names",
    "nav_choose_country": "Choose a country",
    "tool_desc_compare": "Two names side-by-side",
    "tool_desc_picker": "Match your style + criteria",
    "tool_desc_sibling": "Names that pair well",
    "tool_desc_works_with": "Sound-check first + last",
    "tool_desc_initials": "Monograms, instantly",
    "tool_desc_origins": "By culture and language",
    "tool_desc_fiction": "From books and film",
    "tool_desc_decades": "Top names by era",

    # Name page
    "name_primarily": "Primarily a {singular}'s name",
    "name_variants_label": "Also spelled:",
    "rare_variant_of": "→ see {canonical}",
    "name_unisex": "Unisex — {f}% girls / {m}% boys",
    "name_pct_one": "{pct}% {label}",
    "insight_first": ("<strong>{name}</strong> first appears in the data in "
                      "<strong>{year}</strong> and has been recorded in {n} different years "
                      "as a {singular}'s name."),
    "insight_peak_ranked": ("Its single biggest year was <strong>{year}</strong> with "
                            "<strong>{count}</strong> babies (rank #{rank} that year)."),
    "insight_peak": ("Its single biggest year was <strong>{year}</strong> with "
                     "<strong>{count}</strong> babies."),
    "insight_latest_ranked": ("In {year} it was given to {count} {label} "
                              "(rank <strong>#{rank}</strong>)."),
    "insight_latest": "In {year} it was given to {count} {label}.",
    "insight_latest_missing": "It was not in the {year} data.",
    "insight_rising": "The name has been <strong>rising</strong> over the last five years.",
    "insight_declining": "The name has been <strong>declining</strong> over the last five years.",
    "insight_steady": "Its popularity has been <strong>fairly steady</strong> recently.",
    "stat_total": "Total babies (all years)",
    "stat_years": "Years in the data",
    "stat_peak": "Peak in a single year",
    "name_popularity_h2": "Popularity Over Time — {label_cap}",
    "name_yby_h2": "Year-by-Year Detail",
    "name_rank_note": "Rank is among all {label}' names registered that year.",
    "table_year": "Year",
    "table_babies": "Babies",
    "table_rank": "Rank",
    "table_num": "#",
    "table_name": "Name",
    "table_total": "Total babies",
    "table_year_rank": "{year} rank",
    "rel_see_similar": "&rarr; See names similar to {name}",
    "rel_pop_decade": "Popular names of the {d}s",
    "rel_letter_link": "{label_cap} names starting with {letter}",
    "rel_more_popular": "More popular {label}' names",
    "rel_near_in": "Names ranked near {name} in {year}",
    "rel_other_init": "Other {label}' names starting with {letter}",
    "name_title": "{name} — Baby Name Popularity & Trends",
    "name_desc": ("{name} name popularity: {total} births recorded {range}, "
                  "peaking in {year} with {peak}. See yearly counts, rank, "
                  "gender split and an interactive trend chart."),
    "chart_label": "Babies named {name} per year ({label})",
    "chart_y_axis": "Babies per year",

    # Browse A–Z
    "browse_h1": "Browse All Names A–Z",
    "browse_title": "Browse All Names A–Z",
    "browse_intro": ("All {n} names with a dedicated popularity page, grouped by "
                     "first letter. Looking for a rarer name? See the full "
                     "<a href=\"{url}\">A–Z index of rare names</a>."),
    "browse_desc": ("Browse all {n} names A–Z. Click any name for popularity "
                    "trends, rankings and yearly counts from {range}."),
    "browse_explore_h2": "Explore by theme",
    "browse_rising_link": "Rising names",
    "browse_falling_link": "Falling names",
    "browse_decades_link": "Names by decade",
    "browse_girls_by_letter": "Girls' names by letter:",
    "browse_boys_by_letter": "Boys' names by letter:",
    "browse_all_h2": "All names",

    # Year page
    "year_h1": "Top Names of {year}",
    "year_intro": ("The most popular names recorded in {year} were "
                   "<strong>{g}</strong> for girls and <strong>{b}</strong> for boys. "
                   "Full top-50 lists below, from {source} data."),
    "year_girls_h2": "Girls",
    "year_boys_h2": "Boys",
    "year_title": "Top Names of {year} — Rankings & Counts",
    "year_desc": ("Top 50 most popular names of {year} for girls and boys, "
                  "with birth counts from {source} data. #1: {g} and {b}."),

    # Year-in-review (annual editorial recap)
    "yir_title": "{year} Baby Names: The Year in Review",
    "yir_h1": "{year} Baby Names — The Year in Review",
    "yir_intro": ("What changed in {year}? The biggest risers, the biggest "
                   "drops, brand-new names making their first-ever appearance, "
                   "and the names that broke into or out of the top 100."),
    "yir_desc": ("{year} baby names recap: top movers, biggest drops, debut "
                  "names and shake-ups in the top 100. The year's #1: {g} and {b}."),
    "yir_link": "See the {year} year in review →",
    "yir_no_data": "Not enough comparable history for a full recap of {year}.",
    "yir_top_h2": "The year's #1 names",
    "yir_top_lead": "<strong>{g}</strong> topped the girls' list and <strong>{b}</strong> led the boys for {year}.",
    "yir_risers_h2": "Biggest risers",
    "yir_risers_lead": "Names whose rank improved the most year-over-year.",
    "yir_fallers_h2": "Biggest fallers",
    "yir_fallers_lead": "Names whose rank dropped the most year-over-year.",
    "yir_debut_h2": "Debut names",
    "yir_debut_lead": "Names that show up in the data for the very first time.",
    "yir_newcomers_h2": "New in the top 100",
    "yir_newcomers_lead": "Names that broke into the top 100 this year for the first time since {prev}.",
    "yir_exits_h2": "Left the top 100",
    "yir_exits_lead": "Names that fell out of the top 100 this year after being in it in {prev}.",
    "yir_rank_change": "{prev_rank} → {rank} (▲{delta})",
    "yir_rank_drop": "{prev_rank} → {rank} (▼{delta})",
    "yir_rank_new": "New entry — rank #{rank}",
    "yir_rank_exit": "Was #{prev_rank} — now off the chart",
    "yir_count_year": "{n} this year",
    "crumb_yir": "{year} in review",

    # Decade
    "decade_h1": "Most Popular Names of the {label}",
    "decade_intro": ("The top names across the {label} ({span}), totaled over "
                     "the whole decade. The decade's #1 names were "
                     "<strong>{g}</strong> for girls and <strong>{b}</strong> for boys."),
    "decade_girls_h2": "Girls — Top 50",
    "decade_boys_h2": "Boys — Top 50",
    "decade_title": "Most Popular Names of the {label}",
    "decade_desc": ("Most popular names of the {label} ({span}). Top 50 girls "
                    "and boys by total births over the decade, from {source} "
                    "data. #1: {g} and {b}."),
    "decades_h1": "Names by Decade",
    "decades_title": "Names by Decade — Top Names of Every Era",
    "decades_intro": ("Explore the most popular names of each decade from the "
                      "{first}s to the {last}s, based on {source} data."),
    "decades_desc": ("Most popular names by decade, {first}s–{last}s. See the "
                     "top girls' and boys' names of every decade from {source} data."),
    "decades_th_decade": "Decade",
    "decades_th_g": "#1 Girls' Name",
    "decades_th_b": "#1 Boys' Name",

    # Trends
    "trends_h1": "Baby Name Trends",
    "trends_intro": ("Which names are heating up and which are fading? These "
                     "rankings compare recent birth counts with five years "
                     "earlier ({range})."),
    "trends_title": "Name Trends — Rising & Falling Names",
    "trends_desc": ("See which names are rising and falling in popularity "
                    "heading into {year}, plus top names by decade, from {source} data."),
    "trends_rising_h1": "Fastest-Rising Names ({year})",
    "trends_rising_intro": ("The girls' and boys' names growing fastest in "
                            "popularity — comparing average births around five "
                            "years ago with the most recent years. Only names "
                            "with meaningful current usage are included."),
    "trends_rising_title": "Fastest-Rising Names of {year}",
    "trends_rising_desc": ("The fastest-rising names heading into {year}, for "
                           "girls and boys, based on {source} birth data."),
    "trends_falling_h1": "Fastest-Falling Names ({year})",
    "trends_falling_intro": ("Once-common names declining fastest in popularity "
                             "— comparing average births around five years ago "
                             "with the most recent years."),
    "trends_falling_title": "Fastest-Falling Names of {year}",
    "trends_falling_desc": ("Names declining fastest in popularity heading into "
                            "{year}, for girls and boys, from {source} birth data."),
    "trends_th_older": "~5 yrs ago",
    "trends_th_change": "Change",
    "trends_card_rising": "Fastest-Rising Names",
    "trends_card_rising_sub": "Biggest gainers of {year}",
    "trends_card_falling": "Fastest-Falling Names",
    "trends_card_falling_sub": "Biggest declines of {year}",
    "trends_card_decades": "Names by Decade",
    "trends_card_decades_sub": "Top names of every era",

    # Letter page
    "letter_h1": "{label_cap} Names Starting With {letter}",
    "letter_title": "{label_cap} Names Starting With {letter} — Popularity Ranked",
    "letter_intro": ("All {n} {label}' names beginning with <strong>{letter}</strong> "
                     "that have a popularity page, ranked by all-time births ({range}). "
                     "{cross_q}"),
    "letter_cross_link": "{label_cap} names starting with {letter}",
    "letter_cross_q": "Looking for {link}?",
    "letter_desc": ("{label_cap} names that start with {letter}: {n} options "
                    "ranked by popularity, with total births and current rank "
                    "from {source} data."),

    # Rare names
    "rare_h1": "Rare Names — Full A–Z Index",
    "rare_title": "Rare Names — Full A–Z Index",
    "rare_intro": ("These {n} names appear in the records ({range}) but have "
                   "fewer than {min} lifetime births, so they don't yet have "
                   "their own dedicated trend page. They're listed here A–Z "
                   "with lifetime totals."),
    "rare_tip": ("Tip: use <kbd>Ctrl</kbd>+<kbd>F</kbd> (or <kbd>⌘</kbd>+<kbd>F</kbd>) "
                 "to search this page."),
    "rare_letter_count": "({n} names)",
    "rare_mostly": "({total} · mostly {label})",
    "rare_desc": ("Index of {n} rare names ({range}) with fewer than {min} "
                  "lifetime births, listed A–Z."),

    # Works with (lastname compatibility)
    "nav_works_with": "Works with surname",
    "ww_title": "First names that work with your surname — NameCharted",
    "ww_h1": "First names that work with your surname",
    "ww_intro": ("Type your surname and we'll score every first name against it — "
                 "rewarding rhythm contrast and penalising clashing sounds at the "
                 "first-name / surname boundary."),
    "ww_input": "Your surname",
    "ww_go": "Find names",
    "ww_loading": "Scoring names…",
    "ww_empty": "Type a surname above to see suggestions.",
    "ww_tab_all": "All",
    "ww_tab_girls": "Girls",
    "ww_tab_boys": "Boys",
    "ww_result_for": "Best matches for {surname}",
    "ww_score": "Score",
    "ww_desc": ("Find first names that pair well with any surname. We score names "
                "on rhythm, sound clashes and shared initials so you can shortlist "
                "good-sounding combinations fast."),
    "ww_show_more": "Show more",
    "ww_how_h": "What makes a good match",
    "ww_how_1": "<strong>Different starting letter</strong> to your surname — no <em>Sam Smith</em>.",
    "ww_how_2": "<strong>Different syllable count</strong> to your surname — no <em>Jack Black</em>.",
    "ww_how_3": "<strong>No rhyming endings</strong> at the join — no <em>Aiden Hayden</em>.",

    # Picker (swipe / filter / random)
    "nav_picker": "Name picker",
    "picker_title": "Baby name picker — swipe, filter, randomise — NameCharted",
    "picker_h1": "Find your shortlist faster",
    "picker_intro": ("Three ways to discover names you'll love: swipe through "
                     "one at a time, filter by era and style, or roll the dice."),
    "picker_tab_swipe": "Swipe",
    "picker_tab_filter": "Filter",
    "picker_tab_random": "Surprise me",
    "picker_swipe_skip": "Skip",
    "picker_swipe_save": "Save",
    "picker_swipe_undo": "Undo",
    "picker_swipe_saved": "Saved to your favourites.",
    "picker_swipe_exhausted": "That's everyone for now — change the filter or restart.",
    "picker_swipe_restart": "Restart",
    "picker_swipe_filter_sex": "Show",
    "picker_swipe_filter_era": "Era",
    "picker_filter_sex": "Sex",
    "picker_filter_syll": "Syllables",
    "picker_filter_era": "Peak era",
    "picker_filter_letter": "Starts with",
    "picker_filter_rank": "Current popularity",
    "picker_filter_origin": "Origin",
    "picker_filter_any": "Any",
    "picker_filter_rank_top100": "Top 100 right now",
    "picker_filter_rank_top1000": "Top 1,000 right now",
    "picker_filter_rank_rare": "Vintage / off the charts",
    "picker_filter_match_one": "1 name matches",
    "picker_filter_match_many": "{n} names match",
    "picker_filter_match_none": "No names match — try loosening the filters.",
    "picker_random_count": "How many",
    "picker_random_go": "Roll the dice",
    "picker_random_share": "Copy shareable link",
    "picker_random_share_done": "Link copied!",
    "picker_random_again": "Roll again",
    "picker_random_empty": "Nothing matched — try a different combination.",
    "picker_peak_decade": "Peaked in the {d}s",
    "picker_currently_rank": "#{rank} right now",
    "picker_not_ranked": "Off the chart now",
    "picker_desc": ("Discover baby names by swiping, filtering by decade and "
                    "syllables, or rolling for a random list. Save your favourites "
                    "as you go."),

    # Sibling suggester
    "nav_sibling": "Sibling ideas",
    "sibling_title": "Sibling name ideas — find names that pair well — NameCharted",
    "sibling_h1": "Find a sibling name",
    "sibling_intro": ("Give us one to three children's names and we'll suggest "
                      "names that share a similar era and rhythm — without "
                      "rhyming or starting with the same letter."),
    "sibling_input": "Existing child's name",
    "sibling_input_more": "Another child's name",
    "sibling_add_name": "+ Add another name",
    "sibling_remove_name": "Remove this name",
    "sibling_target_sex": "Next baby",
    "sibling_go": "Suggest names",
    "sibling_empty": "Type a name above to see sibling suggestions.",
    "sibling_unknown": ("We don't have data for one of those names, so we'll "
                        "just match on rhythm. For best results pick names "
                        "with their own popularity page."),
    "sibling_result_for": "Names that pair well with {name}",
    "sibling_result_for_set": "Names that pair well with {names}",
    "sibling_show_more": "Show more",
    "sibling_share": "Copy share link",
    "sibling_share_done": "Link copied!",
    "sibling_desc": ("Find sibling names that pair well with a child you've "
                     "already named — or with a set of 2-3 siblings. We match "
                     "on peak era, syllable rhythm and complementary "
                     "starting letters."),

    # Origins
    "nav_origins": "Origins",
    "origins_hub_title": "Baby name origins by language and culture",
    "origins_hub_h1": "Name origins",
    "origins_hub_intro": ("Explore baby names by their language of origin. "
                          "Each page lists popular girls' and boys' names "
                          "rooted in that culture, with birth counts and "
                          "trend pages."),
    "origins_hub_desc": ("Browse baby names grouped by language of origin — "
                         "Irish, Hebrew, Greek, Latin, Japanese, Arabic and "
                         "more. Popular names, meanings and trend data."),
    "origins_hub_count": "{n} names",
    "origin_page_title": "{label} baby names — popularity & trends",
    "origin_page_h1": "{label} baby names",
    "origin_page_intro": ("Popular {label} baby names. These names trace their "
                          "roots to {label} language and culture, listed by "
                          "lifetime popularity in {country}."),
    "origin_page_desc": ("{label} baby names: girls' and boys' name rankings, "
                         "yearly trends and meanings."),
    "origin_page_girls_h2": "{label} girls' names",
    "origin_page_boys_h2": "{label} boys' names",
    "origin_back_to_hub": "← All origins",
    "name_origin_badge": "Origin: {label}",
    "name_meaning_h2": "Meaning",
    "name_meaning_source": "From Wikipedia",
    "numerology_h2": "Numerology of {name}",
    "numerology_intro": ("In Pythagorean numerology, every letter has a value "
                          "from 1 to 9. Adding the letters of <strong>{name}</strong> "
                          "yields these three numbers — a playful read on the "
                          "name's character."),
    "numerology_destiny_lbl": "Destiny number",
    "numerology_destiny_desc": "Sum of every letter. The overall character a name carries.",
    "numerology_soul_lbl": "Soul urge",
    "numerology_soul_desc": "Sum of the vowels — what's said to drive the heart.",
    "numerology_personality_lbl": "Personality",
    "numerology_personality_desc": "Sum of the consonants — the outer impression.",
    "numerology_footer": "Numerology isn't science — it's name-themed fortune-telling. Enjoy it that way.",
    "name_famous_h2": "Famous people named {name}",
    "name_famous_occ_sep": " · ",
    "name_famous_born": "b. {year}",

    # Fiction (curated franchises)
    "nav_fiction": "Fictional names",
    "fiction_hub_title": "Names from books, films and TV — fictional baby names",
    "fiction_hub_h1": "Fictional names",
    "fiction_hub_intro": ("Curated character rosters from {n} franchises — Harry "
                          "Potter, Star Wars, Bridgerton, Jane Austen and more. "
                          "When a fictional name matches a real-world name we have "
                          "data for, we link straight to its popularity page."),
    "fiction_hub_desc": ("Baby names from books, films and TV. Curated rosters "
                         "from Harry Potter, Game of Thrones, Star Wars, "
                         "Bridgerton and more."),
    "fiction_franchise_title": "{title} character names",
    "fiction_franchise_intro": ("{n} character names from {title}. Names linked "
                                "in teal have their own popularity page with "
                                "yearly birth counts."),
    "fiction_franchise_desc": ("Baby names from {title}. Curated character roster "
                               "with links to real-world popularity data."),
    "fiction_back_to_hub": "← All franchises",
    "fiction_card_count": "{n} characters",
    "fiction_year": "since {year}",
    "name_fiction_h2": "Also a character in",
    "name_fiction_in": "In <a href=\"{url}\">{title}</a>: {role}",

    # Saint calendar strings — used by FR, ES, and IT trees.
    "nav_saints": "Saint of the day",
    "saints_hub_title": "Saint of the day — calendar of saints and names",
    "saints_hub_h1": "Saint of the day",
    "saints_hub_intro": ("The traditional Catholic calendar of saints, day by day. "
                         "Click any day to see the saint celebrated and the popularity "
                         "of that name."),
    "saints_hub_desc": ("Daily calendar of saints and name days. Browse all 366 days "
                        "and see the popularity trend of each name."),
    "saint_page_title": "Saint{e} {name} — feast day, dates, name popularity",
    "saint_page_h1": "Saint{e} {name}",
    "saint_page_dates_one": "The feast is celebrated on <strong>{date}</strong>.",
    "saint_page_dates_multi": "Celebrated on <strong>{dates}</strong>.",
    "saint_page_popularity_link": "See the popularity of the name {name} →",
    "saint_page_desc": "Feast day(s), meaning and popularity of the name {name}.",
    "saint_back_to_hub": "← Back to the calendar of saints",
    "saints_today_label": "Today is the feast of Saint {name}",
    "saints_today_label_fem": "Today is the feast of Saint {name}",
    "saints_today_event": "Today: {name}",
    "saints_today_wish": "Happy feast day to everyone called {name}!",

    # Initials maker
    "nav_initials": "Initials maker",
    "initials_title": "Baby name initials generator — pick names from initials",
    "initials_h1": "Spell out the initials",
    "initials_intro": ("Enter the initials you want and we'll roll 20 baby-name "
                       "combinations matching them — a quirky way to brainstorm "
                       "monogram-friendly names."),
    "initials_input": "e.g. A.J.K",
    "initials_go": "Roll combinations",
    "initials_again": "Roll again",
    "initials_filter_sex": "First-name sex",
    "initials_empty": "Type 2 or 3 initials above to get started.",
    "initials_no_match": "We have no first names starting with {letter} in our data — try a different letter.",
    "initials_share": "Copy shareable link",
    "initials_share_done": "Link copied!",
    "initials_desc": ("Generate baby-name combinations from chosen initials. "
                      "First, middle, and last name picks drawn from our "
                      "ranked name database."),
}

STRINGS_FR: dict[str, str] = {
    "nav_home": "Accueil",
    "nav_browse": "Parcourir A–Z",
    "nav_trends": "Tendances",
    "nav_decades": "Décennies",
    "nav_rankings": "Classement {year}",
    "nav_favorites": "Favoris",
    "nav_tools": "Outils",
    "nav_compare": "Comparer",
    "compare_title": "Comparer deux prénoms — NameCharted",
    "compare_h1": "Comparez deux prénoms",
    "compare_intro": "Voyez deux prénoms côte à côte. Choisissez-en deux et nous les comparerons : effectifs, année record, rang actuel et graphique combiné.",
    "compare_desc": "Comparez deux prénoms côte à côte : effectifs annuels, année record, rang actuel et graphique combiné.",
    "compare_input_a": "Premier prénom",
    "compare_input_b": "Deuxième prénom",
    "compare_go": "Comparer",
    "compare_loading": "Chargement…",
    "compare_not_found": "L'un des prénoms est introuvable. Réessayez la recherche.",
    "compare_chart_h2": "Popularité au fil du temps",
    "compare_stat_total": "Naissances totales",
    "compare_stat_peak": "Année record",
    "compare_stat_latest_rank": "Rang {year}",
    "compare_with_link": "Comparer {name} avec un autre prénom →",
    "fav_add_tip": "Ajouter aux favoris",
    "fav_remove_tip": "Retirer des favoris",
    "pin_share_tip": "Épingler sur Pinterest",
    "pin_share_label": "Épingler",
    "share_btn_tip": "Partager ou enregistrer dans Photos",
    "share_btn_label": "Partager",
    "share_copied": "Lien copié !",
    "download_btn_tip": "Télécharger la carte (PNG)",
    "download_btn_label": "Télécharger",
    "tg_share_tip": "Partager sur Telegram",
    "tg_share_label": "Telegram",
    "blog_h1": "Histoires et palmarès",
    "blog_title": "Histoires et palmarès de prénoms — NameCharted",
    "blog_intro": "Tendances, retours en vogue et listes thématiques tirées des données NameCharted.",
    "blog_desc": "Articles éditoriaux sur les prénoms : tendances, retours en vogue, palmarès par décennie — basés sur les classements officiels.",
    "blog_read_more": "Lire",
    "blog_back": "Retour à tous les articles",
    "nav_blog": "Blog",
    "fav_h1": "Vos prénoms enregistrés",
    "fav_title": "Vos prénoms enregistrés — NameCharted",
    "fav_desc": "Votre liste personnelle de prénoms favoris.",
    "fav_intro": "Les prénoms que vous avez enregistrés. Conservés uniquement dans votre navigateur — effacer les données du site les supprime.",
    "fav_empty": "Aucun prénom enregistré. Touchez le cœur sur une page de prénom pour l'ajouter ici.",
    "fav_share_btn": "Copier le lien à partager",
    "fav_share_done": "Lien copié !",
    "fav_print_btn": "Imprimer / Enregistrer en PDF",
    "fav_print_h1": "Mes prénoms favoris",
    "fav_print_foot": "Enregistré depuis namecharted.com",
    "fav_meta_peak": "Pic {d}s",
    "fav_meta_unranked": "non classé",
    "fav_remove": "Retirer",
    "footer_data": "Données : {source} ({range})",

    "crumb_home": "Accueil",
    "crumb_names": "Prénoms",
    "crumb_browse": "Parcourir A–Z",
    "crumb_decades": "Décennies",
    "crumb_trends": "Tendances",
    "crumb_rising": "En hausse",
    "crumb_falling": "En baisse",
    "crumb_similar": "Prénoms similaires",
    "crumb_rare": "Prénoms rares",

    "home_tagline": "Les prénoms, en graphiques.",
    "home_intro": ("Explorez la popularité et les tendances des prénoms de {range}. "
                   "Recherchez un prénom pour voir ses effectifs annuels, son rang, "
                   "sa répartition par sexe et un graphique interactif."),
    "home_search_placeholder": "Tapez un prénom à explorer…",
    "home_try": "Essayez par exemple {samples} &middot; ou",
    "home_browse_link": "parcourez les {n} prénoms A–Z",
    "home_top_h2": "Prénoms les plus donnés de tous les temps",
    "home_cc_callout": "Aussi disponible pour :",
    "home_total_babies": "{n} naissances au total",
    "home_mostly": "majoritairement {label}",
    "home_title": "NameCharted — Chaque prénom, en graphique.",
    "home_desc": ("Chaque prénom, en graphique. Effectifs annuels, classements, "
                  "répartition par sexe et tendances interactives pour plus de "
                  "{n} prénoms dans 8 pays, de {range}."),
    # Redesigned hero (Phase 1) — FR copy
    "home_h1": "Chaque prénom, <span class=\"accent\">en graphique.</span>",
    "home_subhead": ("Effectifs annuels, classements, répartition par sexe et "
                     "tendances interactives — pour chaque prénom dans 8 pays, "
                     "de {range_short}."),
    "home_search_placeholder_v2": "Cherchez un prénom — essayez {samples}…",
    "home_search_cta": "Explorer",
    "home_popular_label": "Populaires en ce moment :",
    "home_browse_chip": "ou parcourez les {n} prénoms de A à Z →",
    "home_stats_years": "{n} ans",
    "home_stats_years_sub": "de données",
    "home_stats_names": "{n}",
    "home_stats_names_sub": "prénoms suivis",
    "home_stats_countries": "{n} pays",
    "home_stats_countries_sub": "couverts",
    "home_tools_label": "Ou essayez un de nos outils",
    "home_range_short": "{start} à {end}",
    "home_race_h2": "La course pour la #1",
    "home_race_sub": "Top 5 des prénoms par année — {range_short}",
    "home_race_pause": "Pause",
    "home_race_play": "Lecture",
    "home_race_restart": "Recommencer",
    "home_race_boys": "Garçons",
    "home_race_girls": "Filles",
    "home_race_no1": "#1 · {name}",
    "home_race_foot": "Regardez les favoris d'une génération monter et descendre.",
    "home_race_foot_link": "Voir toutes les données →",
    "nav_explore": "Explorer",
    "nav_search_aria": "Rechercher des prénoms",
    "nav_choose_country": "Choisir un pays",
    "tool_desc_compare": "Deux prénoms côte à côte",
    "tool_desc_picker": "Selon votre style et critères",
    "tool_desc_sibling": "Prénoms qui s'accordent",
    "tool_desc_works_with": "Test prénom + nom de famille",
    "tool_desc_initials": "Monogrammes en un clic",
    "tool_desc_origins": "Par culture et langue",
    "tool_desc_fiction": "Issus de livres et de films",
    "tool_desc_decades": "Top prénoms par décennie",

    "name_primarily": "Principalement un prénom {of_singular}",
    "name_variants_label": "Variantes :",
    "rare_variant_of": "→ voir {canonical}",
    "name_unisex": "Mixte — {f} % filles / {m} % garçons",
    "name_pct_one": "{pct} % {label}",
    "insight_first": ("<strong>{name}</strong> apparaît pour la première fois "
                      "dans les données en <strong>{year}</strong> et a été enregistré "
                      "sur {n} années différentes comme prénom {of_singular}."),
    "insight_peak_ranked": ("Son année record est <strong>{year}</strong> avec "
                            "<strong>{count}</strong> naissances (rang n°{rank} cette année-là)."),
    "insight_peak": ("Son année record est <strong>{year}</strong> avec "
                     "<strong>{count}</strong> naissances."),
    "insight_latest_ranked": ("En {year}, il a été donné à {count} {label} "
                              "(rang <strong>n°{rank}</strong>)."),
    "insight_latest": "En {year}, il a été donné à {count} {label}.",
    "insight_latest_missing": "Il n'apparaît pas dans les données de {year}.",
    "insight_rising": "Le prénom est en <strong>hausse</strong> depuis cinq ans.",
    "insight_declining": "Le prénom est en <strong>baisse</strong> depuis cinq ans.",
    "insight_steady": "Sa popularité est <strong>plutôt stable</strong> ces dernières années.",
    "stat_total": "Naissances au total (toutes années)",
    "stat_years": "Années couvertes",
    "stat_peak": "Pic sur une seule année",
    "name_popularity_h2": "Popularité au fil du temps — {label_cap}",
    "name_yby_h2": "Détail année par année",
    "name_rank_note": "Le rang est calculé parmi tous les prénoms {label} enregistrés cette année-là.",
    "table_year": "Année",
    "table_babies": "Naissances",
    "table_rank": "Rang",
    "table_num": "n°",
    "table_name": "Prénom",
    "table_total": "Naissances totales",
    "table_year_rank": "Rang {year}",
    "rel_see_similar": "&rarr; Voir des prénoms similaires à {name}",
    "rel_pop_decade": "Prénoms populaires des années {d}",
    "rel_letter_link": "Prénoms {label} commençant par {letter}",
    "rel_more_popular": "Prénoms {label} plus populaires",
    "rel_near_in": "Prénoms classés près de {name} en {year}",
    "rel_other_init": "Autres prénoms {label} commençant par {letter}",
    "name_title": "{name} — Popularité et tendances du prénom",
    "name_desc": ("Popularité du prénom {name} : {total} naissances enregistrées "
                  "{range}, pic en {year} avec {peak}. Effectifs annuels, rang, "
                  "répartition par sexe et graphique interactif."),
    "chart_label": "Naissances de {name} par an ({label})",
    "chart_y_axis": "Naissances par an",

    "browse_h1": "Tous les prénoms — A à Z",
    "browse_title": "Tous les prénoms — A à Z",
    "browse_intro": ("Les {n} prénoms disposant d'une page dédiée, regroupés par "
                     "première lettre. Vous cherchez un prénom plus rare ? "
                     "Voir l'<a href=\"{url}\">index A–Z complet des prénoms rares</a>."),
    "browse_desc": ("Parcourez les {n} prénoms de A à Z. Cliquez sur un prénom "
                    "pour voir ses tendances, son classement et ses effectifs annuels ({range})."),
    "browse_explore_h2": "Explorer par thème",
    "browse_rising_link": "Prénoms en hausse",
    "browse_falling_link": "Prénoms en baisse",
    "browse_decades_link": "Prénoms par décennie",
    "browse_girls_by_letter": "Prénoms de filles par lettre :",
    "browse_boys_by_letter": "Prénoms de garçons par lettre :",
    "browse_all_h2": "Tous les prénoms",

    "year_h1": "Prénoms les plus donnés en {year}",
    "year_intro": ("Les prénoms les plus donnés en {year} étaient "
                   "<strong>{g}</strong> chez les filles et <strong>{b}</strong> chez les garçons. "
                   "Top 50 complet ci-dessous, d'après les données {source}."),
    "year_girls_h2": "Filles",
    "year_boys_h2": "Garçons",
    "year_title": "Prénoms les plus donnés en {year} — classements et effectifs",
    "yir_title": "Prénoms {year} : l'année en revue",
    "yir_h1": "Prénoms {year} — l'année en revue",
    "yir_intro": ("Que retenir de {year} ? Les plus grandes progressions, les "
                   "plus fortes chutes, les prénoms qui apparaissent pour la "
                   "première fois, et ceux qui entrent ou sortent du top 100."),
    "yir_desc": ("Rétrospective des prénoms {year} : plus grandes progressions, "
                  "chutes et nouveautés du top 100. N°1 de l'année : {g} et {b}."),
    "yir_link": "Voir la rétrospective {year} →",
    "yir_no_data": "Pas assez d'historique comparable pour faire la rétro de {year}.",
    "yir_top_h2": "Les n°1 de l'année",
    "yir_top_lead": "<strong>{g}</strong> a dominé le palmarès des filles et <strong>{b}</strong> celui des garçons en {year}.",
    "yir_risers_h2": "Plus grandes progressions",
    "yir_risers_lead": "Prénoms dont le classement a le plus progressé d'une année sur l'autre.",
    "yir_fallers_h2": "Plus fortes chutes",
    "yir_fallers_lead": "Prénoms dont le classement a le plus reculé d'une année sur l'autre.",
    "yir_debut_h2": "Premières apparitions",
    "yir_debut_lead": "Prénoms qui apparaissent dans les données pour la toute première fois.",
    "yir_newcomers_h2": "Nouveaux dans le top 100",
    "yir_newcomers_lead": "Prénoms qui entrent dans le top 100 pour la première fois depuis {prev}.",
    "yir_exits_h2": "Sortis du top 100",
    "yir_exits_lead": "Prénoms qui quittent le top 100 après y avoir figuré en {prev}.",
    "yir_rank_change": "{prev_rank} → {rank} (▲{delta})",
    "yir_rank_drop": "{prev_rank} → {rank} (▼{delta})",
    "yir_rank_new": "Nouvelle entrée — rang n°{rank}",
    "yir_rank_exit": "Était n°{prev_rank} — hors classement",
    "yir_count_year": "{n} naissances cette année",
    "crumb_yir": "Rétro {year}",

    "year_desc": ("Top 50 des prénoms les plus donnés en {year}, filles et garçons, "
                  "avec les effectifs (données {source}). N°1 : {g} et {b}."),

    "decade_h1": "Prénoms les plus populaires des années {label}",
    "decade_intro": ("Les prénoms les plus donnés au cours des années {label} "
                     "({span}), cumulés sur la décennie. Les n°1 de la décennie "
                     "étaient <strong>{g}</strong> chez les filles et "
                     "<strong>{b}</strong> chez les garçons."),
    "decade_girls_h2": "Filles — Top 50",
    "decade_boys_h2": "Garçons — Top 50",
    "decade_title": "Prénoms les plus populaires des années {label}",
    "decade_desc": ("Prénoms les plus donnés des années {label} ({span}). Top 50 "
                    "filles et garçons par naissances cumulées sur la décennie, "
                    "d'après {source}. N°1 : {g} et {b}."),
    "decades_h1": "Prénoms par décennie",
    "decades_title": "Prénoms par décennie — les plus populaires de chaque époque",
    "decades_intro": ("Découvrez les prénoms les plus donnés de chaque décennie, "
                      "des années {first} aux années {last}, d'après les données {source}."),
    "decades_desc": ("Prénoms les plus populaires par décennie, années {first} "
                     "à {last}. Les n°1 filles et garçons de chaque décennie, "
                     "d'après {source}."),
    "decades_th_decade": "Décennie",
    "decades_th_g": "N°1 filles",
    "decades_th_b": "N°1 garçons",

    "trends_h1": "Tendances des prénoms",
    "trends_intro": ("Quels prénoms montent, lesquels reculent ? Ces classements "
                     "comparent les naissances récentes à celles d'il y a cinq ans ({range})."),
    "trends_title": "Tendances des prénoms — en hausse et en baisse",
    "trends_desc": ("Découvrez quels prénoms montent et lesquels reculent à "
                    "l'approche de {year}, ainsi que les prénoms phares de "
                    "chaque décennie, d'après {source}."),
    "trends_rising_h1": "Prénoms en plus forte hausse ({year})",
    "trends_rising_intro": ("Les prénoms filles et garçons qui progressent le "
                            "plus vite en popularité — comparaison entre les "
                            "naissances d'il y a environ cinq ans et les années "
                            "les plus récentes. Seuls les prénoms avec un usage "
                            "actuel significatif sont inclus."),
    "trends_rising_title": "Prénoms en plus forte hausse en {year}",
    "trends_rising_desc": ("Les prénoms qui progressent le plus vite à l'approche "
                           "de {year}, filles et garçons, d'après les données {source}."),
    "trends_falling_h1": "Prénoms en plus forte baisse ({year})",
    "trends_falling_intro": ("Prénoms autrefois courants qui reculent le plus "
                             "vite — comparaison entre les naissances d'il y a "
                             "environ cinq ans et les années les plus récentes."),
    "trends_falling_title": "Prénoms en plus forte baisse en {year}",
    "trends_falling_desc": ("Les prénoms qui reculent le plus vite à l'approche "
                            "de {year}, filles et garçons, d'après {source}."),
    "trends_th_older": "il y a ~5 ans",
    "trends_th_change": "Évolution",
    "trends_card_rising": "Prénoms en hausse",
    "trends_card_rising_sub": "Plus fortes progressions en {year}",
    "trends_card_falling": "Prénoms en baisse",
    "trends_card_falling_sub": "Plus fortes baisses en {year}",
    "trends_card_decades": "Prénoms par décennie",
    "trends_card_decades_sub": "Les phares de chaque époque",

    "letter_h1": "Prénoms {label} commençant par {letter}",
    "letter_title": "Prénoms {label} commençant par {letter} — classement par popularité",
    "letter_intro": ("Les {n} prénoms {label} commençant par <strong>{letter}</strong> "
                     "qui ont une page dédiée, classés par naissances cumulées ({range}). "
                     "{cross_q}"),
    "letter_cross_link": "les prénoms {label} commençant par {letter}",
    "letter_cross_q": "Vous cherchez {link} ?",
    "letter_desc": ("Prénoms {label} qui commencent par {letter} : {n} options "
                    "classées par popularité, avec les naissances cumulées et "
                    "le rang actuel, d'après {source}."),

    "rare_h1": "Prénoms rares — index A–Z complet",
    "rare_title": "Prénoms rares — index A–Z complet",
    "rare_intro": ("Ces {n} prénoms apparaissent dans les données ({range}) mais "
                   "comptent moins de {min} naissances cumulées, et n'ont donc "
                   "pas encore leur propre page. Ils sont listés ici de A à Z "
                   "avec le total des naissances."),
    "rare_tip": ("Astuce : utilisez <kbd>Ctrl</kbd>+<kbd>F</kbd> (ou "
                 "<kbd>⌘</kbd>+<kbd>F</kbd>) pour chercher dans cette page."),
    "rare_letter_count": "({n} prénoms)",
    "rare_mostly": "({total} · majoritairement {label})",
    "rare_desc": ("Index de {n} prénoms rares ({range}) comptant moins de {min} "
                  "naissances cumulées, listés de A à Z."),

    # Works with (compatibilité nom de famille)
    "nav_works_with": "Avec votre nom",
    "ww_title": "Prénoms qui vont bien avec votre nom — NameCharted",
    "ww_h1": "Prénoms qui vont bien avec votre nom",
    "ww_intro": ("Saisissez votre nom de famille : nous évaluons chaque prénom "
                 "selon le rythme, les sons à la jonction prénom / nom et les "
                 "initiales partagées."),
    "ww_input": "Votre nom de famille",
    "ww_go": "Trouver des prénoms",
    "ww_loading": "Calcul des scores…",
    "ww_empty": "Saisissez un nom ci-dessus pour voir les suggestions.",
    "ww_tab_all": "Tous",
    "ww_tab_girls": "Filles",
    "ww_tab_boys": "Garçons",
    "ww_result_for": "Meilleurs prénoms pour {surname}",
    "ww_score": "Score",
    "ww_desc": ("Trouvez les prénoms qui sonnent bien avec n'importe quel nom de "
                "famille. Score basé sur le rythme, les sons heurtés et les "
                "initiales communes pour bâtir vite une short-list."),
    "ww_show_more": "Voir plus",
    "ww_how_h": "Ce qui fait une bonne association",
    "ww_how_1": "<strong>Initiale différente</strong> de votre nom — pas de <em>Sam Smith</em>.",
    "ww_how_2": "<strong>Nombre de syllabes différent</strong> — pas de <em>Jack Black</em>.",
    "ww_how_3": "<strong>Pas de rime à la jonction</strong> — pas de <em>Aiden Hayden</em>.",

    # Picker (sélecteur de prénoms)
    "nav_picker": "Sélecteur",
    "picker_title": "Sélecteur de prénoms — swipez, filtrez, tirez au sort — NameCharted",
    "picker_h1": "Bâtissez votre short-list plus vite",
    "picker_intro": ("Trois façons de découvrir des prénoms : un par un en "
                     "swipant, par filtres d'époque et de style, ou au hasard."),
    "picker_tab_swipe": "Swiper",
    "picker_tab_filter": "Filtres",
    "picker_tab_random": "Surprise",
    "picker_swipe_skip": "Passer",
    "picker_swipe_save": "Garder",
    "picker_swipe_undo": "Annuler",
    "picker_swipe_saved": "Ajouté à vos favoris.",
    "picker_swipe_exhausted": "Plus de prénoms — changez les filtres ou recommencez.",
    "picker_swipe_restart": "Recommencer",
    "picker_swipe_filter_sex": "Afficher",
    "picker_swipe_filter_era": "Époque",
    "picker_filter_sex": "Sexe",
    "picker_filter_syll": "Syllabes",
    "picker_filter_era": "Décennie record",
    "picker_filter_letter": "Commence par",
    "picker_filter_rank": "Popularité actuelle",
    "picker_filter_origin": "Origine",
    "picker_filter_any": "Indifférent",
    "picker_filter_rank_top100": "Top 100 actuel",
    "picker_filter_rank_top1000": "Top 1 000 actuel",
    "picker_filter_rank_rare": "Vintage / hors classement",
    "picker_filter_match_one": "1 prénom correspond",
    "picker_filter_match_many": "{n} prénoms correspondent",
    "picker_filter_match_none": "Aucun prénom — assouplissez les filtres.",
    "picker_random_count": "Combien",
    "picker_random_go": "Tirer au sort",
    "picker_random_share": "Copier le lien à partager",
    "picker_random_share_done": "Lien copié !",
    "picker_random_again": "Re-tirer",
    "picker_random_empty": "Rien trouvé — essayez une autre combinaison.",
    "picker_peak_decade": "Pic dans les années {d}",
    "picker_currently_rank": "#{rank} aujourd'hui",
    "picker_not_ranked": "Hors classement",
    "picker_desc": ("Découvrez des prénoms en swipant, en filtrant par décennie "
                    "et syllabes, ou en tirant au sort. Ajoutez à vos favoris au "
                    "fil de l'eau."),

    # Suggesteur de prénoms de fratrie
    "nav_sibling": "Idées fratrie",
    "sibling_title": "Idées de prénoms pour la fratrie — NameCharted",
    "sibling_h1": "Trouver un prénom pour la fratrie",
    "sibling_intro": ("Donnez-nous un à trois prénoms d'enfants : nous "
                      "proposons des prénoms d'époque et de rythme similaires, "
                      "sans rimer ni commencer par la même lettre."),
    "sibling_input": "Prénom du premier enfant",
    "sibling_input_more": "Prénom d'un autre enfant",
    "sibling_add_name": "+ Ajouter un prénom",
    "sibling_remove_name": "Retirer ce prénom",
    "sibling_target_sex": "Prochain bébé",
    "sibling_go": "Suggérer",
    "sibling_empty": "Saisissez un prénom pour voir des idées.",
    "sibling_unknown": ("Nous n'avons pas de données pour l'un de ces prénoms : "
                        "nous ne ferons que la correspondance de rythme. Pour "
                        "de meilleurs résultats, choisissez des prénoms ayant "
                        "leur propre page."),
    "sibling_result_for": "Prénoms qui vont bien avec {name}",
    "sibling_result_for_set": "Prénoms qui vont bien avec {names}",
    "sibling_show_more": "Voir plus",
    "sibling_share": "Copier le lien",
    "sibling_share_done": "Lien copié !",
    "sibling_desc": ("Trouvez des prénoms pour la fratrie qui s'accordent avec "
                     "le prénom — ou les 2-3 prénoms — d'enfants déjà "
                     "choisis. Score basé sur l'époque, le nombre de syllabes "
                     "et l'initiale."),

    # Origines
    "nav_origins": "Origines",
    "origins_hub_title": "Origines des prénoms par langue et culture",
    "origins_hub_h1": "Origines des prénoms",
    "origins_hub_intro": ("Explorez les prénoms selon leur langue d'origine. "
                          "Chaque page liste les prénoms filles et garçons "
                          "issus de cette culture, avec leur popularité."),
    "origins_hub_desc": ("Parcourez les prénoms par origine linguistique — "
                         "hébraïque, grec, latin, arabe, japonais, irlandais "
                         "et plus."),
    "origins_hub_count": "{n} prénoms",
    "origin_page_title": "Prénoms {label} — popularité et tendances",
    "origin_page_h1": "Prénoms d'origine {label}",
    "origin_page_intro": ("Prénoms populaires d'origine {label}. Ces prénoms "
                          "puisent leurs racines dans la langue et la culture "
                          "{label}, classés ici par popularité totale en {country}."),
    "origin_page_desc": ("Prénoms d'origine {label} : classements filles et "
                         "garçons, tendances annuelles et significations."),
    "origin_page_girls_h2": "Prénoms filles d'origine {label}",
    "origin_page_boys_h2": "Prénoms garçons d'origine {label}",
    "origin_back_to_hub": "← Toutes les origines",
    "name_origin_badge": "Origine : {label}",
    "name_meaning_h2": "Signification",
    "name_meaning_source": "D'après Wikipédia",
    "numerology_h2": "Numérologie de {name}",
    "numerology_intro": ("En numérologie pythagoricienne, chaque lettre a une "
                          "valeur de 1 à 9. La somme des lettres de "
                          "<strong>{name}</strong> donne ces trois nombres — "
                          "une lecture ludique du caractère du prénom."),
    "numerology_destiny_lbl": "Nombre de destinée",
    "numerology_destiny_desc": "Somme de toutes les lettres. Le caractère global du prénom.",
    "numerology_soul_lbl": "Nombre du cœur",
    "numerology_soul_desc": "Somme des voyelles — ce qui anime intérieurement.",
    "numerology_personality_lbl": "Personnalité",
    "numerology_personality_desc": "Somme des consonnes — l'impression extérieure.",
    "numerology_footer": "La numérologie n'est pas une science — c'est de la divination thématique. À prendre comme tel.",
    "name_famous_h2": "Personnalités prénommées {name}",
    "name_famous_occ_sep": " · ",
    "name_famous_born": "né en {year}",

    # Fiction
    "nav_fiction": "Prénoms de fiction",
    "fiction_hub_title": "Prénoms tirés de livres, films et séries",
    "fiction_hub_h1": "Prénoms de fiction",
    "fiction_hub_intro": ("Sélection de personnages de {n} franchises — Harry "
                          "Potter, Star Wars, Bridgerton, Jane Austen et plus. "
                          "Quand un prénom fictif correspond à un prénom réel "
                          "dans nos données, nous lions vers sa page de popularité."),
    "fiction_hub_desc": ("Prénoms tirés de livres, films et séries. Sélection "
                         "issue de Harry Potter, Game of Thrones, Star Wars, "
                         "Bridgerton et plus."),
    "fiction_franchise_title": "Personnages de {title}",
    "fiction_franchise_intro": ("{n} personnages de {title}. Les prénoms en teal "
                                "ont leur propre page de popularité avec leurs "
                                "courbes de naissances."),
    "fiction_franchise_desc": ("Prénoms tirés de {title}. Personnages "
                               "sélectionnés avec liens vers les données réelles."),
    "fiction_back_to_hub": "← Toutes les œuvres",
    "fiction_card_count": "{n} personnages",
    "fiction_year": "depuis {year}",
    "name_fiction_h2": "Aussi un personnage de",
    "name_fiction_in": "Dans <a href=\"{url}\">{title}</a> : {role}",

    # Saints / fête du jour (FR-only)
    "nav_saints": "Fête du jour",
    "saints_hub_title": "Fête du jour — calendrier des saints et des prénoms",
    "saints_hub_h1": "Fête du jour",
    "saints_hub_intro": ("Le calendrier civil français des saints, jour par jour. "
                         "Trouvez le saint du jour, la date d'une fête, ou la "
                         "page de popularité du prénom correspondant."),
    "saints_hub_desc": ("Calendrier français des saints jour par jour. "
                        "Trouvez la fête d'un prénom, le saint du jour, "
                        "et la popularité de chaque prénom en France."),
    "saints_today_label": "Aujourd'hui c'est la Saint-{name}",
    "saints_today_label_fem": "Aujourd'hui c'est la Sainte-{name}",
    "saints_today_event": "Aujourd'hui : {name}",
    "saints_today_wish": "Bonne fête à tous les {name} !",
    "saints_month_jan": "Janvier", "saints_month_feb": "Février",
    "saints_month_mar": "Mars",    "saints_month_apr": "Avril",
    "saints_month_may": "Mai",     "saints_month_jun": "Juin",
    "saints_month_jul": "Juillet", "saints_month_aug": "Août",
    "saints_month_sep": "Septembre", "saints_month_oct": "Octobre",
    "saints_month_nov": "Novembre", "saints_month_dec": "Décembre",
    "saint_page_title": "Saint{e} {name} — fête, dates, popularité du prénom",
    "saint_page_h1": "Saint{e} {name}",
    "saint_page_dates_one": "Sa fête est célébrée le <strong>{date}</strong>.",
    "saint_page_dates_multi": "Fêté(e) les <strong>{dates}</strong>.",
    "saint_page_popularity_link": "Voir la popularité du prénom {name} →",
    "saint_page_desc": "Date(s) de fête, signification et popularité du prénom {name} en France.",
    "saint_back_to_hub": "← Voir tout le calendrier",

    # Initials
    "nav_initials": "Initiales",
    "initials_title": "Générateur de prénoms par initiales",
    "initials_h1": "Composez les initiales",
    "initials_intro": ("Saisissez les initiales souhaitées et nous générons 20 "
                       "combinaisons de prénoms correspondantes — un brainstorming "
                       "ludique pour les monogrammes."),
    "initials_input": "ex. A.J.K",
    "initials_go": "Tirer 20 combinaisons",
    "initials_again": "Re-tirer",
    "initials_filter_sex": "Sexe du prénom",
    "initials_empty": "Saisissez 2 ou 3 initiales pour commencer.",
    "initials_no_match": "Aucun prénom commençant par {letter} dans nos données — essayez une autre lettre.",
    "initials_share": "Copier le lien à partager",
    "initials_share_done": "Lien copié !",
    "initials_desc": ("Générez des combinaisons de prénoms à partir des initiales "
                      "choisies. Tirées de notre base de prénoms classés par popularité."),
}

STRINGS = {"US": STRINGS_EN, "FR": STRINGS_FR, "GB": STRINGS_EN,
           "AU": STRINGS_EN, "CA": STRINGS_EN, "ES": STRINGS_EN,
           "IT": STRINGS_EN, "NL": STRINGS_EN}

# Gendered forms per language. Used for "girls"/"filles", "boy's"/"de garçon", etc.
# URL slugs always use the English form ('girls'/'boys') for cross-country URL parity.
GENDERED_EN = {
    "label_F": "girls", "label_M": "boys",
    "label_cap_F": "Girls", "label_cap_M": "Boys",
    "singular_F": "girl", "singular_M": "boy",
    "of_singular_F": "girl's", "of_singular_M": "boy's",
}
GENDERED_FR = {
    "label_F": "filles", "label_M": "garçons",
    "label_cap_F": "Filles", "label_cap_M": "Garçons",
    "singular_F": "fille", "singular_M": "garçon",
    "of_singular_F": "de fille", "of_singular_M": "de garçon",
}
GENDERED = {"US": GENDERED_EN, "FR": GENDERED_FR, "GB": GENDERED_EN,
            "AU": GENDERED_EN, "CA": GENDERED_EN, "ES": GENDERED_EN,
            "IT": GENDERED_EN, "NL": GENDERED_EN}

# Origin-slug → display label per UI language. Slugs come from
# data/normalized/name_enrichment.json (built by fetchers/enrich_wikidata.py).
# Keep this in sync with that file; unknown slugs fall back to title-cased slug.
# Numerology trait names + one-line descriptions per number. Used on every
# name page — short by design so the section stays compact.
NUMEROLOGY_TRAITS_EN: dict[int, tuple[str, str]] = {
    1: ("The leader", "Independent, pioneering, drawn to going first."),
    2: ("The peacemaker", "Diplomatic, sensitive, partnership-minded."),
    3: ("The creative", "Expressive, sociable, lifted by playful energy."),
    4: ("The builder", "Practical, steady, gets results through hard work."),
    5: ("The free spirit", "Curious, restless, energised by change."),
    6: ("The carer", "Nurturing, responsible, family at the centre."),
    7: ("The seeker", "Introspective, analytical, drawn to the unknown."),
    8: ("The achiever", "Driven, capable, comfortable wielding influence."),
    9: ("The humanitarian", "Idealistic, compassionate, thinks in big-picture terms."),
    11: ("The visionary (master)", "Intuitive and inspirational — a heightened 2."),
    22: ("The master builder", "Turns visions into structures — a heightened 4."),
    33: ("The master teacher", "Selfless guidance and uplift — a heightened 6."),
}
NUMEROLOGY_TRAITS_FR: dict[int, tuple[str, str]] = {
    1: ("Le leader", "Indépendant, pionnier, fait pour ouvrir la voie."),
    2: ("Le pacificateur", "Diplomate, sensible, à l'aise en duo."),
    3: ("Le créatif", "Expressif, sociable, porté par l'énergie joyeuse."),
    4: ("Le bâtisseur", "Pragmatique, posé, obtient par le travail."),
    5: ("L'esprit libre", "Curieux, mobile, stimulé par le changement."),
    6: ("Le protecteur", "Attentionné, responsable, la famille au centre."),
    7: ("Le chercheur", "Introspectif, analytique, attiré par l'inconnu."),
    8: ("L'ambitieux", "Déterminé, capable, à l'aise avec l'influence."),
    9: ("L'humaniste", "Idéaliste, généreux, pense en grand."),
    11: ("Le visionnaire (maître)", "Intuitif, inspirant — un 2 amplifié."),
    22: ("Le maître bâtisseur", "Transforme la vision en structure — un 4 amplifié."),
    33: ("Le maître pédagogue", "Guidance désintéressée — un 6 amplifié."),
}
NUMEROLOGY_TRAITS = {"US": NUMEROLOGY_TRAITS_EN, "FR": NUMEROLOGY_TRAITS_FR,
                     "GB": NUMEROLOGY_TRAITS_EN, "AU": NUMEROLOGY_TRAITS_EN,
                     "CA": NUMEROLOGY_TRAITS_EN, "ES": NUMEROLOGY_TRAITS_EN,
                     "IT": NUMEROLOGY_TRAITS_EN, "NL": NUMEROLOGY_TRAITS_EN}


ORIGIN_LABELS_EN: dict[str, str] = {
    'english': 'English',
    'irish': 'Irish',
    'scottish': 'Scottish',
    'welsh': 'Welsh',
    'french': 'French',
    'german': 'German',
    'italian': 'Italian',
    'spanish': 'Spanish',
    'portuguese': 'Portuguese',
    'dutch': 'Dutch',
    'swedish': 'Swedish',
    'norwegian': 'Norwegian',
    'danish': 'Danish',
    'finnish': 'Finnish',
    'scandinavian': 'Scandinavian',
    'latin': 'Latin',
    'greek': 'Greek',
    'hebrew': 'Hebrew',
    'arabic': 'Arabic',
    'aramaic': 'Aramaic',
    'persian': 'Persian',
    'sanskrit': 'Sanskrit',
    'russian': 'Russian',
    'polish': 'Polish',
    'czech': 'Czech',
    'hungarian': 'Hungarian',
    'romanian': 'Romanian',
    'ukrainian': 'Ukrainian',
    'bulgarian': 'Bulgarian',
    'serbo-croatian': 'Serbo-Croatian',
    'japanese': 'Japanese',
    'chinese': 'Chinese',
    'korean': 'Korean',
    'vietnamese': 'Vietnamese',
    'turkish': 'Turkish',
    'armenian': 'Armenian',
    'tamil': 'Tamil',
    'hindi': 'Hindi',
    'urdu': 'Urdu',
    'thai': 'Thai',
    'indonesian': 'Indonesian',
    'swahili': 'Swahili',
    'yoruba': 'Yoruba',
    'igbo': 'Igbo',
}
ORIGIN_LABELS_FR: dict[str, str] = {
    'english': 'anglais',
    'irish': 'irlandais',
    'scottish': 'écossais',
    'welsh': 'gallois',
    'french': 'français',
    'german': 'allemand',
    'italian': 'italien',
    'spanish': 'espagnol',
    'portuguese': 'portugais',
    'dutch': 'néerlandais',
    'swedish': 'suédois',
    'norwegian': 'norvégien',
    'danish': 'danois',
    'finnish': 'finnois',
    'scandinavian': 'scandinave',
    'latin': 'latin',
    'greek': 'grec',
    'hebrew': 'hébreu',
    'arabic': 'arabe',
    'aramaic': 'araméen',
    'persian': 'persan',
    'sanskrit': 'sanskrit',
    'russian': 'russe',
    'polish': 'polonais',
    'czech': 'tchèque',
    'hungarian': 'hongrois',
    'romanian': 'roumain',
    'ukrainian': 'ukrainien',
    'bulgarian': 'bulgare',
    'serbo-croatian': 'serbo-croate',
    'japanese': 'japonais',
    'chinese': 'chinois',
    'korean': 'coréen',
    'vietnamese': 'vietnamien',
    'turkish': 'turc',
    'armenian': 'arménien',
    'tamil': 'tamoul',
    'hindi': 'hindi',
    'urdu': 'ourdou',
    'thai': 'thaï',
    'indonesian': 'indonésien',
    'swahili': 'swahili',
    'yoruba': 'yoruba',
    'igbo': 'igbo',
}
ORIGIN_LABELS = {"US": ORIGIN_LABELS_EN, "FR": ORIGIN_LABELS_FR,
                 "GB": ORIGIN_LABELS_EN, "AU": ORIGIN_LABELS_EN,
                 "CA": ORIGIN_LABELS_EN, "ES": ORIGIN_LABELS_EN,
                 "IT": ORIGIN_LABELS_EN, "NL": ORIGIN_LABELS_EN}


# Common surnames per country, grouped by first letter — used by the
# initials maker (Phase 6j) to fill in last-name slots. Drawn from public
# census / electoral roll top-lists. Keep it small (~3-6 per letter) — the
# tool is meant to be playful, not exhaustive.
SURNAMES_BY_CC: dict[str, dict[str, list[str]]] = {
    "US": {
        "A": ["Adams", "Allen", "Anderson", "Alvarez"],
        "B": ["Brown", "Baker", "Bell", "Bennett", "Bailey"],
        "C": ["Clark", "Carter", "Collins", "Campbell", "Cooper", "Cook"],
        "D": ["Davis", "Diaz", "Davidson", "Dixon", "Duncan"],
        "E": ["Edwards", "Evans", "Ellis"],
        "F": ["Foster", "Fisher", "Ferguson", "Fox", "Flores"],
        "G": ["Garcia", "Green", "Gray", "Griffin", "Gonzalez"],
        "H": ["Hernandez", "Harris", "Hall", "Hill", "Howard", "Hughes"],
        "I": ["Ingram", "Irwin"],
        "J": ["Johnson", "Jones", "Jackson", "James"],
        "K": ["King", "Kim", "Kelly", "Kennedy"],
        "L": ["Lopez", "Lee", "Lewis", "Long", "Lane"],
        "M": ["Martinez", "Miller", "Moore", "Mitchell", "Murphy", "Morris"],
        "N": ["Nelson", "Nguyen", "Nichols"],
        "O": ["Owens", "Oliver", "Oconnor"],
        "P": ["Perez", "Parker", "Patel", "Phillips", "Price"],
        "Q": ["Quinn", "Quintana"],
        "R": ["Rodriguez", "Robinson", "Roberts", "Russell", "Reed", "Reyes"],
        "S": ["Smith", "Sanchez", "Stewart", "Scott", "Sullivan", "Sanders"],
        "T": ["Taylor", "Thomas", "Thompson", "Turner", "Tucker"],
        "U": ["Underwood", "Upton"],
        "V": ["Vasquez", "Vaughn", "Valdez"],
        "W": ["Williams", "Walker", "Wright", "Wilson", "Ward", "Wood"],
        "X": ["Xavier", "Xiong"],
        "Y": ["Young", "Yang"],
        "Z": ["Zimmerman", "Zhang"],
    },
    "FR": {
        "A": ["Allard", "Arnaud", "Aubry", "Albert"],
        "B": ["Bernard", "Blanc", "Boyer", "Brun", "Bertrand"],
        "C": ["Caron", "Chevalier", "Clément", "Colin", "Charpentier"],
        "D": ["Dubois", "Durand", "Dupont", "David", "Denis"],
        "E": ["Étienne", "Evrard"],
        "F": ["Fontaine", "Faure", "Fournier", "François"],
        "G": ["Garcia", "Gauthier", "Girard", "Guérin", "Gérard"],
        "H": ["Henry", "Hubert"],
        "I": ["Imbert"],
        "J": ["Jacquet", "Joly", "Julien"],
        "K": ["Klein"],
        "L": ["Lefebvre", "Leroy", "Laurent", "Lambert", "Legrand", "Lemoine"],
        "M": ["Martin", "Morel", "Michel", "Marchand", "Mercier", "Moreau"],
        "N": ["Noël", "Nicolas"],
        "O": ["Olivier"],
        "P": ["Petit", "Perrin", "Pierre", "Picard"],
        "Q": ["Quintin"],
        "R": ["Roux", "Robert", "Richard", "Rousseau", "Renaud"],
        "S": ["Simon", "Schmitt", "Sanchez"],
        "T": ["Thomas", "Thibault"],
        "U": ["Urvoy"],
        "V": ["Vincent", "Vidal"],
        "W": ["Weber"],
        "Z": ["Zimmer"],
    },
    "GB": {
        "A": ["Allen", "Adams", "Anderson"],
        "B": ["Brown", "Baker", "Bennett", "Bailey", "Butler"],
        "C": ["Clark", "Cooper", "Campbell", "Cox", "Carter", "Collins"],
        "D": ["Davies", "Dixon", "Davis", "Dawson"],
        "E": ["Edwards", "Evans", "Ellis"],
        "F": ["Fisher", "Ford", "Fox"],
        "G": ["Green", "Gray", "Griffiths"],
        "H": ["Harris", "Hughes", "Hall", "Hill", "Hunt"],
        "I": ["Ingram"],
        "J": ["Jones", "Johnson", "Jackson", "James"],
        "K": ["King", "Knight", "Kelly"],
        "L": ["Lewis", "Lee", "Lloyd"],
        "M": ["Murphy", "Miller", "Morris", "Mitchell", "Moore"],
        "N": ["Nicholson", "Norris"],
        "O": ["Owen", "Oliver"],
        "P": ["Patel", "Phillips", "Parker", "Price"],
        "R": ["Roberts", "Robinson", "Reed", "Russell"],
        "S": ["Smith", "Stewart", "Stone", "Scott", "Shaw"],
        "T": ["Taylor", "Thomas", "Thompson", "Turner"],
        "W": ["Williams", "Wilson", "Walker", "Wright", "Wood"],
        "Y": ["Young"],
    },
    "AU": {
        "A": ["Anderson", "Adams", "Allen"],
        "B": ["Brown", "Bailey", "Bennett", "Baker"],
        "C": ["Campbell", "Clark", "Collins", "Cooper", "Carter"],
        "D": ["Davis", "Dixon", "Dawson"],
        "E": ["Edwards", "Evans"],
        "F": ["Fisher", "Foster", "Fox"],
        "G": ["Green", "Gray", "Griffin"],
        "H": ["Harris", "Hughes", "Hall", "Hill", "Harrison"],
        "J": ["Jones", "Johnson", "Jackson"],
        "K": ["King", "Kelly", "Kennedy"],
        "L": ["Lee", "Lewis", "Lloyd"],
        "M": ["Murphy", "Miller", "Mitchell", "Morris", "McKenzie"],
        "N": ["Nguyen", "Nichols"],
        "O": ["Oconnor", "Oliver"],
        "P": ["Phillips", "Parker", "Price", "Patel"],
        "R": ["Roberts", "Robinson", "Reed", "Ryan"],
        "S": ["Smith", "Stewart", "Scott", "Sullivan", "Singh"],
        "T": ["Taylor", "Thomas", "Thompson", "Turner"],
        "W": ["Williams", "Wilson", "Walker", "Wright", "Walsh"],
        "Y": ["Young"],
    },
    "ES": {
        "A": ["Álvarez", "Alonso", "Aguilar"],
        "B": ["Blanco", "Bravo", "Bello"],
        "C": ["Castillo", "Cruz", "Castro", "Calvo", "Carmona"],
        "D": ["Díaz", "Domínguez", "Delgado"],
        "E": ["Esteban", "Escobar"],
        "F": ["Fernández", "Flores", "Fuentes"],
        "G": ["García", "González", "Gómez", "Gutiérrez", "Giménez"],
        "H": ["Hernández", "Herrera", "Hidalgo"],
        "I": ["Iglesias", "Ibáñez"],
        "J": ["Jiménez", "Juárez"],
        "L": ["López", "León", "Lozano", "Luna"],
        "M": ["Martín", "Martínez", "Mora", "Moreno", "Molina", "Muñoz"],
        "N": ["Navarro", "Núñez"],
        "O": ["Ortega", "Ortiz", "Ojeda"],
        "P": ["Pérez", "Prieto", "Parra", "Pascual"],
        "R": ["Rodríguez", "Ramírez", "Ruiz", "Reyes", "Romero"],
        "S": ["Sánchez", "Suárez", "Serrano", "Salazar"],
        "T": ["Torres", "Torre"],
        "V": ["Vázquez", "Vega", "Vidal"],
        "Y": ["Yáñez"],
        "Z": ["Zamora", "Zúñiga"],
    },
    "IT": {
        "A": ["Amato", "Antonelli", "Agostini"],
        "B": ["Bianchi", "Bruno", "Barbieri", "Benedetti", "Battaglia"],
        "C": ["Colombo", "Costa", "Conti", "Caruso", "Cattaneo"],
        "D": ["De Luca", "D'Angelo", "De Santis", "Donati"],
        "E": ["Esposito", "Endrizzi"],
        "F": ["Ferrari", "Ferrara", "Fontana", "Franchi", "Fiore"],
        "G": ["Greco", "Gallo", "Galli", "Giordano", "Gentile"],
        "H": ["Hofer"],
        "I": ["Innocenti", "Iacobelli"],
        "L": ["Leone", "Lombardi", "Longo", "Lombardo"],
        "M": ["Marino", "Mancini", "Martini", "Moretti", "Marchetti", "Messina"],
        "N": ["Negri", "Neri"],
        "O": ["Orlando", "Olivieri"],
        "P": ["Pellegrini", "Palumbo", "Parisi", "Pellegrino", "Piras"],
        "Q": ["Quaranta"],
        "R": ["Rossi", "Romano", "Ricci", "Russo", "Riva"],
        "S": ["Sala", "Santoro", "Serra", "Silvestri", "Sorrentino"],
        "T": ["Toniolo", "Testa", "Trombetta"],
        "V": ["Villa", "Valentini", "Vitale", "Vinci"],
        "Z": ["Zanetti", "Zanin", "Zito"],
    },
    "NL": {
        "A": ["Aalders", "Aarts", "Akkerman"],
        "B": ["Bakker", "Boer", "Beekman", "Beumer", "Brouwer"],
        "C": ["Claessens", "Coenen"],
        "D": ["De Jong", "De Vries", "Dekker", "Dijkstra", "De Boer"],
        "E": ["Evers", "Engelen"],
        "F": ["Fokker", "Franken"],
        "G": ["Groen", "Goedhart", "Gerritsen"],
        "H": ["Hendriks", "Hoogendoorn", "Hofman", "Huisman"],
        "I": ["IJsbrand"],
        "J": ["Jansen", "Janssen", "Jacobs"],
        "K": ["Kuiper", "Klaassen", "Kok", "Kuijpers"],
        "L": ["Lammers", "Leenders"],
        "M": ["Meijer", "Mulder", "Maas", "Martens"],
        "N": ["Nijhuis", "Nieuwenhuis"],
        "O": ["Oosterhof", "Otten"],
        "P": ["Peters", "Prins", "Pieters"],
        "R": ["Roos", "Reijnders"],
        "S": ["Smit", "Schouten", "Smits", "Schreuder", "Sanders"],
        "T": ["Timmermans", "Ten Have", "Tromp"],
        "V": ["Van den Berg", "Van Dijk", "Visser", "Vermeer", "Van der Meer"],
        "W": ["Wagenaar", "Wijnen", "Willems"],
        "Z": ["Zwart", "Zijlstra"],
    },
    "CA": {
        "A": ["Anderson", "Adams", "Allen"],
        "B": ["Brown", "Bouchard", "Bélanger", "Bergeron", "Bennett"],
        "C": ["Campbell", "Clark", "Côté", "Cooper", "Chan"],
        "D": ["Davis", "Desjardins", "Dubois", "Drouin"],
        "E": ["Evans", "Ellis"],
        "F": ["Fortin", "Ferguson", "Fisher"],
        "G": ["Gagnon", "Gauthier", "Girard", "Gill", "Gosselin"],
        "H": ["Harris", "Hall", "Hill", "Hébert"],
        "J": ["Johnson", "Jones", "Jackson"],
        "K": ["Kim", "Khan", "Kelly"],
        "L": ["Leblanc", "Lavoie", "Lefebvre", "Lee", "Lemieux"],
        "M": ["MacDonald", "Martin", "Morin", "Miller", "McKenzie"],
        "N": ["Nguyen", "Nadeau"],
        "O": ["Ouellet", "Oliver"],
        "P": ["Patel", "Pelletier", "Parker", "Pham"],
        "R": ["Roy", "Roberts", "Robinson", "Ross"],
        "S": ["Smith", "Singh", "Stewart", "Scott", "Saunders"],
        "T": ["Tremblay", "Taylor", "Thompson", "Tran"],
        "W": ["Wilson", "Wong", "Walker", "Williams"],
        "Y": ["Young", "Yu"],
    },
}


def origin_label(slug: str) -> str:
    return ORIGIN_LABELS[ACTIVE_CC].get(slug, slug.replace('-', ' ').title())


def origin_label_cap(slug: str) -> str:
    # EN/AU/GB capitalise origin labels in headlines ("Irish baby names")
    # FR uses lowercase adjective form ("prénoms irlandais").
    return origin_label(slug)[0].upper() + origin_label(slug)[1:] if ACTIVE_CC != 'FR' else origin_label(slug)


def S(key: str, **kwargs) -> str:
    tpl = STRINGS[ACTIVE_CC].get(key) or STRINGS_EN[key]
    return tpl.format(**kwargs) if kwargs else tpl


def fmt(n: int) -> str:
    """Locale-aware integer formatting. FR uses narrow no-break space, others ','."""
    s = f"{n:,}"
    if ACTIVE_CC == "FR":
        return s.replace(",", " ")
    return s


def lang_attr() -> str:
    return "fr" if ACTIVE_CC == "FR" else "en"


def loc_label(sex: str) -> str:
    return GENDERED[ACTIVE_CC][f"label_{sex}"]


def loc_label_cap(sex: str) -> str:
    return GENDERED[ACTIVE_CC][f"label_cap_{sex}"]


def loc_singular(sex: str) -> str:
    return GENDERED[ACTIVE_CC][f"singular_{sex}"]


def loc_of_singular(sex: str) -> str:
    """The 'X's' possessive form in English / 'de X' construction in French."""
    return GENDERED[ACTIVE_CC][f"of_singular_{sex}"]


def slug_label(sex: str) -> str:
    """English form used in URLs ('girls'/'boys') — always English regardless of locale."""
    return 'girls' if sex == 'F' else 'boys'


def homepage_cc_callout() -> str:
    """Cross-country card shown only on each homepage."""
    names = COUNTRY_NAMES_IN_UI[ACTIVE_CC]
    others = [c for c in COUNTRIES if c != ACTIVE_CC]
    links = " <span style=\"color:#8a93a3;\">·</span> ".join(
        f'<a href="{home_path(c)}"><span class="flag" aria-hidden="true">{FLAG[c]}</span>{names[c]}</a>'
        for c in others
    )
    return f'        <p class="cc-callout">{S("home_cc_callout")} {links}</p>\n'


LANG_BANNER_SCRIPT = """
    <script>
    (function() {
        try {
            if (localStorage.getItem('nc-region-prompt') === 'dismissed') return;
        } catch (e) { /* private mode: just proceed */ }
        var active = '__ACTIVE_CC__';
        var lang = ((navigator.language || navigator.userLanguage) || '').toLowerCase();
        var dest = null, text = '', go = '';
        if (lang.indexOf('fr') === 0 && active !== 'FR') {
            dest = '/fr/'; text = 'Voir ce site en français \\u003F'; go = 'Aller à NameCharted France →';
        } else if (lang.indexOf('en-gb') === 0 && active !== 'GB') {
            dest = '/uk/'; text = 'View the UK version of this site?'; go = 'Go to UK rankings →';
        } else if (lang.indexOf('en-au') === 0 && active !== 'AU') {
            dest = '/au/'; text = 'View the Australian version of this site?'; go = 'Go to Australia rankings →';
        } else if (lang.indexOf('en') === 0 && active === 'FR') {
            dest = '/'; text = 'View this site in English?'; go = 'Go to US version →';
        }
        if (!dest) return;
        var bar = document.createElement('div');
        bar.id = 'lang-banner';
        var span = document.createElement('span'); span.textContent = text; bar.appendChild(span);
        var a = document.createElement('a'); a.href = dest; a.textContent = go; bar.appendChild(a);
        var btn = document.createElement('button');
        btn.setAttribute('aria-label', 'Dismiss'); btn.textContent = '×';
        btn.addEventListener('click', function() {
            try { localStorage.setItem('nc-region-prompt', 'dismissed'); } catch (e) {}
            bar.parentNode && bar.parentNode.removeChild(bar);
        });
        bar.appendChild(btn);
        if (document.body.firstChild) {
            document.body.insertBefore(bar, document.body.firstChild);
        } else {
            document.body.appendChild(bar);
        }
    })();
    </script>"""


def lang_banner_script() -> str:
    return LANG_BANNER_SCRIPT.replace('__ACTIVE_CC__', ACTIVE_CC)


FAVORITES_SCRIPT = """
    <script>
    (function() {
        var CC = '__ACTIVE_CC__';
        var PREFIX = '__PREFIX__';
        var KEY = 'nc-favorites-' + CC;

        function read() {
            try { return JSON.parse(localStorage.getItem(KEY) || '[]'); }
            catch (e) { return []; }
        }
        function write(list) {
            try { localStorage.setItem(KEY, JSON.stringify(list)); } catch (e) {}
        }
        function findIdx(list, slug) {
            for (var i = 0; i < list.length; i++) if (list[i].slug === slug) return i;
            return -1;
        }
        function updateBadge() {
            var n = read().length;
            var els = document.querySelectorAll('.fav-nav-count');
            for (var i = 0; i < els.length; i++) {
                els[i].textContent = n ? ' (' + n + ')' : '';
            }
        }
        function clearChildren(el) {
            while (el.firstChild) el.removeChild(el.firstChild);
        }

        // Heart button on name page
        var btn = document.querySelector('.fav-btn[data-slug]');
        if (btn) {
            var slug = btn.getAttribute('data-slug');
            var name = btn.getAttribute('data-name');
            var addTip = btn.getAttribute('title');
            var removeTip = '__FAV_REMOVE_TIP__';
            function syncBtn() {
                var on = findIdx(read(), slug) >= 0;
                btn.classList.toggle('is-fav', on);
                btn.setAttribute('title', on ? removeTip : addTip);
                btn.setAttribute('aria-label', on ? removeTip : addTip);
                btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            }
            btn.addEventListener('click', function() {
                var list = read();
                var i = findIdx(list, slug);
                if (i >= 0) list.splice(i, 1);
                else list.push({slug: slug, name: name});
                write(list);
                syncBtn();
                updateBadge();
            });
            syncBtn();
        }

        // Share / Download buttons on the name page (non-Pinterest users).
        // Web Share API where available — on iOS/Android the share sheet
        // lets users save the pin image to Photos, send via Messages,
        // WhatsApp, AirDrop, etc. Desktop falls back to copy-to-clipboard.
        var sbtn = document.querySelector('.share-btn[data-share-url]');
        if (sbtn) {
            sbtn.addEventListener('click', function() {
                var url = sbtn.getAttribute('data-share-url');
                var title = sbtn.getAttribute('data-share-title') || '';
                var text = sbtn.getAttribute('data-share-text') || '';
                var copied = sbtn.getAttribute('data-copied') || 'Link copied!';
                if (navigator.share) {
                    navigator.share({title: title, text: text, url: url})
                        .catch(function() {});
                    return;
                }
                var done = function() {
                    var flash = document.createElement('span');
                    flash.className = 'share-flash';
                    flash.textContent = copied;
                    sbtn.parentNode.insertBefore(flash, sbtn.nextSibling);
                    setTimeout(function() {
                        if (flash.parentNode) flash.parentNode.removeChild(flash);
                    }, 1800);
                };
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(url).then(done, done);
                } else {
                    try {
                        var ta = document.createElement('textarea');
                        ta.value = url; document.body.appendChild(ta);
                        ta.select(); document.execCommand('copy');
                        document.body.removeChild(ta);
                        done();
                    } catch (e) {}
                }
            });
        }

        // Favorites page
        var ul = document.getElementById('fav-list');
        if (ul) {
            // Meta data for the per-row sub-line (origin, peak decade, latest rank).
            // Shape: meta[slug] = [first, last2, syll, dom, peak_dec, rank, origin]
            var FAV_META = null;
            var FAV_ORIGIN_LABELS = __FAV_ORIGIN_LABELS__;
            var FAV_PEAK_FMT = __FAV_PEAK_FMT__;        // "Peak {d}s"
            var FAV_UNRANKED = __FAV_UNRANKED__;        // "unranked"
            var FAV_GIRL = __FAV_GIRL__;                // "Girl" / "Fille"
            var FAV_BOY = __FAV_BOY__;                  // "Boy" / "Garçon"
            function loadFavMeta() {
                if (FAV_META) return Promise.resolve(FAV_META);
                return fetch(PREFIX + '/name-meta.json')
                    .then(function(r) { return r.json(); })
                    .then(function(d) { FAV_META = d; return d; })
                    .catch(function() { FAV_META = {}; return {}; });
            }
            function metaLineFor(slug) {
                if (!FAV_META) return '';
                var m = FAV_META[slug];
                if (!m) return '';
                var parts = [];
                if (m[3] === 'F') parts.push(FAV_GIRL);
                else if (m[3] === 'M') parts.push(FAV_BOY);
                var orig = m[6];
                if (orig && FAV_ORIGIN_LABELS[orig]) parts.push(FAV_ORIGIN_LABELS[orig]);
                if (m[5]) parts.push('#' + m[5]);
                else parts.push(FAV_UNRANKED);
                if (m[4]) parts.push(FAV_PEAK_FMT.replace('{d}', m[4]));
                return parts.join(' · ');
            }
            function enrichMeta() {
                Array.prototype.forEach.call(ul.querySelectorAll('li[data-slug]'), function(li) {
                    var slug = li.getAttribute('data-slug');
                    var line = metaLineFor(slug);
                    var meta = li.querySelector('.fav-meta');
                    if (line) {
                        if (!meta) {
                            meta = document.createElement('span');
                            meta.className = 'fav-meta';
                            li.appendChild(meta);
                        }
                        meta.textContent = line;
                    } else if (meta) {
                        meta.remove();
                    }
                });
            }
            // Kick off the meta fetch in parallel with the first render.
            loadFavMeta().then(enrichMeta);
            // Shareable hash: /favorites.html#slug1,slug2 merges into the saved list.
            var hash = decodeURIComponent((window.location.hash || '').slice(1));
            if (hash) {
                var slugs = hash.split(',').filter(Boolean);
                var existing = read();
                slugs.forEach(function(s) {
                    if (findIdx(existing, s) < 0) {
                        var displayName = s.replace(/^./, function(c) { return c.toUpperCase(); })
                                           .replace(/-(.)/g, function(_, c) { return ' ' + c.toUpperCase(); });
                        existing.push({slug: s, name: displayName});
                    }
                });
                write(existing);
                history.replaceState(null, '', window.location.pathname);
            }
            var emptyEl = document.getElementById('fav-empty');
            var actionsEl = document.getElementById('fav-actions');
            var shareBtn = document.getElementById('fav-share');
            var shareDone = document.getElementById('fav-share-done');
            function render() {
                var list = read();
                clearChildren(ul);
                if (!list.length) {
                    emptyEl.style.display = '';
                    ul.style.display = 'none';
                    actionsEl.style.display = 'none';
                    return;
                }
                emptyEl.style.display = 'none';
                ul.style.display = '';
                actionsEl.style.display = '';
                list.forEach(function(item) {
                    var li = document.createElement('li');
                    li.setAttribute('data-slug', item.slug);
                    var a = document.createElement('a');
                    a.href = PREFIX + '/name/' + item.slug + '.html';
                    a.textContent = item.name;
                    var rm = document.createElement('button');
                    rm.className = 'fav-remove-btn';
                    rm.setAttribute('aria-label', '__FAV_REMOVE__');
                    rm.setAttribute('data-slug', item.slug);
                    rm.textContent = '\\u00d7';
                    li.appendChild(a); li.appendChild(rm);
                    ul.appendChild(li);
                });
                if (FAV_META) enrichMeta();
            }
            ul.addEventListener('click', function(e) {
                var t = e.target;
                if (t && t.classList.contains('fav-remove-btn')) {
                    var s = t.getAttribute('data-slug');
                    var list = read();
                    var i = findIdx(list, s);
                    if (i >= 0) { list.splice(i, 1); write(list); render(); updateBadge(); }
                }
            });
            var printBtn = document.getElementById('fav-print');
            if (printBtn) {
                printBtn.addEventListener('click', function() {
                    loadFavMeta().then(function() {
                        enrichMeta();
                        setTimeout(function() { window.print(); }, 0);
                    });
                });
            }
            shareBtn.addEventListener('click', function() {
                var list = read();
                if (!list.length) return;
                var url = window.location.origin + window.location.pathname
                          + '#' + list.map(function(x) { return x.slug; }).join(',');
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(url).then(function() {
                        shareDone.style.display = '';
                        setTimeout(function() { shareDone.style.display = 'none'; }, 2500);
                    });
                } else {
                    window.prompt('Copy this link:', url);
                }
            });
            render();
        }

        updateBadge();

        // Register the service worker for offline browsing + installability.
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', function() {
                navigator.serviceWorker.register('/sw.js').catch(function() {});
            });
        }
    })();
    </script>"""


def favorites_script() -> str:
    origin_labels = ORIGIN_LABELS[ACTIVE_CC]
    return (FAVORITES_SCRIPT
            .replace('__ACTIVE_CC__', ACTIVE_CC)
            .replace('__PREFIX__', PREFIX)
            .replace('__FAV_REMOVE_TIP__', S("fav_remove_tip"))
            .replace('__FAV_REMOVE__', S("fav_remove"))
            .replace('__FAV_ORIGIN_LABELS__',
                     json.dumps(origin_labels, ensure_ascii=False))
            .replace('__FAV_PEAK_FMT__',
                     json.dumps(S("fav_meta_peak", d='{d}'), ensure_ascii=False))
            .replace('__FAV_UNRANKED__',
                     json.dumps(S("fav_meta_unranked"), ensure_ascii=False))
            .replace('__FAV_GIRL__',
                     json.dumps(loc_singular('F').capitalize(), ensure_ascii=False))
            .replace('__FAV_BOY__',
                     json.dumps(loc_singular('M').capitalize(), ensure_ascii=False)))


COMPARE_SCRIPT = """
    <script>
    (function() {
        var form = document.getElementById('cmp-form');
        if (!form) return;
        var PREFIX = '__PREFIX__';
        var L_LATEST = __LATEST_YEAR__;
        var L_TOTAL = '__L_TOTAL__';
        var L_PEAK = '__L_PEAK__';
        var L_RANK = '__L_RANK__';
        var L_GIRLS = '__L_GIRLS__';
        var L_BOYS = '__L_BOYS__';
        var L_CHART = '__L_CHART__';

        var aIn = document.getElementById('cmp-a');
        var bIn = document.getElementById('cmp-b');
        var acA = document.getElementById('cmp-ac-a');
        var acB = document.getElementById('cmp-ac-b');
        var loadingEl = document.getElementById('cmp-loading');
        var errorEl = document.getElementById('cmp-error');
        var resultEl = document.getElementById('cmp-result');

        var INDEX = null;
        function loadIndex() {
            if (INDEX) return Promise.resolve(INDEX);
            return fetch(PREFIX + '/name-index.json')
                .then(function(r) { return r.json(); })
                .then(function(d) { INDEX = d.pages || []; return INDEX; });
        }

        function fmt(n) { return Number(n).toLocaleString(); }

        function attachAutocomplete(input, ac) {
            var sel = -1;
            var items = [];
            function render(matches) {
                while (ac.firstChild) ac.removeChild(ac.firstChild);
                items = matches;
                if (!matches.length) { ac.style.display = 'none'; return; }
                ac.style.display = '';
                matches.forEach(function(slug, i) {
                    var d = document.createElement('div');
                    d.textContent = slug.replace(/-/g, ' ').replace(/\\b\\w/g, function(c) { return c.toUpperCase(); });
                    d.setAttribute('data-slug', slug);
                    if (i === sel) d.className = 'sel';
                    d.addEventListener('mousedown', function(e) {
                        e.preventDefault();
                        input.value = slug;
                        ac.style.display = 'none';
                    });
                    ac.appendChild(d);
                });
            }
            function search() {
                var q = input.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-');
                if (!q || !INDEX) { ac.style.display = 'none'; return; }
                var starts = [], contains = [];
                for (var i = 0; i < INDEX.length && starts.length + contains.length < 8; i++) {
                    var s = INDEX[i];
                    if (s.indexOf(q) === 0) starts.push(s);
                    else if (s.indexOf(q) > 0) contains.push(s);
                }
                sel = -1;
                render(starts.concat(contains).slice(0, 8));
            }
            input.addEventListener('input', function() { loadIndex().then(search); });
            input.addEventListener('focus', function() { loadIndex().then(search); });
            input.addEventListener('blur', function() { setTimeout(function() { ac.style.display = 'none'; }, 150); });
            input.addEventListener('keydown', function(e) {
                if (ac.style.display === 'none') return;
                if (e.key === 'ArrowDown') { sel = (sel + 1) % items.length; render(items); e.preventDefault(); }
                else if (e.key === 'ArrowUp') { sel = (sel - 1 + items.length) % items.length; render(items); e.preventDefault(); }
                else if (e.key === 'Enter' && sel >= 0) { input.value = items[sel]; ac.style.display = 'none'; e.preventDefault(); }
                else if (e.key === 'Escape') { ac.style.display = 'none'; }
            });
        }
        attachAutocomplete(aIn, acA);
        attachAutocomplete(bIn, acB);

        function slug(s) {
            return (s || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
        }

        function fetchName(s) {
            return fetch(PREFIX + '/name-data/' + s + '.json')
                .then(function(r) { if (!r.ok) throw new Error('404'); return r.json(); });
        }

        var chart = null;
        function render(a, b) {
            var COLORS = ['#149E91', '#FF6B5C'];
            function fillCard(card, data, color) {
                while (card.firstChild) card.removeChild(card.firstChild);
                var h = document.createElement('h2');
                var dot = document.createElement('span');
                dot.className = 'cmp-dot';
                dot.style.background = color;
                h.appendChild(dot);
                var link = document.createElement('a');
                link.href = PREFIX + '/name/' + slug(data.n) + '.html';
                link.textContent = data.n;
                link.style.color = '#1B2440';
                link.style.textDecoration = 'none';
                h.appendChild(link);
                card.appendChild(h);
                var sub = document.createElement('p');
                sub.style.color = '#7f8c8d';
                sub.style.margin = '0 0 0.5rem';
                sub.textContent = data.d === 'F' ? L_GIRLS : L_BOYS;
                card.appendChild(sub);
                var stats = document.createElement('div');
                stats.className = 'cmp-stats';
                function row(lbl, val) {
                    var r = document.createElement('div');
                    var l = document.createElement('span'); l.className = 'lbl'; l.textContent = lbl;
                    var v = document.createElement('span'); v.className = 'val'; v.textContent = val;
                    r.appendChild(l); r.appendChild(v); stats.appendChild(r);
                }
                row(L_TOTAL, fmt(data.ft + data.mt));
                row(L_PEAK, data.py + ' (' + fmt(data.p) + ')');
                row(L_RANK.replace('{year}', L_LATEST), data.lr ? '#' + fmt(data.lr) : '—');
                card.appendChild(stats);
            }
            fillCard(document.getElementById('cmp-card-a'), a, COLORS[0]);
            fillCard(document.getElementById('cmp-card-b'), b, COLORS[1]);

            var allYears = {};
            a.y.forEach(function(y) { allYears[y] = true; });
            b.y.forEach(function(y) { allYears[y] = true; });
            var years = Object.keys(allYears).map(Number).sort(function(x, y) { return x - y; });
            function aligned(data) {
                var map = {};
                for (var i = 0; i < data.y.length; i++) map[data.y[i]] = data.c[i];
                return years.map(function(y) { return map[y] != null ? map[y] : null; });
            }
            if (chart) chart.destroy();
            var ctx = document.getElementById('cmpChart').getContext('2d');
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: years,
                    datasets: [
                        {label: a.n, data: aligned(a), borderColor: COLORS[0], backgroundColor: 'rgba(20,158,145,0.10)', fill: false, tension: 0.2, pointRadius: 0, borderWidth: 2, spanGaps: true},
                        {label: b.n, data: aligned(b), borderColor: COLORS[1], backgroundColor: 'rgba(255,107,92,0.10)', fill: false, tension: 0.2, pointRadius: 0, borderWidth: 2, spanGaps: true}
                    ]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: true }, tooltip: { mode: 'index', intersect: false } },
                    scales: { y: { beginAtZero: true, title: { display: true, text: L_CHART } } }
                }
            });
            resultEl.style.display = '';
            errorEl.style.display = 'none';
            loadingEl.style.display = 'none';
        }

        function go(sa, sb) {
            loadingEl.style.display = '';
            resultEl.style.display = 'none';
            errorEl.style.display = 'none';
            Promise.all([fetchName(sa), fetchName(sb)])
                .then(function(both) { render(both[0], both[1]); })
                .catch(function() {
                    loadingEl.style.display = 'none';
                    errorEl.style.display = '';
                });
        }

        // Tell search engines not to index query-param variants.
        function setNoindex() {
            var m = document.createElement('meta');
            m.name = 'robots'; m.content = 'noindex';
            document.head.appendChild(m);
        }

        form.addEventListener('submit', function(e) {
            e.preventDefault();
            var sa = slug(aIn.value), sb = slug(bIn.value);
            if (!sa || !sb) return;
            var url = window.location.pathname + '?a=' + sa + '&b=' + sb;
            history.replaceState(null, '', url);
            setNoindex();
            go(sa, sb);
        });

        var qs = new URLSearchParams(window.location.search);
        var qa = qs.get('a'), qb = qs.get('b');
        if (qa) aIn.value = qa;
        if (qb) bIn.value = qb;
        if (qa && qb) { setNoindex(); go(slug(qa), slug(qb)); }
    })();
    </script>"""


def compare_script() -> str:
    return (COMPARE_SCRIPT
            .replace('__PREFIX__', PREFIX)
            .replace('__LATEST_YEAR__', str(LATEST_YEAR))
            .replace('__L_TOTAL__', S("compare_stat_total"))
            .replace('__L_PEAK__', S("compare_stat_peak"))
            .replace('__L_RANK__', S("compare_stat_latest_rank", year='{year}'))
            .replace('__L_GIRLS__', loc_label_cap('F'))
            .replace('__L_BOYS__', loc_label_cap('M'))
            .replace('__L_CHART__', S("chart_y_axis")))


WORKS_WITH_SCRIPT = """
    <script>
    (function() {
        var form = document.getElementById('ww-form');
        if (!form) return;
        var PREFIX = '__PREFIX__';
        var L_GIRLS = '__L_GIRLS__';
        var L_BOYS = '__L_BOYS__';
        var L_SCORE = '__L_SCORE__';
        var L_RESULT_FOR = '__L_RESULT_FOR__';
        var L_LOADING = '__L_LOADING__';

        var input = document.getElementById('ww-input');
        var loadingEl = document.getElementById('ww-loading');
        var emptyEl = document.getElementById('ww-empty');
        var resultEl = document.getElementById('ww-result');
        var headerEl = document.getElementById('ww-result-header');
        var listEl = document.getElementById('ww-result-list');
        var tabs = document.querySelectorAll('.ww-tab');
        var activeSex = 'all';

        var META = null;
        function loadMeta() {
            if (META) return Promise.resolve(META);
            loadingEl.style.display = '';
            return fetch(PREFIX + '/name-meta.json')
                .then(function(r) { return r.json(); })
                .then(function(d) { META = d; loadingEl.style.display = 'none'; return META; });
        }

        // Order matches the python emit: [first, last2, syll, dom, peak_dec, rank, origin]
        var IDX_FIRST = 0, IDX_LAST2 = 1, IDX_SYLL = 2, IDX_DOM = 3, IDX_RANK = 5, IDX_ORIGIN = 6;

        // Light-touch surname-origin detector. Heuristic on prefix/suffix only —
        // when nothing matches we return '' and skip the origin bonus.
        function detectSurnameOrigin(raw) {
            var f = fold(raw);
            if (!f) return '';
            // Apostrophe-prefixed Irish (O'Brien, O'Connor) — check before folding.
            if (/^o['’]/i.test(raw.trim())) return 'irish';
            // Mc / Mac prefixes
            if (/^mc/.test(f)) return 'irish';
            if (/^mac/.test(f) && f.length > 4) return 'scottish';
            // Continental prefixes — only when they appear as a separate token
            if (/^von /i.test(raw.trim())) return 'german';
            if (/^van /i.test(raw.trim())) return 'dutch';
            // Suffix-based — order matters: check longest first
            if (/(opoulos|akis|idis|adis)$/.test(f)) return 'greek';
            if (/(escu|eanu)$/.test(f)) return 'romanian';
            if (/(enko|chenko|chuk)$/.test(f)) return 'ukrainian';
            if (/(sdottir|sson|ssen)$/.test(f)) return 'scandinavian';
            if (/(ski|sky|cki|wicz|czyk)$/.test(f)) return 'polish';
            if (/(ovich|evich|ovsky|evsky)$/.test(f)) return 'russian';
            if (/(ucci|ello|etti|otti|elli|oni|ini)$/.test(f) && f.length > 4) return 'italian';
            if (/(ault|eaux|eau|oux|aud)$/.test(f)) return 'french';
            if (/(stein|berg|mann|bach|burg|feld|haus|thal)$/.test(f)) return 'german';
            if (/(ez)$/.test(f) && f.length > 3) return 'spanish';
            if (/(ov|ev|in)$/.test(f) && f.length > 5) return 'russian';
            return '';
        }
        var PAGE_SIZE = 12;
        var currentResults = [];
        var shown = 0;

        function fold(s) {
            return (s || '').toLowerCase()
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^a-z]/g, '');
        }
        function countSyllables(s) {
            var f = fold(s);
            if (!f) return 1;
            var n = 0, prev = false;
            for (var i = 0; i < f.length; i++) {
                var v = 'aeiouy'.indexOf(f[i]) >= 0;
                if (v && !prev) n++;
                prev = v;
            }
            if (f.slice(-1) === 'e' && n > 1 && f.slice(-2) !== 'le') n--;
            return Math.max(1, n);
        }
        var VOWELS = {a:1, e:1, i:1, o:1, u:1, y:1};

        function score(name, m, lastFold, lastFirst, lastLast2, lastSyll, surnameOrigin) {
            // m = [first, last2, syll, dom, peak_dec, rank, origin]
            var nameFirst = m[IDX_FIRST];
            var nameLast2 = m[IDX_LAST2];
            var nameLast = nameLast2.slice(-1);
            var nameSyll = m[IDX_SYLL];
            var s = 70;

            // Alliteration / shared starting consonant: -15
            if (nameFirst === lastFirst) {
                s -= VOWELS[nameFirst] ? 6 : 15;
            }
            // Boundary repeat (last letter of name == first letter of surname): -12
            if (nameLast === lastFirst) {
                s -= VOWELS[nameLast] ? 5 : 12;
            }
            // Consonant cluster at boundary: -6
            if (!VOWELS[nameLast] && !VOWELS[lastFirst] && nameLast !== lastFirst) {
                s -= 6;
            }
            // Ending rhyme (same last 2 letters): -10
            if (nameLast2 === lastLast2) s -= 10;

            // Rhythm: reward syllable contrast
            var diff = Math.abs(nameSyll - lastSyll);
            if (diff === 0 && nameSyll <= 2) s -= 8;
            else if (diff === 0) s += 0;
            else if (diff === 1) s += 4;
            else s += 9;

            // Slight bonus for 2-3 syllable first names — they read best with most surnames
            if (nameSyll === 2 || nameSyll === 3) s += 2;

            // Familiarity nudge: well-known names get a small bonus so the meter
            // actually shows variance and the top isn't a flat tie of obscure
            // alphabetical clusters. Capped at +5 so it doesn't overpower phonetics.
            var rank = m[IDX_RANK];
            if (rank) {
                if (rank <= 50) s += 5;
                else if (rank <= 200) s += 4;
                else if (rank <= 500) s += 3;
                else if (rank <= 1000) s += 2;
                else if (rank <= 3000) s += 1;
            }

            // Same-origin nudge: when we could infer the surname's etymology
            // (O'Brien → Irish, Schmidt → German, …), gently boost first names
            // with the same origin. Capped at +6 so it never beats phonetics.
            if (surnameOrigin && m[IDX_ORIGIN] === surnameOrigin) s += 6;

            return s;
        }

        function display(slug) {
            return slug.replace(/-/g, ' ').replace(/\\b\\w/g, function(c) { return c.toUpperCase(); });
        }

        function run() {
            var raw = input.value.trim();
            if (!raw) { emptyEl.style.display = ''; resultEl.style.display = 'none'; return; }
            emptyEl.style.display = 'none';
            loadMeta().then(function(meta) {
                var lastFold = fold(raw);
                if (!lastFold) { emptyEl.style.display = ''; resultEl.style.display = 'none'; return; }
                var lastFirst = lastFold[0];
                var lastLast2 = lastFold.slice(-2);
                var lastSyll = countSyllables(raw);
                var surnameOrigin = detectSurnameOrigin(raw);

                var rows = [];
                for (var slug in meta) {
                    var m = meta[slug];
                    if (activeSex !== 'all' && m[IDX_DOM] !== activeSex) continue;
                    if (slug === lastFold) continue;
                    rows.push([score(slug, m, lastFold, lastFirst, lastLast2, lastSyll, surnameOrigin), slug, m]);
                }
                rows.sort(function(a, b) {
                    if (b[0] !== a[0]) return b[0] - a[0];
                    // Tie-break by current popularity so well-known names surface
                    // instead of A-name clusters from the alphabetical fall-through.
                    var ra = a[2][IDX_RANK] || 99999, rb = b[2][IDX_RANK] || 99999;
                    if (ra !== rb) return ra - rb;
                    return a[1] < b[1] ? -1 : 1;
                });
                currentResults = rows.slice(0, 60);
                shown = 0;

                headerEl.textContent = L_RESULT_FOR.replace('{surname}',
                    raw.replace(/\\b\\w/g, function(c) { return c.toUpperCase(); }));
                while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
                renderMore();
                resultEl.style.display = '';
            });
        }

        function renderMore() {
            var next = currentResults.slice(shown, shown + PAGE_SIZE);
            next.forEach(function(r) {
                var slug = r[1], m = r[2];
                var card = document.createElement('a');
                card.className = 'ww-card';
                card.href = PREFIX + '/name/' + slug + '.html';
                var nm = document.createElement('span');
                nm.className = 'ww-name';
                nm.textContent = display(slug);
                var meta2 = document.createElement('span');
                meta2.className = 'ww-meta';
                meta2.textContent = (m[IDX_DOM] === 'F' ? L_GIRLS : L_BOYS) + ' · ' + m[IDX_SYLL] + ' syll';
                card.appendChild(nm); card.appendChild(meta2);
                listEl.appendChild(card);
            });
            shown += next.length;
            var moreBtn = document.getElementById('ww-more');
            if (moreBtn) moreBtn.style.display = shown < currentResults.length ? '' : 'none';
        }
        var moreBtn = document.getElementById('ww-more');
        if (moreBtn) moreBtn.addEventListener('click', renderMore);

        form.addEventListener('submit', function(e) { e.preventDefault(); run(); });
        var debounce = null;
        input.addEventListener('input', function() {
            clearTimeout(debounce);
            debounce = setTimeout(run, 220);
        });
        tabs.forEach(function(t) {
            t.addEventListener('click', function() {
                tabs.forEach(function(x) { x.classList.remove('is-active'); });
                t.classList.add('is-active');
                activeSex = t.getAttribute('data-sex');
                if (input.value.trim()) run();
            });
        });

        // Pre-fill from ?s= for shareable links
        var qs = new URLSearchParams(window.location.search);
        var qsn = qs.get('s');
        if (qsn) { input.value = qsn; run(); }
    })();
    </script>"""


def works_with_script() -> str:
    return (WORKS_WITH_SCRIPT
            .replace('__PREFIX__', PREFIX)
            .replace('__L_GIRLS__', loc_label_cap('F'))
            .replace('__L_BOYS__', loc_label_cap('M'))
            .replace('__L_SCORE__', S("ww_score"))
            .replace('__L_RESULT_FOR__', S("ww_result_for", surname='{surname}'))
            .replace('__L_LOADING__', S("ww_loading")))


PICKER_SCRIPT = """
    <script>
    (function() {
        var root = document.getElementById('picker-root');
        if (!root) return;
        var CC = '__ACTIVE_CC__';
        var PREFIX = '__PREFIX__';
        var FAV_KEY = 'nc-favorites-' + CC;
        var L_GIRLS = __L_GIRLS__;
        var L_BOYS = __L_BOYS__;
        var L_PEAK = __L_PEAK__;
        var L_RANK = __L_RANK__;
        var L_OFFCHART = __L_OFFCHART__;
        var L_SAVED = __L_SAVED__;
        var L_EXHAUSTED = __L_EXHAUSTED__;
        var L_MATCH_ONE = __L_MATCH_ONE__;
        var L_MATCH_MANY = __L_MATCH_MANY__;
        var L_MATCH_NONE = __L_MATCH_NONE__;
        var L_RANDOM_EMPTY = __L_RANDOM_EMPTY__;
        var L_SHARE_DONE = __L_SHARE_DONE__;

        var IDX_FIRST = 0, IDX_SYLL = 2, IDX_DOM = 3, IDX_PEAK = 4, IDX_RANK = 5, IDX_ORIGIN = 6;

        function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

        var META = null, SLUGS = null;
        function loadMeta() {
            if (META) return Promise.resolve();
            return fetch(PREFIX + '/name-meta.json')
                .then(function(r) { return r.json(); })
                .then(function(d) { META = d; SLUGS = Object.keys(d); });
        }

        function favs() {
            try { return JSON.parse(localStorage.getItem(FAV_KEY) || '[]'); }
            catch (e) { return []; }
        }
        function saveFavs(list) {
            try { localStorage.setItem(FAV_KEY, JSON.stringify(list)); } catch (e) {}
            var n = list.length;
            var els = document.querySelectorAll('.fav-nav-count');
            for (var i = 0; i < els.length; i++) {
                els[i].textContent = n ? ' (' + n + ')' : '';
            }
        }
        function addFav(slug, name) {
            var list = favs();
            for (var i = 0; i < list.length; i++) if (list[i].slug === slug) return false;
            list.push({slug: slug, name: name});
            saveFavs(list);
            return true;
        }
        function removeFav(slug) {
            var list = favs();
            for (var i = 0; i < list.length; i++) {
                if (list[i].slug === slug) { list.splice(i, 1); saveFavs(list); return; }
            }
        }

        function display(slug) {
            return slug.replace(/-/g, ' ').replace(/\\b\\w/g, function(c) { return c.toUpperCase(); });
        }
        function metaLine(m) {
            var parts = [m[IDX_DOM] === 'F' ? L_GIRLS : L_BOYS];
            parts.push(L_PEAK.replace('{d}', m[IDX_PEAK]));
            parts.push(m[IDX_RANK] ? L_RANK.replace('{rank}', m[IDX_RANK]) : L_OFFCHART);
            return parts.join(' · ');
        }

        var tabs = root.querySelectorAll('.pk-tab');
        var panels = root.querySelectorAll('.pk-panel');
        tabs.forEach(function(t) {
            t.addEventListener('click', function() {
                var mode = t.getAttribute('data-mode');
                tabs.forEach(function(x) { x.classList.toggle('is-active', x === t); });
                panels.forEach(function(p) { p.style.display = p.getAttribute('data-mode') === mode ? '' : 'none'; });
                if (mode === 'swipe' && !swipeStarted) startSwipe();
            });
        });

        // ---- SWIPE MODE ----
        var swipeStarted = false;
        var swipeDeck = [];
        var swipeHistory = [];
        var swipeSexFilter = 'all';
        var swipeEraFilter = 'all';
        var cardEl, statusEl, exhaustedEl;

        function rebuildDeck() {
            var pool = SLUGS.filter(function(s) {
                var m = META[s];
                if (swipeSexFilter !== 'all' && m[IDX_DOM] !== swipeSexFilter) return false;
                if (swipeEraFilter !== 'all' && m[IDX_PEAK] !== Number(swipeEraFilter)) return false;
                return true;
            });
            for (var i = pool.length - 1; i > 0; i--) {
                var j = Math.floor(Math.random() * (i + 1));
                var t = pool[i]; pool[i] = pool[j]; pool[j] = t;
            }
            swipeDeck = pool.slice(0, 200);
            swipeHistory = [];
        }

        function renderCard() {
            clear(cardEl);
            statusEl.textContent = '';
            if (!swipeDeck.length) {
                exhaustedEl.style.display = '';
                cardEl.style.display = 'none';
                return;
            }
            exhaustedEl.style.display = 'none';
            cardEl.style.display = '';
            var slug = swipeDeck[swipeDeck.length - 1];
            var m = META[slug];
            var name = display(slug);

            var inner = document.createElement('div');
            inner.className = 'pk-card-inner';
            var h = document.createElement('h2');
            var link = document.createElement('a');
            link.href = PREFIX + '/name/' + slug + '.html';
            link.textContent = name;
            link.target = '_blank';
            link.rel = 'noopener';
            h.appendChild(link);
            inner.appendChild(h);
            var meta = document.createElement('p');
            meta.className = 'pk-card-meta';
            meta.textContent = metaLine(m);
            inner.appendChild(meta);
            cardEl.appendChild(inner);
        }

        function popCard(action) {
            if (!swipeDeck.length) return;
            var slug = swipeDeck.pop();
            swipeHistory.push({slug: slug, action: action});
            if (action === 'save') {
                addFav(slug, display(slug));
                statusEl.textContent = L_SAVED;
            } else {
                statusEl.textContent = '';
            }
            renderCard();
        }

        function undoCard() {
            if (!swipeHistory.length) return;
            var last = swipeHistory.pop();
            if (last.action === 'save') removeFav(last.slug);
            swipeDeck.push(last.slug);
            renderCard();
        }

        function startSwipe() {
            swipeStarted = true;
            cardEl = root.querySelector('#pk-card');
            statusEl = root.querySelector('#pk-status');
            exhaustedEl = root.querySelector('#pk-exhausted');
            rebuildDeck();
            renderCard();
            attachSwipeGestures();
        }

        root.querySelectorAll('.pk-swipe-sex').forEach(function(el) {
            el.addEventListener('click', function() {
                root.querySelectorAll('.pk-swipe-sex').forEach(function(x) { x.classList.remove('is-active'); });
                el.classList.add('is-active');
                swipeSexFilter = el.getAttribute('data-sex');
                rebuildDeck();
                renderCard();
            });
        });
        var eraSel = root.querySelector('#pk-swipe-era');
        if (eraSel) {
            eraSel.addEventListener('change', function() {
                swipeEraFilter = eraSel.value;
                rebuildDeck();
                renderCard();
            });
        }
        root.querySelector('#pk-skip').addEventListener('click', function() { popCard('skip'); });
        root.querySelector('#pk-save').addEventListener('click', function() { popCard('save'); });
        root.querySelector('#pk-undo').addEventListener('click', undoCard);
        root.querySelector('#pk-restart').addEventListener('click', function() {
            rebuildDeck(); renderCard();
        });

        function attachSwipeGestures() {
            var dragStartX = null;
            ['mousedown', 'touchstart'].forEach(function(evt) {
                cardEl.addEventListener(evt, function(e) {
                    var p = e.touches ? e.touches[0] : e;
                    dragStartX = p.clientX;
                }, {passive: true});
            });
            ['mouseup', 'touchend'].forEach(function(evt) {
                cardEl.addEventListener(evt, function(e) {
                    if (dragStartX == null) return;
                    var p = e.changedTouches ? e.changedTouches[0] : e;
                    var dx = p.clientX - dragStartX;
                    dragStartX = null;
                    cardEl.style.transform = '';
                    if (dx > 80) popCard('save');
                    else if (dx < -80) popCard('skip');
                }, {passive: true});
            });
            ['mousemove', 'touchmove'].forEach(function(evt) {
                cardEl.addEventListener(evt, function(e) {
                    if (dragStartX == null) return;
                    var p = e.touches ? e.touches[0] : e;
                    var dx = p.clientX - dragStartX;
                    cardEl.style.transform = 'translateX(' + dx + 'px) rotate(' + (dx / 25) + 'deg)';
                }, {passive: true});
            });
        }

        // ---- FILTER MODE ----
        var filterCount = root.querySelector('#pk-filter-count');
        var filterResults = root.querySelector('#pk-filter-results');
        function runFilter() {
            var sex = root.querySelector('input[name=pk-f-sex]:checked').value;
            var sylls = [];
            root.querySelectorAll('input[name=pk-f-syll]:checked').forEach(function(c) { sylls.push(Number(c.value)); });
            var era = root.querySelector('#pk-f-era').value;
            var letter = root.querySelector('#pk-f-letter').value;
            var rank = root.querySelector('#pk-f-rank').value;
            var originSel = root.querySelector('#pk-f-origin');
            var origin = originSel ? originSel.value : 'all';

            var matches = [];
            for (var i = 0; i < SLUGS.length; i++) {
                var s = SLUGS[i], m = META[s];
                if (sex !== 'all' && m[IDX_DOM] !== sex) continue;
                if (sylls.length) {
                    var syll = m[IDX_SYLL]; var key = syll >= 4 ? 4 : syll;
                    if (sylls.indexOf(key) < 0) continue;
                }
                if (era !== 'all' && m[IDX_PEAK] !== Number(era)) continue;
                if (letter !== 'all' && m[IDX_FIRST] !== letter) continue;
                var r = m[IDX_RANK];
                if (rank === 'top100' && (!r || r > 100)) continue;
                if (rank === 'top1000' && (!r || r > 1000)) continue;
                if (rank === 'rare' && r) continue;
                if (origin !== 'all' && m[IDX_ORIGIN] !== origin) continue;
                matches.push([s, m]);
            }
            matches.sort(function(a, b) {
                var ra = a[1][IDX_RANK] || 99999, rb = b[1][IDX_RANK] || 99999;
                if (ra !== rb) return ra - rb;
                return a[0] < b[0] ? -1 : 1;
            });

            if (!matches.length) {
                filterCount.textContent = L_MATCH_NONE;
                clear(filterResults);
                return;
            }
            filterCount.textContent = matches.length === 1
                ? L_MATCH_ONE
                : L_MATCH_MANY.replace('{n}', matches.length.toLocaleString());
            renderGrid(filterResults, matches.slice(0, 120));
        }

        function renderGrid(container, items) {
            clear(container);
            items.forEach(function(r) {
                var slug = r[0], m = r[1];
                var card = document.createElement('a');
                card.className = 'pk-grid-card';
                card.href = PREFIX + '/name/' + slug + '.html';
                var nm = document.createElement('span'); nm.className = 'pk-grid-name';
                nm.textContent = display(slug); card.appendChild(nm);
                var meta = document.createElement('span'); meta.className = 'pk-grid-meta';
                meta.textContent = metaLine(m); card.appendChild(meta);
                container.appendChild(card);
            });
        }

        root.querySelectorAll('.pk-filter-input').forEach(function(el) {
            el.addEventListener('change', runFilter);
        });

        // ---- RANDOM MODE ----
        var randResults = root.querySelector('#pk-random-results');
        function runRandom(pushState) {
            var sex = root.querySelector('input[name=pk-r-sex]:checked').value;
            var era = root.querySelector('#pk-r-era').value;
            var n = Number(root.querySelector('#pk-r-count').value);

            var pool = SLUGS.filter(function(s) {
                var m = META[s];
                if (sex !== 'all' && m[IDX_DOM] !== sex) return false;
                if (era !== 'all' && m[IDX_PEAK] !== Number(era)) return false;
                return true;
            });
            for (var i = pool.length - 1; i > 0; i--) {
                var j = Math.floor(Math.random() * (i + 1));
                var t = pool[i]; pool[i] = pool[j]; pool[j] = t;
            }
            var picks = pool.slice(0, n);
            clear(randResults);
            if (!picks.length) {
                var p = document.createElement('p');
                p.textContent = L_RANDOM_EMPTY;
                p.style.color = '#5B6678';
                randResults.appendChild(p);
                return;
            }
            renderGrid(randResults, picks.map(function(s) { return [s, META[s]]; }));

            if (pushState) {
                var qs = 'sex=' + sex + '&era=' + era + '&n=' + n;
                history.replaceState(null, '', window.location.pathname + '?' + qs);
            }
        }
        root.querySelector('#pk-r-go').addEventListener('click', function(e) { e.preventDefault(); runRandom(true); });
        root.querySelector('#pk-r-again').addEventListener('click', function() { runRandom(true); });
        root.querySelector('#pk-r-share').addEventListener('click', function() {
            if (navigator.clipboard) navigator.clipboard.writeText(window.location.href);
            var done = root.querySelector('#pk-r-share-done');
            done.style.display = '';
            setTimeout(function() { done.style.display = 'none'; }, 1800);
        });

        loadMeta().then(function() {
            var decades = {};
            for (var i = 0; i < SLUGS.length; i++) decades[META[SLUGS[i]][IDX_PEAK]] = true;
            var sortedDecades = Object.keys(decades).map(Number).sort(function(a, b) { return a - b; });
            ['pk-swipe-era', 'pk-f-era', 'pk-r-era'].forEach(function(id) {
                var sel = root.querySelector('#' + id);
                if (!sel) return;
                sortedDecades.forEach(function(d) {
                    var o = document.createElement('option');
                    o.value = d; o.textContent = d + 's';
                    sel.appendChild(o);
                });
            });
            var qs = new URLSearchParams(window.location.search);
            if (qs.get('sex')) {
                var rb = root.querySelector('input[name=pk-r-sex][value=' + qs.get('sex') + ']');
                if (rb) rb.checked = true;
            }
            if (qs.get('era')) {
                var es = root.querySelector('#pk-r-era');
                if (es) es.value = qs.get('era');
            }
            if (qs.get('n')) {
                var ns = root.querySelector('#pk-r-count');
                if (ns) ns.value = qs.get('n');
            }
            if (qs.get('sex') || qs.get('era') || qs.get('n')) {
                root.querySelector('.pk-tab[data-mode=random]').click();
                runRandom(false);
            } else {
                root.querySelector('.pk-tab[data-mode=swipe]').click();
            }
            runFilter();
        });
    })();
    </script>"""


def picker_script() -> str:
    def js(v: str) -> str:
        return json.dumps(v)
    return (PICKER_SCRIPT
            .replace('__ACTIVE_CC__', ACTIVE_CC)
            .replace('__PREFIX__', PREFIX)
            .replace('__L_GIRLS__', js(loc_label_cap('F')))
            .replace('__L_BOYS__', js(loc_label_cap('M')))
            .replace('__L_PEAK__', js(S("picker_peak_decade", d='{d}')))
            .replace('__L_RANK__', js(S("picker_currently_rank", rank='{rank}')))
            .replace('__L_OFFCHART__', js(S("picker_not_ranked")))
            .replace('__L_SAVED__', js(S("picker_swipe_saved")))
            .replace('__L_EXHAUSTED__', js(S("picker_swipe_exhausted")))
            .replace('__L_MATCH_ONE__', js(S("picker_filter_match_one")))
            .replace('__L_MATCH_MANY__', js(S("picker_filter_match_many", n='{n}')))
            .replace('__L_MATCH_NONE__', js(S("picker_filter_match_none")))
            .replace('__L_RANDOM_EMPTY__', js(S("picker_random_empty")))
            .replace('__L_SHARE_DONE__', js(S("picker_random_share_done"))))


SIBLING_SCRIPT = """
    <script>
    (function() {
        var form = document.getElementById('sib-form');
        if (!form) return;
        var PREFIX = __PREFIX__;
        var L_GIRLS = __L_GIRLS__;
        var L_BOYS = __L_BOYS__;
        var L_RESULT_FOR = __L_RESULT_FOR__;
        var L_RESULT_FOR_SET = __L_RESULT_FOR_SET__;
        var L_UNKNOWN = __L_UNKNOWN__;
        var L_PEAK = __L_PEAK__;
        var L_SHOW_MORE = __L_SHOW_MORE__;
        var PAGE_SIZE = 12;
        var MAX_SLOTS = 3;

        var IDX_FIRST = 0, IDX_LAST2 = 1, IDX_SYLL = 2, IDX_DOM = 3, IDX_PEAK = 4, IDX_RANK = 5, IDX_ORIGIN = 6;
        var ORIGIN_LABELS = __ORIGIN_LABELS__;

        var rows = Array.prototype.slice.call(document.querySelectorAll('.sib-row'));
        var addBtn = document.getElementById('sib-add');
        var resultEl = document.getElementById('sib-result');
        var headerEl = document.getElementById('sib-header');
        var listEl = document.getElementById('sib-list');
        var moreBtn = document.getElementById('sib-more');
        var emptyEl = document.getElementById('sib-empty');
        var noteEl = document.getElementById('sib-note');
        var sexTabs = document.querySelectorAll('.sib-sex-tab');
        var targetSex = 'all';
        var currentResults = [];
        var shown = 0;

        function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

        var META = null, INDEX = null;
        function loadData() {
            if (META && INDEX) return Promise.resolve();
            return Promise.all([
                fetch(PREFIX + '/name-meta.json').then(function(r) { return r.json(); }),
                fetch(PREFIX + '/name-index.json').then(function(r) { return r.json(); })
            ]).then(function(both) { META = both[0]; INDEX = both[1].pages || []; });
        }

        function slugify(s) {
            return (s || '').toLowerCase()
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
        }
        function display(slug) {
            return slug.replace(/-/g, ' ').replace(/\\b\\w/g, function(c) { return c.toUpperCase(); });
        }
        function countSyllables(s) {
            var f = (s || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/[^a-z]/g, '');
            if (!f) return 1;
            var n = 0, prev = false;
            for (var i = 0; i < f.length; i++) {
                var v = 'aeiouy'.indexOf(f[i]) >= 0;
                if (v && !prev) n++;
                prev = v;
            }
            if (f.slice(-1) === 'e' && n > 1 && f.slice(-2) !== 'le') n--;
            return Math.max(1, n);
        }

        // ─── Per-row autocomplete + control wiring ──────────────────────
        function setupRow(row) {
            var input = row.querySelector('.sib-input');
            var ac = row.querySelector('.sib-ac');
            var removeBtn = row.querySelector('.sib-remove');
            var sel = -1, items = [];
            function renderAc(matches) {
                clear(ac); items = matches;
                if (!matches.length) { ac.style.display = 'none'; return; }
                ac.style.display = '';
                matches.forEach(function(slug, i) {
                    var d = document.createElement('div');
                    d.textContent = display(slug);
                    if (i === sel) d.className = 'sel';
                    d.addEventListener('mousedown', function(e) {
                        e.preventDefault();
                        input.value = display(slug);
                        ac.style.display = 'none';
                        run();
                    });
                    ac.appendChild(d);
                });
            }
            function searchAc() {
                var q = slugify(input.value);
                if (!q || !INDEX) { ac.style.display = 'none'; return; }
                var starts = [], contains = [];
                for (var i = 0; i < INDEX.length && starts.length + contains.length < 8; i++) {
                    var s = INDEX[i];
                    if (s.indexOf(q) === 0) starts.push(s);
                    else if (s.indexOf(q) > 0) contains.push(s);
                }
                sel = -1;
                renderAc(starts.concat(contains).slice(0, 8));
            }
            input.addEventListener('input', function() { loadData().then(searchAc); });
            input.addEventListener('focus', function() { loadData().then(searchAc); });
            input.addEventListener('blur', function() { setTimeout(function() { ac.style.display = 'none'; }, 150); });
            input.addEventListener('keydown', function(e) {
                if (ac.style.display === 'none') return;
                if (e.key === 'ArrowDown') { sel = (sel + 1) % items.length; renderAc(items); e.preventDefault(); }
                else if (e.key === 'ArrowUp') { sel = (sel - 1 + items.length) % items.length; renderAc(items); e.preventDefault(); }
                else if (e.key === 'Enter' && sel >= 0) { input.value = display(items[sel]); ac.style.display = 'none'; e.preventDefault(); run(); }
                else if (e.key === 'Escape') { ac.style.display = 'none'; }
            });
            if (removeBtn) {
                removeBtn.addEventListener('click', function() {
                    input.value = '';
                    row.style.display = 'none';
                    updateAddBtn();
                    if (anyVisibleHasValue()) run();
                });
            }
        }
        rows.forEach(setupRow);

        function nextHiddenRow() {
            for (var i = 0; i < rows.length; i++) {
                if (rows[i].style.display === 'none') return rows[i];
            }
            return null;
        }
        function visibleRows() {
            return rows.filter(function(r) { return r.style.display !== 'none'; });
        }
        function anyVisibleHasValue() {
            return visibleRows().some(function(r) {
                return r.querySelector('.sib-input').value.trim();
            });
        }
        function updateAddBtn() {
            addBtn.disabled = !nextHiddenRow();
        }
        addBtn.addEventListener('click', function() {
            var nxt = nextHiddenRow();
            if (!nxt) return;
            nxt.style.display = '';
            updateAddBtn();
            nxt.querySelector('.sib-input').focus();
        });
        updateAddBtn();

        // ─── Scoring against an aggregate of refs ───────────────────────
        function scoreCand(slug, m, refs) {
            var s = 0;
            var reasons = {};
            var n = refs.length;

            // Era: penalize distance from the closest ref's peak (within a set,
            // a candidate fits if it's near AT LEAST ONE sibling's era).
            var anyPeak = refs.some(function(r) { return r.peak !== null; });
            if (anyPeak) {
                var bestDiff = Infinity;
                refs.forEach(function(r) {
                    if (r.peak !== null) {
                        bestDiff = Math.min(bestDiff, Math.abs(m[IDX_PEAK] - r.peak));
                    }
                });
                if (bestDiff === 0) { s += 22; reasons.era = 1; }
                else if (bestDiff <= 10) { s += 14; reasons.era = 1; }
                else if (bestDiff <= 20) s += 6;
            }

            // Syllable rhythm: compare to AVERAGE syllable count of the set.
            var avgSyll = refs.reduce(function(a, r) { return a + r.syll; }, 0) / n;
            var syllDiff = Math.abs(m[IDX_SYLL] - avgSyll);
            if (syllDiff < 0.5) { s += 8; reasons.rhythm = 1; }
            else if (syllDiff < 1.5) { s += 5; reasons.rhythm = 1; }

            // Initials: penalize matching ANY existing sibling's first letter.
            var matchedFirst = refs.some(function(r) { return r.first === m[IDX_FIRST]; });
            if (matchedFirst) s -= 10;
            else { s += 3; reasons.contrast = 1; }

            // Rhyme penalty against any sibling.
            var matchedLast = refs.some(function(r) { return r.last2 && r.last2 === m[IDX_LAST2]; });
            if (matchedLast) s -= 14;

            // Origin: if all refs share an origin, big bonus when cand matches.
            // Otherwise (mixed origins) we still reward matching one of them.
            var origins = refs.map(function(r) { return r.origin; }).filter(function(o) { return o; });
            if (origins.length === n) {
                var first = origins[0];
                var allSame = origins.every(function(o) { return o === first; });
                if (allSame && m[IDX_ORIGIN] === first) { s += 10; reasons.origin = 1; }
                else if (!allSame && origins.indexOf(m[IDX_ORIGIN]) >= 0) { s += 5; reasons.origin = 1; }
            } else if (n === 1 && refs[0].origin && m[IDX_ORIGIN] === refs[0].origin) {
                s += 8; reasons.origin = 1;
            }

            // Familiarity bonus.
            var rk = m[IDX_RANK];
            if (rk) {
                if (rk <= 50) s += 5;
                else if (rk <= 200) s += 4;
                else if (rk <= 1000) s += 2;
                else s += 1;
            }
            return s;
        }

        function refFor(rawName) {
            var slug = slugify(rawName);
            if (META[slug]) {
                var rm = META[slug];
                return { slug: slug, known: true,
                         first: rm[IDX_FIRST], last2: rm[IDX_LAST2],
                         syll: rm[IDX_SYLL], peak: rm[IDX_PEAK],
                         origin: rm[IDX_ORIGIN] || '' };
            }
            var folded = rawName.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/[^a-z]/g, '');
            return { slug: slug, known: false,
                     first: folded[0] || 'a', last2: folded.slice(-2) || '',
                     syll: countSyllables(rawName), peak: null, origin: '' };
        }

        function run() {
            var raws = visibleRows().map(function(r) {
                return r.querySelector('.sib-input').value.trim();
            }).filter(function(v) { return v; });
            if (!raws.length) {
                emptyEl.style.display = ''; resultEl.style.display = 'none';
                noteEl.style.display = 'none';
                return;
            }
            emptyEl.style.display = 'none';
            loadData().then(function() {
                var refs = raws.map(refFor);
                var refSlugs = {};
                refs.forEach(function(r) { refSlugs[r.slug] = 1; });
                var anyUnknown = refs.some(function(r) { return !r.known; });
                noteEl.style.display = anyUnknown ? '' : 'none';

                var rowsOut = [];
                for (var s in META) {
                    if (refSlugs[s]) continue;
                    var m = META[s];
                    if (targetSex !== 'all' && m[IDX_DOM] !== targetSex) continue;
                    var sc = scoreCand(s, m, refs);
                    rowsOut.push([sc, s, m]);
                }
                rowsOut.sort(function(a, b) {
                    if (b[0] !== a[0]) return b[0] - a[0];
                    var ra = a[2][IDX_RANK] || 99999, rb = b[2][IDX_RANK] || 99999;
                    if (ra !== rb) return ra - rb;
                    return a[1] < b[1] ? -1 : 1;
                });
                currentResults = rowsOut.slice(0, 60);
                shown = 0;

                if (refs.length === 1) {
                    headerEl.textContent = L_RESULT_FOR.replace('{name}', display(refs[0].slug));
                } else {
                    var names = refs.map(function(r) { return display(r.slug); });
                    var joined = names.slice(0, -1).join(', ') + ' + ' + names[names.length - 1];
                    headerEl.textContent = L_RESULT_FOR_SET.replace('{names}', joined);
                }
                clear(listEl);
                renderMore();
                resultEl.style.display = '';

                // Reflect the current names in the URL so the page is shareable.
                var slugsForQs = refs.map(function(r) { return r.slug; }).join(',');
                if (slugsForQs) {
                    history.replaceState(null, '', window.location.pathname + '?names=' + slugsForQs);
                }

                // Refresh the Telegram-share href to point at the current URL.
                var tgLink = document.getElementById('sib-share-tg');
                if (tgLink) {
                    var tgText = headerEl.textContent || 'NameCharted sibling ideas';
                    tgLink.href = 'https://t.me/share/url?url=' +
                        encodeURIComponent(window.location.href) +
                        '&text=' + encodeURIComponent(tgText);
                }
            });
        }

        function renderMore() {
            var next = currentResults.slice(shown, shown + PAGE_SIZE);
            next.forEach(function(r) {
                var cslug = r[1], m = r[2];
                var card = document.createElement('a');
                card.className = 'sib-card';
                card.href = PREFIX + '/name/' + cslug + '.html';
                var nm = document.createElement('span');
                nm.className = 'sib-name';
                nm.textContent = display(cslug);
                card.appendChild(nm);
                var meta = document.createElement('span');
                meta.className = 'sib-meta';
                var parts = [(m[IDX_DOM] === 'F' ? L_GIRLS : L_BOYS), L_PEAK.replace('{d}', m[IDX_PEAK])];
                var olbl = ORIGIN_LABELS[m[IDX_ORIGIN]];
                if (olbl) parts.push(olbl);
                meta.textContent = parts.join(' · ');
                card.appendChild(meta);
                listEl.appendChild(card);
            });
            shown += next.length;
            moreBtn.style.display = shown < currentResults.length ? '' : 'none';
            moreBtn.textContent = L_SHOW_MORE;
        }

        moreBtn.addEventListener('click', renderMore);

        sexTabs.forEach(function(t) {
            t.addEventListener('click', function() {
                sexTabs.forEach(function(x) { x.classList.remove('is-active'); });
                t.classList.add('is-active');
                targetSex = t.getAttribute('data-sex');
                if (anyVisibleHasValue()) run();
            });
        });
        form.addEventListener('submit', function(e) { e.preventDefault(); run(); });

        var shareBtn = document.getElementById('sib-share');
        var shareDone = document.getElementById('sib-share-done');
        if (shareBtn) {
            shareBtn.addEventListener('click', function() {
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(window.location.href);
                }
                shareDone.style.display = '';
                setTimeout(function() { shareDone.style.display = 'none'; }, 1800);
            });
        }

        // Pre-fill from ?names=a,b,c (comma-separated) or legacy ?name=
        var qs = new URLSearchParams(window.location.search);
        var qNames = qs.get('names') || qs.get('name') || '';
        if (qNames) {
            var parts = qNames.split(',').map(function(x) { return x.trim(); }).filter(Boolean).slice(0, MAX_SLOTS);
            parts.forEach(function(n, i) {
                if (i >= rows.length) return;
                rows[i].style.display = '';
                rows[i].querySelector('.sib-input').value = n;
            });
            updateAddBtn();
            loadData().then(run);
        }
    })();
    </script>"""


INITIALS_SCRIPT = """
    <script>
    (function() {
        var form = document.getElementById('in-form');
        if (!form) return;
        var PREFIX = __PREFIX__;
        var SURNAMES = __SURNAMES__;
        var L_NO_MATCH = __L_NO_MATCH__;
        var L_SHARE_DONE = __L_SHARE_DONE__;

        var input = document.getElementById('in-input');
        var resultEl = document.getElementById('in-result');
        var emptyEl = document.getElementById('in-empty');
        var errorEl = document.getElementById('in-error');
        var sexTabs = document.querySelectorAll('.in-sex-tab');
        var firstSex = 'all';

        var META = null, BY_LETTER = null;
        function loadMeta() {
            if (META) return Promise.resolve();
            return fetch(PREFIX + '/name-meta.json')
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    META = d;
                    BY_LETTER = {};
                    Object.keys(d).forEach(function(s) {
                        var letter = d[s][0].toUpperCase();
                        (BY_LETTER[letter] = BY_LETTER[letter] || []).push(s);
                    });
                });
        }

        function display(slug) {
            return slug.replace(/-/g, ' ').replace(/\\b\\w/g, function(c) { return c.toUpperCase(); });
        }
        function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
        function pickRandom(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

        // Index 5 in name-meta is current rank (0 if unranked). Names with a
        // current rank read more naturally — fall back to the full pool only
        // if the ranked pool for this letter is empty.
        function rankedPool(letter, sexFilter) {
            var all = BY_LETTER[letter] || [];
            var filtered = all.filter(function(s) {
                var m = META[s];
                if (!m[5]) return false;
                if (sexFilter && sexFilter !== 'all' && m[3] !== sexFilter) return false;
                return true;
            });
            return filtered.length ? filtered : all;
        }
        function pickFirstNameForLetter(letter, sexFilter) {
            var pool = rankedPool(letter, sexFilter);
            return pool.length ? pickRandom(pool) : null;
        }
        function pickMidNameForLetter(letter) {
            var pool = rankedPool(letter, 'all');
            return pool.length ? pickRandom(pool) : null;
        }
        function pickSurnameForLetter(letter) {
            var pool = SURNAMES[letter] || [];
            return pool.length ? pickRandom(pool) : null;
        }

        function parseInitials(raw) {
            return (raw || '').toUpperCase().replace(/[^A-Z]/g, '').split('').slice(0, 4);
        }

        function run(pushState) {
            errorEl.style.display = 'none';
            var letters = parseInitials(input.value);
            if (letters.length < 2) {
                emptyEl.style.display = '';
                resultEl.style.display = 'none';
                return;
            }
            emptyEl.style.display = 'none';
            loadMeta().then(function() {
                // Validate first-name letter has matches
                var firstPool = BY_LETTER[letters[0]] || [];
                if (firstSex !== 'all') {
                    firstPool = firstPool.filter(function(s) { return META[s][3] === firstSex; });
                }
                if (!firstPool.length) {
                    errorEl.textContent = L_NO_MATCH.replace('{letter}', letters[0]);
                    errorEl.style.display = '';
                    resultEl.style.display = 'none';
                    return;
                }
                clear(resultEl);
                var seen = {};
                var tries = 0;
                while (Object.keys(seen).length < 21 && tries < 130) {
                    tries++;
                    var parts = [];
                    var firstSlug = pickFirstNameForLetter(letters[0], firstSex);
                    parts.push(display(firstSlug));
                    var firstHref = PREFIX + '/name/' + firstSlug + '.html';
                    for (var i = 1; i < letters.length - 1; i++) {
                        var ms = pickMidNameForLetter(letters[i]);
                        parts.push(ms ? display(ms) : letters[i] + '.');
                    }
                    var lastLetter = letters[letters.length - 1];
                    var sn = pickSurnameForLetter(lastLetter);
                    parts.push(sn || (lastLetter + '.'));
                    var combo = parts.join(' ');
                    if (seen[combo]) continue;
                    seen[combo] = true;
                    var li = document.createElement('li');
                    li.className = 'in-combo';
                    var firstSpan = document.createElement('a');
                    firstSpan.href = firstHref;
                    firstSpan.textContent = parts[0];
                    firstSpan.className = 'in-first';
                    li.appendChild(firstSpan);
                    li.appendChild(document.createTextNode(' ' + parts.slice(1).join(' ')));
                    resultEl.appendChild(li);
                }
                resultEl.style.display = '';

                if (pushState) {
                    var qs = 'i=' + letters.join('') + (firstSex !== 'all' ? '&sex=' + firstSex : '');
                    history.replaceState(null, '', window.location.pathname + '?' + qs);
                }
            });
        }

        form.addEventListener('submit', function(e) { e.preventDefault(); run(true); });
        document.getElementById('in-again').addEventListener('click', function() { run(true); });
        document.getElementById('in-share').addEventListener('click', function() {
            if (navigator.clipboard) navigator.clipboard.writeText(window.location.href);
            var done = document.getElementById('in-share-done');
            done.style.display = '';
            setTimeout(function() { done.style.display = 'none'; }, 1800);
        });
        sexTabs.forEach(function(t) {
            t.addEventListener('click', function() {
                sexTabs.forEach(function(x) { x.classList.remove('is-active'); });
                t.classList.add('is-active');
                firstSex = t.getAttribute('data-sex');
                if (input.value.trim()) run(false);
            });
        });

        var qs = new URLSearchParams(window.location.search);
        if (qs.get('i')) {
            input.value = qs.get('i');
            if (qs.get('sex')) {
                var s = qs.get('sex');
                var tab = document.querySelector('.in-sex-tab[data-sex=' + s + ']');
                if (tab) tab.click();
                firstSex = s;
            }
            loadMeta().then(function() { run(false); });
        }
    })();
    </script>"""


def initials_script() -> str:
    if not SURNAMES_BY_CC.get(ACTIVE_CC):
        return ''
    return (INITIALS_SCRIPT
            .replace('__PREFIX__', json.dumps(PREFIX))
            .replace('__SURNAMES__', json.dumps(SURNAMES_BY_CC[ACTIVE_CC]))
            .replace('__L_NO_MATCH__', json.dumps(S("initials_no_match", letter='{letter}')))
            .replace('__L_SHARE_DONE__', json.dumps(S("initials_share_done"))))


SAINTS_SCRIPT = """
    <script>
    (function() {
        if (__SKIP__) return;
        var CAL = __CAL__;
        var EVENTS = __EVENTS__;
        var L_TODAY_M = __L_TODAY_M__;
        var L_TODAY_F = __L_TODAY_F__;
        var L_TODAY_E = __L_TODAY_E__;
        var L_WISH = __L_WISH__;
        var HUB_PREFIX = __HUB_PREFIX__;
        var SAINT_DIR = __SAINT_DIR__;

        function slugify(s) {
            return (s || '').toLowerCase()
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
        }
        function pad(n) { return n < 10 ? '0' + n : '' + n; }

        var d = new Date();
        var key = pad(d.getMonth() + 1) + '-' + pad(d.getDate());
        var saint = CAL[key];
        if (!saint) return;
        var slug = slugify(saint);
        var isEvent = EVENTS.indexOf(slug) >= 0;
        var last = saint.slice(-1).toLowerCase();
        var fem = (last === 'e' || last === 'a') && !isEvent;
        var template = isEvent ? L_TODAY_E : (fem ? L_TODAY_F : L_TODAY_M);
        var label = template.replace('{name}', saint);
        var href = HUB_PREFIX + '/' + SAINT_DIR + '/' + slug + '.html';

        var box = document.getElementById('sf-today');
        if (box) {
            box.innerHTML = '<a href="' + href + '">' + label + '</a>'
                + (isEvent ? '' : ' &middot; ' + L_WISH.replace('{name}', saint));
            box.style.display = '';
        }
        // Highlight today's cell on the calendar grid
        var todayCell = document.querySelector('.sf-days li[data-key="' + key + '"]');
        if (todayCell) todayCell.classList.add('is-today');

        // Optional: homepage callout container
        var hp = document.getElementById('sf-today-hp');
        if (hp) {
            hp.innerHTML = '<a href="' + href + '">' + label + '</a>'
                + (isEvent ? '' : ' &middot; ' + L_WISH.replace('{name}', saint));
            hp.style.display = '';
        }
    })();
    </script>"""


def saints_script() -> str:
    if ACTIVE_CC not in ('FR', 'ES', 'IT') or not SAINTS_BY_CC.get(ACTIVE_CC):
        return (SAINTS_SCRIPT
                .replace('__SKIP__', 'true')
                .replace('__CAL__', '{}')
                .replace('__EVENTS__', '[]')
                .replace('__L_TODAY_M__', '""')
                .replace('__L_TODAY_F__', '""')
                .replace('__L_TODAY_E__', '""')
                .replace('__L_WISH__', '""')
                .replace('__HUB_PREFIX__', '""')
                .replace('__SAINT_DIR__', '""'))
    saint_dir = {'FR': 'saint', 'ES': 'santo', 'IT': 'onomastico'}[ACTIVE_CC]
    return (SAINTS_SCRIPT
            .replace('__SKIP__', 'false')
            .replace('__CAL__', json.dumps(SAINTS_BY_CC[ACTIVE_CC], ensure_ascii=False))
            .replace('__EVENTS__', json.dumps(sorted(SAINT_EVENTS)))
            .replace('__L_TODAY_M__', json.dumps(S("saints_today_label")))
            .replace('__L_TODAY_F__', json.dumps(S("saints_today_label_fem")))
            .replace('__L_TODAY_E__', json.dumps(S("saints_today_event")))
            .replace('__L_WISH__', json.dumps(S("saints_today_wish")))
            .replace('__HUB_PREFIX__', json.dumps(PREFIX))
            .replace('__SAINT_DIR__', json.dumps(saint_dir)))


def sibling_script() -> str:
    def js(v: str) -> str:
        return json.dumps(v)
    origin_labels = {o: origin_label_cap(o) for o in ORIGIN_LABELS[ACTIVE_CC]}
    return (SIBLING_SCRIPT
            .replace('__PREFIX__', js(PREFIX))
            .replace('__L_GIRLS__', js(loc_label_cap('F')))
            .replace('__L_BOYS__', js(loc_label_cap('M')))
            .replace('__L_RESULT_FOR__', js(S("sibling_result_for", name='{name}')))
            .replace('__L_RESULT_FOR_SET__', js(S("sibling_result_for_set", names='{names}')))
            .replace('__L_UNKNOWN__', js(S("sibling_unknown")))
            .replace('__L_PEAK__', js(S("picker_peak_decade", d='{d}')))
            .replace('__L_SHOW_MORE__', js(S("sibling_show_more")))
            .replace('__ORIGIN_LABELS__', json.dumps(origin_labels, ensure_ascii=False)))


# ---------------------------------------------------------------------------
# Shared markup
# ---------------------------------------------------------------------------
BASE_CSS = """
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Poppins:wght@600;700&display=swap');
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
            margin: 0;
            padding: 0;
            background-color: #F7F8FA;
            color: #333;
            font-feature-settings: 'tnum' 1;
        }
        .container { max-width: 1240px; margin: 0 auto; padding: 2rem; }
        h1, h2, h3, h4 { font-family: 'Poppins', 'Inter', sans-serif; color: #1B2440; }
        h1 { color: #1B2440; }
        .sitenav { background: #1B2440; padding: 0.9rem 1.5rem; }
        .sitenav-inner { max-width: 1240px; margin: 0 auto; display: flex; gap: 1.25rem; align-items: center; flex-wrap: wrap; }
        .sitenav a { color: #EEF2F4; text-decoration: none; font-weight: 500; }
        .sitenav a:hover { color: #fff; }
        .sitenav .brand { font-family: 'Poppins', 'Inter', sans-serif; font-weight: 700; color: #fff; margin-right: auto; display: inline-flex; align-items: center; gap: 0.55rem; font-size: 1.05rem; }
        .sitenav .brand svg { display: block; }
        .sitenav .brand .wm-teal { color: #149E91; }
        .h1-flag { margin: 0 0.15rem; font-size: 0.8em; vertical-align: 0.1em; }

        /* Nav links group (right side) */
        .nav-links { display: inline-flex; align-items: center; gap: 1.5rem; flex-wrap: wrap; }
        .nav-link { color: #EEF2F4; text-decoration: none; font-weight: 500; font-size: 0.96rem; }
        .nav-link:hover { color: #fff; }
        .nav-search-btn { background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); color: #EEF2F4; width: 36px; height: 36px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; cursor: pointer; transition: background 0.15s ease, border-color 0.15s ease, transform 0.1s ease; padding: 0; }
        .nav-search-btn:hover { background: rgba(255,255,255,0.18); border-color: rgba(255,255,255,0.3); transform: translateY(-1px); }
        .nav-search-btn svg { width: 16px; height: 16px; }

        /* Nav dropdowns (Explore / Tools) */
        .nav-dd { position: relative; }
        .nav-dd-btn { background: none; border: 0; cursor: pointer; color: #EEF2F4; font-family: inherit; font-weight: 500; font-size: 0.96rem; padding: 0.4rem 0; display: inline-flex; align-items: center; gap: 0.3rem; transition: color 0.15s ease; }
        .nav-dd-btn:hover { color: #fff; }
        .nav-dd-caret { font-size: 0.7rem; transition: transform 0.18s ease; opacity: 0.75; }
        .nav-dd.is-open .nav-dd-btn { color: #fff; }
        .nav-dd.is-open .nav-dd-caret { transform: rotate(180deg); }
        .nav-dd-menu { display: none; position: absolute; top: calc(100% + 0.55rem); left: -0.5rem; background: #fff; border: 1px solid #E3E7EC; border-radius: 12px; min-width: 220px; padding: 0.4rem; box-shadow: 0 16px 40px -10px rgba(27,36,64,0.25), 0 4px 12px rgba(27,36,64,0.08); z-index: 60; animation: nc-dd-in 0.16s ease-out; }
        .nav-dd.is-open .nav-dd-menu { display: block; }
        .nav-dd-menu a, .nav-dd-menu a:link, .nav-dd-menu a:visited { display: block; padding: 0.6rem 0.9rem; border-radius: 8px; color: #1B2440 !important; text-decoration: none; font-size: 0.95rem; font-weight: 600; transition: background 0.12s ease, color 0.12s ease; }
        .nav-dd-menu a:hover, .nav-dd-menu a:focus { background: #EFF8F6; color: #0E7A70 !important; text-decoration: none; }
        @keyframes nc-dd-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }

        /* Country dropdown (white pill in nav) */
        .cc-dd { position: relative; }
        .cc-dd-btn { display: inline-flex; align-items: center; gap: 0.55rem; background: #fff; border: 1px solid #fff; color: #1B2440; font-family: inherit; font-weight: 600; font-size: 0.92rem; padding: 0.5rem 0.95rem; border-radius: 999px; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.15); transition: background 0.15s ease, transform 0.1s ease, box-shadow 0.15s ease; }
        .cc-dd-btn:hover { background: #F2F5F8; transform: translateY(-1px); box-shadow: 0 4px 14px rgba(0,0,0,0.22); }
        .cc-dd-btn .cc-flag { font-size: 1.15rem; line-height: 1; }
        .cc-dd-btn .cc-caret { font-size: 0.7rem; color: #5B6678; transition: transform 0.18s ease; margin-left: 0.2rem; }
        .cc-dd.is-open .cc-dd-btn { background: #fff; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
        .cc-dd.is-open .cc-dd-btn .cc-caret { transform: rotate(180deg); color: #149E91; }
        .cc-dd-menu { display: none; position: absolute; top: calc(100% + 0.5rem); right: 0; background: #fff; border: 1px solid #E3E7EC; border-radius: 14px; min-width: 240px; padding: 0.5rem; box-shadow: 0 16px 40px -10px rgba(27,36,64,0.25), 0 4px 12px rgba(27,36,64,0.08); z-index: 60; animation: nc-dd-in 0.16s ease-out; }
        .cc-dd.is-open .cc-dd-menu { display: block; }
        .cc-dd-heading { font-size: 0.7rem; letter-spacing: 0.14em; text-transform: uppercase; color: #5B6678; font-weight: 600; padding: 0.5rem 0.75rem 0.4rem; }
        a.cc-dd-item, a.cc-dd-item:link, a.cc-dd-item:visited { display: flex; align-items: center; gap: 0.7rem; padding: 0.6rem 0.8rem; border-radius: 9px; color: #1B2440 !important; text-decoration: none; font-weight: 600; font-size: 0.96rem; transition: background 0.12s ease, color 0.12s ease; }
        a.cc-dd-item:hover { background: #F2F5F8; color: #1B2440 !important; }
        a.cc-dd-item .cc-flag { font-size: 1.2rem; line-height: 1; }
        a.cc-dd-item .cc-code { color: #5B6678 !important; font-size: 0.78rem; margin-left: auto; font-weight: 600; letter-spacing: 0.05em; }
        a.cc-dd-item.is-current { background: #EFF8F6; color: #0E7A70 !important; font-weight: 700; }
        a.cc-dd-item.is-current .cc-code { color: #149E91 !important; }
        a.cc-dd-item .cc-check { margin-left: auto; color: #149E91; font-weight: 700; }
        @media (max-width: 720px) {
            .sitenav .brand span:not(.wm-teal) { font-size: 1rem; }
            .nav-links { gap: 1rem; }
            .cc-dd-btn { padding: 0.4rem 0.75rem; font-size: 0.85rem; }
            .cc-dd-btn .cc-dd-label { display: none; }
        }
        .cc-callout { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.9rem 1.25rem; margin: 1.5rem 0 2rem; font-size: 0.95rem; color: #1B2440; }
        .cc-callout a { color: #149E91; text-decoration: none; font-weight: 600; margin: 0 0.15rem; white-space: nowrap; }
        .cc-callout a:hover { text-decoration: underline; }
        .cc-callout .flag { margin-right: 0.2rem; }
        #lang-banner { background: #149E91; color: #fff; padding: 0.55rem 1rem; text-align: center; font-size: 0.92rem; display: flex; justify-content: center; align-items: center; gap: 0.85rem; flex-wrap: wrap; }
        #lang-banner a { color: #fff; font-weight: 700; text-decoration: underline; }
        #lang-banner button { background: none; border: 0; color: #fff; font-size: 1.25rem; cursor: pointer; line-height: 1; padding: 0 0.3rem; opacity: 0.8; }
        #lang-banner button:hover { opacity: 1; }
        .fav-nav-count { font-size: 0.78rem; color: #c8cfdb; margin-left: 0.2rem; }
        .fav-btn { background: none; border: 0; cursor: pointer; padding: 0.3rem 0.5rem; vertical-align: middle; display: inline-flex; align-items: center; }
        .fav-btn svg { width: 24px; height: 24px; display: block; transition: transform 0.12s ease; }
        .fav-btn:hover svg { transform: scale(1.12); }
        .fav-btn .heart-empty { stroke: #5B6678; fill: none; stroke-width: 2; }
        .fav-btn .heart-full { fill: #FF6B5C; stroke: none; }
        .fav-btn.is-fav .heart-empty { display: none; }
        .fav-btn:not(.is-fav) .heart-full { display: none; }
        h1 .fav-btn { margin-left: 0.5rem; vertical-align: -4px; }
        .blog-list { list-style: none; padding: 0; display: grid; grid-template-columns: 1fr; gap: 1rem; margin: 1.5rem 0; }
        @media (min-width: 720px) { .blog-list { grid-template-columns: 1fr 1fr; } }
        .blog-card { background: #fff; border: 1px solid #d6dde2; border-radius: 10px; padding: 1.1rem 1.3rem; display: flex; flex-direction: column; gap: 0.45rem; }
        .blog-card h3 { margin: 0; color: #1B2440; font-size: 1.2rem; line-height: 1.3; }
        .blog-card a { text-decoration: none; }
        .blog-card a:hover h3 { color: #149E91; }
        .blog-card p { margin: 0; color: #5B6678; font-size: 0.95rem; }
        .blog-meta { font-size: 0.78rem; color: #8A93A3; letter-spacing: 0.04em; text-transform: uppercase; }
        .blog-readmore { color: #149E91; font-weight: 600; margin-top: 0.25rem; font-size: 0.9rem; }
        .blog-post { max-width: 720px; }
        .blog-post h1 { font-size: 2.1rem; line-height: 1.2; margin-bottom: 0.25rem; }
        .blog-post h2 { font-size: 1.45rem; margin-top: 2rem; }
        .blog-post h3 { font-size: 1.15rem; margin-top: 1.5rem; }
        .blog-post p, .blog-post li { font-size: 1.02rem; line-height: 1.65; color: #2a3548; }
        .blog-post ul, .blog-post ol { padding-left: 1.4rem; }
        .blog-post a { color: #149E91; }
        .blog-post a:hover { text-decoration: underline; }
        .blog-post blockquote { border-left: 4px solid #149E91; margin: 1.25rem 0; padding: 0.4rem 1rem; background: #EFF8F6; border-radius: 0 6px 6px 0; color: #2a3548; }
        .blog-table { width: 100%; border-collapse: collapse; margin: 1.25rem 0; font-size: 0.95rem; }
        .blog-table th, .blog-table td { padding: 0.55rem 0.7rem; text-align: left; border-bottom: 1px solid #e3e7ec; }
        .blog-table th { background: #f5f7f9; color: #1B2440; font-weight: 600; }
        .blog-table tbody tr:hover { background: #fafbfc; }
        .blog-post hr { border: 0; border-top: 1px solid #d6dde2; margin: 2rem 0; }
        .blog-back { margin-top: 2rem; }
        .blog-back a { color: #5B6678; text-decoration: none; }
        .blog-back a:hover { color: #149E91; }
        .pin-btn { display: inline-flex; align-items: center; gap: 0.4rem; background: #E60023; color: #fff; border: 0; border-radius: 999px; padding: 0.45rem 1rem 0.45rem 0.85rem; font-weight: 600; font-size: 0.92rem; text-decoration: none; cursor: pointer; transition: background 0.12s ease, transform 0.12s ease; box-shadow: 0 1px 2px rgba(0,0,0,0.08); }
        .pin-btn svg { width: 18px; height: 18px; display: block; }
        .pin-btn:hover { background: #ad081b; transform: translateY(-1px); }
        .share-btn, .download-btn { display: inline-flex; align-items: center; gap: 0.4rem; background: #fff; color: #2a3540; border: 1px solid #cfd6dc; border-radius: 999px; padding: 0.45rem 1rem 0.45rem 0.85rem; font-weight: 600; font-size: 0.92rem; text-decoration: none; cursor: pointer; transition: background 0.12s ease, transform 0.12s ease, border-color 0.12s ease; }
        .share-btn svg, .download-btn svg { width: 18px; height: 18px; display: block; }
        .share-btn:hover, .download-btn:hover { background: #f2f5f8; border-color: #149E91; transform: translateY(-1px); }
        .tg-btn { background: #2AABEE; color: #fff; border-color: #2AABEE; }
        .tg-btn:hover { background: #1f96d3; border-color: #1f96d3; color: #fff; }
        .share-flash { display: inline-flex; align-items: center; font-size: 0.88rem; color: #149E91; font-weight: 600; padding: 0.45rem 0.4rem; }
        .name-share-row { margin: -0.25rem 0 1rem; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
        .fav-list { list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 0.75rem; margin: 1.5rem 0; }
        .fav-list li { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.75rem 1rem; display: grid; grid-template-columns: 1fr auto; grid-template-rows: auto auto; column-gap: 0.5rem; align-items: center; }
        .fav-list a { color: #1B2440; text-decoration: none; font-weight: 600; grid-column: 1; }
        .fav-list a:hover { color: #149E91; }
        .fav-meta { grid-column: 1 / -1; font-size: 0.82rem; color: #5B6678; margin-top: 0.15rem; }
        .fav-remove-btn { background: none; border: 0; cursor: pointer; color: #c0392b; font-size: 1.2rem; line-height: 1; padding: 0 0.25rem; grid-column: 2; grid-row: 1; }
        .fav-remove-btn:hover { color: #7a1f12; }
        .fav-actions { display: flex; gap: 0.75rem; align-items: center; margin: 1rem 0 2rem; flex-wrap: wrap; }
        .fav-share-btn, .fav-print-btn { background: #149E91; color: #fff; border: 0; border-radius: 6px; padding: 0.55rem 1rem; font-weight: 600; cursor: pointer; font-size: 0.92rem; }
        .fav-share-btn:hover, .fav-print-btn:hover { background: #117f74; }
        .fav-print-btn { background: #1B2440; }
        .fav-print-btn:hover { background: #0f1730; }
        .fav-share-done { color: #27ae60; font-size: 0.9rem; }
        .fav-print-header, .fav-print-foot { display: none; }
        @media print {
            body { background: #fff; color: #1B2440; }
            .sitenav, #lang-banner, .footer, .breadcrumb, .fav-actions, .fav-remove-btn,
            .fav-screen-h1, .fav-screen-only { display: none !important; }
            .container { max-width: 100%; padding: 0; }
            .fav-print-header { display: block; border-bottom: 2px solid #149E91; padding-bottom: 0.5rem; margin-bottom: 1.25rem; }
            .fav-print-brand { font-size: 1.4rem; font-weight: 700; color: #149E91; }
            .fav-print-title { font-size: 1.6rem; font-weight: 700; color: #1B2440; margin-top: 0.25rem; }
            .fav-list { display: block !important; margin: 0; }
            .fav-list li { border: 0; border-bottom: 1px solid #d6dde2; border-radius: 0; padding: 0.6rem 0; background: transparent; page-break-inside: avoid; display: block; }
            .fav-list a { display: block; color: #1B2440 !important; font-size: 1.1rem; text-decoration: none; }
            .fav-list a::after { content: ""; }
            .fav-meta { display: block; font-size: 0.85rem; color: #5B6678; margin-top: 0.15rem; }
            .fav-print-foot { display: block; margin-top: 1.5rem; padding-top: 0.5rem; border-top: 1px solid #d6dde2; font-size: 0.8rem; color: #5B6678; text-align: center; }
            @page { margin: 0.75in; }
        }
        .cmp-form { display: flex; gap: 0.75rem; margin: 1.5rem 0; flex-wrap: wrap; align-items: stretch; }
        .cmp-form input { flex: 1; min-width: 160px; padding: 0.7rem 0.9rem; font-size: 1rem; border: 1px solid #d6dde2; border-radius: 6px; background: #fff; }
        .cmp-form button { background: #149E91; color: #fff; border: 0; border-radius: 6px; padding: 0.7rem 1.3rem; font-weight: 600; cursor: pointer; font-size: 1rem; }
        .cmp-form button:hover { background: #117f74; }
        .cmp-form .ac-wrap { position: relative; flex: 1; min-width: 160px; }
        .cmp-form .ac-wrap input { width: 100%; box-sizing: border-box; }
        .cmp-ac { position: absolute; left: 0; right: 0; top: 100%; background: #fff; border: 1px solid #d6dde2; border-top: 0; border-radius: 0 0 6px 6px; max-height: 260px; overflow-y: auto; z-index: 10; }
        .cmp-ac div { padding: 0.5rem 0.9rem; cursor: pointer; }
        .cmp-ac div:hover, .cmp-ac div.sel { background: #EEF2F4; }
        .cmp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin: 1.5rem 0; }
        .cmp-card { background: #fff; border-radius: 8px; padding: 1.25rem; box-shadow: 0 2px 4px rgba(0,0,0,0.06); }
        .cmp-card h2 { margin: 0 0 0.5rem; }
        .cmp-card .cmp-stats { display: grid; grid-template-columns: 1fr; gap: 0.5rem; font-size: 0.95rem; margin-top: 0.75rem; }
        .cmp-card .cmp-stats div { display: flex; justify-content: space-between; border-bottom: 1px dashed #EEF2F4; padding: 0.25rem 0; }
        .cmp-card .cmp-stats .lbl { color: #5B6678; }
        .cmp-card .cmp-stats .val { font-weight: 600; font-variant-numeric: tabular-nums; }
        .cmp-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 0.5rem; vertical-align: 1px; }
        .cmp-error { background: #fdecea; border-left: 4px solid #c0392b; padding: 0.85rem 1rem; border-radius: 6px; margin: 1rem 0; color: #7a1f12; }
        @media (max-width: 600px) { .cmp-grid { grid-template-columns: 1fr; } }
        .nav { margin-bottom: 1.5rem; font-size: 0.9rem; }
        .nav a { color: #149E91; text-decoration: none; }
        .nav a:hover { text-decoration: underline; }
        .breadcrumb { font-size: 0.85rem; color: #5B6678; margin-bottom: 1rem; }
        .breadcrumb a { color: #149E91; text-decoration: none; }
        .insight {
            background: #fff; border-left: 4px solid #149E91; padding: 1rem 1.25rem;
            border-radius: 6px; margin: 1.5rem 0; box-shadow: 0 2px 4px rgba(0,0,0,0.06);
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        .stat {
            background: #fff; padding: 1.5rem; border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1); text-align: center;
        }
        .stat-value { font-family: 'Poppins', 'Inter', sans-serif; font-size: 2rem; font-weight: 700; color: #1B2440; }
        .stat-label { color: #5B6678; margin-top: 0.5rem; font-size: 0.9rem; }
        .chart-wrap { background:#fff; padding:1.25rem; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:2rem; }
        table {
            width: 100%; border-collapse: collapse; margin-bottom: 2rem;
            background: #fff; border-radius: 8px; overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        th, td { padding: 0.85rem 1rem; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #EEF2F4; font-weight: 600; color: #1B2440; }
        tr:hover { background-color: #f8f9fa; }
        .year-column { width: 12%; font-family: monospace; }
        .count-column { width: 18%; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
        .rank-column { width: 15%; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
        .related { display:flex; flex-wrap:wrap; gap:0.5rem; margin:0.5rem 0 1.75rem; }
        .related a {
            background:#fff; border:1px solid #d6dde2; border-radius:20px;
            padding:0.35rem 0.85rem; text-decoration:none; color:#1B2440; font-size:0.9rem;
        }
        .related a:hover { background:#149E91; color:#fff; border-color:#149E91; }
        .azindex { display:flex; flex-wrap:wrap; gap:0.4rem; margin:1rem 0 2rem; }
        .azindex a { background:#fff; border:1px solid #d6dde2; border-radius:6px; padding:0.4rem 0.7rem; text-decoration:none; color:#1B2440; font-weight:600; }
        .azindex a:hover { background:#149E91; color:#fff; }
        .footer { text-align: center; margin-top: 3rem; color: #5B6678; font-size: 0.9rem; }
        a { color: #149E91; }
        .ww-form { display: flex; gap: 0.6rem; margin: 1.5rem 0 1rem; flex-wrap: wrap; }
        .ww-form input { flex: 1; min-width: 200px; padding: 0.7rem 0.9rem; font-size: 1rem; border: 1px solid #d6dde2; border-radius: 6px; background: #fff; }
        .ww-form button { background: #149E91; color: #fff; border: 0; border-radius: 6px; padding: 0.7rem 1.3rem; font-weight: 600; cursor: pointer; font-size: 1rem; }
        .ww-form button:hover { background: #117f74; }
        .ww-tabs { display: flex; gap: 0.4rem; margin: 0 0 1.25rem; }
        .ww-tab { background: #fff; border: 1px solid #d6dde2; color: #1B2440; padding: 0.4rem 0.95rem; border-radius: 20px; cursor: pointer; font-size: 0.9rem; font-weight: 500; }
        .ww-tab.is-active { background: #1B2440; color: #fff; border-color: #1B2440; }
        #ww-result-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 0.6rem; margin-top: 1rem; }
        .ww-card { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.7rem 0.9rem; text-decoration: none; display: flex; flex-direction: column; gap: 0.25rem; transition: border-color 0.15s ease, transform 0.1s ease; }
        .ww-card:hover { border-color: #149E91; transform: translateY(-1px); }
        .ww-card .ww-name { font-weight: 600; color: #1B2440; font-size: 1rem; }
        .ww-card .ww-meta { font-size: 0.8rem; color: #5B6678; }
        #ww-loading { color: #5B6678; font-size: 0.9rem; padding: 0.5rem 0; }
        .how-box { background: #fff; border: 1px solid #d6dde2; border-left: 4px solid #149E91; border-radius: 6px; padding: 0.9rem 1.25rem; margin: 1rem 0 1.5rem; }
        .how-box h2 { margin: 0 0 0.5rem; font-size: 0.95rem; font-family: 'Inter', sans-serif; font-weight: 600; color: #1B2440; }
        .how-box ul { margin: 0; padding-left: 1.1rem; color: #1B2440; font-size: 0.92rem; }
        .how-box li { margin: 0.25rem 0; }
        .how-box em { color: #5B6678; font-style: normal; }
        .ww-more-wrap { text-align: center; margin-top: 1.5rem; }
        #ww-more { background: #fff; border: 1px solid #d6dde2; color: #1B2440; padding: 0.6rem 1.4rem; border-radius: 24px; cursor: pointer; font-weight: 500; font-size: 0.92rem; }
        #ww-more:hover { border-color: #149E91; color: #149E91; }
        .origin-badge { display: inline-block; background: #EEF2F4; color: #1B2440; border: 1px solid #d6dde2; border-radius: 16px; padding: 0.25rem 0.85rem; font-size: 0.85rem; font-weight: 500; text-decoration: none; margin: 0.5rem 0 0.25rem; }
        .origin-badge:hover { background: #149E91; color: #fff; border-color: #149E91; }
        .origin-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 0.6rem; margin: 1.5rem 0; }
        .origin-card { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.85rem 1rem; text-decoration: none; display: flex; flex-direction: column; gap: 0.2rem; transition: border-color 0.15s, transform 0.1s; }
        .origin-card:hover { border-color: #149E91; transform: translateY(-1px); }
        .origin-card-label { font-weight: 600; color: #1B2440; font-size: 1rem; }
        .origin-card-count { color: #5B6678; font-size: 0.82rem; }
        .yir-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin: 0.75rem 0 2rem; }
        .yir-grid > div h3 { margin: 0 0 0.6rem; font-size: 1rem; font-family: 'Inter', sans-serif; color: #5B6678; font-weight: 600; }
        .yir-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.45rem; }
        .yir-card { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.65rem 0.9rem; display: flex; flex-direction: column; gap: 0.2rem; }
        .yir-card-name a { color: #1B2440; text-decoration: none; font-weight: 600; }
        .yir-card-name a:hover { color: #149E91; }
        .yir-card-meta { color: #149E91; font-size: 0.85rem; font-variant-numeric: tabular-nums; }
        .yir-card-count { color: #5B6678; font-size: 0.78rem; }
        @media (max-width: 600px) { .yir-grid { grid-template-columns: 1fr; } }
        .search-ac-wrap { position: relative; display: inline-block; width: 70%; max-width: 400px; text-align: left; }
        .search-ac-wrap input { width: 100%; box-sizing: border-box; padding: 0.75rem; border: 1px solid #d6dde2; border-radius: 4px; font-size: 1rem; background: #fff; }
        #searchAc { position: absolute; left: 0; right: 0; top: 100%; background: #fff; border: 1px solid #d6dde2; border-top: 0; border-radius: 0 0 6px 6px; max-height: 280px; overflow-y: auto; z-index: 10; box-shadow: 0 4px 12px rgba(27,36,64,0.08); }
        #searchAc div { padding: 0.55rem 0.9rem; cursor: pointer; color: #1B2440; }
        #searchAc div:hover, #searchAc div.sel { background: #EEF2F4; }

        /* ---------- HOMEPAGE HERO ---------- */
        .home-hero { position: relative; background: radial-gradient(900px 420px at 80% -10%, rgba(20,158,145,0.18), transparent 60%), radial-gradient(700px 380px at 10% 100%, rgba(27,36,64,0.06), transparent 60%), linear-gradient(180deg, #FFFFFF 0%, #F7F8FA 100%); border-bottom: 1px solid #ECEFF3; padding: 4rem 1.25rem 3.5rem; margin: -2rem -2rem 2.5rem; overflow: hidden; }
        .home-hero-inner { max-width: 760px; margin: 0 auto; text-align: center; position: relative; z-index: 2; }
        .home-hero h1 { font-family: 'Poppins'; font-weight: 700; font-size: clamp(1.9rem, 4.2vw, 3rem); line-height: 1.08; letter-spacing: -0.02em; margin: 0 0 0.9rem; color: #1B2440; white-space: nowrap; }
        .home-hero h1 .accent { color: #149E91; }
        .home-hero-sub { font-size: clamp(0.88rem, 1.05vw, 0.98rem); color: #5B6678; max-width: 600px; margin: 0 auto 2.75rem; line-height: 1.5; }
        @media (max-width: 480px) { .home-hero { padding: 2.5rem 1rem 2.5rem; margin: -2rem -1rem 2rem; } .home-hero h1 { white-space: normal; font-size: 1.8rem; } }
        .home-search-wrap { max-width: 720px; margin: 1.25rem auto 0; padding-top: 0.5rem; padding-bottom: 0.75rem; position: relative; }
        .home-search { display: flex; align-items: center; background: #fff; border: 1px solid #E3E7EC; border-radius: 20px; padding: 8px; box-shadow: 0 1px 2px rgba(27,36,64,0.04), 0 16px 48px -14px rgba(20,158,145,0.28); transition: box-shadow 0.18s ease, border-color 0.18s ease; position: relative; }
        .home-search:focus-within { border-color: #149E91; box-shadow: 0 0 0 4px rgba(20,158,145,0.15), 0 16px 40px -10px rgba(20,158,145,0.35); }
        .home-search-icon { margin: 0 0.35rem 0 0.9rem; flex: 0 0 auto; color: #5B6678; }
        .home-search input { flex: 1; border: 0; outline: 0; background: transparent; font-family: inherit; font-size: 1.15rem; font-weight: 500; color: #1B2440; padding: 1.15rem 0.6rem; min-width: 0; }
        .home-search input::placeholder { color: #97a0ad; font-weight: 400; }
        .home-search button.go { flex: 0 0 auto; background: #149E91; color: #fff; border: 0; border-radius: 14px; padding: 1rem 1.7rem; font-family: inherit; font-weight: 600; font-size: 1.02rem; cursor: pointer; display: inline-flex; align-items: center; gap: 0.4rem; transition: background 0.15s ease, transform 0.1s ease; }
        .home-search button.go:hover { background: #117f74; }
        .home-search button.go:active { transform: translateY(1px); }
        .home-hero #searchAc { left: 8px; right: 8px; top: calc(100% + 4px); border-radius: 12px; border: 1px solid #E3E7EC; box-shadow: 0 16px 40px -10px rgba(27,36,64,0.18); }
        @media (max-width: 560px) { .home-search input { font-size: 1rem; padding: 0.9rem 0.4rem; } .home-search button.go { padding: 0.85rem 1rem; font-size: 0.95rem; } .home-search button.go .go-label { display: none; } }
        .home-hint { margin-top: 1.1rem; font-size: 0.9rem; color: #5B6678; }
        .home-hint .chip { display: inline-block; background: #fff; border: 1px solid #E3E7EC; border-radius: 999px; padding: 0.25rem 0.75rem; margin: 0.15rem 0.15rem; color: #1B2440; font-weight: 500; text-decoration: none; font-size: 0.85rem; transition: border-color 0.12s, color 0.12s, transform 0.1s; }
        .home-hint .chip:hover { border-color: #149E91; color: #149E91; transform: translateY(-1px); }
        .home-browse { margin-top: 0.5rem; font-size: 0.88rem; color: #5B6678; }
        .home-browse a { color: #149E91; font-weight: 600; text-decoration: none; }
        .home-browse a:hover { text-decoration: underline; }
        .home-stats { margin-top: 2.5rem; display: flex; gap: 2.5rem; justify-content: center; flex-wrap: wrap; font-size: 0.88rem; color: #5B6678; }
        .home-stats b { color: #1B2440; font-weight: 700; font-family: 'Poppins'; font-size: 1.05rem; display: block; }
        @media (max-width: 560px) { .home-stats { gap: 1.25rem; } }

        /* ---------- TOOL STRIP ---------- */
        .tool-strip { margin-top: 2.75rem; position: relative; }
        .tool-strip-label { display: flex; align-items: center; gap: 1rem; max-width: 720px; margin: 0 auto 1.25rem; padding: 0 1.5rem; }
        .tool-strip-label .tsl-line { flex: 1; height: 1px; background: linear-gradient(90deg, transparent, #D6DDE2, transparent); }
        .tool-strip-label .tsl-text { font-size: 0.78rem; letter-spacing: 0.18em; text-transform: uppercase; color: #5B6678; font-weight: 600; white-space: nowrap; }
        .tool-track-mask { overflow: hidden; -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 50px, #000 calc(100% - 50px), transparent 100%); mask-image: linear-gradient(90deg, transparent 0, #000 50px, #000 calc(100% - 50px), transparent 100%); padding: 0.5rem 0 1rem; display: flex; flex-direction: column; gap: 0.9rem; }
        .tool-track { display: inline-flex; gap: 1.1rem; padding: 0 0.5rem; will-change: transform; }
        .tool-track-a { animation: tool-scroll-left 48s linear infinite; }
        .tool-track-b { animation: tool-scroll-right 52s linear infinite; margin-left: 160px; }
        .tool-strip:hover .tool-track, .tool-track:focus-within { animation-play-state: paused; }
        @keyframes tool-scroll-left { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
        @keyframes tool-scroll-right { 0% { transform: translateX(-50%); } 100% { transform: translateX(0); } }
        @media (prefers-reduced-motion: reduce) { .tool-track { animation: none; } .tool-track-mask { overflow-x: auto; } }
        .tool-card { flex: 0 0 auto; width: 320px; display: flex; align-items: center; gap: 1rem; background: #fff; border: 1px solid #E3E7EC; border-radius: 18px; padding: 1.1rem 1.25rem; text-decoration: none; box-shadow: 0 2px 4px rgba(27,36,64,0.05); transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease; }
        .tool-card:hover { transform: translateY(-3px); border-color: transparent; box-shadow: 0 16px 32px -10px rgba(27,36,64,0.2), 0 0 0 1px var(--c1); }
        .tool-icon { flex: 0 0 auto; width: 52px; height: 52px; border-radius: 14px; display: inline-flex; align-items: center; justify-content: center; background: linear-gradient(135deg, var(--c1), var(--c2)); color: #fff; font-family: 'Poppins', sans-serif; font-weight: 700; font-size: 1.4rem; letter-spacing: -0.02em; box-shadow: 0 6px 14px -3px color-mix(in srgb, var(--c1) 50%, transparent); }
        .tool-body { min-width: 0; }
        .tool-name { font-family: 'Poppins', sans-serif; font-weight: 700; color: #1B2440; font-size: 1.08rem; line-height: 1.2; white-space: nowrap; }
        .tool-desc { font-size: 0.88rem; color: #5B6678; margin-top: 0.2rem; line-height: 1.35; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        @media (max-width: 560px) {
            .tool-strip { margin-top: 2rem; }
            .tool-strip-label { padding: 0 1rem; margin-bottom: 1rem; }
            .tool-track-mask { -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 24px, #000 calc(100% - 24px), transparent 100%); mask-image: linear-gradient(90deg, transparent 0, #000 24px, #000 calc(100% - 24px), transparent 100%); overflow-x: auto; -webkit-overflow-scrolling: touch; scroll-snap-type: x mandatory; padding: 0.5rem 0 1.5rem; gap: 0; scrollbar-width: none; }
            .tool-track-mask::-webkit-scrollbar { display: none; }
            .tool-track { animation: none !important; gap: 0.7rem; padding: 0 1rem; }
            .tool-track-b { display: none; }
            .tool-card { width: 78vw; max-width: 320px; padding: 1rem 1.1rem; border-radius: 16px; scroll-snap-align: center; box-shadow: 0 6px 18px -8px rgba(27,36,64,0.18); }
            .tool-icon { width: 48px; height: 48px; font-size: 1.3rem; border-radius: 12px; }
            .tool-name { font-size: 1.05rem; }
            .tool-desc { font-size: 0.85rem; white-space: normal; }
        }
        /* ---------- ANIMATED RACE CHART (homepage) ---------- */
        .race-section { margin: 3rem 0 0; padding: 0; }
        .race-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
        .race-head h2 { margin: 0 0 0.25rem; font-family: 'Poppins'; font-weight: 700; font-size: 1.7rem; letter-spacing: -0.01em; color: #1B2440; }
        .race-sub { margin: 0; color: #5B6678; font-size: 0.95rem; }
        .race-controls { display: flex; gap: 0.5rem; }
        .race-btn { background: #1B2440; color: #fff; border: 0; border-radius: 999px; padding: 0.5rem 1rem; font-family: inherit; font-weight: 600; font-size: 0.85rem; cursor: pointer; transition: background 0.15s ease, transform 0.1s ease; }
        .race-btn:hover { background: #0E1530; transform: translateY(-1px); }
        .race-btn-ghost { background: #fff; color: #1B2440; border: 1px solid #E3E7EC; }
        .race-btn-ghost:hover { background: #F2F5F8; border-color: #1B2440; }
        .race-stage { background: radial-gradient(800px 300px at 100% 0%, rgba(20,158,145,0.08), transparent 60%), #fff; border: 1px solid #E3E7EC; border-radius: 20px; padding: 2rem 2rem 1.5rem; box-shadow: 0 4px 16px -8px rgba(27,36,64,0.08); position: relative; }
        .race-year { font-family: 'Poppins'; font-weight: 800; font-size: clamp(3rem, 7vw, 4.5rem); letter-spacing: -0.03em; color: #149E91; line-height: 1; position: absolute; top: 1.5rem; right: 2rem; font-variant-numeric: tabular-nums; opacity: 0.85; }
        .race-split { margin-top: 2rem; display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
        .race-pane { display: flex; flex-direction: column; background: #FBFCFD; border: 1px solid #ECEFF3; border-radius: 14px; padding: 1rem 1rem 0.75rem; min-width: 0; }
        .race-pane-head { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; font-family: 'Poppins', sans-serif; }
        .race-pane-icon { width: 26px; height: 26px; border-radius: 8px; display: inline-flex; align-items: center; justify-content: center; font-size: 1rem; font-weight: 700; color: #fff; }
        .race-pane[data-sex="M"] .race-pane-icon { background: linear-gradient(135deg, #149E91, #0E7A70); }
        .race-pane[data-sex="F"] .race-pane-icon { background: linear-gradient(135deg, #D26A8C, #B14770); }
        .race-pane-title { font-weight: 700; font-size: 1rem; color: #1B2440; }
        .race-pane-tally { margin-left: auto; font-family: 'Inter', sans-serif; font-size: 0.78rem; color: #5B6678; font-variant-numeric: tabular-nums; }
        .race-chart { width: 100%; height: 320px; display: block; overflow: visible; }
        .race-grid line { stroke: #ECEFF3; stroke-width: 1; stroke-dasharray: 3 4; }
        .race-grid line.axis { stroke: #D6DDE2; stroke-dasharray: none; }
        .race-xlabels text, .race-ylabels text { font-family: 'Inter', sans-serif; font-size: 11px; fill: #97A0AD; font-variant-numeric: tabular-nums; }
        .race-ylabels text { text-anchor: end; }
        .race-lines path { fill: none; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; filter: drop-shadow(0 1px 2px rgba(27,36,64,0.04)); }
        .race-clip-rect { transition: width 110ms linear; }
        .race-dots circle { transition: cx 110ms linear, cy 110ms linear, opacity 200ms ease; }
        .race-cursor { pointer-events: none; }
        .race-legend { display: grid; grid-template-columns: 1fr 1fr; gap: 0.3rem 0.85rem; padding: 0.75rem 0 0.25rem; border-top: 1px dashed #ECEFF3; margin-top: 0.5rem; }
        .race-legend-item { display: flex; align-items: center; gap: 0.55rem; font-size: 0.88rem; color: #1B2440; }
        .race-legend-swatch { width: 12px; height: 12px; border-radius: 4px; flex: 0 0 auto; }
        .race-legend-name { font-weight: 600; font-family: 'Poppins'; }
        .race-legend-count { margin-left: auto; color: #5B6678; font-size: 0.78rem; font-variant-numeric: tabular-nums; }
        .race-scrubber { margin-top: 1.25rem; padding-top: 1rem; border-top: 1px dashed #E3E7EC; }
        .race-scrubber input[type="range"] { width: 100%; -webkit-appearance: none; appearance: none; height: 4px; background: #E3E7EC; border-radius: 2px; outline: none; cursor: pointer; }
        .race-scrubber input[type="range"]::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 16px; height: 16px; border-radius: 50%; background: #149E91; border: 3px solid #fff; box-shadow: 0 2px 6px rgba(20,158,145,0.4); cursor: grab; }
        .race-scrubber input[type="range"]::-moz-range-thumb { width: 16px; height: 16px; border-radius: 50%; background: #149E91; border: 3px solid #fff; box-shadow: 0 2px 6px rgba(20,158,145,0.4); cursor: grab; }
        .race-decades { display: flex; justify-content: space-between; margin-top: 0.4rem; font-size: 0.72rem; color: #5B6678; font-weight: 500; font-variant-numeric: tabular-nums; }
        .race-foot { text-align: center; color: #5B6678; font-size: 0.9rem; margin-top: 1.5rem; }
        .race-foot a { color: #149E91; font-weight: 600; text-decoration: none; }
        .race-foot a:hover { text-decoration: underline; }
        @media (max-width: 900px) { .race-split { grid-template-columns: 1fr; gap: 1rem; } .race-chart { height: 280px; } }
        @media (max-width: 600px) { .race-stage { padding: 1.25rem 1rem 1rem; } .race-year { font-size: 2.4rem; top: 1rem; right: 1.25rem; } }
        /* Mobile uses a pre-rendered video instead of the live SVG to dodge
           the per-frame redraw cost of 80+ paths on low-end GPUs. Desktop
           keeps the interactive chart with scrub + clickable legend. */
        .race-video { display: none; width: 100%; border-radius: 18px; background: #F7F8FA; box-shadow: 0 4px 16px -8px rgba(27,36,64,0.08); }
        @media (max-width: 760px) {
            .race-section .race-stage, .race-section .race-controls { display: none; }
            .race-section .race-video { display: block; }
        }

        .num-box { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 1rem 1.25rem; margin: 1.5rem 0; }
        .num-box h2 { margin: 0 0 0.4rem; font-size: 1.1rem; }
        .num-box > p { margin: 0 0 1rem; color: #5B6678; font-size: 0.95rem; }
        .num-grid { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.85rem; }
        .num-card { background: #F7F8FA; border: 1px solid #EEF2F4; border-radius: 8px; padding: 0.85rem 0.95rem; display: flex; flex-direction: column; gap: 0.25rem; }
        .num-card-n { font-family: 'Poppins', 'Inter', sans-serif; font-size: 2rem; font-weight: 700; color: #149E91; line-height: 1; }
        .num-card-label { color: #5B6678; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
        .num-card-trait { font-weight: 600; color: #1B2440; font-size: 1rem; }
        .num-card-desc { color: #1B2440; font-size: 0.9rem; line-height: 1.4; }
        .num-card-axis { color: #8a93a3; font-size: 0.78rem; margin-top: 0.2rem; }
        .num-box .num-footer { color: #8a93a3; font-size: 0.8rem; margin: 1rem 0 0; font-style: italic; }
        .meaning-box { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.85rem 1.1rem 0.6rem; margin: 1rem 0 1.5rem; }
        .meaning-box h2 { margin: 0 0 0.4rem; font-size: 1rem; font-family: 'Inter', sans-serif; font-weight: 600; color: #1B2440; }
        .meaning-box p { margin: 0; color: #1B2440; font-size: 0.95rem; line-height: 1.5; }
        .meaning-box .meaning-source { color: #8a93a3; font-size: 0.78rem; margin-top: 0.4rem; }
        .famous-list { list-style: none; padding: 0; margin: 1rem 0 2rem; display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem; }
        @media (max-width: 600px) { .famous-list { grid-template-columns: 1fr; } }
        .famous-item { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.65rem 0.9rem; display: flex; flex-direction: column; gap: 0.2rem; }
        .famous-name a { color: #1B2440; text-decoration: none; font-weight: 600; }
        .famous-name a:hover { color: #149E91; text-decoration: underline; }
        .famous-sub { color: #5B6678; font-size: 0.82rem; }
        .fiction-list { list-style: none; padding: 0; margin: 1.5rem 0 2rem; display: grid; grid-template-columns: 1fr; gap: 0.5rem; }
        .fiction-row { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.7rem 1rem; display: flex; gap: 0.85rem; align-items: baseline; }
        .fiction-name { font-weight: 600; min-width: 110px; flex-shrink: 0; }
        .fiction-name a { color: #149E91; text-decoration: none; }
        .fiction-name a:hover { text-decoration: underline; }
        .fiction-name .name-unlinked { color: #1B2440; }
        .fiction-role { color: #5B6678; font-size: 0.9rem; }
        .fiction-appears { list-style: disc; padding-left: 1.25rem; margin: 1rem 0 2rem; color: #1B2440; }
        .fiction-appears li { margin: 0.35rem 0; }
        .fiction-appears a { color: #149E91; text-decoration: none; font-weight: 500; }
        .fiction-appears a:hover { text-decoration: underline; }
        .sf-today { background: #149E91; color: #fff; padding: 0.85rem 1.25rem; border-radius: 8px; margin: 1rem 0 1.5rem; font-size: 1rem; }
        .sf-today a { color: #fff; font-weight: 700; text-decoration: underline; }
        .sf-calendar { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; }
        .sf-month { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 1rem 1.1rem; }
        .sf-month h2 { font-size: 1.05rem; margin: 0 0 0.6rem; }
        .sf-days { list-style: none; padding: 0; margin: 0; }
        .sf-days li { display: flex; gap: 0.5rem; padding: 0.2rem 0; font-size: 0.9rem; border-bottom: 1px dashed #EEF2F4; }
        .sf-days li:last-child { border-bottom: 0; }
        .sf-days li.is-today { background: #FFF4D6; border-radius: 4px; padding-left: 0.3rem; padding-right: 0.3rem; }
        .sf-days .sf-day { color: #5B6678; min-width: 1.6em; font-variant-numeric: tabular-nums; text-align: right; }
        .sf-days li a { color: #1B2440; text-decoration: none; }
        .sf-days li a:hover { color: #149E91; }
        .sf-dates { color: #1B2440; font-size: 1.05rem; }
        .in-form { display: flex; gap: 0.6rem; margin: 1.5rem 0 1rem; flex-wrap: wrap; }
        .in-form input { flex: 1; min-width: 180px; padding: 0.7rem 0.9rem; font-size: 1.1rem; border: 1px solid #d6dde2; border-radius: 6px; background: #fff; letter-spacing: 0.1em; text-transform: uppercase; font-family: 'Poppins', sans-serif; }
        .in-form button { background: #149E91; color: #fff; border: 0; border-radius: 6px; padding: 0.7rem 1.3rem; font-weight: 600; cursor: pointer; font-size: 1rem; }
        .in-form button:hover { background: #117f74; }
        .in-sex-tabs { display: flex; gap: 0.4rem; margin: 0 0 1rem; }
        .in-sex-tab { background: #fff; border: 1px solid #d6dde2; color: #1B2440; padding: 0.4rem 0.95rem; border-radius: 20px; cursor: pointer; font-size: 0.9rem; font-weight: 500; }
        .in-sex-tab.is-active { background: #1B2440; color: #fff; border-color: #1B2440; }
        .in-error { background: #fdecea; border-left: 4px solid #c0392b; padding: 0.7rem 1rem; border-radius: 6px; color: #7a1f12; }
        .in-list { list-style: none; padding: 0; margin: 1.25rem 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 0.5rem; }
        .in-combo { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.7rem 0.95rem; font-size: 1rem; color: #1B2440; font-family: 'Poppins', sans-serif; font-weight: 500; }
        .in-combo .in-first { color: #149E91; text-decoration: none; font-weight: 600; }
        .in-combo .in-first:hover { text-decoration: underline; }
        .in-actions { display: flex; gap: 0.6rem; align-items: center; flex-wrap: wrap; margin-top: 1rem; }
        .in-actions button { background: #fff; border: 1px solid #d6dde2; color: #1B2440; border-radius: 6px; padding: 0.5rem 1rem; cursor: pointer; font-weight: 500; }
        .in-actions button:hover { border-color: #149E91; color: #149E91; }
        .pk-tabs { display: flex; gap: 0.4rem; margin: 1.5rem 0 1.25rem; flex-wrap: wrap; }
        .pk-tab { background: #fff; border: 1px solid #d6dde2; color: #1B2440; padding: 0.5rem 1.1rem; border-radius: 22px; cursor: pointer; font-size: 0.95rem; font-weight: 500; }
        .pk-tab.is-active { background: #1B2440; color: #fff; border-color: #1B2440; }
        .pk-panel { background: #fff; border: 1px solid #d6dde2; border-radius: 10px; padding: 1.25rem 1.4rem 1.5rem; }
        .pk-controls { display: flex; gap: 0.75rem 1.25rem; flex-wrap: wrap; align-items: center; margin-bottom: 1rem; font-size: 0.92rem; color: #5B6678; }
        .pk-controls label { display: inline-flex; align-items: center; gap: 0.35rem; }
        .pk-controls select { padding: 0.35rem 0.5rem; border: 1px solid #d6dde2; border-radius: 5px; background: #fff; font-size: 0.92rem; color: #1B2440; }
        .pk-controls .pk-pill-group { display: inline-flex; gap: 0.3rem; flex-wrap: wrap; }
        .pk-pill { background: #fff; border: 1px solid #d6dde2; color: #1B2440; padding: 0.3rem 0.75rem; border-radius: 18px; cursor: pointer; font-size: 0.85rem; }
        .pk-pill.is-active { background: #149E91; color: #fff; border-color: #149E91; }
        #pk-card { background: linear-gradient(135deg, #fff 0%, #f7fbfa 100%); border: 1px solid #d6dde2; border-radius: 14px; padding: 2.5rem 1.5rem 2rem; text-align: center; min-height: 200px; box-shadow: 0 4px 12px rgba(27,36,64,0.08); cursor: grab; user-select: none; touch-action: pan-y; transition: transform 0.18s ease-out; }
        #pk-card:active { cursor: grabbing; transition: none; }
        #pk-card h2 { font-size: 2.5rem; margin: 0; }
        #pk-card h2 a { color: #1B2440; text-decoration: none; }
        #pk-card h2 a:hover { color: #149E91; }
        #pk-card .pk-card-meta { color: #5B6678; margin-top: 0.75rem; font-size: 0.95rem; }
        .pk-swipe-buttons { display: flex; gap: 0.75rem; justify-content: center; margin-top: 1.25rem; flex-wrap: wrap; }
        .pk-swipe-buttons button { padding: 0.7rem 1.4rem; border-radius: 24px; border: 0; cursor: pointer; font-weight: 600; font-size: 0.95rem; }
        .pk-btn-skip { background: #EEF2F4; color: #5B6678; }
        .pk-btn-skip:hover { background: #dde3e8; }
        .pk-btn-save { background: #FF6B5C; color: #fff; }
        .pk-btn-save:hover { background: #e85a4c; }
        .pk-btn-undo { background: #fff; border: 1px solid #d6dde2 !important; color: #1B2440; }
        .pk-btn-restart { background: #149E91; color: #fff; }
        #pk-status { color: #149E91; text-align: center; min-height: 1.2em; margin-top: 0.75rem; font-size: 0.9rem; font-weight: 500; }
        #pk-exhausted { text-align: center; padding: 2rem 1rem; color: #5B6678; }
        #pk-filter-count { color: #5B6678; font-size: 0.9rem; margin: 0.5rem 0 1rem; }
        #pk-filter-results, #pk-random-results { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.6rem; }
        .pk-grid-card { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.7rem 0.9rem; text-decoration: none; display: flex; flex-direction: column; gap: 0.2rem; transition: border-color 0.15s, transform 0.1s; }
        .pk-grid-card:hover { border-color: #149E91; transform: translateY(-1px); }
        .pk-grid-name { font-weight: 600; color: #1B2440; font-size: 1rem; }
        .pk-grid-meta { font-size: 0.78rem; color: #5B6678; }
        .pk-random-form { display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center; margin-bottom: 1rem; }
        .pk-random-form button { background: #149E91; color: #fff; border: 0; border-radius: 6px; padding: 0.6rem 1.2rem; font-weight: 600; cursor: pointer; }
        .pk-random-form button:hover { background: #117f74; }
        .pk-random-actions { display: flex; gap: 0.75rem; align-items: center; margin-top: 1rem; flex-wrap: wrap; }
        #pk-r-share, #pk-r-again { background: #fff; border: 1px solid #d6dde2; color: #1B2440; border-radius: 6px; padding: 0.5rem 1rem; cursor: pointer; font-weight: 500; }
        #pk-r-share:hover, #pk-r-again:hover { border-color: #149E91; color: #149E91; }
        #pk-r-share-done { color: #149E91; font-size: 0.9rem; }
        .sib-form { display: flex; gap: 0.6rem; margin: 1.5rem 0 1rem; flex-wrap: wrap; align-items: flex-start; }
        .sib-inputs { display: flex; flex-direction: column; gap: 0.5rem; flex: 1; min-width: 220px; }
        .sib-row { display: flex; gap: 0.4rem; align-items: stretch; }
        .sib-row .ac-wrap { position: relative; flex: 1; min-width: 0; }
        .sib-row input { width: 100%; box-sizing: border-box; padding: 0.7rem 0.9rem; font-size: 1rem; border: 1px solid #d6dde2; border-radius: 6px; background: #fff; }
        .sib-remove { background: none; border: 1px solid #d6dde2; color: #5B6678; border-radius: 6px; padding: 0 0.7rem; cursor: pointer; font-size: 1.3rem; line-height: 1; }
        .sib-remove:hover { color: #c0392b; border-color: #c0392b; }
        .sib-add { background: transparent; border: 1px dashed #149E91; color: #149E91; border-radius: 6px; padding: 0.55rem 0.9rem; cursor: pointer; font-size: 0.92rem; font-weight: 500; }
        .sib-add:hover { background: #EFF8F6; }
        .sib-add:disabled { opacity: 0.4; cursor: not-allowed; }
        .sib-submit { background: #149E91; color: #fff; border: 0; border-radius: 6px; padding: 0.7rem 1.3rem; font-weight: 600; cursor: pointer; font-size: 1rem; align-self: flex-start; }
        .sib-submit:hover { background: #117f74; }
        .sib-form-actions { display: flex; gap: 0.4rem; flex-wrap: wrap; align-items: flex-start; }
        .sib-ac { position: absolute; left: 0; right: 0; top: 100%; background: #fff; border: 1px solid #d6dde2; border-top: 0; border-radius: 0 0 6px 6px; max-height: 260px; overflow-y: auto; z-index: 10; }
        .sib-ac div { padding: 0.5rem 0.9rem; cursor: pointer; }
        .sib-ac div:hover, .sib-ac div.sel { background: #EEF2F4; }
        .sib-sex-tabs { display: flex; gap: 0.4rem; margin: 0 0 1rem; }
        .sib-sex-tab { background: #fff; border: 1px solid #d6dde2; color: #1B2440; padding: 0.4rem 0.95rem; border-radius: 20px; cursor: pointer; font-size: 0.9rem; font-weight: 500; }
        .sib-sex-tab.is-active { background: #1B2440; color: #fff; border-color: #1B2440; }
        #sib-note { background: #FFF4D6; border-left: 4px solid #f0c14b; padding: 0.7rem 0.95rem; border-radius: 6px; color: #5b4a16; margin: 0.75rem 0 1rem; font-size: 0.9rem; }
        #sib-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 0.6rem; margin-top: 1rem; }
        .sib-card { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.7rem 0.9rem; text-decoration: none; display: flex; flex-direction: column; gap: 0.3rem; transition: border-color 0.15s, transform 0.1s; }
        .sib-card:hover { border-color: #149E91; transform: translateY(-1px); }
        .sib-name { font-weight: 600; color: #1B2440; font-size: 1rem; }
        .sib-meta { font-size: 0.78rem; color: #5B6678; }
        .sib-more-wrap { text-align: center; margin-top: 1.5rem; }
        #sib-more { background: #fff; border: 1px solid #d6dde2; color: #1B2440; padding: 0.6rem 1.4rem; border-radius: 24px; cursor: pointer; font-weight: 500; font-size: 0.92rem; }
        #sib-more:hover { border-color: #149E91; color: #149E91; }
        .sib-share-wrap { margin: 0.4rem 0 0.2rem; display: flex; align-items: center; gap: 0.7rem; }
        .sib-share-btn { background: #fff; border: 1px solid #d6dde2; color: #1B2440; padding: 0.35rem 0.95rem; border-radius: 20px; cursor: pointer; font-weight: 500; font-size: 0.85rem; text-decoration: none; display: inline-block; }
        .sib-share-btn:hover { border-color: #149E91; color: #149E91; }
        #sib-share-tg { background: #2AABEE; border-color: #2AABEE; color: #fff; }
        #sib-share-tg:hover { background: #1f96d3; border-color: #1f96d3; color: #fff; }
        .sib-share-done { color: #149E91; font-size: 0.85rem; font-weight: 500; }
"""


def country_switcher_html() -> str:
    """Country dropdown — white pill in nav, opens to a vertical list with flags + country codes."""
    active_name = COUNTRY_NAMES_IN_UI[ACTIVE_CC][ACTIVE_CC]
    items = []
    for c in COUNTRIES:
        href = home_path(c)
        flag = f'<span class="cc-flag" aria-hidden="true">{FLAG[c]}</span>'
        label = COUNTRY_NAMES_IN_UI[ACTIVE_CC][c]
        if c == ACTIVE_CC:
            items.append(
                f'<a class="cc-dd-item is-current" href="{href}" role="option" aria-selected="true">'
                f'{flag}{label}<span class="cc-check" aria-hidden="true">✓</span></a>'
            )
        else:
            items.append(
                f'<a class="cc-dd-item" href="{href}" role="option">'
                f'{flag}{label}<span class="cc-code">{COUNTRY_LABEL[c]}</span></a>'
            )
    return (
        '<div class="cc-dd">'
        '<button type="button" class="cc-dd-btn" aria-haspopup="listbox" aria-expanded="false">'
        f'<span class="cc-flag" aria-hidden="true">{FLAG[ACTIVE_CC]}</span>'
        f'<span class="cc-dd-label">{active_name}</span>'
        '<span class="cc-caret" aria-hidden="true">▾</span>'
        '</button>'
        f'<div class="cc-dd-menu" role="listbox" aria-label="{S("nav_choose_country")}">'
        f'<div class="cc-dd-heading">{S("nav_choose_country")}</div>'
        + ''.join(items)
        + '</div></div>'
    )


def nav_tools_script() -> str:
    """Generic dropdown handler. Drives both .nav-dd (Explore/Tools) and .cc-dd
    (country picker) on every page. Clicking one closes any other open dropdown."""
    return """
    <script>
    (function() {
        var dropdowns = Array.prototype.slice.call(document.querySelectorAll('.nav-dd, .cc-dd'));
        if (!dropdowns.length) return;
        function closeAll(except) {
            dropdowns.forEach(function(dd) {
                if (dd === except) return;
                dd.classList.remove('is-open');
                var b = dd.querySelector('.nav-dd-btn, .cc-dd-btn');
                if (b) b.setAttribute('aria-expanded', 'false');
            });
        }
        dropdowns.forEach(function(dd) {
            var btn = dd.querySelector('.nav-dd-btn, .cc-dd-btn');
            if (!btn) return;
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                var open = dd.classList.toggle('is-open');
                btn.setAttribute('aria-expanded', open ? 'true' : 'false');
                if (open) closeAll(dd);
            });
        });
        document.addEventListener('click', function(e) {
            var inside = dropdowns.some(function(dd) { return dd.contains(e.target); });
            if (!inside) closeAll(null);
        });
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') closeAll(null);
        });

        // Nav search icon focuses the hero search if present, otherwise
        // routes to the home page where it exists.
        var navSearch = document.getElementById('navSearchBtn');
        if (navSearch) {
            navSearch.addEventListener('click', function() {
                var input = document.getElementById('searchInput');
                if (input) {
                    input.scrollIntoView({behavior: 'smooth', block: 'center'});
                    setTimeout(function() { input.focus(); }, 300);
                } else {
                    window.location.href = navSearch.getAttribute('data-home') || '/';
                }
            });
        }
    })();
    </script>"""


def site_nav_html() -> str:
    p = PREFIX
    blog_link = f'<a href="{p}/blog/" class="nav-link">{S("nav_blog")}</a>' if BLOG_POSTS_BY_CC.get(ACTIVE_CC) else ''
    saints_link = ''
    if ACTIVE_CC in ('FR', 'ES', 'IT') and SAINTS_BY_CC.get(ACTIVE_CC):
        saints_path = {'FR': 'jour-de-fete.html', 'ES': 'dia-del-santo.html', 'IT': 'onomastico.html'}[ACTIVE_CC]
        saints_link = f'<a href="{p}/{saints_path}" role="menuitem">{S("nav_saints")}</a>'
    return f"""
    <div class="sitenav"><div class="sitenav-inner">
        <a class="brand" href="{home_path()}"><svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true"><rect x="1" y="1" width="30" height="30" rx="7" fill="#149E91"/><polyline points="6,22 12,17 17,20 24,10" fill="none" stroke="#FFFFFF" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="24" cy="10" r="3" fill="#FF6B5C"/></svg><span>Name<span class="wm-teal">Charted</span></span></a>
        <div class="nav-links">
            <a href="{home_path()}" class="nav-link">{S("nav_home")}</a>
            <div class="nav-dd">
                <button type="button" class="nav-dd-btn" aria-haspopup="true" aria-expanded="false">{S("nav_explore")} <span class="nav-dd-caret" aria-hidden="true">▾</span></button>
                <div class="nav-dd-menu" role="menu">
                    <a href="{p}/names.html" role="menuitem">{S("nav_browse")}</a>
                    <a href="{p}/trends.html" role="menuitem">{S("nav_trends")}</a>
                    <a href="{p}/decades.html" role="menuitem">{S("nav_decades")}</a>
                    <a href="{p}/year/{LATEST_YEAR}.html" role="menuitem">{S("nav_rankings", year=LATEST_YEAR)}</a>
                    <a href="{p}/rare-names.html" role="menuitem">{S("crumb_rare")}</a>
                    <a href="{p}/favorites.html" role="menuitem">{S("nav_favorites")}<span class="fav-nav-count"></span></a>
                </div>
            </div>
            <div class="nav-dd">
                <button type="button" class="nav-dd-btn" aria-haspopup="true" aria-expanded="false">{S("nav_tools")} <span class="nav-dd-caret" aria-hidden="true">▾</span></button>
                <div class="nav-dd-menu" role="menu">
                    <a href="{p}/compare.html" role="menuitem">{S("nav_compare")}</a>
                    <a href="{p}/works-with.html" role="menuitem">{S("nav_works_with")}</a>
                    <a href="{p}/picker.html" role="menuitem">{S("nav_picker")}</a>
                    <a href="{p}/sibling.html" role="menuitem">{S("nav_sibling")}</a>
                    <a href="{p}/initials.html" role="menuitem">{S("nav_initials")}</a>
                    <a href="{p}/origins.html" role="menuitem">{S("nav_origins")}</a>
                    <a href="{p}/fiction.html" role="menuitem">{S("nav_fiction")}</a>
                    {saints_link}
                </div>
            </div>
            {blog_link}
            <button type="button" class="nav-search-btn" id="navSearchBtn" data-home="{home_path()}" aria-label="{S("nav_search_aria")}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="20" y1="20" x2="16.65" y2="16.65"></line></svg></button>
        </div>
        {country_switcher_html()}
    </div></div>"""


def footer_html() -> str:
    return f"""
        <div class="footer">
            <p>&copy; 2026 NameCharted</p>
            <p style="font-size:0.75rem; color:#8a93a3; margin-top:0.25rem;">{S("footer_data", source=data_source_full(), range=DATA_RANGE)}</p>
        </div>"""


def page(title, body, description="", canonical="", extra_head="",
         og_image_url="", og_image_w=1200, og_image_h=630):
    desc_tag = f'\n    <meta name="description" content="{description}">' if description else ""
    canon_tag = f'\n    <link rel="canonical" href="{canonical}">' if canonical else ""
    og = ""
    if description:
        # Country flag in social-card title — cheapest way to make /fr/ vs /
        # vs /uk/ vs /au/ visually distinct in Twitter/Facebook previews,
        # without needing four separate OG images.
        og_title = f"{FLAG[ACTIVE_CC]} {title}"
        img_url = og_image_url or f"{BASE_URL}/og-default.png"
        og = (
            f'\n    <meta property="og:title" content="{og_title}">'
            f'\n    <meta property="og:description" content="{description}">'
            f'\n    <meta property="og:type" content="website">'
            f'\n    <meta property="og:site_name" content="NameCharted">'
            f'\n    <meta property="og:locale" content="{"fr_FR" if ACTIVE_CC == "FR" else ("en_GB" if ACTIVE_CC == "GB" else ("en_AU" if ACTIVE_CC == "AU" else "en_US"))}">'
            f'\n    <meta property="og:image" content="{img_url}">'
            f'\n    <meta property="og:image:width" content="{og_image_w}">'
            f'\n    <meta property="og:image:height" content="{og_image_h}">'
            f'\n    <meta name="twitter:card" content="summary_large_image">'
            f'\n    <meta name="twitter:title" content="{og_title}">'
            f'\n    <meta name="twitter:description" content="{description}">'
            f'\n    <meta name="twitter:image" content="{img_url}">'
        )
        if canonical:
            og += f'\n    <meta property="og:url" content="{canonical}">'
    return f"""<!DOCTYPE html>
<html lang="{lang_attr()}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="p:domain_verify" content="872e3998c36a9123b5ec260a9f351adf"/>
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-Q5KY6BP0VV"></script>
    <script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-Q5KY6BP0VV');</script>
    <title>{title}</title>{desc_tag}{canon_tag}{og}
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <link rel="apple-touch-icon" href="/apple-touch-icon.png">
    <link rel="manifest" href="/manifest.webmanifest">
    <meta name="theme-color" content="#149E91">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-title" content="NameCharted">
    <meta name="mobile-web-app-capable" content="yes">
    <style>{BASE_CSS}</style>{extra_head}
</head>
<body>{site_nav_html()}
    <div class="container">
{body}
{footer_html()}
    </div>{nav_tools_script()}{lang_banner_script()}{favorites_script()}{compare_script()}{works_with_script()}{picker_script()}{sibling_script()}{saints_script()}{initials_script()}
</body>
</html>"""


def person_jsonld_block(famous: list, given_name: str) -> str:
    """Emit one Person entity per bearer in the famous-list. Each carries the
    Wikipedia URL as `sameAs` so search engines can fuse with the existing
    knowledge graph entry — and `givenName` to make the link to the name page
    explicit."""
    out = []
    for p in famous[:5]:
        person = {
            "@context": "https://schema.org",
            "@type": "Person",
            "name": p.get('name'),
            "givenName": given_name,
        }
        if p.get('url'):
            person["sameAs"] = p['url']
        if p.get('occupation'):
            person["jobTitle"] = p['occupation']
        if p.get('born') and p['born'] > 0:
            person["birthDate"] = str(p['born'])
        out.append('\n    <script type="application/ld+json">' + json.dumps(person, ensure_ascii=False) + '</script>')
    return ''.join(out)


def itemlist_jsonld(name_url_pairs: list, list_name: str) -> str:
    """ItemList of name → URL pairs. Used on year + decade pages so search
    engines can surface 'top names of 2024' carousels."""
    elements = []
    for i, (nm, url) in enumerate(name_url_pairs, 1):
        elements.append({
            "@type": "ListItem",
            "position": i,
            "name": nm,
            "url": url,
        })
    data = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": list_name,
        "itemListElement": elements,
    }
    return '\n    <script type="application/ld+json">' + json.dumps(data, ensure_ascii=False) + '</script>'


def breadcrumb_jsonld(items):
    elements = []
    for i, (nm, url) in enumerate(items):
        el = {"@type": "ListItem", "position": i + 1, "name": nm}
        if url:
            el["item"] = url
        elements.append(el)
    data = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": elements,
    }
    return '\n    <script type="application/ld+json">' + json.dumps(data) + '</script>'


# ---------------------------------------------------------------------------
# Homepage
# ---------------------------------------------------------------------------
def generate_homepage():
    p = PREFIX
    items = ""
    for name, total in top_names[:20]:
        dom = dominant_sex(name)
        items += (
            f'                <li><a href="{p}/name/{slugify(name)}.html"><h3>{name}</h3></a>'
            f'<p>{S("home_total_babies", n=fmt(total))}</p>'
            f'<p style="font-size:0.8rem">{S("home_mostly", label=loc_label(dom))}</p></li>\n'
        )
    samples = ", ".join(n for n, _ in top_names[:5])
    n_pages = len(pages_to_generate)
    # Top 5 names as clickable chips, leading the user to a real name page.
    chip_names = [n for n, _ in top_names[:5]]
    chips_html = "".join(
        f'<a href="{p}/name/{slugify(n)}.html" class="chip">{n}</a>'
        for n in chip_names
    )
    chip_samples = ", ".join(chip_names[:3])
    # Stats strip — derived from real data
    years_count = LATEST_YEAR - 1880 + 1
    countries_count = len(COUNTRIES)
    # Range short for subhead (e.g. "1880 to 2024" / "1880 à 2024") — uses
    # the locale's home_range_short connector when provided.
    range_short = S("home_range_short", start=1880, end=LATEST_YEAR)
    saints_callout = '<div id="sf-today-hp" class="sf-today" style="display:none;"></div>\n' if ACTIVE_CC == 'FR' else ''

    # Tool card data: (slug + html, gradient c1, gradient c2, icon char, name key, desc key)
    tool_cards = [
        ('compare.html',    '#149E91', '#0E7A70', '⇄',   'nav_compare',    'tool_desc_compare'),
        ('picker.html',     '#F4A340', '#E0871D', '✦',   'nav_picker',     'tool_desc_picker'),
        ('sibling.html',    '#6C8FE0', '#4B6FC4', '♕',   'nav_sibling',    'tool_desc_sibling'),
        ('works-with.html', '#D26A8C', '#B14770', 'A·B', 'nav_works_with', 'tool_desc_works_with'),
        ('initials.html',   '#7DBC6F', '#599A4C', '◉',   'nav_initials',   'tool_desc_initials'),
        ('origins.html',    '#9A6BD0', '#7847B5', '⊕',   'nav_origins',    'tool_desc_origins'),
        ('fiction.html',    '#E07A6B', '#C0584A', '✎',   'nav_fiction',    'tool_desc_fiction'),
        ('decades.html',    '#2C8FB5', '#1C6F90', '⌛',   'nav_decades',    'tool_desc_decades'),
    ]

    def _card(slug, c1, c2, icon, name_key, desc_key, dup=False):
        attrs = ' aria-hidden="true" tabindex="-1"' if dup else ''
        return (
            f'<a class="tool-card" href="{p}/{slug}" style="--c1:{c1};--c2:{c2};"{attrs}>'
            f'<div class="tool-icon">{icon}</div>'
            f'<div class="tool-body"><div class="tool-name">{S(name_key)}</div>'
            f'<div class="tool-desc">{S(desc_key)}</div></div></a>'
        )

    # Row A: first 4 tools (and dup). Row B: last 4 tools (and dup).
    row_a = "".join(_card(*c) for c in tool_cards[:4]) + "".join(_card(*c, dup=True) for c in tool_cards[:4])
    row_b = "".join(_card(*c) for c in tool_cards[4:]) + "".join(_card(*c, dup=True) for c in tool_cards[4:])

    # Inline-JS template placeholders. Pre-quoted so the f-string interpolates
    # them as JS string literals without double-quoting headaches.
    pause_label = json.dumps(S("home_race_pause"))
    play_label = json.dumps(S("home_race_play"))
    no1_tpl = json.dumps(S("home_race_no1", name='{name}'))
    p_json = json.dumps(p)

    body = f"""        <section class="home-hero">
            <div class="home-hero-inner">
                <h1>{S("home_h1")}</h1>
                <p class="home-hero-sub">{S("home_subhead", range_short=range_short)}</p>
                <div class="home-search-wrap">
                    <form class="home-search" onsubmit="event.preventDefault();var i=document.getElementById('searchInput');if(i&&i.value)i.dispatchEvent(new Event('input'));">
                        <svg class="home-search-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="20" y1="20" x2="16.65" y2="16.65"></line></svg>
                        <input type="text" id="searchInput" autocomplete="off" placeholder="{S("home_search_placeholder_v2", samples=chip_samples)}">
                        <button type="submit" class="go"><span class="go-label">{S("home_search_cta")} </span>→</button>
                        <div id="searchAc"></div>
                    </form>
                    <div class="home-hint">{S("home_popular_label")} {chips_html}</div>
                    <p class="home-browse"><a href="{p}/names.html">{S("home_browse_chip", n=fmt(n_pages))}</a></p>
                </div>
                <div class="home-stats">
                    <div><b>{S("home_stats_years", n=years_count)}</b>{S("home_stats_years_sub")}</div>
                    <div><b>{S("home_stats_names", n=fmt(n_pages))}</b>{S("home_stats_names_sub")}</div>
                    <div><b>{S("home_stats_countries", n=countries_count)}</b>{S("home_stats_countries_sub")}</div>
                </div>
            </div>
            <div class="tool-strip" aria-label="{S("home_tools_label")}">
                <div class="tool-strip-label"><span class="tsl-line"></span><span class="tsl-text">{S("home_tools_label")}</span><span class="tsl-line"></span></div>
                <div class="tool-track-mask">
                    <div class="tool-track tool-track-a">{row_a}</div>
                    <div class="tool-track tool-track-b">{row_b}</div>
                </div>
            </div>
        </section>
{saints_callout}
        <section class="race-section">
            <div class="race-head">
                <div>
                    <h2>{S("home_race_h2")}</h2>
                    <p class="race-sub">{S("home_race_sub", range_short=range_short)}</p>
                </div>
                <div class="race-controls">
                    <button type="button" id="raceToggle" class="race-btn">⏸ {S("home_race_pause")}</button>
                    <button type="button" id="raceRestart" class="race-btn race-btn-ghost">↻ {S("home_race_restart")}</button>
                </div>
            </div>
            <div class="race-stage">
                <div class="race-year" id="raceYear">1880</div>
                <div class="race-split">
                    <div class="race-pane" data-sex="M">
                        <div class="race-pane-head">
                            <span class="race-pane-icon" aria-hidden="true">♂</span>
                            <span class="race-pane-title">{S("home_race_boys")}</span>
                            <span class="race-pane-tally" id="raceTallyM"></span>
                        </div>
                        <svg class="race-chart" data-sex="M" viewBox="0 0 800 320" preserveAspectRatio="none" aria-hidden="true">
                            <defs>
                                <linearGradient id="raceCursorM" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="0%" stop-color="#149E91" stop-opacity="0"/>
                                    <stop offset="50%" stop-color="#149E91" stop-opacity="0.7"/>
                                    <stop offset="100%" stop-color="#149E91" stop-opacity="0"/>
                                </linearGradient>
                            </defs>
                            <g class="race-grid"></g>
                            <g class="race-lines"></g>
                            <g class="race-dots"></g>
                            <line class="race-cursor" x1="0" y1="20" x2="0" y2="290" stroke="url(#raceCursorM)" stroke-width="2"/>
                            <g class="race-xlabels"></g>
                            <g class="race-ylabels"></g>
                        </svg>
                        <div class="race-legend" data-sex="M"></div>
                    </div>
                    <div class="race-pane" data-sex="F">
                        <div class="race-pane-head">
                            <span class="race-pane-icon" aria-hidden="true">♀</span>
                            <span class="race-pane-title">{S("home_race_girls")}</span>
                            <span class="race-pane-tally" id="raceTallyF"></span>
                        </div>
                        <svg class="race-chart" data-sex="F" viewBox="0 0 800 320" preserveAspectRatio="none" aria-hidden="true">
                            <defs>
                                <linearGradient id="raceCursorF" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="0%" stop-color="#D26A8C" stop-opacity="0"/>
                                    <stop offset="50%" stop-color="#D26A8C" stop-opacity="0.7"/>
                                    <stop offset="100%" stop-color="#D26A8C" stop-opacity="0"/>
                                </linearGradient>
                            </defs>
                            <g class="race-grid"></g>
                            <g class="race-lines"></g>
                            <g class="race-dots"></g>
                            <line class="race-cursor" x1="0" y1="20" x2="0" y2="290" stroke="url(#raceCursorF)" stroke-width="2"/>
                            <g class="race-xlabels"></g>
                            <g class="race-ylabels"></g>
                        </svg>
                        <div class="race-legend" data-sex="F"></div>
                    </div>
                </div>
                <div class="race-scrubber">
                    <input type="range" id="raceScrub" min="0" max="0" value="0" step="1" aria-label="Year">
                    <div class="race-decades" id="raceDecades"></div>
                </div>
            </div>
            <video class="race-video" autoplay loop muted playsinline preload="metadata" aria-label="{S("home_race_h2")}">
                <source src="{p}/top-race.webm" type="video/webm">
            </video>
            <p class="race-foot">{S("home_race_foot")} <a href="{p}/year/{LATEST_YEAR}.html">{S("home_race_foot_link")}</a></p>
        </section>

        <script>
        (function() {{
            var PAGES = null;       // ordered array (most popular first) for autocomplete ranking
            var PAGE_SET = null;
            var SSA_SET = null;
            function loadIndex() {{
                if (PAGES) return Promise.resolve();
                return fetch('{p}/name-index.json').then(function(r) {{ return r.json(); }})
                    .then(function(d) {{
                        PAGES = d.pages || [];
                        PAGE_SET = new Set(PAGES);
                        SSA_SET = new Set(d.ssa || []);
                    }});
            }}
            function slugify(s) {{
                return (s || '').toLowerCase()
                    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
            }}
            function display(slug) {{
                return slug.replace(/-/g, ' ').replace(/\\b\\w/g, function(c) {{ return c.toUpperCase(); }});
            }}
            function route(slug) {{
                if (!slug) return;
                if (PAGE_SET.has(slug)) {{ window.location.href = '{p}/name/' + slug + '.html'; return; }}
                if (SSA_SET.has(slug)) {{ window.location.href = '{p}/rare-names.html?q=' + encodeURIComponent(slug); return; }}
                window.location.href = '/404.html';
            }}

            var input = document.getElementById('searchInput');
            var ac = document.getElementById('searchAc');
            var sel = -1, items = [];

            function clear(el) {{ while (el.firstChild) el.removeChild(el.firstChild); }}
            function render(matches) {{
                clear(ac); items = matches;
                if (!matches.length) {{ ac.style.display = 'none'; return; }}
                ac.style.display = '';
                matches.forEach(function(slug, i) {{
                    var d = document.createElement('div');
                    d.textContent = display(slug);
                    if (i === sel) d.className = 'sel';
                    d.addEventListener('mousedown', function(e) {{ e.preventDefault(); route(slug); }});
                    ac.appendChild(d);
                }});
            }}
            function search() {{
                var q = slugify(input.value);
                if (!q || !PAGES) {{ ac.style.display = 'none'; return; }}
                // Scan the full popularity-ordered list so that niche prefix
                // matches (e.g. "Serene" for "ser") aren't crowded out by
                // popular substring matches (e.g. "Kaiser"). Cap each bucket
                // so the scan stays cheap on large lists.
                var starts = [], contains = [];
                for (var i = 0; i < PAGES.length; i++) {{
                    var s = PAGES[i];
                    if (s.indexOf(q) === 0) {{
                        if (starts.length < 10) starts.push(s);
                    }} else if (s.indexOf(q) > 0) {{
                        if (contains.length < 10) contains.push(s);
                    }}
                    if (starts.length >= 10 && contains.length >= 10) break;
                }}
                sel = -1;
                // Prefix matches always come first; fill the rest with
                // substring matches up to a 10-row dropdown.
                render(starts.concat(contains).slice(0, 10));
            }}
            input.addEventListener('input', function() {{ loadIndex().then(search); }});
            input.addEventListener('focus', function() {{ loadIndex().then(search); }});
            input.addEventListener('blur', function() {{ setTimeout(function() {{ ac.style.display = 'none'; }}, 150); }});
            input.addEventListener('keydown', function(e) {{
                if (e.key === 'Enter') {{
                    e.preventDefault();
                    if (sel >= 0 && items[sel]) {{ route(items[sel]); return; }}
                    var slug = slugify(input.value);
                    if (!slug) return;
                    loadIndex().then(function() {{ route(slug); }});
                    return;
                }}
                if (ac.style.display === 'none') return;
                if (e.key === 'ArrowDown') {{ sel = (sel + 1) % items.length; render(items); e.preventDefault(); }}
                else if (e.key === 'ArrowUp') {{ sel = (sel - 1 + items.length) % items.length; render(items); e.preventDefault(); }}
                else if (e.key === 'Escape') {{ ac.style.display = 'none'; }}
            }});
            loadIndex();
        }})();

        /* ---------- ANIMATED RACE CHART ---------- */
        (function() {{
            var stage = document.querySelector('.race-section');
            if (!stage) return;
            var yearEl = document.getElementById('raceYear');
            var scrub = document.getElementById('raceScrub');
            var toggle = document.getElementById('raceToggle');
            var restart = document.getElementById('raceRestart');
            var decadesEl = document.getElementById('raceDecades');
            var tallyM = document.getElementById('raceTallyM');
            var tallyF = document.getElementById('raceTallyF');
            var pauseLabel = '⏸ ' + {pause_label};
            var playLabel = '▶ ' + {play_label};
            var no1Tpl = {no1_tpl};
            var nameUrlPrefix = {p_json};

            // Stable color per name across the whole timeline.
            var PALETTE = ['#149E91','#F4A340','#1B2440','#6C8FE0','#2C8FB5','#9A6BD0','#7DBC6F','#E07A6B','#C28442','#D26A8C','#4B6FC4','#0E7A70','#B14770','#E0871D','#599A4C','#7847B5','#3E5BA6','#C0584A','#94772E','#8E5797'];
            var nameColors = {{}};
            function colorFor(name) {{
                if (nameColors[name]) return nameColors[name];
                // Deterministic hash so order doesn't matter
                var h = 0; for (var i = 0; i < name.length; i++) {{ h = (h * 31 + name.charCodeAt(i)) | 0; }}
                var c = PALETTE[Math.abs(h) % PALETTE.length];
                nameColors[name] = c;
                return c;
            }}
            function slugify(s) {{
                return (s || '').toLowerCase()
                    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
            }}
            function fmtK(v) {{ return v >= 1000 ? (v / 1000).toFixed(1) + 'k' : Math.round(v); }}

            var SVG_NS = 'http://www.w3.org/2000/svg';
            function svgEl(name) {{ return document.createElementNS(SVG_NS, name); }}

            var VB_W = 800, VB_H = 320;
            var PAD = {{ l: 44, r: 12, t: 16, b: 32 }};
            var PW = VB_W - PAD.l - PAD.r;
            var PH = VB_H - PAD.t - PAD.b;

            function pathOf(pts, xOf, yOf) {{
                if (!pts.length) return '';
                var d = 'M ' + xOf(pts[0][0]).toFixed(2) + ' ' + yOf(pts[0][1]).toFixed(2);
                for (var i = 1; i < pts.length; i++) {{
                    var x0 = xOf(pts[i-1][0]), y0 = yOf(pts[i-1][1]);
                    var x1 = xOf(pts[i][0]),   y1 = yOf(pts[i][1]);
                    var cx = (x0 + x1) / 2;
                    d += ' C ' + cx.toFixed(2) + ' ' + y0.toFixed(2) + ', ' + cx.toFixed(2) + ' ' + y1.toFixed(2) + ', ' + x1.toFixed(2) + ' ' + y1.toFixed(2);
                }}
                return d;
            }}

            function buildPane(sex, data, years, yMax, xOf, yOf) {{
                var pane = document.querySelector('.race-pane[data-sex="' + sex + '"]');
                var svg = pane.querySelector('.race-chart');
                var gGrid = svg.querySelector('.race-grid');
                var gLines = svg.querySelector('.race-lines');
                var gDots = svg.querySelector('.race-dots');
                var gX = svg.querySelector('.race-xlabels');
                var gY = svg.querySelector('.race-ylabels');
                var cursor = svg.querySelector('.race-cursor');
                var legend = pane.querySelector('.race-legend');

                // Build a clipPath that grows in width by year. All lines are
                // clipped at the same X, so a name that peaks sharply doesn't
                // race ahead of a flatter name when revealed.
                var clipId = 'raceClip-' + sex + '-' + Math.random().toString(36).slice(2, 8);
                var defs = svgEl('defs');
                var clip = svgEl('clipPath');
                clip.setAttribute('id', clipId);
                var clipRect = svgEl('rect');
                clipRect.setAttribute('class', 'race-clip-rect');
                clipRect.setAttribute('x', PAD.l);
                clipRect.setAttribute('y', 0);
                clipRect.setAttribute('width', 0);
                clipRect.setAttribute('height', VB_H);
                clip.appendChild(clipRect);
                defs.appendChild(clip);
                svg.insertBefore(defs, svg.firstChild);
                gLines.setAttribute('clip-path', 'url(#' + clipId + ')');

                [0, 0.5, 1].forEach(function(t) {{
                    var v = t * yMax;
                    var ln = svgEl('line');
                    ln.setAttribute('x1', PAD.l); ln.setAttribute('x2', VB_W - PAD.r);
                    ln.setAttribute('y1', yOf(v)); ln.setAttribute('y2', yOf(v));
                    if (v === 0) ln.setAttribute('class', 'axis');
                    gGrid.appendChild(ln);
                    var tx = svgEl('text');
                    tx.setAttribute('x', PAD.l - 8); tx.setAttribute('y', yOf(v) + 4);
                    tx.textContent = v >= 1000 ? (v / 1000).toFixed(0) + 'k' : Math.round(v);
                    gY.appendChild(tx);
                }});
                var YR_MIN = years[0], YR_MAX = years[years.length - 1];
                var labelYears = [YR_MIN, Math.round((YR_MIN + YR_MAX)/2 - (YR_MAX-YR_MIN)/4), Math.round((YR_MIN+YR_MAX)/2), Math.round((YR_MIN+YR_MAX)/2 + (YR_MAX-YR_MIN)/4), YR_MAX];
                labelYears.forEach(function(yr) {{
                    var t = svgEl('text');
                    t.setAttribute('x', xOf(yr)); t.setAttribute('y', VB_H - 10);
                    t.setAttribute('text-anchor', 'middle');
                    t.textContent = yr;
                    gX.appendChild(t);
                }});

                // Build per-name continuous series across all years.
                // Names appearing in any year's top-10 get a line; in years they're
                // not in top-10 we plot 0 so the line naturally rises/falls in/out.
                var names = {{}};
                data.forEach(function(top10) {{ top10.forEach(function(row) {{ names[row[0]] = true; }}); }});
                var seriesByName = {{}};
                Object.keys(names).forEach(function(n) {{ seriesByName[n] = []; }});
                data.forEach(function(top10, i) {{
                    var year = years[i];
                    var present = {{}};
                    top10.forEach(function(row) {{
                        seriesByName[row[0]].push([year, row[1]]);
                        present[row[0]] = true;
                    }});
                    Object.keys(seriesByName).forEach(function(n) {{
                        if (!present[n]) seriesByName[n].push([year, 0]);
                    }});
                }});

                var SERIES = Object.keys(seriesByName).map(function(name) {{
                    var pts = seriesByName[name];
                    var color = colorFor(name);
                    var path = svgEl('path');
                    path.setAttribute('stroke', color);
                    path.setAttribute('d', pathOf(pts, xOf, yOf));
                    gLines.appendChild(path);
                    var dot = svgEl('circle');
                    dot.setAttribute('r', '4.5');
                    dot.setAttribute('fill', color);
                    dot.setAttribute('stroke', '#fff');
                    dot.setAttribute('stroke-width', '2');
                    dot.setAttribute('cx', xOf(YR_MIN));
                    dot.setAttribute('cy', yOf(0));
                    dot.setAttribute('opacity', '0');
                    gDots.appendChild(dot);
                    return {{ name: name, color: color, pts: pts, path: path, dot: dot }};
                }});

                var legendItems = [];
                for (var li = 0; li < 4; li++) {{
                    var row = document.createElement('div');
                    row.className = 'race-legend-item';
                    var sw = document.createElement('span'); sw.className = 'race-legend-swatch';
                    var link = document.createElement('a');
                    link.className = 'race-legend-name'; link.style.color = '#1B2440'; link.style.textDecoration = 'none';
                    var ct = document.createElement('span'); ct.className = 'race-legend-count';
                    row.appendChild(sw); row.appendChild(link); row.appendChild(ct);
                    legend.appendChild(row);
                    legendItems.push({{ row: row, sw: sw, link: link, ct: ct }});
                }}

                return {{ series: SERIES, cursor: cursor, legendItems: legendItems, sex: sex, clipRect: clipRect }};
            }}

            var DATA = null;
            function init(json) {{
                var years = json.years;
                var allTop1 = 0;
                ['M', 'F'].forEach(function(sex) {{
                    json[sex].forEach(function(top10) {{
                        if (top10.length && top10[0][1] > allTop1) allTop1 = top10[0][1];
                    }});
                }});
                var yMax = Math.ceil(allTop1 / 10000) * 10000;
                function xOf(year) {{ return PAD.l + ((year - years[0]) / (years[years.length-1] - years[0])) * PW; }}
                function yOf(val)  {{ return PAD.t + (1 - val / yMax) * PH; }}

                var PANES = {{
                    M: buildPane('M', json.M, years, yMax, xOf, yOf),
                    F: buildPane('F', json.F, years, yMax, xOf, yOf)
                }};
                DATA = {{ years: years, yMax: yMax, xOf: xOf, yOf: yOf, panes: PANES }};

                scrub.max = String(years.length - 1);
                while (decadesEl.firstChild) decadesEl.removeChild(decadesEl.firstChild);
                var decadeStep = years.length < 60 ? 20 : 40;
                var seenLabels = {{}};
                years.forEach(function(y, i) {{
                    if (y % decadeStep === 0 || i === years.length - 1) {{
                        if (seenLabels[y]) return;
                        seenLabels[y] = true;
                        var s = document.createElement('span'); s.textContent = y; decadesEl.appendChild(s);
                    }}
                }});

                render(0);
                if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {{
                    playing = false; toggle.textContent = playLabel;
                    frameIdx = years.length - 1; render(frameIdx);
                }} else {{
                    start();
                }}
            }}

            function renderPane(pane, frameIdx, tallyEl, jsonForSex) {{
                var cx = DATA.xOf(DATA.years[frameIdx]);
                var topVal = 0, topName = '';
                // Grow the per-pane clip rectangle to the year position. All
                // lines clip at the same X, so the leading edge stays unified
                // across every name regardless of each curve's total length.
                pane.clipRect.setAttribute('width', Math.max(0, cx - PAD.l));
                pane.series.forEach(function(s) {{
                    var cur = s.pts[frameIdx];
                    s.dot.setAttribute('cx', DATA.xOf(cur[0]));
                    s.dot.setAttribute('cy', DATA.yOf(cur[1]));
                    s.dot.setAttribute('opacity', cur[1] > DATA.yMax * 0.02 ? '1' : '0');
                    s.curVal = cur[1];
                    if (cur[1] > topVal) {{ topVal = cur[1]; topName = s.name; }}
                }});
                pane.cursor.setAttribute('x1', cx);
                pane.cursor.setAttribute('x2', cx);

                var sorted = pane.series.slice().sort(function(a, b) {{ return b.curVal - a.curVal; }});
                for (var i = 0; i < pane.legendItems.length; i++) {{
                    var s = sorted[i];
                    if (!s) {{ pane.legendItems[i].row.style.visibility = 'hidden'; continue; }}
                    pane.legendItems[i].row.style.visibility = '';
                    pane.legendItems[i].sw.style.background = s.color;
                    pane.legendItems[i].sw.style.boxShadow = '0 0 0 2px ' + s.color + '30';
                    pane.legendItems[i].link.textContent = s.name;
                    pane.legendItems[i].link.href = nameUrlPrefix + '/name/' + slugify(s.name) + '.html';
                    pane.legendItems[i].ct.textContent = fmtK(s.curVal);
                }}
                if (tallyEl) tallyEl.textContent = topName ? no1Tpl.replace('{{name}}', topName) : '';
            }}

            function render(frameIdx) {{
                yearEl.textContent = DATA.years[frameIdx];
                renderPane(DATA.panes.M, frameIdx, tallyM);
                renderPane(DATA.panes.F, frameIdx, tallyF);
            }}

            var frameIdx = 0;
            var playing = true;
            var FRAME_MS = 90;
            var timer = null;
            function step() {{
                if (!DATA) return;
                render(frameIdx);
                scrub.value = String(frameIdx);
                if (frameIdx >= DATA.years.length - 1) frameIdx = 0;
                else frameIdx++;
            }}
            function start() {{ stop(); timer = setInterval(step, FRAME_MS); }}
            function stop() {{ if (timer) clearInterval(timer); timer = null; }}
            toggle.addEventListener('click', function() {{
                if (!DATA) return;
                playing = !playing;
                if (playing) {{ toggle.textContent = pauseLabel; start(); }}
                else {{ toggle.textContent = playLabel; stop(); }}
            }});
            restart.addEventListener('click', function() {{ if (!DATA) return; frameIdx = 0; step(); }});
            scrub.addEventListener('input', function() {{
                if (!DATA) return;
                playing = false; toggle.textContent = playLabel; stop();
                frameIdx = parseInt(scrub.value, 10);
                render(frameIdx);
            }});

            // Defer load until the main thread is idle so it never competes
            // with the rest of the homepage's first paint. requestIdleCallback
            // where available, falling back to a short setTimeout.
            function load() {{
                fetch(nameUrlPrefix + '/top-race.json')
                    .then(function(r) {{ return r.json(); }})
                    .then(init)
                    .catch(function(err) {{
                        console && console.warn && console.warn('race chart load failed', err);
                        stage.style.display = 'none';
                    }});
            }}
            if (typeof requestIdleCallback === 'function') {{
                requestIdleCallback(load, {{ timeout: 1500 }});
            }} else {{
                setTimeout(load, 600);
            }}
        }})();
        </script>"""
    desc = S("home_desc", country=COUNTRY_NAME[ACTIVE_CC], range=DATA_RANGE, n=fmt(n_pages))
    title = S("home_title", country=COUNTRY_NAME[ACTIVE_CC])
    (OUT_DIR / 'index.html').write_text(
        page(title, body, description=desc, canonical=home_url(),
             extra_head=hreflang_for_hub("")),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Related names helpers
# ---------------------------------------------------------------------------
def related_more_popular(name, dom, k=6):
    lst = same_sex_ranked[dom]
    idx = same_sex_index[dom].get(name)
    if idx is None:
        return []
    out = []
    i = idx - 1
    while i >= 0 and len(out) < k:
        out.append(lst[i])
        i -= 1
    return list(reversed(out))


def related_latest_neighbors(name, dom, k=6):
    lst = latest_year_ranked[dom]
    idx = latest_year_index[dom].get(name)
    if idx is None:
        return []
    out = []
    lo, hi = idx - 1, idx + 1
    while len(out) < k and (lo >= 0 or hi < len(lst)):
        if lo >= 0:
            out.append(lst[lo][0]); lo -= 1
        if hi < len(lst) and len(out) < k:
            out.append(lst[hi][0]); hi += 1
    return out


def related_same_initial(name, dom, k=6):
    out = []
    for n in by_initial.get(name[0].upper(), []):
        if n != name and dominant_sex(n) == dom:
            out.append(n)
        if len(out) >= k:
            break
    return out


def related_block(label, names):
    if not names:
        return ""
    p = PREFIX
    links = "".join(f'<a href="{p}/name/{slugify(n)}.html">{n}</a>' for n in names)
    return f'        <h3 style="margin-bottom:0.25rem;">{label}</h3>\n        <div class="related">{links}</div>\n'


# ---------------------------------------------------------------------------
# Pinterest pin card — top 1000 names per country get a 1000x1500 PNG
# ---------------------------------------------------------------------------
# Short-meaning extractor — pulls a one-liner from Wikipedia's full paragraph
# so the pin can show "Derived from Latin oliva, olive" instead of the whole
# article opener. Falls back to "" when nothing meaningful can be salvaged.
_PIN_MEANING_KEYWORDS = (
    'derived from', 'derivative of', 'meaning', 'means',
    'short form of', 'diminutive of', 'variant of',
    'feminine form of', 'masculine form of', 'female form of', 'male form of',
    'feminine version of', 'masculine version of',
    'anglicised version', 'anglicized version', 'anglicised form', 'anglicized form',
    'from latin', 'from greek', 'from hebrew', 'from arabic', 'from germanic',
    'from old english', 'from old norse', 'from sanskrit', 'from irish', 'from welsh',
    'from persian', 'from gaelic', 'from french', 'from italian', 'from spanish',
    'from german', 'from aramaic', 'from egyptian', 'from the latin', 'from the greek',
    'from the hebrew', 'from the arabic', 'from the germanic', 'from the old',
    'cognate of',
    'germanic origin', 'latin origin', 'greek origin', 'hebrew origin', 'arabic origin',
    'irish origin', 'welsh origin', 'gaelic origin', 'persian origin', 'sanskrit origin',
    'norse origin', 'french origin', 'english origin',
)
_PIN_OPENER = re.compile(
    r'^[A-Z][\w\-,\s]{0,60}?\s+(?:is|was|are)\s+[\w\s]{0,40}?given name'
    r'(?:\s+(?:in|of)\s+[\w\s]{1,40}?)?[\s,.]+',
    re.I,
)
_PIN_LEAD = re.compile(
    r'^(It|This name|This)\s+(?:is|can be|was|may be|comes|originated|has been)\s+',
    re.I,
)
_PIN_TAIL_FRAG = re.compile(
    r'^(also|that|which|and|but|originally|historically|sometimes)\s+',
    re.I,
)


def _pin_strip_unrenderable(s: str) -> str:
    """Drop characters our PIL fonts can't render (Hebrew, Arabic, Greek,
    Cyrillic, CJK, etc.) — they'd show as tofu boxes on the pin. Keep
    ASCII + Latin-Extended + general punctuation."""
    out = []
    for c in s:
        cp = ord(c)
        if 0x20 <= cp <= 0x7E:           # ASCII printable
            out.append(c)
        elif 0xA0 <= cp <= 0x024F:       # Latin-1 + Latin Extended A/B
            out.append(c)
        elif 0x2010 <= cp <= 0x205F:     # General punctuation (em dash, smart quotes)
            out.append(c)
    cleaned = ''.join(out)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    cleaned = re.sub(r'\s+([,;:.])', r'\1', cleaned)
    # Collapse leftover ", ," / "  ," runs and trim leading punctuation.
    cleaned = re.sub(r'[,;:]\s*(?=[,;:])', '', cleaned)
    cleaned = re.sub(r'^[\s,;:.\-—]+', '', cleaned)
    return cleaned.strip()


_PIN_SYNTH_PATH_EN = {
    1: "leadership", 2: "partnership", 3: "expression", 4: "craft",
    5: "freedom", 6: "care", 7: "discovery", 8: "ambition", 9: "service",
}
_PIN_SYNTH_SOUL_EN = {
    1: "independence", 2: "harmony", 3: "joy", 4: "structure",
    5: "novelty", 6: "family", 7: "meaning", 8: "achievement", 9: "compassion",
}
_PIN_SYNTH_FACE_EN = {
    1: "confidence", 2: "warmth", 3: "charm", 4: "reliability",
    5: "spark", 6: "gentleness", 7: "calm", 8: "poise", 9: "openness",
}
_PIN_SYNTH_PATH_FR = {
    1: "leadership", 2: "partenariat", 3: "expression", 4: "métier",
    5: "liberté", 6: "soin", 7: "découverte", 8: "ambition", 9: "service",
}
_PIN_SYNTH_SOUL_FR = {
    1: "indépendance", 2: "harmonie", 3: "joie", 4: "structure",
    5: "nouveauté", 6: "famille", 7: "sens", 8: "réussite", 9: "compassion",
}
_PIN_SYNTH_FACE_FR = {
    1: "assurance", 2: "chaleur", 3: "charme", 4: "fiabilité",
    5: "vivacité", 6: "douceur", 7: "calme", 8: "prestance", 9: "ouverture",
}
# Hand-curated overrides for top names — slug -> sentence. EN by default;
# nested {'fr': ...} keys give the French version.
_PIN_SYNTH_OVERRIDES: dict[str, dict] = {
    'olivia': {
        'en': "A restless explorer with a contemplative core — drawn outward by curiosity, drawn inward by depth.",
        'fr': "Une exploratrice agitée au cœur contemplatif — tirée vers le dehors par la curiosité, vers le dedans par la profondeur.",
    },
    'liam': {'en': "A determined builder with a quiet warmth — steady on the surface, principled at the core."},
    'noah': {'en': "A peaceful path with a partner's heart — reassuring presence, faithful intent."},
    'emma': {'en': "A generous spirit with an open face — wide-ranging compassion, easy warmth."},
    'theodore': {'en': "A practical craftsman with creative fire — patient hands, expressive mind."},
    'charlotte': {'en': "A graceful builder with refined depth — steady purpose, considered presence."},
    'amelia': {'en': "A spirited carer with a curious mind — energetic warmth, genuine interest."},
    'sophia': {'en': "Wisdom worn lightly — depth without weight, calm without distance."},
    'james': {'en': "A reliable presence with quiet ambition — trustworthy by default, capable when called."},
    'ava': {'en': "Bright and direct — open warmth without unnecessary edges."},
    'mia': {'en': "Small in profile, generous in spirit — easy to love, hard to forget."},

    # --- Top 50 boys, continued ---
    'oliver':    {'en': "Big-hearted and quietly thoughtful — gentle by default, generous on purpose."},
    'mateo':     {'en': "An open, expressive warmth — easy with people, generous with attention."},
    'elijah':    {'en': "A wide-hearted spirit — protective at the core, expressive on the outside."},
    'lucas':     {'en': "A grounded dreamer — steady hands, searching mind, a quiet visionary streak."},
    'william':   {'en': "Thoughtful and self-directed — reliable presence, independent inner compass."},
    'benjamin':  {'en': "Restless yet rooted — adventurous outwardly, family-minded at heart."},
    'levi':      {'en': "An expressive wanderer — playful surface, contemplative undercurrent."},
    'ezra':      {'en': "Quietly driven, deeply loyal — looks ambitious, feels protective."},
    'sebastian': {'en': "Big-picture and inwardly searching — a contemplative idealist with vision."},
    'jack':      {'en': "Quiet leader, soft presence — confident without bravado, kind by default."},
    'daniel':    {'en': "Generous, dependable, expressive — warmth on the outside, conviction within."},
    'samuel':    {'en': "Quietly driven and broadly principled — ambition tempered by conscience."},
    'michael':   {'en': "A guiding warmth — natural mentor, wide-hearted, instinctively protective."},
    'ethan':     {'en': "Expressive and tender-hearted — playful on the surface, loyal underneath."},
    'asher':     {'en': "Quietly devoted — family at the centre, with a generous outward reach."},
    'john':      {'en': "Harmonising and easygoing — gentle steadiness with an adventurous streak."},
    'hudson':    {'en': "A triple-9 spirit — wide-open, generous, idealism running all the way through."},
    'luca':      {'en': "Independent and reliable — leads quietly, builds patiently, holds close to home."},
    'leo':       {'en': "Bright, restless, expressive — wanderer with an inspired core."},
    'elias':     {'en': "A steady leader with a tender heart — pragmatic, protective, dependable."},
    'owen':      {'en': "Inventive and self-directed — an inspired mind that takes its own lead."},
    'alexander': {'en': "Expressive through and through — creative core, big-hearted reach."},
    'dylan':     {'en': "Driven yet diplomatic — capable, expressive, easy to work alongside."},
    'santiago':  {'en': "Adventurous and ambitious, rooted by family — a worldly heart that comes home."},
    'julian':    {'en': "A serious builder with a wide outlook — turns ideals into structure."},
    'david':     {'en': "Self-directed and expressive — leads, builds, and speaks his mind."},
    'joseph':    {'en': "Visionary at heart, driven in action — leads with conviction and ambition."},
    'matthew':   {'en': "Generous and warmly expressive — caring core, creative voice."},
    'luke':      {'en': "Steady, capable, and curious — disciplined hands with an adventurous mind."},
    'jackson':   {'en': "Independent and reflective — leads quietly, thinks deeply, speaks creatively."},
    'maverick':  {'en': "Goes his own way with a grounded heart — bold leader, protective core."},
    'miles':     {'en': "Restless ambition with serious craft — builds big, stays moving."},
    'wyatt':     {'en': "Doubly driven, openly principled — ambition tempered by big-picture care."},
    'thomas':    {'en': "A quiet builder with a thoughtful core — steady, considered, family-anchored."},
    'isaac':     {'en': "Inspired and devoted — gentle, faithful, sees the bigger picture."},
    'jacob':     {'en': "Steady, reflective, and devoted — patient hands, contemplative mind."},
    'mason':     {'en': "Driven and self-possessed — confident leader with a contemplative undercurrent."},
    'gabriel':   {'en': "Wide-hearted and warmly expressive — generous spirit, creative voice."},
    'anthony':   {'en': "Curious and easygoing — explores quietly, harmonises naturally."},
    'carter':    {'en': "Inspired, devoted, and curious — a warm visionary with an adventurous streak."},
    'logan':     {'en': "A thoughtful builder with a tender core — patient, reflective, devoted."},
    'aiden':     {'en': "Quietly protective, broadly generous — tender at home, open beyond it."},
    'grayson':   {'en': "Adventurous and broadly principled — restless idealism with serious build."},
    'caleb':     {'en': "Restless, loyal, and driven — adventurous spirit grounded by family and ambition."},
    'cooper':    {'en': "Driven and openly principled — leads with conviction, thinks in big terms."},

    # --- Top 50 girls, continued ---
    'isabella':  {'en': "Quietly searching with a wide-open heart — contemplative depth, generous reach."},
    'evelyn':    {'en': "Inspired, driven, expressive — visionary mind with the force to ship."},
    'sofia':     {'en': "An adventurous searcher — outward energy, reflective core, thoughtful presence."},
    'camila':    {'en': "Expressive, inspired, self-directed — a creative voice that leads."},
    'harper':    {'en': "Expressive and quietly guiding — creative core, naturally mentoring."},
    'luna':      {'en': "Creative and capable — bright ideas with the discipline to build."},
    'eleanor':   {'en': "Quietly ambitious, deeply considered — drive paired with thoughtfulness."},
    'violet':    {'en': "Inspired, gentle, big-hearted — vision delivered with grace."},
    'aurora':    {'en': "Doubly inspired, openly generous — an idealistic dreamer with reach."},
    'elizabeth': {'en': "Reflective, harmonising, with a restless streak — depth, grace, curiosity."},
    'eliana':    {'en': "Devoted, reflective, and driven — quiet ambition with a tender core."},
    'hazel':     {'en': "Thoughtful, devoted, and self-directed — gentle leadership with depth."},
    'chloe':     {'en': "Inspired and curious — restless mind, inward-looking spirit."},
    'ellie':     {'en': "Quietly self-directed, warmly devoted — independent with a tender core."},
    'nora':      {'en': "Expressive, reflective, restless — playful surface, thoughtful undercurrent."},
    'gianna':    {'en': "Inspired, driven, self-directed — a visionary leader with quiet ambition."},
    'lily':      {'en': "A serious builder with a contemplative core — patient, thoughtful, devoted."},
    'emily':     {'en': "Self-directed and expressive — leads with a creative voice, thinks deeply."},
    'aria':      {'en': "Harmonising, inspired, broadly generous — graceful and big-hearted."},
    'scarlett':  {'en': "Driven and warm — ambition tempered by gentle, harmonising care."},
    'penelope':  {'en': "Quietly contemplative with a creative voice — thoughtful depth, serious craft."},
    'zoe':       {'en': "Inspired and self-directed — visionary spark with the drive to deliver."},
    'ella':      {'en': "Expressive and devoted — creative warmth with a deeply caring core."},
    'avery':     {'en': "Capable, disciplined, dependable — steady ambition through and through."},
    'abigail':   {'en': "Adventurous, gentle, expressive — restless energy with a harmonising touch."},
    'mila':      {'en': "Driven and self-directed with a reflective core — quiet confidence."},
    'lucy':      {'en': "Thoughtful and self-directed — gentle leadership with quiet depth."},
    'isla':      {'en': "Adventurous and grounded — restless explorer with steady foundations."},
    'ivy':       {'en': "Harmonising and reflective — gentle surface, steady core, quiet depth."},
    'layla':     {'en': "Wide-hearted and devoted — generous reach with a tender, family-centred core."},
    'lainey':    {'en': "Expressive with serious build — creative voice, disciplined ambition."},
    'nova':      {'en': "A searching spirit with reach — contemplative depth, open-hearted outlook."},
    'grace':     {'en': "Thoughtful, devoted, and self-directed — quiet grace with inner steel."},
    'willow':    {'en': "Steady, devoted, and reflective — patient hands, caring heart, thoughtful core."},
    'riley':     {'en': "A naturally guiding spirit, expressive throughout — warmth that teaches."},
    'emilia':    {'en': "Patient and devoted with a reflective core — steady warmth, contemplative depth."},
    'naomi':     {'en': "Quietly searching with an open heart — contemplative depth, generous outlook."},
    'elena':     {'en': "Inspired and self-directed — leads with vision, delivers with quiet drive."},
    'madison':   {'en': "Expressive, reflective, and restless — playful surface, thoughtful undercurrent."},
    'valentina': {'en': "Quietly driven and contemplative — confident leader with reflective depth."},
    'victoria':  {'en': "Thoughtful and broadly generous — contemplative depth with an open hand."},
    'stella':    {'en': "Devoted and generous — tender at home, open-hearted beyond it."},
    'delilah':   {'en': "A naturally guiding warmth — protective core, wide-hearted reach."},

    # --- Boys 51-150 ---
    'charles':    {'en': "Expressive and tender-hearted — creative voice with a deeply caring core."},
    'roman':      {'en': "Quietly contemplative with a generous outlook — reflective depth, open heart."},
    'josiah':     {'en': "Quietly driven with a reflective core — confident leader who thinks before speaking."},
    'thiago':     {'en': "A naturally guiding spirit — contemplative depth, quiet ambition, warm authority."},
    'wesley':     {'en': "Doubly driven, broadly principled — capable and open-hearted."},
    'jayden':     {'en': "Restless, steady, and self-directed — adventurous spark, builder's foundation."},
    'bennett':    {'en': "Quietly driven and self-possessed — independent leader with a reflective undercurrent."},
    'christopher':{'en': "A patient builder with a gentle, inspired core — steady, harmonising, visionary."},
    'nathan':     {'en': "A serious builder with twin peacemaker harmonies — grounded, gentle, dependable."},
    'angel':      {'en': "Expressive and tender-hearted — creative warmth with a deeply caring centre."},
    'nolan':      {'en': "Harmonising and reflective — gentle steadiness, thoughtful core, patient build."},
    'waylon':     {'en': "Wide-hearted and adventurous — generous spirit, restless inner energy, steady ground."},
    'cameron':    {'en': "A naturally guiding, expressive spirit — warmth that teaches, creativity through and through."},
    'brooks':     {'en': "Driven and creative with a restless streak — ambition expressed playfully."},
    'beau':       {'en': "An inspired, generous soul with a harmonising face — visionary, big-hearted, gentle."},
    'weston':     {'en': "Devoted and inspired with a builder's foundation — gentle authority, steady purpose."},
    'rowan':      {'en': "Quietly driven and self-possessed — confident leader with a reflective undercurrent."},
    'adrian':     {'en': "Twin-visionary and broadly principled — inspired core, idealistic outlook."},
    'enzo':       {'en': "Devoted and inspired with a steady foundation — gentle warmth, big imagination, patient build."},
    'ian':        {'en': "Devoted, self-directed, and curious — gentle leader with a restless streak."},
    'kai':        {'en': "Expressive and self-directed with a harmonising face — creative leadership delivered gently."},
    'christian':  {'en': "Twin-leader confidence with an inspired core — direct, principled, visionary."},
    'aaron':      {'en': "A serious builder with restless drive — disciplined ambition, adventurous outlook."},
    'theo':       {'en': "Inspired and self-directed with a creative voice — visionary core that leads."},
    'silas':      {'en': "Devoted, self-directed, and curious — gentle leader with an adventurous spark."},
    'walker':     {'en': "Quietly self-directed and devoted — reflective depth with steady leadership."},
    'jonathan':   {'en': "Inspired and driven with a creative voice — visionary ambition, expressive outlook."},
    'leonardo':   {'en': "Doubly expressive with a generous soul — creative through and through, broadly principled."},
    'everett':    {'en': "Restless, devoted, and driven — adventurous spirit grounded by family and ambition."},
    'micah':      {'en': "Quietly self-directed and devoted — reflective depth, gentle leadership."},
    'ryan':       {'en': "A serious builder with restless drive — disciplined ambition, adventurous outlook."},
    'august':     {'en': "Driven and reflective with a leader's presence — quiet confidence, considered depth."},
    'gael':       {'en': "Devoted and self-directed with a reflective core — gentle leadership, thoughtful depth."},
    'robert':     {'en': "A naturally guiding teacher with master-builder vision — wide-hearted authority, lasting craft."},
    'jose':       {'en': "A patient builder with a visionary core — steady purpose, harmonising face."},
    'eli':        {'en': "Restless and expressive with a driven core — adventurous outlook, creative energy."},
    'jeremiah':   {'en': "Devoted and harmonising with a master-builder face — gentle core, serious craft."},
    'luka':       {'en': "Wide-hearted and grounded — generous spirit, patient foundation, restless outlook."},
    'amir':       {'en': "Restless and self-directed with a steady foundation — adventurous, independent, dependable."},
    'jaxon':      {'en': "Self-directed and reflective — leads quietly, thinks deeply, speaks creatively."},
    'parker':     {'en': "A naturally guiding warmth — protective core, wide-hearted reach."},
    'colton':     {'en': "Quietly reflective and expressive — contemplative depth, creative voice, steady foundation."},
    'myles':      {'en': "Harmonising and creative with a driven core — gentle surface, ambitious depth."},
    'adam':       {'en': "Self-directed and harmonising with a driven core — independent, gentle, ambitious."},
    'atlas':      {'en': "Driven and devoted with a harmonising face — ambition tempered by gentle care."},
    'xavier':     {'en': "Quietly self-directed and devoted — reflective depth, gentle leadership."},
    'easton':     {'en': "Harmonising and creative with a driven core — gentle surface, ambitious depth."},
    'jordan':     {'en': "Quietly driven and self-possessed — confident leader with a contemplative undercurrent."},
    'arthur':     {'en': "Restless, steady, and self-directed — adventurous spark, builder's patience, quiet leadership."},
    'landon':     {'en': "Devoted and reflective with a driven core — gentle warmth, contemplative depth, real ambition."},
    'austin':     {'en': "Expressive and disciplined with a driven core — creative voice, steady hands, real ambition."},
    'dominic':    {'en': "Steady and devoted with a reflective core — patient build, gentle warmth, contemplative depth."},
    'adriel':     {'en': "Steady and devoted with a reflective core — patient hands, caring heart, thoughtful face."},
    'damian':     {'en': "Devoted and inspired with a builder's foundation — gentle warmth, big vision, patient craft."},
    'vincent':    {'en': "A naturally guiding spirit with restless energy — warm authority, adventurous core."},
    'river':      {'en': "Wide-hearted and restless with a master-builder face — generous, adventurous, seriously made."},
    'emiliano':   {'en': "Devoted and doubly expressive — gentle core, creative voice, warm presence."},
    'jace':       {'en': "Self-directed and devoted with a steady foundation — independent, gentle, dependable."},
    'archer':     {'en': "Driven and devoted with an inspired face — ambition tempered by care, visionary presence."},
    'lorenzo':    {'en': "Devoted and driven with a contemplative core — gentle warmth, real ambition, reflective depth."},
    'jameson':    {'en': "Restless and expressive with an inspired face — adventurous spark, creative voice, visionary presence."},
    'nicholas':   {'en': "Wide-hearted and contemplative with a harmonising face — generous, reflective, gently grounded."},
    'emmett':     {'en': "A serious builder with a leader's drive and creative voice — steady, direct, expressive."},
    'milo':       {'en': "A serious builder with a tender core — patient craft, gentle warmth, contemplative depth."},
    'harrison':   {'en': "Expressive and reflective with a restless face — creative voice, thoughtful depth, adventurous spark."},
    'giovanni':   {'en': "Self-directed and reflective with a creative voice — leads quietly, thinks deeply, speaks expressively."},
    'carson':     {'en': "Quietly searching with a wide-open heart — contemplative depth, generous reach."},
    'george':     {'en': "Expressive and reflective with a restless face — creative voice, thoughtful depth, adventurous spark."},
    'kayden':     {'en': "Devoted and disciplined with an inspired face — gentle warmth, steady hands, visionary presence."},
    'jonah':      {'en': "Expressive and reflective with a restless face — playful surface, thoughtful undercurrent, curious eye."},
    'greyson':    {'en': "Steady and broadly principled with a master-builder face — patient, generous, seriously made."},
    'hunter':     {'en': "Restless and driven with a devoted core — adventurous spirit, real ambition, family-anchored."},
    'graham':     {'en': "Expressive and harmonising with a self-directed face — creative warmth, gentle confidence."},
    'luis':       {'en': "Quietly contemplative and expressive with a builder's foundation — reflective depth, creative voice."},
    'declan':     {'en': "Expressive and tender-hearted — creative warmth with a deeply caring core."},
    'sawyer':     {'en': "Self-directed and disciplined with a devoted face — independent, capable, family-anchored."},
    'jasper':     {'en': "Quietly devoted with a generous outlook — tender at home, open-hearted beyond it."},
    'ryder':      {'en': "Reflective and expressive with a master-builder face — contemplative depth, creative voice, serious craft."},
    'carlos':     {'en': "An adventurous searcher with a contemplative presence — restless spirit, reflective depth."},
    'connor':     {'en': "Reflective and expressive with a master-builder face — thoughtful core, creative voice, serious craft."},
    'juan':       {'en': "Self-directed and disciplined with a devoted face — independent leader, patient hands, family-anchored."},
    'matteo':     {'en': "Harmonising and creative with a driven core — gentle surface, expressive voice, ambitious depth."},
    'dawson':     {'en': "A serious builder with a contemplative core — patient, thoughtful, family-anchored."},
    'calvin':     {'en': "Quietly self-directed and devoted — reflective depth, gentle leadership, family-anchored core."},
    'leon':       {'en': "Self-directed and inspired with a driven face — leads with vision, delivers with quiet ambition."},
    'dean':       {'en': "Quietly devoted with a generous outlook — tender at home, open-hearted beyond it."},
    'evan':       {'en': "Quietly devoted with a generous outlook — tender presence, open-hearted reach."},
    'nathaniel':  {'en': "Expressive and reflective with a restless face — creative voice, thoughtful core, adventurous spark."},
    'diego':      {'en': "A patient builder with a harmonising core and visionary face — steady, gentle, inspired."},
    'arlo':       {'en': "Self-directed and reflective with a creative voice — independent, contemplative, expressive."},
    'bryson':     {'en': "Expressive and disciplined with a driven core — creative voice, steady hands, real ambition."},
    'jason':      {'en': "An adventurous searcher — restless outward energy, reflective core, thoughtful presence."},
    'malachi':    {'en': "Twin-visionary and broadly principled — inspired core, idealistic outlook, wide reach."},
    'elliot':     {'en': "Self-directed and harmonising with a driven core — independent leader, gentle face, real ambition."},

    # --- Girls 51-150 ---
    'maya':       {'en': "Steady and broadly generous — patient hands, wide-hearted reach, dependable presence."},
    'hannah':     {'en': "Self-directed and harmonising with a driven core — independent leader, gentle face, real ambition."},
    'leah':       {'en': "Driven and devoted with an inspired face — quiet ambition, tender core, visionary presence."},
    'lillian':    {'en': "A naturally guiding spirit with restless energy — warm authority, independent core, adventurous outlook."},
    'genesis':    {'en': "A naturally guiding spirit with restless energy — warm authority, independent core, adventurous reach."},
    'josephine':  {'en': "Inspired and contemplative with a master-builder face — visionary core, thoughtful depth, serious craft."},
    'sadie':      {'en': "Harmonising and devoted with a restless face — gentle surface, caring core, adventurous spark."},
    'adeline':    {'en': "Restless and harmonising with a creative voice — adventurous spark, gentle warmth, expressive depth."},
    'zoey':       {'en': "Driven and broadly generous — ambition tempered by care, doubly capable, openly principled."},
    'sophie':     {'en': "Wide-hearted and harmonising with a reflective core — generous reach, gentle surface, thoughtful depth."},
    'paisley':    {'en': "A naturally guiding spirit with master-builder vision — warm authority, serious craft, inspired presence."},
    'alice':      {'en': "Expressive and tender-hearted — creative warmth with a deeply caring core."},
    'ruby':       {'en': "Expressive and self-directed with an inspired face — creative warmth, independent core, visionary presence."},
    'eloise':     {'en': "Inspired and reflective with a builder's foundation — visionary depth, thoughtful core, patient craft."},
    'madelyn':    {'en': "Inspired and disciplined with a reflective face — visionary mind, steady hands, contemplative depth."},
    'leilani':    {'en': "Driven and devoted with an inspired face — quiet ambition, tender core, visionary presence."},
    'claire':     {'en': "Expressive and tender-hearted — creative warmth with a deeply caring core."},
    'addison':    {'en': "Expressive, reflective, and restless — playful surface, thoughtful undercurrent, adventurous spark."},
    'ayla':       {'en': "Doubly expressive with a generous soul — creative core, wide-hearted reach."},
    'emery':      {'en': "Expressive and driven with a builder's face — creative voice, real ambition, patient craft."},
    'iris':       {'en': "Self-directed and broadly generous with a leader's presence — independent core, wide reach, gentle confidence."},
    'eden':       {'en': "Twin-leader confidence with a generous outlook — direct, principled, openly compassionate."},
    'natalie':    {'en': "Driven and reflective with a leader's face — ambition tempered by depth, confident outlook."},
    'maria':      {'en': "Devoted and inspired with a builder's foundation — gentle warmth, visionary core, patient hands."},
    'maeve':      {'en': "Self-directed and inspired with a driven face — leads with vision, delivers with quiet ambition."},
    'daisy':      {'en': "A serious builder with restless drive — disciplined ambition tempered by adventurous spark."},
    'vivian':     {'en': "Restless and self-directed with a builder's foundation — adventurous spirit, independent core, steady hands."},
    'clara':      {'en': "Driven and harmonising with a devoted face — quiet ambition, gentle warmth, tender presence."},
    'autumn':     {'en': "Wide-hearted and reflective with a visionary face — generous core, thoughtful depth, inspired outlook."},
    'liliana':    {'en': "Steady and harmonising with a visionary face — patient hands, gentle warmth, inspired presence."},
    'everly':     {'en': "A naturally guiding spirit with serious drive — warm authority, real ambition, contemplative outlook."},
    'audrey':     {'en': "Inspired and reflective with a builder's foundation — visionary depth, thoughtful core, patient craft."},
    'lyla':       {'en': "Restless and driven with a devoted face — adventurous spirit, real ambition, family-anchored warmth."},
    'jade':       {'en': "Inspired and devoted with a restless face — visionary core, gentle warmth, adventurous spark."},
    'kinsley':    {'en': "Restless and expressive with an inspired face — adventurous spark, creative voice, visionary presence."},
    'millie':     {'en': "A naturally guiding spirit with adventurous energy — warm authority, restless core, gentle leadership."},
    'madeline':   {'en': "Wide-hearted and harmonising with a reflective core — generous reach, gentle surface, thoughtful depth."},
    'josie':      {'en': "A serious builder with twin harmonies — disciplined craft delivered with gentle steadiness."},
    'kennedy':    {'en': "A naturally guiding spirit with serious drive — warm authority, real ambition, contemplative outlook."},
    'athena':     {'en': "A serious builder with a contemplative core — patient, thoughtful, devoted, seriously made."},
    'melody':     {'en': "Twin-visionary with a generous core — inspired through and through, broadly compassionate."},
    'caroline':   {'en': "Restless and expressive with a harmonising face — adventurous spark, creative voice, gentle surface."},
    'aaliyah':    {'en': "Expressive and self-directed with an inspired face — creative voice, independent core, visionary presence."},
    'anna':       {'en': "Expressive and harmonising with a leader's face — creative core, gentle warmth, confident outlook."},
    'sarah':      {'en': "Doubly harmonising with a generous core — gentle, diplomatic, broadly compassionate."},
    'quinn':      {'en': "Doubly expressive with a generous soul — creative through and through, openly principled."},
    'lydia':      {'en': "Devoted and driven with a reflective core — gentle warmth, real ambition, contemplative depth."},
    'lucia':      {'en': "Self-directed and steady with a devoted face — independent leader, patient hands, family-anchored core."},
    'allison':    {'en': "Self-directed and reflective with a creative voice — independent core, thoughtful depth, expressive outlook."},
    'hailey':     {'en': "A naturally guiding spirit with master-builder vision — warm authority, serious craft, inspired presence."},
    'cora':       {'en': "Self-directed and reflective with a creative voice — independent core, contemplative depth, expressive outlook."},
    'ariana':     {'en': "Driven and creative with a restless face — ambition expressed playfully, adventurous outlook."},
    'natalia':    {'en': "A serious builder with an expressive core — disciplined craft, creative voice, confident outlook."},
    'gabriella':  {'en': "Steady and contemplative with a devoted face — patient hands, thoughtful core, gentle warmth."},
    'savannah':   {'en': "Driven and creative with a restless face — ambition expressed playfully, adventurous outlook."},
    'brooklyn':   {'en': "Steady and self-directed with a creative face — patient foundation, independent core, expressive outlook."},
    'bella':      {'en': "Restless and devoted with a driven face — adventurous spirit, tender core, real ambition."},
    'georgia':    {'en': "Driven and creative with a restless face — ambition expressed playfully, adventurous outlook."},
    'juniper':    {'en': "Expressive and driven with a master-builder face — creative voice, real ambition, serious craft."},
    'alaia':      {'en': "Devoted and doubly expressive — gentle core, creative warmth, playful surface."},
    'raelynn':    {'en': "Driven and disciplined with a master-builder face — ambition, steady hands, serious craft."},
    'hadley':     {'en': "Self-directed and disciplined with a devoted face — independent leader, patient hands, family-anchored core."},
    'rose':       {'en': "Expressive and inspired with a leader's face — creative core, visionary depth, confident outlook."},
    'julia':      {'en': "Driven and twin-disciplined — quiet ambition built on doubly steady hands."},
    'serenity':   {'en': "Reflective and doubly driven — contemplative depth, persistent ambition, considered outlook."},
    'eliza':      {'en': "Driven and devoted with an inspired face — quiet ambition, tender core, visionary presence."},
    'margaret':   {'en': "Inspired and reflective with a builder's face — visionary depth, thoughtful core, patient craft."},
    'eva':        {'en': "Self-directed and devoted with a builder's foundation — independent leader, tender core, dependable hands."},
    'amara':      {'en': "Quietly reflective and expressive with a builder's foundation — contemplative depth, creative voice, steady hands."},
    'melanie':    {'en': "Restless and harmonising with a creative voice — adventurous spark, gentle warmth, expressive depth."},
    'cecilia':    {'en': "A naturally guiding warmth with a generous outlook — protective core, wide-hearted reach."},
    'ashley':     {'en': "Reflective and disciplined with a creative face — contemplative depth, patient hands, expressive outlook."},
    'rylee':      {'en': "Inspired and driven with an expressive face — visionary mind, real ambition, creative voice."},
    'margot':     {'en': "Inspired and reflective with a master-builder face — visionary core, thoughtful depth, serious craft."},
    'samantha':   {'en': "Restless and expressive with a harmonising face — adventurous spark, creative voice, gentle surface."},
    'catalina':   {'en': "Quietly reflective and expressive with a builder's foundation — contemplative depth, creative voice, steady hands."},
    'juliette':   {'en': "Expressive with master-builder vision and a driven face — creative voice, serious craft, real ambition."},
    'aubrey':     {'en': "Wide-hearted and reflective with a visionary face — generous core, thoughtful depth, inspired outlook."},
    'esther':     {'en': "Expressive and self-directed with a harmonising face — creative voice, independent core, gentle surface."},
    'mary':       {'en': "Expressive and driven with a builder's foundation — creative voice, real ambition, patient hands."},
    'nevaeh':     {'en': "Self-directed and inspired with a driven face — leads with vision, delivers with quiet ambition."},
    'skylar':     {'en': "Restless and driven with a devoted face — adventurous spirit, real ambition, tender core."},
    'alina':      {'en': "Self-directed and inspired with a driven face — leads with vision, delivers with quiet ambition."},
    'amira':      {'en': "Devoted and inspired with a builder's foundation — gentle warmth, big vision, patient hands."},
    'ember':      {'en': "Quietly contemplative and self-directed with a devoted face — reflective depth, independent core, gentle warmth."},
    'magnolia':   {'en': "Wide-hearted and driven with a leader's face — generous reach, real ambition, confident outlook."},
    'sienna':     {'en': "Driven and devoted with an inspired face — quiet ambition, tender core, visionary presence."},
    'charlie':    {'en': "Inspired and devoted with a restless face — visionary core, gentle warmth, adventurous spark."},
    'elliana':    {'en': "Wide-hearted and reflective with a visionary face — generous core, thoughtful depth, inspired outlook."},
    'summer':     {'en': "Doubly driven, openly generous — persistent ambition tempered by big-picture care."},
    'alana':      {'en': "Inspired and expressive with a driven face — visionary mind, creative voice, real ambition."},
    'brielle':    {'en': "Wide-hearted and self-directed with a driven face — generous core, independent leader, real ambition."},
    'remi':       {'en': "Wide-hearted and restless with a builder's foundation — generous spirit, adventurous spark, patient hands."},
    'sage':       {'en': "Restless and devoted with a driven face — adventurous spirit, tender core, real ambition."},
    'valerie':    {'en': "Wide-hearted and harmonising with a reflective core — generous reach, gentle surface, thoughtful depth."},
    'hallie':     {'en': "Inspired and devoted with a restless face — visionary core, gentle warmth, adventurous spark."},
    'wrenley':    {'en': "Expressive and driven with a master-builder face — creative voice, real ambition, serious craft."},
    'kehlani':    {'en': "A naturally guiding warmth with a generous outlook — protective core, wide-hearted reach."},

    # --- Boys 151-300 ---
    'zion':          {'en': "A wide-open idealist with a builder's resolve — ambitious in vision, seriously made."},
    'emilio':        {'en': "Warmly expressive and quietly driven — open-hearted presence, real ambition."},
    'ivan':          {'en': "Steady and quietly determined — patient in build, resolute under pressure."},
    'hayden':        {'en': "Reflective and quietly capable — thoughtful before speaking, steady in action."},
    'stetson':       {'en': "Self-possessed and grounded — confident without noise, capable without show."},
    'jude':          {'en': "An old soul with a restless spirit — contemplative depth, adventurous reach."},
    'legend':        {'en': "A name that carries its own weight — bold presence, quietly serious ambition."},
    'matias':        {'en': "Open-hearted and driven — easy warmth with real intent."},
    'callum':        {'en': "Quietly principled and broadly generous — steady hands, open heart."},
    'hayes':         {'en': "Self-directed and dependable — leads quietly, delivers consistently."},
    'jett':          {'en': "Fast-moving and self-possessed — direct, energetic, surprisingly grounded."},
    'cole':          {'en': "Spare and capable — no wasted motion, dependable by default."},
    'elliott':       {'en': "Thoughtful and broadly principled — reflective core, steady outer presence."},
    'jesus':         {'en': "Rooted in devotion, open in reach — tender core, wide-hearted purpose."},
    'ace':           {'en': "Quietly competitive and self-directed — aims high, moves deliberately."},
    'beckett':       {'en': "An old-soul name with restless energy — contemplative beneath the surface, adventurous in reach."},
    'alan':          {'en': "Grounded and gently expressive — reliable presence, easy warmth."},
    'beckham':       {'en': "Confident and quietly capable — strong presence, consistent follow-through."},
    'jayce':         {'en': "Expressive and fast-moving — direct presence, restless energy."},
    'braxton':       {'en': "Self-possessed and driven — direct ambition, steady foundation."},
    'jaxson':        {'en': "Fast-moving and independent — direct, energetic, reliably grounded."},
    'amari':         {'en': "Open-hearted and broadly generous — wide reach, easy warmth."},
    'chase':         {'en': "Direct and driven — ambitious in motion, uncomplicated in purpose."},
    'rhett':         {'en': "A quiet intensity — contained ambition, confident inner world."},
    'max':           {'en': "Quietly confident and broadly capable — steady competence, no fuss."},
    'felix':         {'en': "Lightly carried joy — expressive warmth, effortless openness."},
    'kingston':      {'en': "A name with presence — confident bearing, quietly serious depth."},
    'judah':         {'en': "Rooted and broadly principled — steady conviction, open-hearted reach."},
    'antonio':       {'en': "Warmly expressive with a family-centred core — generous presence, devoted intent."},
    'emmanuel':      {'en': "Wide-hearted and quietly purposeful — devoted at the core, open in reach."},
    'maxwell':       {'en': "Capable and quietly driven — patient in build, serious in intent."},
    'ryker':         {'en': "Self-possessed and direct — confident presence, consistently capable."},
    'alejandro':     {'en': "Warmly expressive with a bold presence — generous spirit, ambitious core."},
    'nicolas':       {'en': "Broadly principled and quietly capable — steady foundation, open-hearted reach."},
    'barrett':       {'en': "Quietly determined and broadly dependable — patient in craft, reliable in result."},
    'jesse':         {'en': "Warm and quietly driven — easy presence, real intent."},
    'ashton':        {'en': "Self-directed and dependable — independent core, steady outer presence."},
    'miguel':        {'en': "Open-hearted and driven — expressive warmth, ambitious core."},
    'brayden':       {'en': "Restless and energetic — always moving forward, reliably grounded."},
    'tyler':         {'en': "Practical and quietly capable — steady hands, direct approach."},
    'peter':         {'en': "Solid and broadly generous — steady foundation, open-hearted presence."},
    'camden':        {'en': "Easy-going and quietly capable — friendly surface, dependable core."},
    'zachary':       {'en': "Warm and broadly principled — friendly presence, principled core."},
    'tatum':         {'en': "Direct and quietly capable — energetic presence, steady intent."},
    'kevin':         {'en': "Practical and broadly capable — steady hands, reliable presence."},
    'andres':        {'en': "Open-hearted and driven — expressive warmth with real ambition."},
    'finn':          {'en': "Quick-minded and openly warm — restless energy, easy companionship."},
    'justin':        {'en': "Broadly principled and quietly capable — fair-minded, steady in purpose."},
    'tucker':        {'en': "Easy-going and quietly capable — practical hands, warm presence."},
    'bentley':       {'en': "Quietly confident and broadly capable — polished presence, steady depth."},
    'zayden':        {'en': "Direct and energetic — moves fast, lands steady."},
    'messiah':       {'en': "Carries weight with purpose — wide-hearted intent, quietly serious depth."},
    'abraham':       {'en': "Steady at the root, wide in reach — patient foundation, broadly generous spirit."},
    'alex':          {'en': "Quietly capable and broadly adaptable — consistent, easy to rely on."},
    'adonis':        {'en': "A striking presence with quiet depth — confident outwardly, searching within."},
    'kaiden':        {'en': "Energetic and self-directed — moves with intention, settles with warmth."},
    'timothy':       {'en': "Quietly faithful and broadly principled — steady conviction, warm presence."},
    'knox':          {'en': "Quietly resolute — minimal words, consistent action."},
    'tate':          {'en': "Direct and quietly capable — clean presence, dependable intent."},
    'caden':         {'en': "Energetic and broadly warm — direct, dependable, easy."},
    'ayden':         {'en': "Warm and directly capable — easy presence, steady purpose."},
    'nico':          {'en': "Bright and openly warm — quick presence, genuine care."},
    'victor':        {'en': "Quietly determined and broadly capable — patient ambition, confident bearing."},
    'maddox':        {'en': "Self-possessed and quietly driven — direct, energetic, consistently capable."},
    'xander':        {'en': "Bold and quietly principled — confident presence, considered core."},
    'oscar':         {'en': "Warmly expressive with a contemplative edge — easy presence, thoughtful depth."},
    'colter':        {'en': "Self-possessed and quietly capable — rugged confidence, steady intent."},
    'joel':          {'en': "Warmly practical and broadly principled — family-centred, dependably solid."},
    'abel':          {'en': "Gentle and broadly generous — open-hearted core, quiet goodness."},
    'patrick':       {'en': "Steadily principled and quietly warm — reliable presence, broad-spirited intent."},
    'rafael':        {'en': "Warmly expressive and quietly driven — open-hearted energy, real purpose."},
    'griffin':       {'en': "Bold and quietly principled — confident presence with a searching undercurrent."},
    'brody':         {'en': "Warm and openly capable — easy companionship, reliable core."},
    'jaziel':        {'en': "Broadly principled and quietly expressive — steady intent, warm reach."},
    'rory':          {'en': "Bright and openly generous — quick energy, warm, broadly principled."},
    'eithan':        {'en': "Steady and quietly principled — enduring strength, considered depth."},
    'edward':        {'en': "Enduring and quietly substantial — old-soul confidence, steady reliability."},
    'brandon':       {'en': "Broad-minded and quietly capable — steady foundation, open reach."},
    'milan':         {'en': "Open-hearted and quietly expressive — warm presence, searching depth."},
    'richard':       {'en': "Steadily capable and broadly principled — patient build, reliable result."},
    'malakai':       {'en': "Broadly principled and warm-hearted — steady foundation, wide reach."},
    'ismael':        {'en': "Broadly principled and quietly driven — steady conviction, open spirit."},
    'kyrie':         {'en': "Quick-moving and openly warm — direct energy, genuine care."},
    'louis':         {'en': "Quietly refined and broadly capable — steady grace, considered depth."},
    'elian':         {'en': "Warm and openly expressive — easy energy, genuine care."},
    'kairo':         {'en': "Bold and quietly self-directed — confident presence, steady inner world."},
    'cohen':         {'en': "Quick-minded and broadly principled — direct, fair, consistently capable."},
    'nash':          {'en': "Direct and quietly capable — clean presence, dependable purpose."},
    'grant':         {'en': "Broadly capable and quietly principled — steady hands, open-hearted intent."},
    'callan':        {'en': "Warm and quietly principled — easy presence, considered depth."},
    'dallas':        {'en': "Self-directed and broadly capable — easy presence, steady core."},
    'harvey':        {'en': "Warmly capable and broadly principled — reliable presence, steady warmth."},
    'muhammad':      {'en': "Deeply principled and broadly generous — rooted in faith, open in reach."},
    'mark':          {'en': "Steadily principled and directly capable — reliable, no-fuss, genuinely solid."},
    'javier':        {'en': "Warmly expressive and quietly driven — open-hearted energy, real purpose."},
    'karter':        {'en': "Quietly determined and broadly capable — patient build, consistent result."},
    'zayn':          {'en': "Quietly self-possessed — minimal and direct, with an understated depth."},
    'crew':          {'en': "Directly capable and quietly principled — team-minded, reliably solid."},
    'eric':          {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'simon':         {'en': "Quietly thoughtful and broadly principled — steady conviction, warm presence."},
    'aziel':         {'en': "Broadly principled and quietly expressive — steady intent, warm reach."},
    'cyrus':         {'en': "Quietly commanding and broadly principled — patient authority, considered depth."},
    'gavin':         {'en': "Warm and openly capable — easy-going competence, genuine warmth."},
    'marcus':        {'en': "Quietly capable and broadly principled — steady ambition, confident bearing."},
    'ronan':         {'en': "Boldly principled and quietly warm — direct conviction, easy companionship."},
    'derek':         {'en': "Steadily capable and broadly principled — practical, reliable, solid."},
    'warren':        {'en': "Steady and broadly principled — patient build, reliable result."},
    'lennox':        {'en': "Bold and quietly principled — direct confidence, searching undercurrent."},
    'paul':          {'en': "Steadily principled and quietly capable — reliable, considered, broadly solid."},
    'jeremy':        {'en': "Broadly warm and quietly principled — easy presence, steady conviction."},
    'tristan':       {'en': "Bold and quietly searching — a restless idealism paired with steady resolve."},
    'lukas':         {'en': "Steadily capable and quietly warm — dependable presence, broad-minded core."},
    'steven':        {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'emerson':       {'en': "Broadly principled and quietly expressive — steady conviction, open reach."},
    'walter':        {'en': "Patient and quietly substantial — old-soul steadiness, reliable depth."},
    'cade':          {'en': "Direct and quietly capable — clean presence, dependable core."},
    'ellis':         {'en': "Warmly capable and broadly principled — easy presence, steady core."},
    'otto':          {'en': "Steady and quietly capable — minimal, direct, reliably solid."},
    'phoenix':       {'en': "Broadly principled and quietly driven — resilience as a foundation, rising as a habit."},
    'colt':          {'en': "Self-possessed and direct — confident presence, quietly capable."},
    'atticus':       {'en': "Quietly principled and broadly generous — deep conviction, wide-hearted reach."},
    'kaleb':         {'en': "Steadily driven and broadly warm — patient ambition, open-hearted presence."},
    'israel':        {'en': "Deeply rooted and broadly principled — faithful foundation, open-hearted reach."},
    'tobias':        {'en': "Warmly principled and quietly capable — steady conviction, easy presence."},
    'holden':        {'en': "A restless idealist with a searching core — questioning the surface, faithful at depth."},
    'saint':         {'en': "Quietly purposeful and broadly generous — principled at the root, wide-hearted in reach."},
    'romeo':         {'en': "Warmly expressive and boldly devoted — open heart, passionate intent."},
    'kenneth':       {'en': "Steadily capable and broadly principled — patient, reliable, quietly solid."},
    'jorge':         {'en': "Warmly capable and broadly principled — easy presence, steady core."},
    'angelo':        {'en': "Warmly expressive and broadly principled — generous presence, open-hearted intent."},
    'remington':     {'en': "Self-possessed and quietly capable — confident bearing, steady depth."},
    'paxton':        {'en': "Broadly principled and quietly capable — steady, peaceful, dependable."},
    'cody':          {'en': "Easy-going and quietly capable — warm presence, practical core."},
    'finley':        {'en': "Warm and openly capable — easy companionship, reliable core."},
    'kayson':        {'en': "Warm and quietly driven — easy presence, steady purpose."},
    'koa':           {'en': "Rooted and broadly generous — steady strength, warm, open-hearted presence."},
    'kash':          {'en': "Direct and quietly capable — clean presence, dependable purpose."},
    'josue':         {'en': "Broadly principled and quietly driven — steady conviction, open-hearted reach."},
    'ares':          {'en': "Bold and quietly intense — confident presence, searching inner world."},
    'hendrix':       {'en': "Self-possessed and directly expressive — confident presence, creative undercurrent."},
    'bryce':         {'en': "Direct and quietly capable — clean presence, dependable core."},
    'maximiliano':   {'en': "Broadly principled and quietly commanding — patient authority, warm, open reach."},
    'zyaire':        {'en': "Bold and quietly self-directed — direct presence, steady inner world."},
    'reid':          {'en': "Quietly principled and broadly capable — clean presence, dependable depth."},

    # --- Girls 151-300 ---
    'emersyn':       {'en': "Broadly principled and warmly expressive — steady conviction, open-hearted reach."},
    'june':          {'en': "Lightly carried warmth — effortless openness, genuinely bright presence."},
    'sloane':        {'en': "Self-possessed and quietly capable — direct, confident, reliably grounded."},
    'elsie':         {'en': "Warmly steady and openly generous — easy joy, reliable core."},
    'oaklynn':       {'en': "Grounded and quietly warm — steady presence, easy openness."},
    'oakley':        {'en': "Self-directed and quietly capable — confident presence, steady core."},
    'blakely':       {'en': "Direct and quietly capable — clean presence, dependable warmth."},
    'freya':         {'en': "A Norse boldness with a warm heart — self-possessed, broadly generous."},
    'piper':         {'en': "Quick and openly warm — restless energy, easy companionship."},
    'valeria':       {'en': "Warmly expressive and quietly driven — open-hearted energy, real purpose."},
    'arya':          {'en': "Bold and quietly self-directed — direct presence, searching undercurrent."},
    'adalynn':       {'en': "Warmly steady and broadly principled — easy warmth, quiet conviction."},
    'everleigh':     {'en': "Broadly principled and quietly warm — steady foundation, open-hearted reach."},
    'genevieve':     {'en': "Quietly refined and broadly generous — elegant depth, open-hearted reach."},
    'anastasia':     {'en': "A composed elegance with searching depth — quietly principled, widely generous."},
    'isabel':        {'en': "Warmly principled and broadly capable — easy warmth, steady conviction."},
    'peyton':        {'en': "Direct and quietly capable — practical, warm, dependably solid."},
    'amaya':         {'en': "Warmly expressive and broadly generous — easy presence, open-hearted reach."},
    'isabelle':      {'en': "Warmly principled and broadly capable — refined presence, steady conviction."},
    'olive':         {'en': "Quietly warm and broadly generous — easy presence, open-hearted core."},
    'ruth':          {'en': "Steady and broadly principled — patient loyalty, quietly enduring warmth."},
    'ximena':        {'en': "Bold and warmly expressive — self-possessed presence, open-hearted depth."},
    'evangeline':    {'en': "A quietly principled idealist — broad-hearted in reach, devoted at the core."},
    'katherine':     {'en': "Broadly principled and quietly capable — steady confidence, considered depth."},
    'callie':        {'en': "Openly warm and broadly capable — easy companionship, reliable core."},
    'rosalie':       {'en': "Warmly expressive and quietly devoted — gentle presence, tender core."},
    'alani':         {'en': "Warm and openly generous — easy energy, broad-hearted presence."},
    'lilah':         {'en': "Gently warm and broadly generous — easy presence, tender core."},
    'kaia':          {'en': "Bright and openly warm — quick energy, easy companionship."},
    'brianna':       {'en': "Broadly warm and quietly capable — easy presence, steady core."},
    'bailey':        {'en': "Easy-going and warmly capable — open presence, reliable core."},
    'phoebe':        {'en': "Bright and openly warm — quick spirit, genuinely generous."},
    'vivienne':      {'en': "Quietly refined and broadly generous — elegant presence, open-hearted depth."},
    'andrea':        {'en': "Broadly capable and quietly principled — steady, dependable, easy to trust."},
    'myla':          {'en': "Warmly self-directed and broadly capable — easy warmth, steady independence."},
    'lia':           {'en': "Lightly carried warmth — easy openness, genuine connection."},
    'sara':          {'en': "Warmly principled and broadly generous — easy presence, open-hearted core."},
    'kylie':         {'en': "Bright and openly warm — quick energy, easy companionship."},
    'reese':         {'en': "Direct and quietly capable — practical warmth, dependable core."},
    'annie':         {'en': "Openly warm and broadly capable — easy joy, reliable presence."},
    'daphne':        {'en': "A quick mind with a warm heart — bright presence, broadly generous spirit."},
    'ada':           {'en': "Quietly principled and broadly capable — precise in thought, warm in presence."},
    'adaline':       {'en': "Warmly steady and broadly principled — easy warmth, quiet conviction."},
    'arianna':       {'en': "Warmly expressive and broadly generous — open-hearted energy, genuine reach."},
    'ariella':       {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted core."},
    'sutton':        {'en': "Self-possessed and quietly capable — direct, dependable, reliably warm."},
    'celeste':       {'en': "Quietly elevated and broadly generous — serene presence, open-hearted depth."},
    'jasmine':       {'en': "Warmly expressive and broadly generous — easy warmth, open reach."},
    'mackenzie':     {'en': "Broadly capable and quietly principled — steady, practical, reliably warm."},
    'haven':         {'en': "Quietly steady and broadly warm — serene presence, open-hearted core."},
    'scottie':       {'en': "Direct and openly warm — quick energy, easy companionship."},
    'gemma':         {'en': "Warmly capable and broadly principled — easy presence, quiet depth."},
    'ana':           {'en': "Warmly principled and broadly generous — easy presence, open-hearted core."},
    'arabella':      {'en': "Quietly refined and broadly generous — elegant presence, open-hearted depth."},
    'lila':          {'en': "Gently warm and openly generous — easy presence, tender core."},
    'molly':         {'en': "Openly warm and broadly capable — easy joy, reliably caring."},
    'stevie':        {'en': "Self-directed and openly warm — direct, confident, easy to be around."},
    'blake':         {'en': "Direct and quietly capable — clean presence, dependable warmth."},
    'aitana':        {'en': "Warm and openly expressive — easy energy, genuine care."},
    'alaina':        {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'wren':          {'en': "A spare name with searching depth — quietly self-directed, broadly principled."},
    'noelle':        {'en': "Warmly principled and quietly generous — steady grace, open-hearted reach."},
    'delaney':       {'en': "Broadly capable and warmly generous — easy energy, reliable core."},
    'journee':       {'en': "Broadly open and quietly searching — expansive spirit, always moving forward."},
    'blair':         {'en': "Self-possessed and quietly capable — direct presence, dependable warmth."},
    'adalyn':        {'en': "Warmly steady and broadly principled — easy warmth, quiet conviction."},
    'kaylee':        {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'alexandra':     {'en': "Broadly principled and quietly commanding — patient authority, open-hearted reach."},
    'mabel':         {'en': "Warmly steady and openly generous — vintage warmth, reliably caring."},
    'norah':         {'en': "Warmly principled and broadly generous — easy presence, steady warmth."},
    'presley':       {'en': "Self-possessed and broadly warm — direct confidence, easy companionship."},
    'alora':         {'en': "Warmly self-directed and broadly open — easy energy, genuine depth."},
    'vera':          {'en': "Quietly honest and broadly principled — steady conviction, warm presence."},
    'celine':        {'en': "Quietly refined and broadly generous — elegant presence, open-hearted depth."},
    'amy':           {'en': "Openly warm and broadly generous — easy joy, reliably caring."},
    'brynlee':       {'en': "Warmly capable and broadly principled — easy presence, steady warmth."},
    'nyla':          {'en': "Warmly self-directed and broadly open — easy energy, genuine presence."},
    'saylor':        {'en': "Self-directed and broadly open — confident presence, forward-moving spirit."},
    'khloe':         {'en': "Warmly expressive and broadly capable — easy warmth, open reach."},
    'antonella':     {'en': "Warmly expressive and broadly principled — generous presence, open-hearted core."},
    'zara':          {'en': "Self-possessed and broadly capable — direct confidence, warm depth."},
    'aliyah':        {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'cataleya':      {'en': "Warmly expressive and broadly generous — exotic warmth, open-hearted reach."},
    'lennon':        {'en': "Self-possessed and broadly principled — creative confidence, quietly searching depth."},
    'kiara':         {'en': "Warmly expressive and broadly capable — easy presence, open-hearted core."},
    'camille':       {'en': "Quietly refined and broadly generous — elegant presence, open-hearted depth."},
    'dahlia':        {'en': "Quietly striking with a warm heart — self-possessed, broadly generous."},
    'kaylani':       {'en': "Warmly generous and broadly open — easy energy, open-hearted presence."},
    'mariana':       {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'diana':         {'en': "Quietly commanding and broadly principled — self-possessed presence, wide-open heart."},
    'reagan':        {'en': "Broadly capable and quietly principled — steady, practical, reliably warm."},
    'selena':        {'en': "Warmly expressive and broadly generous — easy warmth, luminous presence."},
    'kimberly':      {'en': "Broadly capable and quietly principled — steady, dependable, reliably warm."},
    'rachel':        {'en': "Warmly principled and broadly generous — steady conviction, open-hearted presence."},
    'gracie':        {'en': "Openly warm and broadly capable — easy joy, reliably caring."},
    'faith':         {'en': "Quietly faithful and broadly generous — steady conviction, open-hearted presence."},
    'juliana':       {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'miriam':        {'en': "Broadly principled and quietly capable — steady conviction, warm presence."},
    'elise':         {'en': "Quietly refined and broadly capable — elegant presence, steady warmth."},
    'noa':           {'en': "Quietly self-directed and broadly open — independent spirit, warm reach."},
    'elaina':        {'en': "Warmly principled and broadly capable — easy warmth, steady conviction."},
    'maisie':        {'en': "Openly warm and broadly capable — easy joy, vintage charm."},
    'lilith':        {'en': "Self-possessed and quietly searching — bold presence, deep undercurrent."},
    'collins':       {'en': "Directly capable and broadly principled — practical warmth, reliable core."},
    'palmer':        {'en': "Self-directed and quietly capable — confident presence, dependable warmth."},
    'lilly':         {'en': "Warmly devoted and broadly generous — easy warmth, tender core."},
    'shiloh':        {'en': "Broadly open and quietly principled — serene presence, wide-hearted reach."},
    'ophelia':       {'en': "Quietly searching with a wide-open heart — contemplative depth, broadly generous spirit."},
    'elianna':       {'en': "Warmly principled and broadly generous — easy warmth, open-hearted reach."},
    'lena':          {'en': "Quietly warm and broadly capable — steady presence, easy warmth."},
    'harmony':       {'en': "Broadly open and quietly principled — generous reach, harmonising spirit."},
    'aspen':         {'en': "Broadly open and quietly principled — serene presence, forward-moving spirit."},
    'gia':           {'en': "Bright and openly warm — quick energy, easy companionship."},
    'leila':         {'en': "Warmly principled and broadly generous — easy presence, open-hearted reach."},
    'jane':          {'en': "Quietly principled and broadly capable — spare, direct, reliably solid."},
    'talia':         {'en': "Warmly principled and broadly generous — easy warmth, open-hearted reach."},
    'adelaide':      {'en': "Quietly refined and broadly principled — elegant presence, steady depth."},
    'dakota':        {'en': "Broadly open and quietly self-directed — expansive spirit, confident presence."},
    'lola':          {'en': "Openly warm and broadly expressive — easy joy, warm companionship."},
    'lucille':       {'en': "Warmly steady and broadly generous — vintage warmth, open-hearted core."},
    'kailani':       {'en': "Warmly generous and broadly open — easy energy, open-hearted presence."},
    'morgan':        {'en': "Broadly capable and quietly principled — steady, dependable, warm."},
    'zuri':          {'en': "Warmly expressive and broadly generous — easy warmth, bright presence."},
    'milani':        {'en': "Warmly expressive and broadly capable — easy presence, open-hearted reach."},
    'daniela':       {'en': "Warmly expressive and broadly principled — generous presence, open-hearted core."},
    'selah':         {'en': "Quietly principled and broadly generous — serene presence, open-hearted depth."},
    'alessia':       {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted core."},
    'angela':        {'en': "Warmly principled and broadly generous — easy presence, open-hearted core."},
    'juliet':        {'en': "Warmly expressive and quietly devoted — open heart, passionate intent."},
    'evie':          {'en': "Openly warm and broadly capable — easy joy, reliably caring."},
    'amora':         {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'marley':        {'en': "Broadly warm and quietly capable — easy presence, reliable core."},
    'sydney':        {'en': "Broadly capable and quietly principled — steady, dependable, reliably warm."},
    'alanna':        {'en': "Warmly principled and broadly generous — easy warmth, open-hearted reach."},
    'leia':          {'en': "Quietly self-possessed with a warm heart — bold presence, deeply caring."},
    'luciana':       {'en': "Warmly expressive and broadly generous — easy warmth, luminous reach."},
    'kamila':        {'en': "Warmly capable and broadly principled — easy presence, steady warmth."},
    'harlow':        {'en': "Self-possessed and quietly warm — vintage glamour, easy depth."},
    'kali':          {'en': "Boldly self-directed and broadly principled — direct presence, warm depth."},
    'octavia':       {'en': "Quietly commanding and broadly principled — self-possessed presence, wide-open heart."},
    'gabriela':      {'en': "Warmly expressive and broadly principled — generous presence, open-hearted core."},
    'ariel':         {'en': "Broadly open and quietly searching — expansive spirit, warm-hearted depth."},
    'maggie':        {'en': "Openly warm and broadly capable — easy joy, genuinely caring."},

    # --- Boys 301-500 ---
    'brian':         {'en': "Broadly principled and quietly capable — steady, practical, reliable."},
    'bodhi':         {'en': "Quietly searching and broadly open — contemplative spirit, generous reach."},
    'cruz':          {'en': "Quietly principled and broadly warm — steady conviction, easy presence."},
    'kaden':         {'en': "Energetic and broadly warm — direct, dependable, easy."},
    'bryan':         {'en': "Broadly principled and quietly capable — steady, practical, reliable."},
    'zane':          {'en': "Direct and quietly capable — clean presence, dependable depth."},
    'francisco':     {'en': "Warmly expressive and broadly principled — generous presence, open-hearted intent."},
    'martin':        {'en': "Steady and quietly capable — patient build, broad-minded purpose."},
    'brady':         {'en': "Warm and openly capable — easy companionship, reliable core."},
    'casey':         {'en': "Broadly capable and quietly warm — easy presence, steady core."},
    'shepherd':      {'en': "Quietly guiding and broadly generous — protective core, wide-hearted reach."},
    'aidan':         {'en': "Warm and directly capable — easy energy, steady purpose."},
    'baker':         {'en': "Quietly capable and broadly warm — steady hands, easy presence."},
    'malcolm':       {'en': "Broadly principled and quietly capable — steady, considered, reliable."},
    'jax':           {'en': "Fast-moving and direct — energetic presence, reliably grounded."},
    'cash':          {'en': "Direct and quietly capable — clean presence, dependable core."},
    'clayton':       {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'kohen':         {'en': "Quick-minded and broadly principled — direct, fair, consistently capable."},
    'leonel':        {'en': "Quietly driven and broadly warm — patient ambition, easy warmth."},
    'cristian':      {'en': "Twin-leader confidence with a principled core — direct, faithful, visionary."},
    'bowen':         {'en': "Broadly principled and quietly capable — steady foundation, warm reach."},
    'dante':         {'en': "Quietly determined and broadly principled — patient ambition, considered depth."},
    'ali':           {'en': "Broadly principled and quietly capable — steady, direct, reliable."},
    'jaylen':        {'en': "Warm and openly capable — easy energy, reliable core."},
    'orion':         {'en': "Broadly searching and quietly principled — expansive spirit, steady inner world."},
    'briggs':        {'en': "Direct and quietly capable — clean presence, dependable core."},
    'jensen':        {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'preston':       {'en': "Quietly capable and broadly principled — steady, dependable, easy to trust."},
    'maximus':       {'en': "Broadly principled and quietly commanding — patient authority, confident bearing."},
    'gideon':        {'en': "Broadly principled and quietly driven — steady conviction, wide-hearted reach."},
    'erick':         {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'archie':        {'en': "Warm and openly capable — easy-going competence, genuine warmth."},
    'colin':         {'en': "Broadly principled and quietly warm — steady, easy, dependable."},
    'sonny':         {'en': "Openly warm and broadly capable — easy joy, reliably caring."},
    'mathias':       {'en': "Broadly principled and quietly capable — steady conviction, warm presence."},
    'ezequiel':      {'en': "Broadly principled and quietly driven — steady conviction, open-hearted reach."},
    'sullivan':      {'en': "Warm and openly capable — easy companionship, reliable core."},
    'joaquin':       {'en': "Warmly expressive and quietly driven — open-hearted energy, real purpose."},
    'wade':          {'en': "Direct and quietly capable — clean presence, dependable core."},
    'king':          {'en': "Quietly commanding and broadly principled — self-possessed bearing, steady authority."},
    'niko':          {'en': "Bright and openly warm — quick presence, genuine care."},
    'damien':        {'en': "Devoted and inspired with a builder's foundation — gentle warmth, big vision, patient craft."},
    'kade':          {'en': "Direct and quietly capable — clean presence, dependable core."},
    'bodie':         {'en': "Easy-going and broadly capable — warm presence, practical core."},
    'dariel':        {'en': "Broadly principled and quietly warm — steady conviction, easy presence."},
    'luciano':       {'en': "Warmly expressive and broadly generous — easy warmth, luminous reach."},
    'cayden':        {'en': "Energetic and broadly warm — direct, dependable, easy."},
    'andre':         {'en': "Open-hearted and driven — expressive warmth, ambitious core."},
    'manuel':        {'en': "Broadly principled and quietly driven — steady conviction, open-hearted reach."},
    'fernando':      {'en': "Bold and warmly expressive — confident presence, open-hearted depth."},
    'colson':        {'en': "Quietly capable and broadly principled — steady foundation, reliable depth."},
    'cairo':         {'en': "Bold and quietly self-directed — confident presence, steady inner world."},
    'anderson':      {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'kyler':         {'en': "Broadly capable and quietly principled — steady, dependable, reliable."},
    'onyx':          {'en': "Self-possessed and quietly deep — bold presence, searching undercurrent."},
    'ibrahim':       {'en': "Steady at the root, wide in reach — patient foundation, broadly generous spirit."},
    'cesar':         {'en': "Quietly commanding and broadly principled — patient authority, confident bearing."},
    'travis':        {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'santino':       {'en': "Warmly expressive and broadly principled — generous presence, open-hearted intent."},
    'callahan':      {'en': "Warm and openly capable — easy-going energy, reliable core."},
    'bradley':       {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'baylor':        {'en': "Self-directed and broadly capable — confident presence, steady core."},
    'banks':         {'en': "Direct and quietly capable — clean presence, dependable depth."},
    'russell':       {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'desmond':       {'en': "Broadly principled and quietly warm — steady, easy, dependable."},
    'killian':       {'en': "Bold and quietly principled — direct conviction, searching undercurrent."},
    'grady':         {'en': "Broadly capable and quietly warm — steady presence, easy openness."},
    'rylan':         {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'sterling':      {'en': "Quietly refined and broadly capable — steady grace, considered depth."},
    'kylo':          {'en': "Self-possessed and quietly intense — bold presence, searching inner world."},
    'eduardo':       {'en': "Warmly capable and broadly principled — easy presence, steady core."},
    'ricardo':       {'en': "Broadly capable and quietly principled — steady ambition, confident bearing."},
    'wells':         {'en': "Quietly capable and broadly principled — clean presence, dependable depth."},
    'stephen':       {'en': "Broadly principled and quietly capable — steady conviction, warm presence."},
    'zander':        {'en': "Bold and quietly principled — confident presence, considered core."},
    'raymond':       {'en': "Broadly principled and quietly warm — steady protection, easy presence."},
    'hector':        {'en': "Bold and quietly principled — direct conviction, steady purpose."},
    'eliam':         {'en': "Broadly principled and quietly driven — steady conviction, open-hearted reach."},
    'edwin':         {'en': "Broadly capable and quietly principled — steady, warm, reliable."},
    'titus':         {'en': "Broadly principled and quietly commanding — patient authority, warm presence."},
    'iker':          {'en': "Direct and quietly capable — clean presence, dependable core."},
    'franklin':      {'en': "Broadly principled and quietly capable — steady, practical, reliable."},
    'kamari':        {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'marco':         {'en': "Broadly capable and quietly principled — steady ambition, confident bearing."},
    'spencer':       {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'julius':        {'en': "Quietly commanding and broadly principled — patient authority, considered depth."},
    'khalil':        {'en': "Warmly principled and broadly generous — easy presence, open-hearted core."},
    'marshall':      {'en': "Broadly capable and quietly principled — steady hands, reliable purpose."},
    'wilder':        {'en': "Self-directed and quietly capable — confident presence, steady core."},
    'jared':         {'en': "Broadly principled and quietly capable — steady, reliable, warm."},
    'jaden':         {'en': "Warm and broadly open — easy energy, genuine care."},
    'kashton':       {'en': "Direct and quietly capable — clean presence, dependable purpose."},
    'jay':           {'en': "Bright and quietly capable — quick energy, easy companionship."},
    'karson':        {'en': "Quietly searching with a wide-open heart — contemplative depth, generous reach."},
    'mario':         {'en': "Broadly capable and quietly principled — steady ambition, confident bearing."},
    'remy':          {'en': "Wide-hearted and restless — generous spirit, adventurous spark."},
    'pedro':         {'en': "Solid and broadly generous — steady foundation, open-hearted presence."},
    'sergio':        {'en': "Quietly capable and broadly principled — steady, practical, reliable."},
    'hugo':          {'en': "Quietly driven and broadly principled — patient ambition, open-hearted reach."},
    'prince':        {'en': "Quietly commanding and broadly principled — confident bearing, steady authority."},
    'winston':       {'en': "Broadly principled and quietly capable — steady, considered, reliable."},
    'pablo':         {'en': "Steadily principled and quietly capable — reliable, considered, broadly solid."},
    'forrest':       {'en': "Quietly grounded and broadly generous — steady presence, open-hearted reach."},
    'augustus':      {'en': "Quietly commanding and broadly principled — patient authority, considered depth."},
    'kobe':          {'en': "Broadly capable and quietly principled — steady, direct, reliable."},
    'daxton':        {'en': "Direct and energetic — moves fast, lands steady."},
    'tadeo':         {'en': "Broadly principled and quietly warm — steady conviction, easy presence."},
    'apollo':        {'en': "Bold and broadly principled — direct confidence, inspired outlook."},
    'lawson':        {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'kian':          {'en': "Broadly principled and quietly capable — steady, warm, reliable."},
    'solomon':       {'en': "Quietly wise and broadly principled — patient depth, wide-hearted reach."},
    'chance':        {'en': "Direct and quietly capable — energetic presence, steady intent."},
    'kayce':         {'en': "Broadly capable and quietly warm — easy presence, steady core."},
    'raphael':       {'en': "Wide-hearted and broadly principled — generous spirit, visionary reach."},
    'reed':          {'en': "Quietly principled and broadly capable — clean presence, dependable depth."},
    'jake':          {'en': "Warm and quietly capable — easy presence, reliable core."},
    'frederick':     {'en': "Broadly principled and quietly commanding — patient authority, considered depth."},
    'armani':        {'en': "Quietly self-possessed and broadly capable — confident bearing, steady depth."},
    'hank':          {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'nehemiah':      {'en': "Broadly principled and quietly driven — steady conviction, open-hearted reach."},
    'royal':         {'en': "Quietly commanding and broadly principled — confident bearing, steady purpose."},
    'kameron':       {'en': "A naturally guiding, expressive spirit — warmth that teaches, creativity through and through."},
    'malik':         {'en': "Broadly principled and quietly commanding — patient authority, warm presence."},
    'alijah':        {'en': "A wide-hearted spirit — protective at the core, expressive on the outside."},
    'kane':          {'en': "Bold and quietly principled — direct conviction, easy companionship."},
    'dalton':        {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'lewis':         {'en': "Quietly refined and broadly capable — steady grace, considered depth."},
    'noel':          {'en': "Warmly principled and quietly generous — steady grace, open-hearted reach."},
    'benson':        {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'sean':          {'en': "Broadly principled and quietly warm — reliable presence, easy companionship."},
    'clark':         {'en': "Direct and quietly capable — clean presence, dependable core."},
    'miller':        {'en': "Broadly capable and quietly principled — steady hands, reliable purpose."},
    'kyle':          {'en': "Directly capable and broadly principled — practical warmth, reliable core."},
    'kieran':        {'en': "Boldly principled and quietly warm — direct conviction, easy companionship."},
    'fabian':        {'en': "Warmly capable and broadly principled — easy presence, quiet depth."},
    'tanner':        {'en': "Broadly capable and quietly principled — steady hands, practical warmth."},
    'marcelo':       {'en': "Broadly capable and quietly principled — steady ambition, confident bearing."},
    'rowen':         {'en': "Quietly driven and self-possessed — confident leader with a reflective undercurrent."},
    'isaias':        {'en': "Broadly principled and quietly driven — steady conviction, open-hearted reach."},
    'zayne':         {'en': "Direct and quietly capable — clean presence, dependable depth."},
    'nasir':         {'en': "Broadly principled and quietly capable — steady, direct, reliable."},
    'raiden':        {'en': "Bold and quietly principled — direct conviction, steady purpose."},
    'francis':       {'en': "Broadly principled and quietly capable — steady, considerate, reliable."},
    'bo':            {'en': "Direct and quietly capable — spare presence, dependable core."},
    'valentino':     {'en': "Warmly expressive and broadly principled — generous presence, open-hearted intent."},
    'rome':          {'en': "Boldly principled and quietly searching — confident presence, ancient depth."},
    'damon':         {'en': "Broadly principled and quietly driven — steady conviction, open-hearted reach."},
    'reece':         {'en': "Bold and quietly principled — direct conviction, easy companionship."},
    'esteban':       {'en': "Broadly principled and quietly capable — steady conviction, warm presence."},
    'edgar':         {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'johnny':        {'en': "Broadly principled and quietly warm — reliable presence, easy companionship."},
    'kylian':        {'en': "Bold and quietly principled — direct conviction, searching undercurrent."},
    'tyson':         {'en': "Direct and quietly capable — fast-moving, reliably grounded."},
    'uriel':         {'en': "Broadly principled and quietly visionary — steady conviction, inspired reach."},
    'royce':         {'en': "Quietly refined and broadly capable — steady grace, considered depth."},
    'cillian':       {'en': "Bold and quietly principled — direct conviction, easy companionship."},
    'koda':          {'en': "Broadly warm and quietly capable — easy presence, steady core."},
    'kyson':         {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'jalen':         {'en': "Warm and openly capable — easy energy, reliable core."},
    'frank':         {'en': "Direct and broadly capable — clean presence, dependable purpose."},
    'conrad':        {'en': "Broadly principled and quietly capable — steady, reliable, practical."},
    'jasiah':        {'en': "Broadly principled and quietly warm — steady conviction, easy presence."},
    'matthias':      {'en': "Broadly principled and quietly capable — steady conviction, warm presence."},
    'zaire':         {'en': "Broadly open and quietly searching — expansive spirit, warm-hearted depth."},
    'corbin':        {'en': "Quietly principled and broadly capable — clean presence, dependable depth."},
    'asa':           {'en': "Quietly devoted and broadly generous — tender at home, open-hearted beyond it."},
    'yusuf':         {'en': "Visionary at heart, driven in action — leads with conviction and ambition."},
    'erik':          {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'callen':        {'en': "Warm and quietly principled — easy presence, considered depth."},
    'kendrick':      {'en': "Bold and quietly principled — direct conviction, steady purpose."},
    'odin':          {'en': "Self-possessed and quietly searching — bold presence, deep undercurrent."},
    'brantley':      {'en': "Broadly capable and quietly principled — steady, practical, reliable."},
    'rodrigo':       {'en': "Bold and warmly expressive — confident presence, open-hearted depth."},
    'marcos':        {'en': "Broadly principled and quietly capable — steady ambition, confident bearing."},
    'gianni':        {'en': "Warmly expressive and broadly principled — generous presence, easy warmth."},
    'alexis':        {'en': "Broadly principled and quietly capable — steady, adaptable, reliable."},
    'lucian':        {'en': "Self-directed and broadly generous — independent core, luminous reach."},
    'denver':        {'en': "Broadly open and quietly self-directed — expansive spirit, confident presence."},
    'sylas':         {'en': "Devoted, self-directed, and curious — gentle leader with an adventurous spark."},
    'andy':          {'en': "Open-hearted and broadly capable — easy warmth, reliable core."},
    'collin':        {'en': "Broadly principled and quietly warm — steady, easy, dependable."},
    'hezekiah':      {'en': "Broadly principled and quietly driven — steady conviction, open-hearted reach."},
    'moshe':         {'en': "Broadly principled and quietly driven — steady conviction, wide-hearted purpose."},
    'finnegan':      {'en': "Warm and openly capable — easy-going energy, reliable core."},
    'ronin':         {'en': "Self-directed and quietly principled — independent path, steady inner world."},
    'atreus':        {'en': "Bold and quietly searching — confident presence, ancient depth."},
    'adan':          {'en': "Self-directed and harmonising with a driven core — independent, gentle, ambitious."},
    'emanuel':       {'en': "Wide-hearted and quietly purposeful — devoted at the core, open in reach."},
    'mack':          {'en': "Direct and quietly capable — clean presence, dependable core."},
    'leandro':       {'en': "Warmly expressive and broadly principled — generous presence, open-hearted intent."},
    'rocco':         {'en': "Self-possessed and quietly capable — confident bearing, steady depth."},

    # --- Girls 301-500 ---
    'rosemary':      {'en': "Warmly devoted and broadly generous — vintage warmth, open-hearted core."},
    'ryleigh':       {'en': "Openly warm and broadly capable — easy energy, reliably caring."},
    'tessa':         {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'evelynn':       {'en': "Inspired and driven with a warmly expressive face — visionary mind, creative voice."},
    'londyn':        {'en': "Self-directed and broadly open — confident presence, forward-moving spirit."},
    'danna':         {'en': "Warmly expressive and broadly generous — easy presence, open-hearted reach."},
    'amina':         {'en': "Quietly principled and broadly generous — steady conviction, warm presence."},
    'brooke':        {'en': "Direct and quietly capable — clean presence, dependable warmth."},
    'samara':        {'en': "Warmly principled and broadly generous — easy presence, open-hearted depth."},
    'kendall':       {'en': "Self-directed and broadly capable — confident presence, practical warmth."},
    'rosie':         {'en': "Openly warm and broadly capable — easy joy, reliably caring."},
    'alayna':        {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'angelina':      {'en': "Warmly principled and broadly generous — easy presence, open-hearted core."},
    'francesca':     {'en': "Warmly expressive and broadly principled — generous presence, easy depth."},
    'adelyn':        {'en': "Warmly steady and broadly principled — easy warmth, quiet conviction."},
    'fatima':        {'en': "Deeply principled and broadly generous — rooted in faith, open in reach."},
    'hope':          {'en': "Quietly principled and broadly generous — serene presence, forward-looking spirit."},
    'nicole':        {'en': "Broadly capable and quietly principled — steady, dependable, warm."},
    'nayeli':        {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'catherine':     {'en': "Broadly principled and quietly capable — steady confidence, considered depth."},
    'nina':          {'en': "Warmly expressive and broadly capable — easy presence, quiet depth."},
    'journey':       {'en': "Broadly open and quietly searching — expansive spirit, always moving forward."},
    'adriana':       {'en': "Twin-visionary and broadly principled — inspired core, idealistic outlook."},
    'camilla':       {'en': "Warmly expressive and quietly capable — easy presence, steady depth."},
    'ailani':        {'en': "Warmly generous and broadly principled — easy energy, open-hearted presence."},
    'malia':         {'en': "Warmly devoted and broadly generous — easy warmth, tender core."},
    'meadow':        {'en': "Broadly open and quietly warm — serene presence, easy openness."},
    'jordyn':        {'en': "Quietly driven and self-possessed — confident leader with a contemplative undercurrent."},
    'joanna':        {'en': "Warmly principled and broadly generous — easy presence, open-hearted core."},
    'emory':         {'en': "Expressive and driven with a builder's face — creative voice, real ambition, patient craft."},
    'malani':        {'en': "Warmly generous and broadly open — easy energy, open-hearted presence."},
    'serena':        {'en': "Quietly serene and broadly generous — steady presence, open-hearted depth."},
    'teagan':        {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'aurelia':       {'en': "Quietly refined and broadly generous — elegant presence, golden depth."},
    'vanessa':       {'en': "Self-directed and broadly capable — independent core, warm presence."},
    'kayla':         {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'navy':          {'en': "Direct and quietly capable — clean presence, dependable warmth."},
    'poppy':         {'en': "Openly warm and broadly expressive — easy joy, warm companionship."},
    'kalani':        {'en': "Warmly generous and broadly open — easy energy, open-hearted presence."},
    'regina':        {'en': "Quietly commanding and broadly principled — self-possessed presence, warm authority."},
    'adelina':       {'en': "Warmly steady and broadly principled — easy warmth, quiet conviction."},
    'rebecca':       {'en': "Broadly principled and quietly capable — steady, warm, reliable."},
    'ariyah':        {'en': "Harmonising, inspired, broadly generous — graceful and big-hearted."},
    'esme':          {'en': "Quietly refined and broadly generous — elegant presence, open-hearted depth."},
    'heidi':         {'en': "Quietly refined and broadly principled — elegant presence, steady depth."},
    'aisha':         {'en': "Deeply principled and broadly generous — warm presence, open-hearted reach."},
    'julieta':       {'en': "Warmly expressive and quietly devoted — open heart, passionate intent."},
    'thea':          {'en': "Quietly inspired and broadly generous — serene presence, open-hearted depth."},
    'annabelle':     {'en': "Warmly principled and broadly generous — easy warmth, open-hearted core."},
    'esmeralda':     {'en': "Quietly striking and broadly generous — self-possessed warmth, wide-open heart."},
    'lauren':        {'en': "Broadly capable and quietly principled — steady, dependable, warm."},
    'julianna':      {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'taylor':        {'en': "Broadly capable and quietly principled — steady, practical, reliably warm."},
    'wrenlee':       {'en': "Expressive and quietly capable — creative voice, steady warmth."},
    'london':        {'en': "Self-directed and broadly open — confident presence, forward-moving spirit."},
    'giselle':       {'en': "Warmly expressive and broadly principled — generous presence, elegant depth."},
    'sabrina':       {'en': "Broadly open and quietly searching — expansive spirit, warm-hearted depth."},
    'laura':         {'en': "Broadly capable and quietly principled — steady, dependable, warm."},
    'sylvie':        {'en': "Quietly warm and broadly generous — steady presence, easy warmth."},
    'sylvia':        {'en': "Broadly open and quietly principled — grounded presence, warm reach."},
    'alaya':         {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted core."},
    'raya':          {'en': "Warmly principled and broadly generous — easy presence, open-hearted reach."},
    'elora':         {'en': "Warmly principled and broadly capable — easy warmth, steady conviction."},
    'mya':           {'en': "Steady and broadly generous — patient hands, wide-hearted reach, dependable presence."},
    'dream':         {'en': "Broadly open and quietly searching — expansive spirit, warm-hearted depth."},
    'viviana':       {'en': "Warmly expressive and broadly capable — easy presence, open-hearted depth."},
    'elaine':        {'en': "Broadly capable and quietly principled — steady, warm, reliable."},
    'elodie':        {'en': "Quietly warm and broadly generous — easy presence, refined depth."},
    'laila':         {'en': "Warmly principled and broadly generous — easy presence, open-hearted reach."},
    'sunny':         {'en': "Openly warm and broadly expressive — easy joy, warm companionship."},
    'briella':       {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'lana':          {'en': "Quietly warm and broadly capable — steady presence, easy warmth."},
    'paige':         {'en': "Direct and quietly capable — clean presence, dependable warmth."},
    'itzel':         {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'mckenna':       {'en': "Broadly capable and quietly principled — steady, practical, reliably warm."},
    'melissa':       {'en': "Warmly principled and broadly generous — easy presence, steady warmth."},
    'frances':       {'en': "Broadly principled and quietly capable — steady, considerate, reliable."},
    'mira':          {'en': "Warmly expressive and broadly generous — easy presence, open-hearted depth."},
    'hattie':        {'en': "Warmly steady and broadly generous — vintage warmth, reliably caring."},
    'astrid':        {'en': "Quietly bold and broadly principled — self-possessed presence, warm depth."},
    'brynn':         {'en': "Quietly grounded and broadly principled — steady presence, warm reach."},
    'winter':        {'en': "Quietly principled and broadly open — serene presence, forward-moving spirit."},
    'aylin':         {'en': "Warmly expressive and broadly generous — easy warmth, luminous presence."},
    'miley':         {'en': "Openly warm and broadly expressive — easy joy, warm companionship."},
    'raven':         {'en': "Self-possessed and quietly searching — bold presence, deep undercurrent."},
    'jocelyn':       {'en': "Broadly capable and quietly principled — steady, practical, reliably warm."},
    'maryam':        {'en': "Broadly principled and quietly capable — steady conviction, warm presence."},
    'veronica':      {'en': "Quietly principled and broadly capable — steady conviction, warm presence."},
    'gwendolyn':     {'en': "Quietly principled and broadly generous — steady grace, open-hearted reach."},
    'anya':          {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'alivia':        {'en': "Broadly open and warmly generous — easy presence, open-hearted reach."},
    'harley':        {'en': "Self-directed and broadly capable — confident presence, practical warmth."},
    'charlee':       {'en': "Warmly expressive and broadly devoted — easy warmth, genuinely caring."},
    'alyssa':        {'en': "Broadly capable and quietly principled — steady, dependable, warm."},
    'colette':       {'en': "Quietly refined and broadly generous — elegant presence, open-hearted depth."},
    'lorelai':       {'en': "Warmly expressive and quietly searching — open heart, luminous depth."},
    'jayla':         {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'ivory':         {'en': "Quietly elegant and broadly generous — refined presence, open-hearted reach."},
    'anaya':         {'en': "Warmly principled and broadly generous — easy warmth, open-hearted reach."},
    'fiona':         {'en': "Quietly self-possessed and broadly principled — direct confidence, warm depth."},
    'trinity':       {'en': "Broadly principled and quietly capable — steady conviction, open-hearted reach."},
    'aubree':        {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'michelle':      {'en': "Broadly capable and quietly principled — steady, dependable, warm."},
    'matilda':       {'en': "Self-possessed and broadly principled — direct confidence, quietly capable."},
    'lilliana':      {'en': "Warmly devoted and broadly generous — easy warmth, gentle, inspired presence."},
    'mallory':       {'en': "Broadly capable and quietly principled — steady, practical, reliably warm."},
    'mariah':        {'en': "Broadly principled and quietly capable — steady conviction, warm presence."},
    'helena':        {'en': "Quietly inspired and broadly capable — steady warmth, luminous presence."},
    'wynter':        {'en': "Quietly principled and broadly open — serene presence, forward-moving spirit."},
    'carmen':        {'en': "Warmly expressive and broadly principled — generous presence, easy depth."},
    'alayah':        {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted core."},
    'liana':         {'en': "Warmly expressive and broadly generous — easy presence, open-hearted depth."},
    'holly':         {'en': "Openly warm and broadly capable — easy joy, reliably caring."},
    'madilyn':       {'en': "Warmly capable and broadly principled — easy presence, steady warmth."},
    'raelyn':        {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'willa':         {'en': "Self-directed and quietly warm — independent core, tender presence."},
    'helen':         {'en': "Quietly inspired and broadly capable — steady warmth, luminous presence."},
    'emely':         {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'alessandra':    {'en': "Broadly principled and quietly commanding — open-hearted leadership, warm authority."},
    'gracelynn':     {'en': "Warmly devoted and broadly generous — easy grace, open-hearted reach."},
    'carolina':      {'en': "Broadly principled and warmly expressive — easy warmth, steady conviction."},
    'arleth':        {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'salem':         {'en': "Broadly principled and quietly searching — serene presence, open-hearted depth."},
    'dorothy':       {'en': "Warmly devoted and broadly generous — vintage warmth, open-hearted core."},
    'xiomara':       {'en': "Warmly expressive and broadly generous — exotic warmth, open-hearted reach."},
    'elisa':         {'en': "Warmly principled and broadly capable — easy warmth, steady conviction."},
    'reign':         {'en': "Quietly commanding and broadly principled — self-possessed presence, open authority."},
    'florence':      {'en': "Broadly principled and quietly capable — steady, warm, luminous presence."},
    'alicia':        {'en': "Broadly capable and quietly principled — steady, warm, reliable."},
    'madeleine':     {'en': "Quietly refined and broadly capable — elegant presence, thoughtful depth."},
    'melany':        {'en': "Restless and harmonising with a creative voice — adventurous spark, gentle warmth."},
    'katalina':      {'en': "Quietly reflective and expressive — contemplative depth, creative voice, steady hands."},
    'zariah':        {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'bonnie':        {'en': "Openly warm and broadly capable — easy joy, genuinely caring."},
    'joy':           {'en': "Lightly carried warmth — easy openness, genuine brightness."},
    'kaliyah':       {'en': "Boldly self-directed and broadly principled — direct presence, warm depth."},
    'haisley':       {'en': "Warmly capable and broadly principled — easy presence, steady warmth."},
    'sarai':         {'en': "Warmly principled and broadly generous — easy warmth, open-hearted reach."},
    'blaire':        {'en': "Self-possessed and quietly capable — direct presence, dependable warmth."},
    'elowyn':        {'en': "Warmly principled and broadly capable — easy warmth, steady conviction."},
    'saige':         {'en': "Warmly self-directed and broadly open — easy energy, genuine depth."},
    'adelynn':       {'en': "Warmly steady and broadly principled — easy warmth, quiet conviction."},
    'opal':          {'en': "Quietly luminous and broadly generous — refined presence, open-hearted reach."},
    'demi':          {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'nylah':         {'en': "Warmly self-directed and broadly open — easy energy, genuine presence."},
    'emmy':          {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted core."},
    'camryn':        {'en': "Broadly capable and quietly expressive — warm presence, steady core."},
    'kira':          {'en': "Self-directed and quietly capable — independent core, warm presence."},
    'lorelei':       {'en': "Warmly expressive and quietly searching — open heart, luminous depth."},
    'daleyza':       {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'maia':          {'en': "Steady and broadly generous — patient hands, wide-hearted reach, dependable presence."},
    'bianca':        {'en': "Quietly elegant and broadly generous — refined presence, warm reach."},
    'aniyah':        {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'annalise':      {'en': "Warmly principled and broadly generous — easy warmth, open-hearted core."},
    'alexandria':    {'en': "Broadly principled and quietly commanding — patient authority, open-hearted reach."},
    'amirah':        {'en': "Warmly principled and broadly generous — easy presence, open-hearted reach."},
    'alison':        {'en': "Broadly capable and quietly principled — steady, warm, reliable."},
    'anahi':         {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'gracelyn':      {'en': "Warmly devoted and broadly generous — easy grace, open-hearted reach."},
    'brooklynn':     {'en': "Broadly capable and quietly principled — steady foundation, warm reach."},
    'miracle':       {'en': "Broadly open and quietly principled — serene presence, wide-hearted reach."},
    'everlee':       {'en': "Warmly self-directed and broadly generous — easy energy, genuine depth."},
    'adhara':        {'en': "Broadly principled and quietly searching — serene presence, open-hearted depth."},
    'alma':          {'en': "Quietly warm and broadly generous — easy presence, steady conviction."},
    'macie':         {'en': "Warmly capable and broadly principled — easy presence, steady warmth."},
    'murphy':        {'en': "Broadly capable and quietly principled — steady, dependable, warm."},
    'romina':        {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'cassidy':       {'en': "Broadly capable and quietly principled — steady, practical, reliably warm."},
    'clementine':    {'en': "Warmly principled and broadly generous — easy warmth, open-hearted core."},
    'heaven':        {'en': "Broadly open and quietly principled — serene presence, wide-hearted reach."},
    'elle':          {'en': "Warmly self-directed and broadly capable — clean presence, easy warmth."},
    'skye':          {'en': "Broadly open and quietly searching — expansive spirit, serene depth."},
    'destiny':       {'en': "Broadly open and quietly principled — forward-moving spirit, serene conviction."},
    'lyra':          {'en': "Broadly expressive and quietly searching — creative voice, warm-hearted depth."},
    'rylie':         {'en': "Openly warm and broadly capable — easy energy, reliably caring."},
    'paris':         {'en': "Quietly self-possessed with a warm heart — bold presence, broadly generous."},
    'felicity':      {'en': "Openly warm and broadly generous — easy joy, luminous presence."},
    'maddison':      {'en': "Warmly expressive and broadly capable — easy presence, steady warmth."},
    'leona':         {'en': "Quietly driven and broadly warm — patient ambition, easy warmth."},
    'scarlet':       {'en': "Quietly driven and warmly generous — real ambition, gentle care."},
    'kora':          {'en': "Self-directed and reflective with a creative voice — independent core, contemplative depth."},
    'mariam':        {'en': "Broadly principled and quietly capable — steady conviction, warm presence."},
    'meredith':      {'en': "Quietly refined and broadly generous — elegant depth, open-hearted reach."},
    'mckenzie':      {'en': "Broadly capable and quietly principled — steady, practical, reliably warm."},
    'dayana':        {'en': "Warmly expressive and broadly generous — easy warmth, open-hearted reach."},
    'cali':          {'en': "Openly warm and broadly capable — easy joy, easy companionship."},
    'amanda':        {'en': "Warmly principled and broadly generous — easy presence, open-hearted core."},
    'arielle':       {'en': "Broadly open and quietly searching — expansive spirit, warm-hearted depth."},
    'calliope':      {'en': "Broadly expressive and quietly searching — creative voice, warm-hearted depth."},
    'fernanda':      {'en': "Broadly principled and warmly expressive — confident presence, open-hearted depth."},
}


def _pin_synthesis(destiny: int, soul: int, personality: int, name: str) -> str:
    """One-or-two-sentence 'Together' synthesis for the pin's bottom band.
    Hand-curated when an override exists; templated otherwise."""
    if not destiny:
        return ""
    slug = slugify(name)
    ov = _PIN_SYNTH_OVERRIDES.get(slug)
    if ov:
        if ACTIVE_CC == 'FR':
            return ov.get('fr', ov.get('en', ''))
        return ov.get('en', '')

    d_red = _reduce_numerology(destiny)
    s_red = _reduce_numerology(soul) if soul else d_red
    p_red = _reduce_numerology(personality) if personality else d_red
    if ACTIVE_CC == 'FR':
        path = _PIN_SYNTH_PATH_FR.get(d_red, "")
        soul_w = _PIN_SYNTH_SOUL_FR.get(s_red, "")
        face_w = _PIN_SYNTH_FACE_FR.get(p_red, "")
        if not (path and soul_w and face_w):
            return ""
        return f"Une voie de {path} — animée intérieurement par {soul_w}, portée extérieurement par {face_w}."
    path = _PIN_SYNTH_PATH_EN.get(d_red, "")
    soul_w = _PIN_SYNTH_SOUL_EN.get(s_red, "")
    face_w = _PIN_SYNTH_FACE_EN.get(p_red, "")
    if not (path and soul_w and face_w):
        return ""
    return f"A path of {path} — drawn inward by {soul_w}, projecting {face_w} outward."


def _extract_pin_meaning(text: str) -> str:
    if not text:
        return ''
    text = text.replace('\n', ' ').strip()
    sentences = re.split(r'(?<=[a-z0-9\)\"\'\.])\.\s+', text)
    cands = []
    for i, s in enumerate(sentences[:6]):
        sl = s.lower()
        score = sum(2 for k in _PIN_MEANING_KEYWORDS if k in sl)
        if score > 0:
            cands.append((score, i, s.strip()))
    if not cands:
        return ''
    cands.sort(key=lambda x: (-x[0], x[1]))
    best = cands[0][2]
    best = _PIN_OPENER.sub('', best)
    best = _PIN_LEAD.sub('', best)
    best = _PIN_TAIL_FRAG.sub('', best)
    best = _pin_strip_unrenderable(best)
    best = best.lstrip(' ,;.')
    if not best or len(best) < 15:
        return ''
    best = best[0].upper() + best[1:]
    if len(best) > 100:
        truncated = best[:100].rsplit(' ', 1)[0]
        # Strip trailing punctuation that would read as ",…" or ";…"
        truncated = re.sub(r'[\s,;:\-—.]+$', '', truncated)
        best = truncated + '…'
    return best.rstrip(' .')


def _prerender_pins_parallel() -> None:
    """Build all missing per-name pins for the active country in parallel.
    Skips names whose pin already exists on disk (cache hit)."""
    targets: list[tuple[str, Path]] = []
    for name in top_pin_set:
        pin_path = OUT_DIR / 'pin' / f'{slugify(name)}.png'
        if not pin_path.exists():
            targets.append((name, pin_path))
    if not targets:
        return
    from concurrent.futures import ThreadPoolExecutor
    print(f"  {len(targets):,} pin renders (parallel)…")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda t: _render_pin_for(*t), targets))


def _render_pin_for(name: str, out_path: Path) -> None:
    """Build the per-name pin from active-country data. Idempotent — callers
    skip when out_path already exists."""
    dom = dominant_sex(name)
    meta = name_meta[name]

    # Gender label
    if ACTIVE_CC == 'FR':
        gender_label = "Prénom féminin" if dom == 'F' else "Prénom masculin"
    else:
        gender_label = f"{loc_singular(dom).capitalize()} name"

    # Origin chip text (localized) — empty if unknown
    enrich = ENRICHMENT.get(slugify(name), {})
    origin = enrich.get('origin')
    origin_label = ''
    if origin and origin in ORIGIN_LABELS_EN:
        origin_label = origin_label_cap(origin)

    # Popularity headline: prefer the more flattering of (latest rank) vs
    # (peak rank). A historical name like Ludovic ranks #2600 today but #10
    # at its 1970s peak — the latter is the better Pinterest hook.
    latest_rank = rank_by_year_sex.get((LATEST_YEAR, dom), {}).get(name)
    series = counts[name][dom]
    peak_year = max(series, key=series.get) if series else None
    peak_rank = rank_by_year_sex.get((peak_year, dom), {}).get(name) if peak_year else None
    cc_short = COUNTRY_LABEL[ACTIVE_CC]
    use_latest = latest_rank and (not peak_rank or latest_rank <= peak_rank or latest_rank <= 200)
    if use_latest:
        if ACTIVE_CC == 'FR':
            popularity = f"#{latest_rank} en {cc_short}, {LATEST_YEAR}"
        else:
            popularity = f"#{latest_rank} in the {cc_short}, {LATEST_YEAR}"
    elif peak_rank:
        if ACTIVE_CC == 'FR':
            popularity = f"#{peak_rank} en {peak_year} (apogée)"
        else:
            popularity = f"#{peak_rank} in {peak_year} (peak)"
    else:
        popularity = ""

    # Peak era — skip if popularity already names the peak year (would just
    # repeat "1977 (peak)" + "Peaked in the 1970s").
    if not use_latest:
        peak_era = ""
    else:
        peak_dec = meta.get('peak_dec')
        peak_era = S("picker_peak_decade", d=peak_dec) if peak_dec else ""

    # Sound (syllables)
    syll = meta.get('syll') or 0
    if syll:
        if ACTIVE_CC == 'FR':
            sound = f"{syll} syllabe" + ("s" if syll != 1 else "")
        else:
            sound = f"{syll} syllable" + ("s" if syll != 1 else "")
    else:
        sound = ""

    # Meaning blurb (extracted from Wikipedia) + numerology cards.
    meaning_text = enrich.get('meaning_fr' if ACTIVE_CC == 'FR' else 'meaning_en') \
        or enrich.get('meaning_en') or ''
    meaning = _extract_pin_meaning(meaning_text)
    # Curated override fills gaps where the Wikipedia extractor returns nothing.
    if not meaning:
        ov = enrich.get('meaning_pin_override')
        if ov:
            meaning = _pin_strip_unrenderable(ov)[:100]

    destiny, soul, pers = numerology_numbers(name)
    traits = NUMEROLOGY_TRAITS[ACTIVE_CC]

    def _trait(n: int) -> tuple[str, str]:
        t = traits.get(n) or traits.get(_reduce_numerology(n))
        return (t[0], t[1]) if t else ('', '')

    if ACTIVE_CC == 'FR':
        num_labels = ('DESTINÉE', 'CŒUR', 'PERSONNALITÉ')
    else:
        num_labels = ('DESTINY', 'SOUL', 'PERSONALITY')
    numerology = []
    if destiny:
        for n, lbl in ((destiny, num_labels[0]),
                       (soul, num_labels[1]),
                       (pers, num_labels[2])):
            tn, td = _trait(n)
            numerology.append((n, lbl, tn, td))

    synthesis = _pin_synthesis(destiny, soul, pers, name) if destiny else ""

    if ACTIVE_CC == 'FR':
        together_lbl = 'ENSEMBLE'
    else:
        together_lbl = 'TOGETHER'

    render_pin(
        out_path,
        name=name,
        gender_label=gender_label,
        origin_label=origin_label,
        popularity=popularity,
        peak_era=peak_era,
        sound=sound,
        meaning=meaning,
        numerology=numerology,
        synthesis=synthesis,
        together_label=together_lbl,
        url=f"namecharted.com{PREFIX}/name/{slugify(name)}",
        country_label=COUNTRY_NAME[ACTIVE_CC],
    )


# ---------------------------------------------------------------------------
# Name page
# ---------------------------------------------------------------------------
def generate_name_page(name):
    p = PREFIX
    dom = dominant_sex(name)
    series = counts[name][dom]
    years = sorted(series.keys())
    chart_counts = [series[y] for y in years]
    ft = name_sex_total[(name, 'F')]
    mt = name_sex_total[(name, 'M')]
    total = ft + mt
    peak = max(series.values()) if series else 0
    peak_year = max(series, key=series.get) if series else None
    f_pct = round(100 * ft / total) if total else 0
    label = loc_label(dom)
    singular_of = loc_of_singular(dom)

    if ft and mt and min(ft, mt) / total >= 0.10:
        gender_text = S("name_unisex", f=f_pct, m=100 - f_pct)
    else:
        pct = f_pct if dom == 'F' else (100 - f_pct)
        gender_text = S("name_pct_one", pct=pct, label=label)

    first_year = years[0] if years else None
    latest_rank = rank_by_year_sex.get((LATEST_YEAR, dom), {}).get(name)
    latest_count = series.get(LATEST_YEAR)
    insight_parts = []
    if first_year is not None:
        insight_parts.append(S("insight_first", name=name, year=first_year,
                               n=len(years), of_singular=singular_of,
                               singular=loc_singular(dom)))
    if peak_year is not None:
        peak_rank = rank_by_year_sex.get((peak_year, dom), {}).get(name)
        if peak_rank:
            insight_parts.append(S("insight_peak_ranked", year=peak_year,
                                   count=fmt(peak), rank=fmt(peak_rank)))
        else:
            insight_parts.append(S("insight_peak", year=peak_year, count=fmt(peak)))
    if latest_count and latest_rank:
        insight_parts.append(S("insight_latest_ranked", year=LATEST_YEAR,
                               count=fmt(latest_count), label=label,
                               rank=fmt(latest_rank)))
    elif latest_count:
        insight_parts.append(S("insight_latest", year=LATEST_YEAR,
                               count=fmt(latest_count), label=label))
    else:
        insight_parts.append(S("insight_latest_missing", year=LATEST_YEAR))

    recent = [series.get(y, 0) for y in range(LATEST_YEAR - 4, LATEST_YEAR + 1)]
    prior = [series.get(y, 0) for y in range(LATEST_YEAR - 9, LATEST_YEAR - 4)]
    ra, pa = sum(recent) / 5, sum(prior) / 5
    if pa > 0:
        change = (ra - pa) / pa
        if change > 0.15:
            insight_parts.append(S("insight_rising"))
        elif change < -0.15:
            insight_parts.append(S("insight_declining"))
        else:
            insight_parts.append(S("insight_steady"))

    insight = " ".join(insight_parts)

    rows = ""
    for year in years:
        count = series[year]
        rank = rank_by_year_sex.get((year, dom), {}).get(name)
        rank_disp = f"#{fmt(rank)}" if rank else "–"
        rows += (
            f'                <tr><td class="year-column">{year}</td>'
            f'<td class="count-column">{fmt(count)}</td>'
            f'<td class="rank-column">{rank_disp}</td></tr>\n'
        )

    peak_dec = name_meta[name]['peak_dec']
    letter = name[0].upper()
    rel = (
        f'        <p style="margin:0.75rem 0 1.5rem;">'
        f'<a href="{p}/similar/{slugify(name)}.html"><strong>{S("rel_see_similar", name=name)}</strong></a>'
        f' &nbsp;&middot;&nbsp; <a href="{p}/compare.html?a={slugify(name)}">{S("compare_with_link", name=name)}</a>'
        f' &nbsp;&middot;&nbsp; <a href="{p}/decade/{peak_dec}s.html">{S("rel_pop_decade", d=peak_dec)}</a>'
        f' &nbsp;&middot;&nbsp; <a href="{p}/letter/{slug_label(dom)}-{name[0].lower()}.html">'
        f'{S("rel_letter_link", label=label, label_cap=loc_label_cap(dom), letter=letter)}</a></p>\n'
    )
    rel += related_block(S("rel_more_popular", label=label),
                         related_more_popular(name, dom))
    rel += related_block(S("rel_near_in", name=name, year=LATEST_YEAR),
                         related_latest_neighbors(name, dom))
    rel += related_block(S("rel_other_init", label=label, letter=letter),
                         related_same_initial(name, dom))

    canonical = f"{BASE_URL}{p}/name/{slugify(name)}.html"
    chart_id = "trendChart"
    chart_label_text = S("chart_label", name=name, label=label).replace("'", "\\'")
    chart_y_text = S("chart_y_axis")
    chart_js = (
        "\n    <script src=\"https://cdn.jsdelivr.net/npm/chart.js@4\"></script>"
        "\n    <script>"
        "\n    window.addEventListener('DOMContentLoaded', function() {"
        "\n      var ctx = document.getElementById('" + chart_id + "');"
        "\n      new Chart(ctx, {"
        "\n        type: 'line',"
        "\n        data: {"
        "\n          labels: " + json.dumps(years) + ","
        "\n          datasets: [{"
        "\n            label: '" + chart_label_text + "',"
        "\n            data: " + json.dumps(chart_counts) + ","
        "\n            borderColor: '" + ('#149E91' if dom == 'F' else '#FF6B5C') + "',"
        "\n            backgroundColor: '" + ('rgba(20,158,145,0.12)' if dom == 'F' else 'rgba(255,107,92,0.12)') + "',"
        "\n            fill: true, tension: 0.2, pointRadius: 0, borderWidth: 2"
        "\n          }]"
        "\n        },"
        "\n        options: {"
        "\n          responsive: true,"
        "\n          plugins: { legend: { display: true }, tooltip: { mode: 'index', intersect: false } },"
        "\n          scales: { y: { beginAtZero: true, title: { display: true, text: '" + chart_y_text + "' } } }"
        "\n        }"
        "\n      });"
        "\n    });"
        "\n    </script>"
    )
    famous_for_jsonld = filter_famous_for(
        (ENRICHMENT.get(slugify(name), {}) or {}).get('famous', []), name)
    extra_head = breadcrumb_jsonld([
        (S("crumb_home"), home_url()),
        (S("crumb_names"), f"{BASE_URL}{p}/names.html"),
        (name, canonical),
    ]) + person_jsonld_block(famous_for_jsonld, name) + chart_js + hreflang_for_name(slugify(name))

    variants = VARIANTS_OF.get(name, [])
    variants_line = ""
    if variants:
        shown = variants[:10]
        parts = " &middot; ".join(f"{n} ({fmt(t)})" for n, t in shown)
        more = ""
        if len(variants) > 10:
            more = f" + {len(variants) - 10}"
        variants_line = (f'\n        <p style="color:#7f8c8d; font-size:0.9rem; '
                         f'margin:-0.25rem 0 1rem;">{S("name_variants_label")} '
                         f'{parts}{more}</p>')

    heart_svg = ('<svg viewBox="0 0 24 24" aria-hidden="true">'
                 '<path class="heart-empty" d="M12 21s-7-4.5-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 5.5-7 10-7 10z"/>'
                 '<path class="heart-full" d="M12 21s-7-4.5-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 5.5-7 10-7 10z"/>'
                 '</svg>')
    safe_name = name.replace('"', '&quot;')
    fav_btn = (f'<button class="fav-btn" data-slug="{slugify(name)}" data-name="{safe_name}" '
               f'aria-label="{S("fav_add_tip")}" title="{S("fav_add_tip")}">{heart_svg}</button>')

    # Pinterest pin (Phase 14). Top-1000-per-country names get a custom
    # 1000x1500 PNG share card; others fall back to og-default.png.
    pin_slug = slugify(name)
    pin_url = ""
    pin_btn = ""
    if name in top_pin_set:
        pin_rel = f"{PREFIX}/pin/{pin_slug}.png"
        pin_path = OUT_DIR / 'pin' / f'{pin_slug}.png'
        pin_url = f"{BASE_URL}{pin_rel}"
        if not pin_path.exists():
            _render_pin_for(name, pin_path)
        pin_share = (
            f"https://www.pinterest.com/pin/create/button/"
            f"?url={BASE_URL}{p}/name/{pin_slug}.html"
            f"&media={pin_url}"
            f"&description={name}%20%E2%80%94%20{COUNTRY_NAME[ACTIVE_CC].replace(' ', '%20')}%20baby%20name%20popularity%20%26%20trends"
        )
        pin_svg = ('<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
                   '<path d="M12 2C6.477 2 2 6.477 2 12c0 4.237 2.636 7.855 6.356 9.312-.087-.79-.166-2.005.035-2.868.181-.78 1.172-4.971 1.172-4.971s-.299-.6-.299-1.486c0-1.392.806-2.432 1.81-2.432.853 0 1.265.641 1.265 1.41 0 .859-.548 2.143-.83 3.334-.236.997.5 1.811 1.483 1.811 1.78 0 3.149-1.879 3.149-4.59 0-2.4-1.725-4.078-4.19-4.078-2.853 0-4.527 2.14-4.527 4.353 0 .863.332 1.788.748 2.291.082.099.094.186.069.287-.075.314-.243.997-.276 1.137-.043.183-.144.222-.333.134-1.244-.578-2.022-2.397-2.022-3.857 0-3.141 2.283-6.026 6.582-6.026 3.456 0 6.142 2.463 6.142 5.758 0 3.435-2.165 6.198-5.171 6.198-1.009 0-1.959-.524-2.284-1.143l-.621 2.366c-.225.866-.832 1.952-1.238 2.614C9.685 21.875 10.825 22 12 22c5.523 0 10-4.477 10-10S17.523 2 12 2z"/>'
                   '</svg>')
        pin_btn = (f'<a class="pin-btn" href="{pin_share}" target="_blank" rel="noopener" '
                   f'title="{S("pin_share_tip")}">{pin_svg}<span>{S("pin_share_label")}</span></a>')
        # Non-Pinterest fallbacks: native Web Share API (mobile → save to Photos,
        # send via Messages/WhatsApp/AirDrop; desktop falls back to copy-link)
        # and a direct PNG download for "save the card" without any third party.
        share_svg = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                     'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                     '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/>'
                     '<path d="M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/></svg>')
        dl_svg = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                  '<path d="M12 4v12"/><path d="M7 11l5 5 5-5"/>'
                  '<path d="M5 20h14"/></svg>')
        page_url = f"{BASE_URL}{p}/name/{pin_slug}.html"
        pin_btn += (
            f'<button type="button" class="share-btn" data-share-url="{page_url}" '
            f'data-share-img="{pin_url}" data-share-title="{safe_name}" '
            f'data-share-text="{safe_name} — {COUNTRY_NAME[ACTIVE_CC]} baby name popularity &amp; trends" '
            f'data-copied="{S("share_copied")}" '
            f'title="{S("share_btn_tip")}">{share_svg}<span>{S("share_btn_label")}</span></button>'
            f'<a class="download-btn" href="{pin_rel}" download="{pin_slug}.png" '
            f'title="{S("download_btn_tip")}">{dl_svg}<span>{S("download_btn_label")}</span></a>'
        )
        # Telegram one-tap share — works in app on mobile, web client on desktop.
        tg_text = f"{name} — {COUNTRY_NAME[ACTIVE_CC]} baby name popularity & trends"
        tg_share_url = (
            f"https://t.me/share/url?url={page_url}"
            f"&text={tg_text.replace(' ', '%20').replace('&', '%26')}"
        )
        tg_svg = ('<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
                  '<path d="M9.78 18.65l.28-4.23 7.68-6.92c.34-.31-.07-.46-.52-.19L7.74 13.3 3.64 12c-.88-.25-.89-.86.2-1.3l15.97-6.16c.73-.33 1.43.18 1.15 1.3l-2.72 12.81c-.19.91-.74 1.13-1.5.71L12.6 16.3l-1.99 1.93c-.23.23-.42.42-.83.42z"/>'
                  '</svg>')
        pin_btn += (
            f'<a class="share-btn tg-btn" href="{tg_share_url}" target="_blank" rel="noopener" '
            f'title="{S("tg_share_tip")}">{tg_svg}<span>{S("tg_share_label")}</span></a>'
        )

    # Origin badge + famous people from the global ENRICHMENT map.
    enrich = ENRICHMENT.get(slugify(name), {})
    origin_badge_html = ''
    origin = enrich.get('origin')
    if origin and origin in ORIGIN_LABELS_EN:
        origin_lbl = origin_label_cap(origin)
        origin_badge_html = (
            f'<a class="origin-badge" href="{p}/origin/{origin}.html">'
            f'{S("name_origin_badge", label=origin_lbl)}</a>'
        )
    famous = filter_famous_for(enrich.get('famous', []), name)
    # Numerology — playful per-name section, three Pythagorean numbers.
    destiny, soul, pers = numerology_numbers(name)
    traits = NUMEROLOGY_TRAITS[ACTIVE_CC]
    def num_card(num: int, label: str, desc: str) -> str:
        t = traits.get(num) or traits.get(_reduce_numerology(num))
        if not t:
            return ''
        trait_name, trait_desc = t
        return (
            '<li class="num-card">'
            f'<span class="num-card-n">{num}</span>'
            f'<span class="num-card-label">{label}</span>'
            f'<span class="num-card-trait">{trait_name}</span>'
            f'<span class="num-card-desc">{trait_desc}</span>'
            f'<span class="num-card-axis">{desc}</span>'
            '</li>'
        )
    numerology_section_html = ''
    if destiny:
        cards = (num_card(destiny, S("numerology_destiny_lbl"), S("numerology_destiny_desc"))
                 + num_card(soul, S("numerology_soul_lbl"), S("numerology_soul_desc"))
                 + num_card(pers, S("numerology_personality_lbl"), S("numerology_personality_desc")))
        numerology_section_html = (
            f'<div class="num-box"><h2>{S("numerology_h2", name=name)}</h2>'
            f'<p>{S("numerology_intro", name=name)}</p>'
            f'<ul class="num-grid">{cards}</ul>'
            f'<p class="num-footer">{S("numerology_footer")}</p></div>'
        )
    meaning_key = 'meaning_fr' if ACTIVE_CC == 'FR' else 'meaning_en'
    meaning_text = enrich.get(meaning_key) or enrich.get('meaning_en') or ''
    meaning_section_html = ''
    if meaning_text:
        safe = (meaning_text.replace('&', '&amp;')
                            .replace('<', '&lt;')
                            .replace('>', '&gt;'))
        meaning_section_html = (
            f'<div class="meaning-box"><h2>{S("name_meaning_h2")}</h2>'
            f'<p>{safe}</p>'
            f'<p class="meaning-source">{S("name_meaning_source")}</p></div>'
        )
    # Fiction appearances — "Also a character in:" block (Phase 6h)
    fiction_section_html = ''
    appearances = FICTION_BY_NAME.get(slugify(name), [])
    if appearances:
        items = []
        for app in appearances:
            url = f"{p}/fiction/{app['slug']}.html"
            items.append('<li>' + S("name_fiction_in",
                                    url=url, title=app['title'],
                                    role=app['role']) + '</li>')
        fiction_section_html = (
            f'<h2>{S("name_fiction_h2")}</h2>'
            f'<ul class="fiction-appears">{"".join(items)}</ul>'
        )

    famous_section_html = ''
    if famous:
        items = []
        for person in famous[:5]:
            occ = person.get('occupation') or ''
            born = person.get('born')
            bits = []
            if occ:
                bits.append(occ)
            if born:
                year_disp = (f"{-born} av. J.-C." if ACTIVE_CC == 'FR' else f"{-born} BC") if born < 0 else born
                bits.append(S("name_famous_born", year=year_disp))
            sub = S("name_famous_occ_sep").join(bits)
            url = person.get('url') or ''
            link = (f'<a href="{url}" target="_blank" rel="noopener nofollow">{person["name"]}</a>'
                    if url else person["name"])
            items.append(
                f'<li class="famous-item"><span class="famous-name">{link}</span>'
                + (f'<span class="famous-sub">{sub}</span>' if sub else '')
                + '</li>'
            )
        famous_section_html = (
            f'<h2>{S("name_famous_h2", name=name)}</h2>'
            f'<ul class="famous-list">{"".join(items)}</ul>'
        )

    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; <a href="{p}/names.html">{S("crumb_names")}</a> &rsaquo; {name}</div>
        <h1>{name}{fav_btn}</h1>
        {('<div class="name-share-row">' + pin_btn + '</div>') if pin_btn else ''}
        <p style="color:#7f8c8d; margin-top:-0.5rem;">{S("name_primarily", singular=loc_singular(dom), of_singular=singular_of)} &middot; {gender_text}</p>{variants_line}
        {origin_badge_html}

        {meaning_section_html}

        <div class="insight">{insight}</div>

        {numerology_section_html}

        <div class="stats">
            <div class="stat"><div class="stat-value">{fmt(total)}</div><div class="stat-label">{S("stat_total")}</div></div>
            <div class="stat"><div class="stat-value">{len(years)}</div><div class="stat-label">{S("stat_years")}</div></div>
            <div class="stat"><div class="stat-value">{fmt(peak)}</div><div class="stat-label">{S("stat_peak")}</div></div>
        </div>

        <h2>{S("name_popularity_h2", label_cap=loc_label_cap(dom))}</h2>
        <div class="chart-wrap"><canvas id="trendChart" height="120"></canvas></div>

        {fiction_section_html}
        {famous_section_html}

{rel}
        <h2>{S("name_yby_h2")}</h2>
        <p style="color:#7f8c8d; font-size:0.9rem;">{S("name_rank_note", label=label)}</p>
        <table>
            <thead><tr>
                <th class="year-column">{S("table_year")}</th>
                <th class="count-column">{S("table_babies")}</th>
                <th class="rank-column">{S("table_rank")}</th>
            </tr></thead>
            <tbody>
{rows}            </tbody>
        </table>"""

    desc = S("name_desc", name=name, total=fmt(total), range=DATA_RANGE,
             year=peak_year, peak=fmt(peak))
    page_kwargs = dict(description=desc, canonical=canonical, extra_head=extra_head)
    if pin_url:
        page_kwargs.update(og_image_url=pin_url, og_image_w=1000, og_image_h=1500)
    (OUT_DIR / 'name' / f'{slugify(name)}.html').write_text(
        page(S("name_title", name=name), body, **page_kwargs),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# A-Z browse index
# ---------------------------------------------------------------------------
def generate_browse_index():
    p = PREFIX
    letters = sorted(by_initial.keys())
    jump = '<div class="azindex">' + "".join(
        f'<a href="#letter-{l}">{l}</a>' for l in letters) + '</div>'
    sections = ""
    for l in letters:
        names = by_initial[l]
        links = "".join(
            f'<a href="{p}/name/{slugify(n)}.html">{n}</a>' for n in names)
        sections += (
            f'        <h2 id="letter-{l}" style="border-bottom:2px solid #EEF2F4; padding-bottom:0.3rem;">{l} '
            f'<span style="font-size:0.6em; color:#7f8c8d;">({len(names)})</span></h2>\n'
            f'        <div class="related">{links}</div>\n')
    girl_letters = "".join(
        f'<a href="{p}/letter/girls-{l.lower()}.html">{l}</a>'
        for l in sorted(letter_names['F'].keys()))
    boy_letters = "".join(
        f'<a href="{p}/letter/boys-{l.lower()}.html">{l}</a>'
        for l in sorted(letter_names['M'].keys()))
    explore = f"""        <div style="background:#fff; padding:1.25rem 1.5rem; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.06); margin-bottom:2rem;">
            <h2 style="margin-top:0;">{S("browse_explore_h2")}</h2>
            <p style="margin:0.25rem 0;"><a href="{p}/trends.html"><strong>{S("nav_trends")}</strong></a> &middot;
            <a href="{p}/trends/rising.html">{S("browse_rising_link")}</a> &middot;
            <a href="{p}/trends/falling.html">{S("browse_falling_link")}</a> &middot;
            <a href="{p}/decades.html"><strong>{S("browse_decades_link")}</strong></a></p>
            <p style="margin:0.75rem 0 0.25rem;"><strong>{S("browse_girls_by_letter")}</strong></p>
            <div class="azindex">{girl_letters}</div>
            <p style="margin:0.5rem 0 0.25rem;"><strong>{S("browse_boys_by_letter")}</strong></p>
            <div class="azindex">{boy_letters}</div>
        </div>"""
    n_pages = len(pages_to_generate)
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("crumb_browse")}</div>
        <h1>{S("browse_h1")}</h1>
        <p>{S("browse_intro", n=fmt(n_pages), url=f"{p}/rare-names.html")}</p>
{explore}
        <h2>{S("browse_all_h2")}</h2>
        {jump}
{sections}"""
    desc = S("browse_desc", n=fmt(n_pages), range=DATA_RANGE)
    (OUT_DIR / 'names.html').write_text(
        page(S("browse_title"), body,
             description=desc, canonical=f"{BASE_URL}{p}/names.html",
             extra_head=hreflang_for_hub("names.html")),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Year page
# ---------------------------------------------------------------------------
def generate_year_page(year):
    p = PREFIX
    def table_for(sex):
        ranked = sorted(rank_by_year_sex.get((year, sex), {}).items(), key=lambda x: x[1])[:50]
        rows = ""
        for name, rank in ranked:
            c = counts[name][sex][year]
            link = (f'<a href="{p}/name/{slugify(name)}.html">{name}</a>'
                    if name in HAS_PAGE else name)
            rows += (
                f'                <tr><td class="rank-column">{rank}</td>'
                f'<td>{link}</td>'
                f'<td class="count-column">{fmt(c)}</td></tr>\n'
            )
        return rows

    prev_link = f'<a href="{p}/year/{year-1}.html">← {year-1}</a>' if (year - 1) in YEARS_SET else ''
    next_link = f'<a href="{p}/year/{year+1}.html">{year+1} →</a>' if (year + 1) in YEARS_SET else ''
    _girls_rank = rank_by_year_sex.get((year, 'F'), {})
    _boys_rank = rank_by_year_sex.get((year, 'M'), {})
    top_girl = sorted(_girls_rank.items(), key=lambda x: x[1])[0][0] if _girls_rank else '—'
    top_boy = sorted(_boys_rank.items(), key=lambda x: x[1])[0][0] if _boys_rank else '—'
    yir_callout = ''
    if year == LATEST_YEAR and (year - 1) in YEARS_SET:
        yir_callout = (f'\n        <p style="margin:0.25rem 0 1.25rem;"><a href="{p}/year-in-review-{year}.html" '
                       f'style="color:#149E91; font-weight:600;">{S("yir_link", year=year)}</a></p>')
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {year}</div>
        <nav class="nav">{prev_link} &nbsp; {next_link}</nav>
        <h1>{S("year_h1", year=year)}</h1>
        <p>{S("year_intro", year=year, g=top_girl, b=top_boy, source=data_source_label())}</p>{yir_callout}
        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:2rem;">
            <div>
                <h2 style="color:#149E91;">{S("year_girls_h2")}</h2>
                <table><thead><tr><th class="rank-column">{S("table_num")}</th><th>{S("table_name")}</th><th class="count-column">{S("table_babies")}</th></tr></thead>
                <tbody>
{table_for('F')}                </tbody></table>
            </div>
            <div>
                <h2 style="color:#FF6B5C;">{S("year_boys_h2")}</h2>
                <table><thead><tr><th class="rank-column">{S("table_num")}</th><th>{S("table_name")}</th><th class="count-column">{S("table_babies")}</th></tr></thead>
                <tbody>
{table_for('M')}                </tbody></table>
            </div>
        </div>"""
    desc = S("year_desc", year=year, g=top_girl, b=top_boy, source=data_source_label())
    canonical = f"{BASE_URL}{p}/year/{year}.html"
    top_items: list[tuple[str, str]] = []
    for sex in ('F', 'M'):
        ranked = sorted(rank_by_year_sex.get((year, sex), {}).items(), key=lambda x: x[1])[:25]
        for n, _ in ranked:
            if n in HAS_PAGE:
                top_items.append((n, f"{BASE_URL}{p}/name/{slugify(n)}.html"))
    extra_head = breadcrumb_jsonld([
        (S("crumb_home"), home_url()),
        (str(year), canonical),
    ]) + itemlist_jsonld(top_items, f"Top baby names in {year}") + hreflang_for_year(year)
    (OUT_DIR / 'year' / f'{year}.html').write_text(
        page(S("year_title", year=year), body,
             description=desc, canonical=canonical, extra_head=extra_head),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Year-in-review (editorial recap of LATEST_YEAR per country)
# ---------------------------------------------------------------------------
def _first_year_for(name: str, sex: str) -> int | None:
    series = counts.get(name, {}).get(sex, {})
    return min(series) if series else None


def generate_year_in_review_page(year: int) -> None:
    """Annual recap page — risers, fallers, debuts, top-100 turnover.
    Skipped if year-1 data isn't available for this country."""
    prev = year - 1
    if prev not in YEARS_SET:
        return
    p = PREFIX

    def link(n: str) -> str:
        if n in HAS_PAGE:
            return f'<a href="{p}/name/{slugify(n)}.html">{n}</a>'
        return n

    # ----- compute sections -----
    risers_by_sex: dict[str, list] = {'F': [], 'M': []}
    fallers_by_sex: dict[str, list] = {'F': [], 'M': []}
    debut_by_sex: dict[str, list] = {'F': [], 'M': []}
    newcomers_by_sex: dict[str, list] = {'F': [], 'M': []}
    exits_by_sex: dict[str, list] = {'F': [], 'M': []}

    for sex in ('F', 'M'):
        ranks_now = rank_by_year_sex.get((year, sex), {})
        ranks_prev = rank_by_year_sex.get((prev, sex), {})

        movers: list[tuple[int, str, int, int, int]] = []
        for n, r in ranks_now.items():
            pr = ranks_prev.get(n)
            if pr is None:
                continue
            delta = pr - r  # positive = rank improved
            cnow = counts[n][sex].get(year, 0)
            movers.append((delta, n, r, pr, cnow))
        movers.sort(key=lambda t: (-t[0], t[2]))
        # Only consider names with a current-year count of at least 25 to
        # avoid headline movers driven by 5→4 noise.
        risers_by_sex[sex] = [m for m in movers if m[0] > 0 and m[4] >= 25][:10]
        fallers_by_sex[sex] = sorted([m for m in movers if m[0] < 0 and m[4] >= 25],
                                      key=lambda t: (t[0], t[2]))[:10]

        # Debut: first-ever appearance for this name+sex IS this year
        for n in ranks_now:
            if _first_year_for(n, sex) == year and year != min(YEARS):
                cnow = counts[n][sex].get(year, 0)
                debut_by_sex[sex].append((cnow, n, ranks_now[n]))
        debut_by_sex[sex].sort(key=lambda t: (-t[0], t[1]))
        debut_by_sex[sex] = debut_by_sex[sex][:8]

        # Newcomer to top 100: rank ≤ 100 now, was > 100 (or unranked) last year
        for n, r in ranks_now.items():
            if r > 100:
                continue
            pr = ranks_prev.get(n)
            if pr is None or pr > 100:
                newcomers_by_sex[sex].append((r, n, pr, counts[n][sex].get(year, 0)))
        newcomers_by_sex[sex].sort(key=lambda t: t[0])
        newcomers_by_sex[sex] = newcomers_by_sex[sex][:10]

        # Exits from top 100: was in top 100 prev year, not now
        for n, pr in ranks_prev.items():
            if pr > 100:
                continue
            r = ranks_now.get(n)
            if r is None or r > 100:
                exits_by_sex[sex].append((pr, n, r, counts[n][sex].get(prev, 0)))
        exits_by_sex[sex].sort(key=lambda t: t[0])
        exits_by_sex[sex] = exits_by_sex[sex][:10]

    _girls_rank = rank_by_year_sex.get((year, 'F'), {})
    _boys_rank = rank_by_year_sex.get((year, 'M'), {})
    top_girl = sorted(_girls_rank.items(), key=lambda x: x[1])[0][0] if _girls_rank else '—'
    top_boy = sorted(_boys_rank.items(), key=lambda x: x[1])[0][0] if _boys_rank else '—'

    # ----- render -----
    def card(rank_meta: str, name: str, count: int | None) -> str:
        cnt = f'<span class="yir-card-count">{S("yir_count_year", n=fmt(count))}</span>' if count else ''
        return ('<li class="yir-card">'
                f'<span class="yir-card-name">{link(name)}</span>'
                f'<span class="yir-card-meta">{rank_meta}</span>{cnt}</li>')

    def section_movers(label_F: str, label_M: str, rows_F: list, rows_M: list, drop: bool = False) -> str:
        def col(label: str, rows: list) -> str:
            if not rows:
                return ''
            items = []
            for delta, n, r, pr, cnow in rows:
                if drop:
                    meta = S("yir_rank_drop", prev_rank=pr, rank=r, delta=abs(delta))
                else:
                    meta = S("yir_rank_change", prev_rank=pr, rank=r, delta=delta)
                items.append(card(meta, n, cnow))
            return f'<div><h3>{label}</h3><ul class="yir-list">{"".join(items)}</ul></div>'
        return f'<div class="yir-grid">{col(label_F, rows_F)}{col(label_M, rows_M)}</div>'

    def section_debut(rows_F: list, rows_M: list) -> str:
        def col(label: str, rows: list) -> str:
            if not rows:
                return ''
            items = []
            for cnow, n, r in rows:
                meta = S("yir_rank_new", rank=r)
                items.append(card(meta, n, cnow))
            return f'<div><h3>{label}</h3><ul class="yir-list">{"".join(items)}</ul></div>'
        return f'<div class="yir-grid">{col(loc_label_cap("F"), rows_F)}{col(loc_label_cap("M"), rows_M)}</div>'

    def section_newcomers(rows_F: list, rows_M: list) -> str:
        def col(label: str, rows: list) -> str:
            if not rows:
                return ''
            items = []
            for r, n, pr, cnow in rows:
                meta = (S("yir_rank_change", prev_rank=pr, rank=r, delta=pr - r)
                        if pr else S("yir_rank_new", rank=r))
                items.append(card(meta, n, cnow))
            return f'<div><h3>{label}</h3><ul class="yir-list">{"".join(items)}</ul></div>'
        return f'<div class="yir-grid">{col(loc_label_cap("F"), rows_F)}{col(loc_label_cap("M"), rows_M)}</div>'

    def section_exits(rows_F: list, rows_M: list) -> str:
        def col(label: str, rows: list) -> str:
            if not rows:
                return ''
            items = []
            for pr, n, r, cprev in rows:
                meta = (S("yir_rank_drop", prev_rank=pr, rank=r, delta=r - pr)
                        if r else S("yir_rank_exit", prev_rank=pr))
                items.append(card(meta, n, cprev))
            return f'<div><h3>{label}</h3><ul class="yir-list">{"".join(items)}</ul></div>'
        return f'<div class="yir-grid">{col(loc_label_cap("F"), rows_F)}{col(loc_label_cap("M"), rows_M)}</div>'

    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("crumb_yir", year=year)}</div>
        <h1>{S("yir_h1", year=year)}</h1>
        <p>{S("yir_intro", year=year)}</p>

        <div class="insight"><h2 style="margin-top:0;">{S("yir_top_h2")}</h2>
        <p style="margin:0;">{S("yir_top_lead", g=link(top_girl), b=link(top_boy), year=year)}</p></div>

        <h2>{S("yir_risers_h2")}</h2>
        <p>{S("yir_risers_lead")}</p>
        {section_movers(loc_label_cap('F'), loc_label_cap('M'), risers_by_sex['F'], risers_by_sex['M'])}

        <h2>{S("yir_fallers_h2")}</h2>
        <p>{S("yir_fallers_lead")}</p>
        {section_movers(loc_label_cap('F'), loc_label_cap('M'), fallers_by_sex['F'], fallers_by_sex['M'], drop=True)}

        <h2>{S("yir_debut_h2")}</h2>
        <p>{S("yir_debut_lead")}</p>
        {section_debut(debut_by_sex['F'], debut_by_sex['M'])}

        <h2>{S("yir_newcomers_h2")}</h2>
        <p>{S("yir_newcomers_lead", prev=prev)}</p>
        {section_newcomers(newcomers_by_sex['F'], newcomers_by_sex['M'])}

        <h2>{S("yir_exits_h2")}</h2>
        <p>{S("yir_exits_lead", prev=prev)}</p>
        {section_exits(exits_by_sex['F'], exits_by_sex['M'])}"""

    canonical = f"{BASE_URL}{p}/year-in-review-{year}.html"
    desc = S("yir_desc", year=year, g=top_girl, b=top_boy)
    extra_head = breadcrumb_jsonld([
        (S("crumb_home"), home_url()),
        (S("crumb_yir", year=year), canonical),
    ])
    (OUT_DIR / f'year-in-review-{year}.html').write_text(
        page(S("yir_title", year=year), body,
             description=desc, canonical=canonical, extra_head=extra_head),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Comparison page (US only)
# ---------------------------------------------------------------------------
def generate_comparison_page(name1, name2):
    p = PREFIX
    def column(name):
        dom = dominant_sex(name)
        series = counts[name][dom]
        rows = ""
        for year in sorted(series.keys()):
            rows += (
                f'                        <tr><td class="year-column">{year}</td>'
                f'<td class="count-column">{series[year]:,}</td></tr>\n'
            )
        return dom, rows

    dom1, rows1 = column(name1)
    dom2, rows2 = column(name2)
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">Home</a> &rsaquo; Compare</div>
        <h1>{name1} vs {name2}</h1>
        <p>Side-by-side popularity comparison of <strong>{name1}</strong> and
        <strong>{name2}</strong> year by year ({DATA_RANGE}).</p>
        <div style="display:flex; gap:2rem; flex-wrap:wrap;">
            <div style="flex:1; min-width:280px;">
                <h2 style="color:#149E91;"><a href="{p}/name/{slugify(name1)}.html">{name1}</a> <span style="font-size:0.7em; color:#7f8c8d;">({sex_label(dom1)})</span></h2>
                <table><thead><tr><th class="year-column">Year</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{rows1}                </tbody></table>
            </div>
            <div style="flex:1; min-width:280px;">
                <h2 style="color:#149E91;"><a href="{p}/name/{slugify(name2)}.html">{name2}</a> <span style="font-size:0.7em; color:#7f8c8d;">({sex_label(dom2)})</span></h2>
                <table><thead><tr><th class="year-column">Year</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{rows2}                </tbody></table>
            </div>
        </div>"""
    desc = (f"{name1} vs {name2}: compare name popularity year by year using {data_source_label()} "
            f"data from {DATA_RANGE}.")
    fname = f'{slugify(name1)}-vs-{slugify(name2)}.html'
    (OUT_DIR / 'compare' / fname).write_text(
        page(f"{name1} vs {name2} — Baby Name Comparison", body,
             description=desc, canonical=f"{BASE_URL}{p}/compare/{fname}"),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Similar names page
# ---------------------------------------------------------------------------
def generate_similar_page(name):
    p = PREFIX
    dom = dominant_sex(name)
    label = loc_label(dom)
    label_cap = loc_label_cap(dom)
    sims = similar_names(name)
    cards = ""
    for n in sims:
        lr = name_meta[n]['latest_rank']
        if lr:
            rank_txt = S("rank_in_year", rank=fmt(lr), year=LATEST_YEAR) if "rank_in_year" in STRINGS[ACTIVE_CC] else (f"n°{fmt(lr)} en {LATEST_YEAR}" if ACTIVE_CC == "FR" else f"#{fmt(lr)} in {LATEST_YEAR}")
        else:
            rank_txt = "rare aujourd'hui" if ACTIVE_CC == "FR" else "rare today"
        total_txt = f"{fmt(name_total[n])} au total" if ACTIVE_CC == "FR" else f"{fmt(name_total[n])} total"
        cards += (
            f'            <li><a href="{p}/name/{slugify(n)}.html"><h3 style="margin:0;">{n}</h3></a>'
            f'<p style="margin:0.2rem 0; color:#7f8c8d; font-size:0.85rem;">{total_txt} &middot; {rank_txt}</p></li>\n'
        )
    canonical = f"{BASE_URL}{p}/similar/{slugify(name)}.html"
    similar_h1 = f"Prénoms similaires à {name}" if ACTIVE_CC == "FR" else f"Names Similar to {name}"
    similar_title = (f"Prénoms similaires à {name} — idées de prénoms {label}"
                     if ACTIVE_CC == "FR"
                     else f"Names Similar to {name} — {label_cap}' Name Ideas")
    if ACTIVE_CC == "FR":
        similar_intro = (f"Si vous aimez <a href=\"{p}/name/{slugify(name)}.html\"><strong>{name}</strong></a>, voici {len(sims)} "
                         f"prénoms {label} au son, à la longueur ou à l'époque de popularité similaires — classés par proximité. "
                         f"Période couverte : {DATA_RANGE}.")
        similar_desc = (f"{len(sims)} prénoms similaires à {name} — prénoms {label} comparables par sonorité, "
                        f"longueur et popularité. Voir la popularité de chacun.")
    else:
        similar_intro = (f"If you like <a href=\"{p}/name/{slugify(name)}.html\"><strong>{name}</strong></a>, here are {len(sims)} "
                         f"{label}' names with a similar sound, length, or popularity era — ranked by how close they are. "
                         f"Data range: {DATA_RANGE}.")
        similar_desc = (f"{len(sims)} names similar to {name} — comparable {label}' names by sound, length and "
                        f"popularity. See popularity for each.")
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; <a href="{p}/name/{slugify(name)}.html">{name}</a> &rsaquo; {S("crumb_similar")}</div>
        <h1>{similar_h1}</h1>
        <p>{similar_intro}</p>
        <ul class="trending-list" style="display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:1rem; list-style:none; padding:0;">
{cards}        </ul>"""
    extra_head = breadcrumb_jsonld([
        (S("crumb_home"), home_url()),
        (name, f"{BASE_URL}{p}/name/{slugify(name)}.html"),
        (S("crumb_similar"), canonical),
    ]) + hreflang_for_similar(slugify(name))
    (OUT_DIR / 'similar' / f'{slugify(name)}.html').write_text(
        page(similar_title, body,
             description=similar_desc, canonical=canonical, extra_head=extra_head),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Decade pages
# ---------------------------------------------------------------------------
def generate_decade_page(decade):
    p = PREFIX
    label = f"{decade}s"
    yrs = [y for y in range(decade, decade + 10) if y in YEARS_SET]
    span = f"{yrs[0]}–{yrs[-1]}" if yrs else label

    def table_for(sex):
        items = sorted(decade_sex_counts[(decade, sex)].items(), key=lambda x: (-x[1], x[0]))[:50]
        rows = ""
        for i, (name, tot) in enumerate(items):
            link = (f'<a href="{p}/name/{slugify(name)}.html">{name}</a>'
                    if name in HAS_PAGE else name)
            rows += (f'                <tr><td class="rank-column">{i+1}</td>'
                     f'<td>{link}</td><td class="count-column">{fmt(tot)}</td></tr>\n')
        top = items[0][0] if items else ''
        return rows, top

    grows, gtop = table_for('F')
    brows, btop = table_for('M')
    idx = DECADES.index(decade)
    prev_link = f'<a href="{p}/decade/{DECADES[idx-1]}s.html">← {DECADES[idx-1]}s</a>' if idx > 0 else ''
    next_link = f'<a href="{p}/decade/{DECADES[idx+1]}s.html">{DECADES[idx+1]}s →</a>' if idx < len(DECADES) - 1 else ''
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; <a href="{p}/decades.html">{S("crumb_decades")}</a> &rsaquo; {label}</div>
        <nav class="nav">{prev_link} &nbsp; {next_link}</nav>
        <h1>{S("decade_h1", label=label)}</h1>
        <p>{S("decade_intro", label=label, span=span, g=gtop, b=btop)}</p>
        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:2rem;">
            <div>
                <h2 style="color:#149E91;">{S("decade_girls_h2")}</h2>
                <table><thead><tr><th class="rank-column">{S("table_num")}</th><th>{S("table_name")}</th><th class="count-column">{S("table_babies")}</th></tr></thead>
                <tbody>
{grows}                </tbody></table>
            </div>
            <div>
                <h2 style="color:#FF6B5C;">{S("decade_boys_h2")}</h2>
                <table><thead><tr><th class="rank-column">{S("table_num")}</th><th>{S("table_name")}</th><th class="count-column">{S("table_babies")}</th></tr></thead>
                <tbody>
{brows}                </tbody></table>
            </div>
        </div>"""
    desc = S("decade_desc", label=label, span=span, source=data_source_label(),
             g=gtop, b=btop)
    canonical = f"{BASE_URL}{p}/decade/{decade}s.html"
    top_items: list[tuple[str, str]] = []
    for sex in ('F', 'M'):
        ranked = sorted(decade_sex_counts[(decade, sex)].items(),
                        key=lambda x: (-x[1], x[0]))[:25]
        for n, _ in ranked:
            if n in HAS_PAGE:
                top_items.append((n, f"{BASE_URL}{p}/name/{slugify(n)}.html"))
    extra_head = breadcrumb_jsonld([
        (S("crumb_home"), home_url()),
        (S("crumb_decades"), f"{BASE_URL}{p}/decades.html"),
        (label, canonical),
    ]) + itemlist_jsonld(top_items, f"Top baby names of the {label}") + hreflang_for_decade(decade)
    (OUT_DIR / 'decade' / f'{decade}s.html').write_text(
        page(S("decade_title", label=label), body,
             description=desc, canonical=canonical, extra_head=extra_head),
        encoding='utf-8')


def generate_decades_hub():
    p = PREFIX
    links = ""
    for d in reversed(DECADES):
        gtop = sorted(decade_sex_counts[(d, 'F')].items(), key=lambda x: (-x[1], x[0]))
        btop = sorted(decade_sex_counts[(d, 'M')].items(), key=lambda x: (-x[1], x[0]))
        g = gtop[0][0] if gtop else '—'
        b = btop[0][0] if btop else '—'
        links += (f'                <tr><td><a href="{p}/decade/{d}s.html"><strong>{d}s</strong></a></td>'
                  f'<td>{g}</td><td>{b}</td></tr>\n')
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("crumb_decades")}</div>
        <h1>{S("decades_h1")}</h1>
        <p>{S("decades_intro", first=DECADES[0], last=DECADES[-1], source=data_source_label())}</p>
        <table>
            <thead><tr><th>{S("decades_th_decade")}</th><th>{S("decades_th_g")}</th><th>{S("decades_th_b")}</th></tr></thead>
            <tbody>
{links}            </tbody>
        </table>"""
    desc = S("decades_desc", first=DECADES[0], last=DECADES[-1], source=data_source_label())
    (OUT_DIR / 'decades.html').write_text(
        page(S("decades_title"), body,
             description=desc, canonical=f"{BASE_URL}{p}/decades.html",
             extra_head=hreflang_for_hub("decades.html")),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Trend pages
# ---------------------------------------------------------------------------
def _trend_windows(name):
    dom = name_meta[name]['dom']
    series = counts[name][dom]
    recent = sum(series.get(y, 0) for y in range(LATEST_YEAR - 2, LATEST_YEAR + 1)) / 3.0
    older = sum(series.get(y, 0) for y in range(LATEST_YEAR - 7, LATEST_YEAR - 4)) / 3.0
    return recent, older


def _trend_table(rows_data, sex):
    p = PREFIX
    rows = ""
    for name, recent, older, pct in rows_data:
        arrow = "▲" if pct >= 0 else "▼"
        color = "#27ae60" if pct >= 0 else "#c0392b"
        rows += (f'                <tr><td><a href="{p}/name/{slugify(name)}.html">{name}</a></td>'
                 f'<td class="count-column">{fmt(round(older))}</td>'
                 f'<td class="count-column">{fmt(round(recent))}</td>'
                 f'<td class="count-column" style="color:{color}; white-space:nowrap;">{arrow}&nbsp;{abs(pct):.0f}%</td></tr>\n')
    return rows


def generate_trends_pages():
    p = PREFIX
    rising = {'F': [], 'M': []}
    falling = {'F': [], 'M': []}
    for name in pages_to_generate:
        sex = name_meta[name]['dom']
        recent, older = _trend_windows(name)
        if older >= 10 and recent >= 150 and recent > older:
            pct = 100 * (recent - older) / older
            rising[sex].append((name, recent, older, pct))
        if older >= 200 and recent < older:
            pct = 100 * (recent - older) / older
            falling[sex].append((name, recent, older, pct))
    for sex in ('F', 'M'):
        rising[sex].sort(key=lambda r: -r[3])
        falling[sex].sort(key=lambda r: r[3])

    head = (f'<thead><tr><th>{S("table_name")}</th><th class="count-column">{S("trends_th_older")}</th>'
            f'<th class="count-column">{LATEST_YEAR}</th><th class="count-column">{S("trends_th_change")}</th></tr></thead>')

    def two_col(data, n=30):
        return f"""        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:2rem;">
            <div><h2 style="color:#149E91;">{S("year_girls_h2")}</h2><table>{head}<tbody>
{_trend_table(data['F'][:n], 'F')}            </tbody></table></div>
            <div><h2 style="color:#FF6B5C;">{S("year_boys_h2")}</h2><table>{head}<tbody>
{_trend_table(data['M'][:n], 'M')}            </tbody></table></div>
        </div>"""

    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; <a href="{p}/trends.html">{S("crumb_trends")}</a> &rsaquo; {S("crumb_rising")}</div>
        <h1>{S("trends_rising_h1", year=LATEST_YEAR)}</h1>
        <p>{S("trends_rising_intro")}</p>
{two_col(rising)}"""
    (OUT_DIR / 'trends' / 'rising.html').write_text(
        page(S("trends_rising_title", year=LATEST_YEAR), body,
             description=S("trends_rising_desc", year=LATEST_YEAR, source=data_source_label()),
             canonical=f"{BASE_URL}{p}/trends/rising.html",
             extra_head=breadcrumb_jsonld([(S("crumb_home"), home_url()),
                                           (S("crumb_trends"), f"{BASE_URL}{p}/trends.html"),
                                           (S("crumb_rising"), f"{BASE_URL}{p}/trends/rising.html")])
                        + hreflang_for_hub("trends/rising.html")),
        encoding='utf-8')

    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; <a href="{p}/trends.html">{S("crumb_trends")}</a> &rsaquo; {S("crumb_falling")}</div>
        <h1>{S("trends_falling_h1", year=LATEST_YEAR)}</h1>
        <p>{S("trends_falling_intro")}</p>
{two_col(falling)}"""
    (OUT_DIR / 'trends' / 'falling.html').write_text(
        page(S("trends_falling_title", year=LATEST_YEAR), body,
             description=S("trends_falling_desc", year=LATEST_YEAR, source=data_source_label()),
             canonical=f"{BASE_URL}{p}/trends/falling.html",
             extra_head=breadcrumb_jsonld([(S("crumb_home"), home_url()),
                                           (S("crumb_trends"), f"{BASE_URL}{p}/trends.html"),
                                           (S("crumb_falling"), f"{BASE_URL}{p}/trends/falling.html")])
                        + hreflang_for_hub("trends/falling.html")),
        encoding='utf-8')


def generate_trends_hub():
    p = PREFIX
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("crumb_trends")}</div>
        <h1>{S("trends_h1")}</h1>
        <p>{S("trends_intro", range=DATA_RANGE)}</p>
        <div class="stats">
            <a class="stat" style="text-decoration:none;" href="{p}/trends/rising.html">
                <div class="stat-value" style="color:#27ae60;">▲</div>
                <div class="stat-label"><strong>{S("trends_card_rising")}</strong><br>{S("trends_card_rising_sub", year=LATEST_YEAR)}</div></a>
            <a class="stat" style="text-decoration:none;" href="{p}/trends/falling.html">
                <div class="stat-value" style="color:#c0392b;">▼</div>
                <div class="stat-label"><strong>{S("trends_card_falling")}</strong><br>{S("trends_card_falling_sub", year=LATEST_YEAR)}</div></a>
            <a class="stat" style="text-decoration:none;" href="{p}/decades.html">
                <div class="stat-value" style="color:#149E91;">★</div>
                <div class="stat-label"><strong>{S("trends_card_decades")}</strong><br>{S("trends_card_decades_sub")}</div></a>
        </div>"""
    (OUT_DIR / 'trends.html').write_text(
        page(S("trends_title"), body,
             description=S("trends_desc", year=LATEST_YEAR, source=data_source_label()),
             canonical=f"{BASE_URL}{p}/trends.html",
             extra_head=hreflang_for_hub("trends.html")),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Letter pages
# ---------------------------------------------------------------------------
def generate_letter_page(sex, letter):
    p = PREFIX
    label = loc_label(sex)
    label_cap = loc_label_cap(sex)
    url_slug = slug_label(sex)
    names = letter_names[sex].get(letter, [])
    rows = ""
    for i, name in enumerate(names):
        lr = rank_by_year_sex.get((LATEST_YEAR, sex), {}).get(name)
        lr_disp = f"#{fmt(lr)}" if lr else "–"
        rows += (f'                <tr><td class="rank-column">{i+1}</td>'
                 f'<td><a href="{p}/name/{slugify(name)}.html">{name}</a></td>'
                 f'<td class="count-column">{fmt(name_sex_total[(name, sex)])}</td>'
                 f'<td class="rank-column">{lr_disp}</td></tr>\n')
    other_sex = 'M' if sex == 'F' else 'F'
    other_label = loc_label(other_sex)
    other_label_cap = loc_label_cap(other_sex)
    other_url_slug = slug_label(other_sex)
    if letter in letter_names[other_sex]:
        cross_link = (f'<a href="{p}/letter/{other_url_slug}-{letter.lower()}.html">'
                      f'{S("letter_cross_link", label=other_label, label_cap=other_label_cap, letter=letter)}</a>')
        cross_q = S("letter_cross_q", link=cross_link)
    else:
        cross_q = ''
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; <a href="{p}/names.html">{S("crumb_names")}</a> &rsaquo; {label_cap} / {letter}</div>
        <h1>{S("letter_h1", label=label, label_cap=label_cap, letter=letter)}</h1>
        <p>{S("letter_intro", n=len(names), label=label, letter=letter, range=DATA_RANGE, cross_q=cross_q)}</p>
        <table>
            <thead><tr><th class="rank-column">{S("table_num")}</th><th>{S("table_name")}</th>
                <th class="count-column">{S("table_total")}</th><th class="rank-column">{S("table_year_rank", year=LATEST_YEAR)}</th></tr></thead>
            <tbody>
{rows}            </tbody>
        </table>"""
    desc = S("letter_desc", label=label, label_cap=label_cap, letter=letter,
             n=len(names), source=data_source_label())
    canonical = f"{BASE_URL}{p}/letter/{url_slug}-{letter.lower()}.html"
    extra_head = breadcrumb_jsonld([
        (S("crumb_home"), home_url()),
        (S("crumb_names"), f"{BASE_URL}{p}/names.html"),
        (f"{label_cap} {letter}", canonical),
    ]) + hreflang_for_letter(sex, letter)
    (OUT_DIR / 'letter' / f'{url_slug}-{letter.lower()}.html').write_text(
        page(S("letter_title", label=label, label_cap=label_cap, letter=letter), body,
             description=desc, canonical=canonical, extra_head=extra_head),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Rare-names page + name-index JSON
# ---------------------------------------------------------------------------
def generate_rare_names_page():
    p = PREFIX
    rare = [n for n in name_total if n not in HAS_PAGE]
    rare.sort(key=lambda n: n.lower())
    by_letter = {}
    for n in rare:
        ch = n[0].upper() if n and n[0].isalpha() else '#'
        by_letter.setdefault(ch, []).append(n)
    letters = sorted(by_letter.keys())
    jump = '<div class="azindex">' + ''.join(
        f'<a href="#letter-{l}">{l}</a>' for l in letters) + '</div>'
    sections = []
    for l in letters:
        items = []
        for n in by_letter[l]:
            dom = dominant_sex(n)
            canon = CANONICAL_OF.get(n)
            if canon:
                variant_hint = (f' <a href="{p}/name/{slugify(canon)}.html" '
                                f'style="color:#149E91; font-size:0.85em;">'
                                f'{S("rare_variant_of", canonical=canon)}</a>')
            else:
                variant_hint = ""
            items.append(
                f'<li id="n-{slugify(n)}" style="break-inside:avoid;">{n} '
                f'<span style="color:#5B6678; font-size:0.85em;">'
                f'{S("rare_mostly", total=fmt(name_total[n]), label=loc_label(dom))}</span>'
                f'{variant_hint}</li>')
        sections.append(
            f'<details id="letter-{l}" style="margin:1rem 0; background:#fff; '
            f'border:1px solid #EEF2F4; border-radius:8px; padding:1rem 1.25rem;">'
            f'<summary style="cursor:pointer; font-family:\'Poppins\',sans-serif; '
            f'font-weight:600; color:#1B2440;">{l} '
            f'<span style="color:#5B6678; font-weight:400;">{S("rare_letter_count", n=fmt(len(by_letter[l])))}</span>'
            f'</summary>'
            f'<ul style="columns:3; column-gap:1.5rem; list-style:none; padding:0; margin:1rem 0 0; font-size:0.92rem;">'
            + ''.join(items) + '</ul></details>')
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; <a href="{p}/names.html">{S("crumb_browse")}</a> &rsaquo; {S("crumb_rare")}</div>
        <h1>{S("rare_h1")}</h1>
        <p>{S("rare_intro", n=fmt(len(rare)), range=DATA_RANGE, min=fmt(PAGE_MIN_TOTAL))}</p>
        <p style="color:#5B6678; font-size:0.9rem;">{S("rare_tip")}</p>
        {jump}
{''.join(sections)}
        <script>
        (function() {{
            var q = new URLSearchParams(window.location.search).get('q');
            if (!q) return;
            var letter = q[0].toUpperCase();
            var section = document.getElementById('letter-' + letter);
            if (section) section.open = true;
            setTimeout(function() {{
                var target = document.getElementById('n-' + q);
                if (target) {{
                    target.style.background = '#FFF4D6';
                    target.style.padding = '0.25rem 0.5rem';
                    target.style.borderRadius = '4px';
                    target.scrollIntoView({{behavior:'smooth', block:'center'}});
                }}
            }}, 50);
        }})();
        </script>"""
    desc = S("rare_desc", n=fmt(len(rare)), range=DATA_RANGE, min=fmt(PAGE_MIN_TOTAL))
    (OUT_DIR / 'rare-names.html').write_text(
        page(S("rare_title"), body,
             description=desc, canonical=f"{BASE_URL}{p}/rare-names.html",
             extra_head=hreflang_for_hub("rare-names.html")),
        encoding='utf-8')


def generate_name_data_json(name):
    """Tiny per-name JSON consumed by /compare.html. Keys are short to keep
    files small (~2–3 KB each). One file per page-eligible name."""
    dom = dominant_sex(name)
    series = counts[name][dom]
    years = sorted(series.keys())
    cnts = [series[y] for y in years]
    ranks = []
    for y in years:
        r = rank_by_year_sex.get((y, dom), {}).get(name)
        ranks.append(r if r else None)
    ft = name_sex_total[(name, 'F')]
    mt = name_sex_total[(name, 'M')]
    peak = max(cnts) if cnts else 0
    peak_year = years[cnts.index(peak)] if cnts else None
    latest_rank = name_meta[name]['latest_rank']
    payload = {
        "n": name,
        "d": dom,
        "ft": ft,
        "mt": mt,
        "p": peak,
        "py": peak_year,
        "lr": latest_rank,
        "y": years,
        "c": cnts,
        "r": ranks,
    }
    (OUT_DIR / 'name-data' / f'{slugify(name)}.json').write_text(
        json.dumps(payload, separators=(',', ':')),
        encoding='utf-8')


def generate_compare_page():
    """Empty shell — JS reads ?a= and ?b= and fetches name-data JSON."""
    p = PREFIX
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("nav_compare")}</div>
        <h1>{S("compare_h1")}</h1>
        <p>{S("compare_intro")}</p>
        <form id="cmp-form" class="cmp-form" autocomplete="off">
            <span class="ac-wrap">
                <input type="text" id="cmp-a" placeholder="{S("compare_input_a")}" aria-label="{S("compare_input_a")}">
                <div class="cmp-ac" id="cmp-ac-a" style="display:none;"></div>
            </span>
            <span class="ac-wrap">
                <input type="text" id="cmp-b" placeholder="{S("compare_input_b")}" aria-label="{S("compare_input_b")}">
                <div class="cmp-ac" id="cmp-ac-b" style="display:none;"></div>
            </span>
            <button type="submit">{S("compare_go")}</button>
        </form>
        <div id="cmp-loading" style="display:none;">{S("compare_loading")}</div>
        <div id="cmp-error" class="cmp-error" style="display:none;">{S("compare_not_found")}</div>
        <div id="cmp-result" style="display:none;">
            <div class="cmp-grid">
                <div class="cmp-card" id="cmp-card-a"></div>
                <div class="cmp-card" id="cmp-card-b"></div>
            </div>
            <h2>{S("compare_chart_h2")}</h2>
            <div class="chart-wrap"><canvas id="cmpChart" height="120"></canvas></div>
        </div>"""
    # noindex when query params are present is enforced by JS, but the bare
    # /compare.html landing page is indexable so we don't add noindex here.
    extra_head = (
        '\n    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>'
        + hreflang_for_hub("compare.html")
    )
    (OUT_DIR / 'compare.html').write_text(
        page(S("compare_title"), body,
             description=S("compare_desc"),
             canonical=f"{BASE_URL}{p}/compare.html",
             extra_head=extra_head),
        encoding='utf-8')


def generate_name_index_json():
    # Sort by lifetime popularity so autocomplete surfaces well-known names
    # first ("ol" → Olivia, not Olalla). All tools that read this file iterate
    # in order and break after 8 matches.
    pages = [slugify(n) for n in sorted(pages_to_generate, key=lambda n: (-name_total[n], n))]
    # Hand-written easter-egg pages (e.g. /name/air.html) live outside the
    # threshold gate but still need to be searchable & routable from PAGES.
    if ACTIVE_CC == 'US':
        for extra in ('air',):
            if extra not in pages:
                pages.append(extra)
    ssa = [slugify(n) for n in sorted((n for n in name_total if n not in HAS_PAGE),
                                       key=lambda n: (-name_total[n], n))]
    (OUT_DIR / 'name-index.json').write_text(
        json.dumps({"pages": pages, "ssa": ssa}, separators=(',', ':')),
        encoding='utf-8')


def generate_top_race_json():
    """Top-10 names per year per sex for the homepage animated chart.
    Compact array form keeps the file small (~40 KB raw, ~12 KB gzipped)
    so it can be fetched lazily without blocking initial render.
    Schema:
        {"years": [1880, ..., 2024],
         "M": [[["John", 9655], ["William", 9532], ...10], ...145 years],
         "F": [...same shape]}"""
    race = top_race_by_country.get(ACTIVE_CC)
    if not race:
        return
    years = race['years']
    out = {
        'years': years,
        'M': [race['M'].get(y, []) for y in years],
        'F': [race['F'].get(y, []) for y in years],
    }
    (OUT_DIR / 'top-race.json').write_text(
        json.dumps(out, separators=(',', ':'), ensure_ascii=False),
        encoding='utf-8')


def generate_name_meta_json():
    """Compact per-name metadata feeding works-with-surname (6c) and the
    picker/sibling tools (6e/6f). Array form keeps the file small enough to
    fetch lazily on the client (~150–250 KB per country, gzip ~50 KB).
    Index 6 is the origin slug from ENRICHMENT (empty string when unknown)."""
    out: dict[str, list] = {}
    for n in pages_to_generate:
        m = name_meta[n]
        rec = ENRICHMENT.get(slugify(n)) or {}
        origin = rec.get('origin') or ''
        if origin and origin not in ORIGIN_LABELS_EN:
            origin = ''
        out[slugify(n)] = [m['first'], m['last2'], m['syll'], m['dom'], m['peak_dec'],
                            m['latest_rank'] or 0, origin]
    (OUT_DIR / 'name-meta.json').write_text(
        json.dumps(out, separators=(',', ':')), encoding='utf-8')


def generate_works_with_page():
    """Empty shell — JS reads ?s= and scores names from name-meta.json against
    the surname. Query-param variants get noindex'd by JS."""
    p = PREFIX
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("nav_works_with")}</div>
        <h1>{S("ww_h1")}</h1>
        <p>{S("ww_intro")}</p>
        <div class="how-box">
            <h2>{S("ww_how_h")}</h2>
            <ul>
                <li>{S("ww_how_1")}</li>
                <li>{S("ww_how_2")}</li>
                <li>{S("ww_how_3")}</li>
            </ul>
        </div>
        <form id="ww-form" autocomplete="off">
            <div class="ww-form">
                <input type="text" id="ww-input" placeholder="{S("ww_input")}" aria-label="{S("ww_input")}">
                <button type="submit">{S("ww_go")}</button>
            </div>
        </form>
        <div class="ww-tabs">
            <button type="button" class="ww-tab is-active" data-sex="all">{S("ww_tab_all")}</button>
            <button type="button" class="ww-tab" data-sex="F">{S("ww_tab_girls")}</button>
            <button type="button" class="ww-tab" data-sex="M">{S("ww_tab_boys")}</button>
        </div>
        <div id="ww-loading" style="display:none;">{S("ww_loading")}</div>
        <p id="ww-empty">{S("ww_empty")}</p>
        <div id="ww-result" style="display:none;">
            <h2 id="ww-result-header"></h2>
            <div id="ww-result-list"></div>
            <div class="ww-more-wrap">
                <button type="button" id="ww-more" style="display:none;">{S("ww_show_more")}</button>
            </div>
        </div>"""
    extra_head = hreflang_for_hub("works-with.html")
    (OUT_DIR / 'works-with.html').write_text(
        page(S("ww_title"), body,
             description=S("ww_desc"),
             canonical=f"{BASE_URL}{p}/works-with.html",
             extra_head=extra_head),
        encoding='utf-8')


def generate_picker_page():
    """Swipe / Filter / Random tool. All client-side off name-meta.json.
    ?sex/era/n query params switch to the random tab and pre-fill it (so
    'roll the dice' results are shareable). Query-param variants are
    noindex'd as a fragment of the JS bootstrap."""
    p = PREFIX
    girls = loc_label_cap('F')
    boys = loc_label_cap('M')
    syll_labels = ['1', '2', '3', '4+']
    syll_boxes = ''.join(
        f'<label><input type="checkbox" name="pk-f-syll" class="pk-filter-input" '
        f'value="{i+1}"> {syll_labels[i]}</label>'
        for i in range(4)
    )
    az_options = ''.join(f'<option value="{c}">{c.upper()}</option>'
                         for c in 'abcdefghijklmnopqrstuvwxyz')
    by_origin = collect_origin_names_for_active()
    origin_options = ''.join(
        f'<option value="{o}">{origin_label_cap(o)}</option>'
        for o in sorted(by_origin, key=lambda o: (-len(by_origin[o]), o))
    )

    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("nav_picker")}</div>
        <h1>{S("picker_h1")}</h1>
        <p>{S("picker_intro")}</p>
        <div id="picker-root">
            <div class="pk-tabs" role="tablist">
                <button type="button" class="pk-tab" data-mode="swipe">{S("picker_tab_swipe")}</button>
                <button type="button" class="pk-tab" data-mode="filter">{S("picker_tab_filter")}</button>
                <button type="button" class="pk-tab" data-mode="random">{S("picker_tab_random")}</button>
            </div>

            <div class="pk-panel" data-mode="swipe" style="display:none;">
                <div class="pk-controls">
                    <span>{S("picker_swipe_filter_sex")}:</span>
                    <span class="pk-pill-group">
                        <button type="button" class="pk-pill pk-swipe-sex is-active" data-sex="all">{S("ww_tab_all")}</button>
                        <button type="button" class="pk-pill pk-swipe-sex" data-sex="F">{girls}</button>
                        <button type="button" class="pk-pill pk-swipe-sex" data-sex="M">{boys}</button>
                    </span>
                    <label>{S("picker_swipe_filter_era")}:
                        <select id="pk-swipe-era"><option value="all">{S("picker_filter_any")}</option></select>
                    </label>
                </div>
                <div id="pk-card"></div>
                <p id="pk-status"></p>
                <div class="pk-swipe-buttons">
                    <button type="button" id="pk-skip" class="pk-btn-skip">← {S("picker_swipe_skip")}</button>
                    <button type="button" id="pk-undo" class="pk-btn-undo">{S("picker_swipe_undo")}</button>
                    <button type="button" id="pk-save" class="pk-btn-save">♥ {S("picker_swipe_save")}</button>
                </div>
                <div id="pk-exhausted" style="display:none;">
                    <p>{S("picker_swipe_exhausted")}</p>
                    <button type="button" id="pk-restart" class="pk-btn-restart">{S("picker_swipe_restart")}</button>
                </div>
            </div>

            <div class="pk-panel" data-mode="filter" style="display:none;">
                <div class="pk-controls">
                    <span>{S("picker_filter_sex")}:</span>
                    <label><input type="radio" name="pk-f-sex" class="pk-filter-input" value="all" checked> {S("ww_tab_all")}</label>
                    <label><input type="radio" name="pk-f-sex" class="pk-filter-input" value="F"> {girls}</label>
                    <label><input type="radio" name="pk-f-sex" class="pk-filter-input" value="M"> {boys}</label>
                </div>
                <div class="pk-controls">
                    <span>{S("picker_filter_syll")}:</span>{syll_boxes}
                </div>
                <div class="pk-controls">
                    <label>{S("picker_filter_era")}:
                        <select id="pk-f-era" class="pk-filter-input"><option value="all">{S("picker_filter_any")}</option></select>
                    </label>
                    <label>{S("picker_filter_letter")}:
                        <select id="pk-f-letter" class="pk-filter-input">
                            <option value="all">{S("picker_filter_any")}</option>{az_options}
                        </select>
                    </label>
                    <label>{S("picker_filter_rank")}:
                        <select id="pk-f-rank" class="pk-filter-input">
                            <option value="all">{S("picker_filter_any")}</option>
                            <option value="top100">{S("picker_filter_rank_top100")}</option>
                            <option value="top1000">{S("picker_filter_rank_top1000")}</option>
                            <option value="rare">{S("picker_filter_rank_rare")}</option>
                        </select>
                    </label>
                    {f'''<label>{S("picker_filter_origin")}:
                        <select id="pk-f-origin" class="pk-filter-input">
                            <option value="all">{S("picker_filter_any")}</option>{origin_options}
                        </select>
                    </label>''' if origin_options else ''}
                </div>
                <p id="pk-filter-count"></p>
                <div id="pk-filter-results"></div>
            </div>

            <div class="pk-panel" data-mode="random" style="display:none;">
                <form class="pk-random-form">
                    <span>{S("picker_filter_sex")}:</span>
                    <label><input type="radio" name="pk-r-sex" value="all" checked> {S("ww_tab_all")}</label>
                    <label><input type="radio" name="pk-r-sex" value="F"> {girls}</label>
                    <label><input type="radio" name="pk-r-sex" value="M"> {boys}</label>
                    <label>{S("picker_filter_era")}:
                        <select id="pk-r-era"><option value="all">{S("picker_filter_any")}</option></select>
                    </label>
                    <label>{S("picker_random_count")}:
                        <select id="pk-r-count">
                            <option value="5">5</option>
                            <option value="10" selected>10</option>
                            <option value="20">20</option>
                            <option value="50">50</option>
                        </select>
                    </label>
                    <button type="submit" id="pk-r-go">{S("picker_random_go")}</button>
                </form>
                <div id="pk-random-results"></div>
                <div class="pk-random-actions">
                    <button type="button" id="pk-r-again">{S("picker_random_again")}</button>
                    <button type="button" id="pk-r-share">{S("picker_random_share")}</button>
                    <span id="pk-r-share-done" style="display:none;">{S("picker_random_share_done")}</span>
                </div>
            </div>
        </div>"""
    extra_head = hreflang_for_hub("picker.html")
    (OUT_DIR / 'picker.html').write_text(
        page(S("picker_title"), body,
             description=S("picker_desc"),
             canonical=f"{BASE_URL}{p}/picker.html",
             extra_head=extra_head),
        encoding='utf-8')


def generate_sibling_page():
    """Empty shell — JS reads the typed name (or ?name=), looks it up in
    name-meta.json, and scores candidates on era, syllable rhythm and
    starting letter."""
    p = PREFIX
    girls = loc_label_cap('F')
    boys = loc_label_cap('M')
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("nav_sibling")}</div>
        <h1>{S("sibling_h1")}</h1>
        <p>{S("sibling_intro")}</p>
        <form id="sib-form" autocomplete="off">
            <div class="sib-form">
                <div class="sib-inputs" id="sib-inputs">
                    <div class="sib-row" data-slot="0">
                        <span class="ac-wrap">
                            <input type="text" class="sib-input" placeholder="{S("sibling_input")}" aria-label="{S("sibling_input")}">
                            <div class="sib-ac" style="display:none;"></div>
                        </span>
                        <button type="button" class="sib-remove" aria-label="{S("sibling_remove_name")}" style="display:none;">×</button>
                    </div>
                    <div class="sib-row" data-slot="1" style="display:none;">
                        <span class="ac-wrap">
                            <input type="text" class="sib-input" placeholder="{S("sibling_input_more")}" aria-label="{S("sibling_input_more")}">
                            <div class="sib-ac" style="display:none;"></div>
                        </span>
                        <button type="button" class="sib-remove" aria-label="{S("sibling_remove_name")}">×</button>
                    </div>
                    <div class="sib-row" data-slot="2" style="display:none;">
                        <span class="ac-wrap">
                            <input type="text" class="sib-input" placeholder="{S("sibling_input_more")}" aria-label="{S("sibling_input_more")}">
                            <div class="sib-ac" style="display:none;"></div>
                        </span>
                        <button type="button" class="sib-remove" aria-label="{S("sibling_remove_name")}">×</button>
                    </div>
                </div>
                <div class="sib-form-actions">
                    <button type="button" class="sib-add" id="sib-add">{S("sibling_add_name")}</button>
                    <button type="submit" class="sib-submit">{S("sibling_go")}</button>
                </div>
            </div>
        </form>
        <div class="sib-sex-tabs" role="tablist" aria-label="{S("sibling_target_sex")}">
            <button type="button" class="sib-sex-tab is-active" data-sex="all">{S("ww_tab_all")}</button>
            <button type="button" class="sib-sex-tab" data-sex="F">{girls}</button>
            <button type="button" class="sib-sex-tab" data-sex="M">{boys}</button>
        </div>
        <p id="sib-empty">{S("sibling_empty")}</p>
        <div id="sib-note" style="display:none;">{S("sibling_unknown")}</div>
        <div id="sib-result" style="display:none;">
            <h2 id="sib-header"></h2>
            <div class="sib-share-wrap">
                <button type="button" id="sib-share" class="sib-share-btn">{S("sibling_share")}</button>
                <a id="sib-share-tg" class="sib-share-btn" target="_blank" rel="noopener" href="#">{S("tg_share_label")}</a>
                <span id="sib-share-done" class="sib-share-done" style="display:none;">{S("sibling_share_done")}</span>
            </div>
            <div id="sib-list"></div>
            <div class="sib-more-wrap">
                <button type="button" id="sib-more" style="display:none;">{S("sibling_show_more")}</button>
            </div>
        </div>"""
    extra_head = hreflang_for_hub("sibling.html")
    (OUT_DIR / 'sibling.html').write_text(
        page(S("sibling_title"), body,
             description=S("sibling_desc"),
             canonical=f"{BASE_URL}{p}/sibling.html",
             extra_head=extra_head),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Origins (Phase 6d) — hub + per-origin pages reading data/normalized/
# name_enrichment.json. Skipped silently when no enrichment data exists yet.
# ---------------------------------------------------------------------------
def collect_origin_names_for_active() -> dict[str, list[str]]:
    """For the currently active country: build origin_slug → [names...] using
    the global ENRICHMENT plus pages_to_generate (so we only show names that
    have their own page in this country)."""
    by_origin: dict[str, list[str]] = defaultdict(list)
    if not ENRICHMENT:
        return {}
    for name in pages_to_generate:
        rec = ENRICHMENT.get(slugify(name))
        if not rec:
            continue
        origin = rec.get('origin')
        if origin and origin in ORIGIN_LABELS_EN:
            by_origin[origin].append(name)
    # Sort each list by all-time popularity
    for origin in by_origin:
        by_origin[origin].sort(key=lambda n: (-name_total[n], n))
    return dict(by_origin)


def generate_origins_hub_page(by_origin: dict[str, list[str]]) -> None:
    if not by_origin:
        return
    p = PREFIX
    items = []
    for origin in sorted(by_origin, key=lambda o: -len(by_origin[o])):
        label = origin_label_cap(origin)
        n = len(by_origin[origin])
        items.append(
            f'<a class="origin-card" href="{p}/origin/{origin}.html">'
            f'<span class="origin-card-label">{label}</span>'
            f'<span class="origin-card-count">{S("origins_hub_count", n=fmt(n))}</span>'
            f'</a>'
        )
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("nav_origins")}</div>
        <h1>{S("origins_hub_h1")}</h1>
        <p>{S("origins_hub_intro")}</p>
        <div class="origin-grid">
{''.join(items)}
        </div>"""
    (OUT_DIR / 'origins.html').write_text(
        page(S("origins_hub_title"), body,
             description=S("origins_hub_desc"),
             canonical=f"{BASE_URL}{p}/origins.html",
             extra_head=hreflang_for_hub("origins.html")),
        encoding='utf-8')


def generate_origin_page(origin: str, names: list[str]) -> None:
    p = PREFIX
    label = origin_label(origin)
    label_cap = origin_label_cap(origin)
    country = COUNTRY_NAMES_IN_UI[ACTIVE_CC][ACTIVE_CC]

    girls = [n for n in names if dominant_sex(n) == 'F'][:80]
    boys = [n for n in names if dominant_sex(n) == 'M'][:80]

    def list_section(heading: str, ns: list[str]) -> str:
        if not ns:
            return ''
        items = []
        for i, n in enumerate(ns, 1):
            total = name_total[n]
            items.append(
                f'<tr><td class="rank-column">{i}</td>'
                f'<td><a href="{p}/name/{slugify(n)}.html">{n}</a></td>'
                f'<td class="count-column">{fmt(total)}</td></tr>'
            )
        return (f'<h2>{heading}</h2>'
                f'<table><thead><tr>'
                f'<th>{S("table_num")}</th><th>{S("table_name")}</th>'
                f'<th class="count-column">{S("table_total")}</th>'
                f'</tr></thead><tbody>{"".join(items)}</tbody></table>')

    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; <a href="{p}/origins.html">{S("nav_origins")}</a> &rsaquo; {label_cap}</div>
        <h1>{S("origin_page_h1", label=label_cap)}</h1>
        <p>{S("origin_page_intro", label=label, country=country)}</p>
        {list_section(S("origin_page_girls_h2", label=label_cap), girls)}
        {list_section(S("origin_page_boys_h2", label=label_cap), boys)}
        <p style="margin-top:2rem;"><a href="{p}/origins.html">{S("origin_back_to_hub")}</a></p>"""
    (OUT_DIR / 'origin' / f'{origin}.html').write_text(
        page(S("origin_page_title", label=label_cap), body,
             description=S("origin_page_desc", label=label),
             canonical=f"{BASE_URL}{p}/origin/{origin}.html",
             extra_head=hreflang_for_origin(origin)),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Fiction hub + per-franchise pages (Phase 6h). Fiction data is global; each
# country's pages link to the country's own /name/<slug>.html when available.
# ---------------------------------------------------------------------------
def generate_fiction_hub_page() -> None:
    if not FICTION.get('franchises'):
        return
    p = PREFIX
    items = []
    for fr in FICTION['franchises']:
        items.append(
            f'<a class="origin-card" href="{p}/fiction/{fr["slug"]}.html">'
            f'<span class="origin-card-label">{fr["title"]}</span>'
            f'<span class="origin-card-count">{S("fiction_card_count", n=len(fr.get("names", [])))}'
            f' &middot; {fr.get("kind", "")}</span>'
            f'</a>'
        )
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("nav_fiction")}</div>
        <h1>{S("fiction_hub_h1")}</h1>
        <p>{S("fiction_hub_intro", n=len(FICTION["franchises"]))}</p>
        <div class="origin-grid">
{''.join(items)}
        </div>"""
    (OUT_DIR / 'fiction.html').write_text(
        page(S("fiction_hub_title"), body,
             description=S("fiction_hub_desc"),
             canonical=f"{BASE_URL}{p}/fiction.html",
             extra_head=hreflang_for_hub("fiction.html")),
        encoding='utf-8')


FR_MONTH_NAMES = ['', 'Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
                  'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre']
ES_MONTH_NAMES = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                  'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
IT_MONTH_NAMES = ['', 'Gennaio', 'Febbraio', 'Marzo', 'Aprile', 'Maggio', 'Giugno',
                  'Luglio', 'Agosto', 'Settembre', 'Ottobre', 'Novembre', 'Dicembre']
EN_MONTH_NAMES = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                  'July', 'August', 'September', 'October', 'November', 'December']

MONTH_NAMES_BY_CC = {
    'FR': FR_MONTH_NAMES, 'ES': ES_MONTH_NAMES, 'IT': IT_MONTH_NAMES,
    # Other CCs fall back to English; saints calendars currently exist only for FR/ES/IT.
}

# Saints that are events rather than personal names — never get an "à tous les
# X" wish, no name-page link, and slugged but treated as events on hub.
SAINT_EVENTS = {
    # French event slugs
    'marie', 'presentation-de-jesus', 'notre-dame-de-lourdes', 'conversion-de-saint-paul',
    'annonciation', 'visitation', 'transfiguration', 'assomption',
    'nativite-de-marie', 'croix-glorieuse', 'toussaint', 'defunts',
    'presentation-de-marie', 'immaculee-conception', 'noel', 'innocents',
    'pierre-et-paul', 'anne-et-joachim', 'cosme-et-damien', 'cote-et-damien',
    'come-et-damien', 'simon-et-jude',
    # Spanish event slugs (santoral)
    'reyes-magos', 'conversion-de-san-pablo', 'candelaria', 'catedra-de-san-pedro',
    'anunciacion', 'visitacion-de-la-virgen', 'transfiguracion', 'asuncion',
    'natividad-de-la-virgen', 'nombre-de-maria', 'exaltacion-de-la-cruz',
    'dolores', 'angeles-custodios', 'virgen-del-rosario', 'virgen-del-carmen',
    'virgen-de-fatima', 'virgen-de-la-medalla-milagrosa', 'pilar', 'maria-auxiliadora',
    'maria-reina', 'todos-los-santos', 'fieles-difuntos', 'presentacion-de-maria',
    'inmaculada-concepcion', 'guadalupe', 'loreto', 'nieves',
    'navidad', 'santos-inocentes', 'pedro-y-pablo', 'joaquin-y-ana',
    'cosme-y-damian', 'simon-y-judas', 'miguel-gabriel-y-rafael',
    # Italian event slugs (santorale)
    'epifania', 'presentazione-del-signore', 'madonna-di-lourdes',
    'conversione-di-san-paolo', 'cattedra-di-san-pietro', 'annunciazione',
    'visitazione-di-maria', 'trasfigurazione', 'assunzione-di-maria',
    'nativita-di-maria', 'esaltazione-della-croce', 'addolorata',
    'angeli-custodi', 'madonna-del-rosario', 'madonna-del-carmine',
    'madonna-di-fatima', 'madonna-della-neve', 'maria-ausiliatrice',
    'maria-regina', 'tutti-i-santi', 'defunti', 'presentazione-di-maria',
    'immacolata-concezione', 'natale', 'santi-innocenti',
    'pietro-e-paolo', 'anna-e-gioacchino', 'cosma-e-damiano',
    'simone-e-giuda', 'michele-gabriele-e-raffaele',
    'filippo-e-giacomo', 'marcellino-e-pietro', 'proto-e-giacinto',
    'timoteo-e-tito',
}


def _saints_hub_filename() -> str:
    """Per-country URL for the saints calendar hub."""
    return {'FR': 'jour-de-fete.html', 'ES': 'dia-del-santo.html',
            'IT': 'onomastico.html'}.get(ACTIVE_CC, 'saints.html')


def _saint_page_dir() -> str:
    """Per-country directory for individual saint pages."""
    return {'FR': 'saint', 'ES': 'santo',
            'IT': 'onomastico'}.get(ACTIVE_CC, 'saint')


def generate_saints_hub_page() -> None:
    """One big calendar — 12 month sections × ~30 days each, with today's
    cell highlighted client-side via JS. Pure HTML so it's all crawlable."""
    if not SAINTS_FR:
        return
    p = PREFIX
    month_names = MONTH_NAMES_BY_CC.get(ACTIVE_CC, EN_MONTH_NAMES)
    saint_dir = _saint_page_dir()
    sections = []
    for month in range(1, 13):
        rows = []
        for day in range(1, 32):
            key = f'{month:02d}-{day:02d}'
            if key not in SAINTS_FR:
                continue
            saint = SAINTS_FR[key]
            slug = slugify(saint)
            href = f'{p}/{saint_dir}/{slug}.html'
            rows.append(
                f'<li data-key="{key}"><span class="sf-day">{day}</span>'
                f'<a href="{href}">{saint}</a></li>'
            )
        sections.append(
            f'<section class="sf-month" id="m{month:02d}">'
            f'<h2>{month_names[month]}</h2>'
            f'<ol class="sf-days">{"".join(rows)}</ol>'
            f'</section>'
        )
    hub_file = _saints_hub_filename()
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("nav_saints")}</div>
        <h1>{S("saints_hub_h1")}</h1>
        <p>{S("saints_hub_intro")}</p>
        <div id="sf-today" class="sf-today" style="display:none;"></div>
        <div class="sf-calendar">{''.join(sections)}</div>"""
    (OUT_DIR / hub_file).write_text(
        page(S("saints_hub_title"), body,
             description=S("saints_hub_desc"),
             canonical=f"{BASE_URL}{p}/{hub_file}"),
        encoding='utf-8')


def generate_saint_page(slug: str, dates: list[str]) -> None:
    p = PREFIX
    saint_name = SAINTS_FR[dates[0]]   # first occurrence's official spelling
    is_event = slug in SAINT_EVENTS
    has_name_page = slug in SLUGS_WITH_PAGE_BY_CC.get(ACTIVE_CC, set())
    # Sainte vs Saint — naive heuristic: ends in 'e' or 'a' → fem
    # Only applied for French tree (the {e} placeholder is silently dropped on
    # English/Italian/Spanish strings, so it's safe to compute either way).
    last = saint_name[-1].lower()
    fem_suffix = 'e' if last in 'ae' and not is_event else ''

    month_names = MONTH_NAMES_BY_CC.get(ACTIVE_CC, EN_MONTH_NAMES)
    saint_dir = _saint_page_dir()
    hub_file = _saints_hub_filename()

    def pretty_date(d: str) -> str:
        m, dd = d.split('-')
        return f'{int(dd)} {month_names[int(m)].lower()}'

    if len(dates) == 1:
        dates_html = S("saint_page_dates_one", date=pretty_date(dates[0]))
    else:
        ds = ', '.join(pretty_date(d) for d in dates)
        dates_html = S("saint_page_dates_multi", dates=ds)

    pop_link = ''
    if has_name_page and not is_event:
        pop_link = (f'<p><a href="{p}/name/{slug}.html"><strong>'
                    + S("saint_page_popularity_link", name=saint_name)
                    + '</strong></a></p>')

    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; <a href="{p}/{hub_file}">{S("nav_saints")}</a> &rsaquo; {saint_name}</div>
        <h1>{S("saint_page_h1", e=fem_suffix, name=saint_name)}</h1>
        <p class="sf-dates">{dates_html}</p>
        {pop_link}
        <p style="margin-top:2.5rem;"><a href="{p}/{hub_file}">{S("saint_back_to_hub")}</a></p>"""
    (OUT_DIR / saint_dir / f'{slug}.html').parent.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / saint_dir / f'{slug}.html').write_text(
        page(S("saint_page_title", e=fem_suffix, name=saint_name), body,
             description=S("saint_page_desc", name=saint_name),
             canonical=f"{BASE_URL}{p}/{saint_dir}/{slug}.html"),
        encoding='utf-8')


def generate_initials_page() -> None:
    """Empty shell. JS reads ?i=ABC[&sex=F|M] and rolls 20 combos drawn from
    name-meta.json (first + optional middle) plus the bundled surname list."""
    p = PREFIX
    girls = loc_label_cap('F')
    boys = loc_label_cap('M')
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("nav_initials")}</div>
        <h1>{S("initials_h1")}</h1>
        <p>{S("initials_intro")}</p>
        <form id="in-form" autocomplete="off">
            <div class="in-form">
                <input type="text" id="in-input" placeholder="{S("initials_input")}" aria-label="{S("initials_input")}" maxlength="8">
                <button type="submit">{S("initials_go")}</button>
            </div>
        </form>
        <div class="in-sex-tabs" role="tablist" aria-label="{S("initials_filter_sex")}">
            <button type="button" class="in-sex-tab is-active" data-sex="all">{S("ww_tab_all")}</button>
            <button type="button" class="in-sex-tab" data-sex="F">{girls}</button>
            <button type="button" class="in-sex-tab" data-sex="M">{boys}</button>
        </div>
        <p id="in-empty">{S("initials_empty")}</p>
        <p id="in-error" class="in-error" style="display:none;"></p>
        <ul id="in-result" class="in-list" style="display:none;"></ul>
        <div class="in-actions">
            <button type="button" id="in-again">{S("initials_again")}</button>
            <button type="button" id="in-share">{S("initials_share")}</button>
            <span id="in-share-done" style="display:none; color:#149E91; font-size:0.9rem;">{S("initials_share_done")}</span>
        </div>"""
    (OUT_DIR / 'initials.html').write_text(
        page(S("initials_title"), body,
             description=S("initials_desc"),
             canonical=f"{BASE_URL}{p}/initials.html",
             extra_head=hreflang_for_hub("initials.html")),
        encoding='utf-8')


def generate_fiction_franchise_page(fr: dict) -> None:
    p = PREFIX
    items = []
    for entry in fr.get('names', []):
        name = entry['name']
        slug = slugify(name)
        role = entry.get('role', '')
        if slug in SLUGS_WITH_PAGE_BY_CC[ACTIVE_CC]:
            name_html = f'<a href="{p}/name/{slug}.html">{name}</a>'
        else:
            name_html = f'<span class="name-unlinked">{name}</span>'
        items.append(
            f'<li class="fiction-row"><span class="fiction-name">{name_html}</span>'
            f'<span class="fiction-role">{role}</span></li>'
        )
    title = fr['title']
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; <a href="{p}/fiction.html">{S("nav_fiction")}</a> &rsaquo; {title}</div>
        <h1>{S("fiction_franchise_title", title=title)}</h1>
        <p>{fr.get("blurb", "")}</p>
        <p style="color:#5B6678; font-size:0.9rem;">{S("fiction_franchise_intro", n=len(fr.get("names", [])), title=title)}</p>
        <ul class="fiction-list">{''.join(items)}</ul>
        <p style="margin-top:2rem;"><a href="{p}/fiction.html">{S("fiction_back_to_hub")}</a></p>"""
    (OUT_DIR / 'fiction' / f'{fr["slug"]}.html').write_text(
        page(S("fiction_franchise_title", title=title), body,
             description=S("fiction_franchise_desc", title=title),
             canonical=f"{BASE_URL}{p}/fiction/{fr['slug']}.html",
             extra_head=hreflang_for_hub(f"fiction/{fr['slug']}.html")),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Single 404 (country-neutral), single sitemap + robots (root)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Blog (/blog/) — markdown posts from data/blog/*.md, per-country.
# ---------------------------------------------------------------------------
def _blog_date_display(iso: str) -> str:
    """'2026-06-05' → 'June 5, 2026' (English) or '5 juin 2026' (French)."""
    try:
        from datetime import date
        d = date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    if ACTIVE_CC == 'FR':
        months = ["janvier", "février", "mars", "avril", "mai", "juin",
                  "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
        return f"{d.day} {months[d.month - 1]} {d.year}"
    months = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    return f"{months[d.month - 1]} {d.day}, {d.year}"


def generate_blog_index():
    posts = BLOG_POSTS_BY_CC.get(ACTIVE_CC, [])
    if not posts:
        return
    p = PREFIX
    items = []
    for post in posts:
        items.append(
            f'<li class="blog-card">'
            f'<a href="{p}/blog/{post["slug"]}.html"><h3>{post["title"]}</h3></a>'
            f'<p class="blog-meta">{_blog_date_display(post["date"])}</p>'
            f'<p>{post["description"]}</p>'
            f'<a class="blog-readmore" href="{p}/blog/{post["slug"]}.html">{S("blog_read_more")} →</a>'
            f'</li>'
        )
    body = (
        f'        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("blog_h1")}</div>\n'
        f'        <h1>{S("blog_h1")}</h1>\n'
        f'        <p>{S("blog_intro")}</p>\n'
        f'        <ul class="blog-list">{"".join(items)}</ul>'
    )
    (OUT_DIR / 'blog').mkdir(parents=True, exist_ok=True)
    (OUT_DIR / 'blog' / 'index.html').write_text(
        page(S("blog_title"), body,
             description=S("blog_desc"),
             canonical=f"{BASE_URL}{p}/blog/"),
        encoding='utf-8')


def generate_blog_post(post: dict):
    p = PREFIX
    canonical = f"{BASE_URL}{p}/blog/{post['slug']}.html"
    # Internal links in markdown that start with "/name/..." get prefixed so
    # they land on the active country's tree (FR posts → /fr/name/...).
    html = post['html']
    if p:
        html = re.sub(r'href="(/(?:name|similar|origin|fiction|year|decade|letter)/[^"]+)"',
                      lambda m: f'href="{p}{m.group(1)}"', html)
    body = (
        f'        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; '
        f'<a href="{p}/blog/">{S("blog_h1")}</a> &rsaquo; {post["title"]}</div>\n'
        f'        <article class="blog-post">\n'
        f'            <h1>{post["title"]}</h1>\n'
        f'            <p class="blog-meta">{_blog_date_display(post["date"])}</p>\n'
        f'            {html}\n'
        f'        </article>\n'
        f'        <p class="blog-back"><a href="{p}/blog/">← {S("blog_back")}</a></p>'
    )
    (OUT_DIR / 'blog').mkdir(parents=True, exist_ok=True)
    (OUT_DIR / 'blog' / f'{post["slug"]}.html').write_text(
        page(post['title'] + ' — NameCharted', body,
             description=post['description'],
             canonical=canonical),
        encoding='utf-8')


def generate_favorites_page():
    """Empty shell — JS fills in the list from localStorage on load.
    noindex so search engines don't try to crawl an empty page."""
    p = PREFIX
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("nav_favorites")}</div>
        <div class="fav-print-header" aria-hidden="true">
            <div class="fav-print-brand">NameCharted</div>
            <div class="fav-print-title">{S("fav_print_h1")}</div>
        </div>
        <h1 class="fav-screen-h1">{S("fav_h1")}</h1>
        <p class="fav-screen-only">{S("fav_intro")}</p>
        <div class="fav-actions" id="fav-actions" style="display:none;">
            <button class="fav-share-btn" id="fav-share">{S("fav_share_btn")}</button>
            <span class="fav-share-done" id="fav-share-done" style="display:none;">{S("fav_share_done")}</span>
            <button class="fav-print-btn" id="fav-print">{S("fav_print_btn")}</button>
        </div>
        <p id="fav-empty">{S("fav_empty")}</p>
        <ul class="fav-list" id="fav-list" style="display:none;"></ul>
        <div class="fav-print-foot" aria-hidden="true">{S("fav_print_foot")}</div>"""
    extra_head = '\n    <meta name="robots" content="noindex">'
    (OUT_DIR / 'favorites.html').write_text(
        page(S("fav_title"), body,
             description=S("fav_desc"),
             canonical=f"{BASE_URL}{p}/favorites.html",
             extra_head=extra_head),
        encoding='utf-8')


def generate_404_page():
    # Render under whichever country is currently active; we call this last
    # after set_active("US") so the nav looks correct.
    body = """        <div style="text-align:center; padding:2rem 0;">
        <h1>Name not found</h1>
        <p style="color:#5B6678; max-width:520px; margin:1rem auto;">
        We couldn't find a dedicated page for that name. NameCharted has full
        trend pages for every name with at least 500 lifetime births. Rarer
        names are listed in our complete A–Z index — pick a country to browse.</p>
        <p style="margin-top:2rem; display:flex; gap:0.6rem; justify-content:center; flex-wrap:wrap;">
            <a href="/" style="padding:0.6rem 1.1rem; background:#149E91; color:#fff; text-decoration:none; border-radius:6px; font-weight:600;">US</a>
            <a href="/fr/" style="padding:0.6rem 1.1rem; background:#fff; color:#1B2440; border:1px solid #d6dde2; text-decoration:none; border-radius:6px; font-weight:600;">France</a>
            <a href="/uk/" style="padding:0.6rem 1.1rem; background:#fff; color:#1B2440; border:1px solid #d6dde2; text-decoration:none; border-radius:6px; font-weight:600;">UK</a>
            <a href="/au/" style="padding:0.6rem 1.1rem; background:#fff; color:#1B2440; border:1px solid #d6dde2; text-decoration:none; border-radius:6px; font-weight:600;">Australia</a>
        </p>
        </div>"""
    (OUTPUT_DIR / '404.html').write_text(
        page("Name not found — NameCharted", body,
             description="The name you searched isn't in our index. Pick a country to browse the full A–Z list."),
        encoding='utf-8')


def collect_country_urls(cc: str, compare_files: list[str]) -> list[str]:
    slug = COUNTRY_SLUG[cc]
    p = '' if not slug else f'/{slug}'
    urls = [f"{BASE_URL}{p}/", f"{BASE_URL}{p}/names.html",
            f"{BASE_URL}{p}/trends.html", f"{BASE_URL}{p}/decades.html",
            f"{BASE_URL}{p}/trends/rising.html", f"{BASE_URL}{p}/trends/falling.html",
            f"{BASE_URL}{p}/rare-names.html", f"{BASE_URL}{p}/compare.html",
            f"{BASE_URL}{p}/works-with.html",
            f"{BASE_URL}{p}/picker.html",
            f"{BASE_URL}{p}/sibling.html",
            f"{BASE_URL}{p}/initials.html"]
    if cc in ORIGIN_TO_NAMES_BY_CC and ORIGIN_TO_NAMES_BY_CC[cc]:
        urls.append(f"{BASE_URL}{p}/origins.html")
        urls += [f"{BASE_URL}{p}/origin/{o}.html"
                 for o in ORIGIN_TO_NAMES_BY_CC[cc]]
    if FICTION.get('franchises'):
        urls.append(f"{BASE_URL}{p}/fiction.html")
        urls += [f"{BASE_URL}{p}/fiction/{fr['slug']}.html"
                 for fr in FICTION['franchises']]
    if cc in ('FR', 'ES', 'IT') and SAINTS_BY_CC.get(cc):
        hub_file = {'FR': 'jour-de-fete.html', 'ES': 'dia-del-santo.html',
                    'IT': 'onomastico.html'}[cc]
        saint_dir = {'FR': 'saint', 'ES': 'santo', 'IT': 'onomastico'}[cc]
        urls.append(f"{BASE_URL}{p}/{hub_file}")
        urls += [f"{BASE_URL}{p}/{saint_dir}/{s}.html"
                 for s in SAINT_TO_DATES_BY_CC[cc].keys()]
    if BLOG_POSTS_BY_CC.get(cc):
        urls.append(f"{BASE_URL}{p}/blog/")
        urls += [f"{BASE_URL}{p}/blog/{post['slug']}.html" for post in BLOG_POSTS_BY_CC[cc]]
    urls += [f"{BASE_URL}{p}/name/{slugify(n)}.html" for n in pages_to_generate_by_country[cc]]
    urls += [f"{BASE_URL}{p}/similar/{slugify(n)}.html" for n in pages_to_generate_by_country[cc]]
    urls += [f"{BASE_URL}{p}/year/{y}.html" for y in years_by_country[cc]]
    latest = years_by_country[cc][-1] if years_by_country[cc] else None
    prev_latest = latest - 1 if latest else None
    if latest and prev_latest in YEARS_SET_BY_CC[cc]:
        urls.append(f"{BASE_URL}{p}/year-in-review-{latest}.html")
    urls += [f"{BASE_URL}{p}/decade/{d}s.html" for d in decades_by_country[cc]]
    urls += [f"{BASE_URL}{p}/letter/{sex_label(sex)}-{letter.lower()}.html"
             for sex in ('F', 'M') for letter in sorted(letter_names_by_country[cc][sex].keys())]
    if cc == 'US':
        urls += [f"{BASE_URL}/compare/{f}" for f in compare_files]
    return urls


def write_sitemaps_and_robots(urls_by_cc: dict[str, list[str]]) -> None:
    """Write one child <urlset> per country + a <sitemapindex> at /sitemap.xml.
    Google's per-file limit is 50K URLs; splitting per country keeps each
    child comfortably under it and makes country-level coverage easy to audit
    in Search Console."""
    total = 0
    child_files: list[str] = []
    for cc in COUNTRIES:
        urls = urls_by_cc.get(cc, [])
        seen: set[str] = set()
        deduped: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        child_slug = cc.lower() if cc != 'GB' else 'uk'
        child_name = f'sitemap-{child_slug}.xml'
        body = '<?xml version="1.0" encoding="UTF-8"?>\n'
        body += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        for u in deduped:
            body += f'  <url><loc>{u}</loc></url>\n'
        body += '</urlset>\n'
        (OUTPUT_DIR / child_name).write_text(body, encoding='utf-8')
        child_files.append(child_name)
        total += len(deduped)
        print(f"  {child_name}: {len(deduped):,} URLs")

    idx = '<?xml version="1.0" encoding="UTF-8"?>\n'
    idx += '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for child in child_files:
        idx += f'  <sitemap><loc>{BASE_URL}/{child}</loc></sitemap>\n'
    idx += '</sitemapindex>\n'
    (OUTPUT_DIR / 'sitemap.xml').write_text(idx, encoding='utf-8')
    print(f"sitemap.xml (index): {len(child_files)} children, {total:,} URLs total")

    robots = (
        "User-agent: *\n"
        "Allow: /\n\n"
        f"Sitemap: {BASE_URL}/sitemap.xml\n"
    )
    (OUTPUT_DIR / 'robots.txt').write_text(robots, encoding='utf-8')


# ---------------------------------------------------------------------------
# Per-country build runner
# ---------------------------------------------------------------------------
def generate_air_easter_egg():
    """A hand-written /name/air.html that overrides the empty default —
    the name "Air" doesn't clear PAGE_MIN_TOTAL on its own (5 babies in
    2024 per SSA), so without this we'd 404. This is the creator's note."""
    p = PREFIX
    canonical = f"{BASE_URL}/name/air.html"
    body = """        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; Air</div>
        <div class="air-card">
            <div class="air-card-eyebrow">A note from the creator</div>
            <h1 class="air-card-h1">Congratulations! You found the creator of this website</h1>
            <p class="air-card-lede">
                <strong>Air</strong> is known in Telegram as a deeply loyal, humorous,
                quirky, weird, intelligent guy who believes fiercely in true love.
            </p>
            <p class="air-card-lede">
                He obsesses over charts and statistics, hates hot weather and coriander,
                is scared of horror movies and missing out on life, and looks forward to
                being pushed to his limits — or towards a steep downhill in a wheelchair.
            </p>
            <p class="air-card-lede">
                He built this whole site in case he and <strong>______</strong> have
                babies one day and don't have any ideas on what to name them.
            </p>
            <p class="air-card-cta">
                <a href="/" class="air-card-link">Browse baby names →</a>
                <a href="/sibling.html" class="air-card-link-alt">Try the sibling tool</a>
            </p>
        </div>
        <style>
            .air-card { background: linear-gradient(180deg, #f7fafa 0%, #ffffff 100%); border: 1px solid #d6dde2; border-radius: 14px; padding: 2.25rem 2rem; margin: 1.5rem 0; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
            .air-card-eyebrow { color: #149E91; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; font-size: 0.78rem; margin-bottom: 0.5rem; }
            .air-card-h1 { margin: 0 0 1.25rem; font-size: 1.85rem; line-height: 1.15; color: #1f2933; }
            .air-card-lede { font-size: 1.05rem; line-height: 1.6; color: #2a3540; margin: 0 0 1rem; }
            .air-card-lede strong { color: #149E91; }
            .air-card-cta { margin: 1.5rem 0 0; display: flex; flex-wrap: wrap; gap: 0.6rem; }
            .air-card-link { background: #FF6B5C; color: #fff; padding: 0.55rem 1.1rem; border-radius: 999px; text-decoration: none; font-weight: 600; transition: background 0.12s ease, transform 0.12s ease; }
            .air-card-link:hover { background: #e85a4c; transform: translateY(-1px); }
            .air-card-link-alt { background: #fff; color: #1f2933; border: 1px solid #cfd6dc; padding: 0.55rem 1.1rem; border-radius: 999px; text-decoration: none; font-weight: 600; transition: background 0.12s ease, border-color 0.12s ease; }
            .air-card-link-alt:hover { background: #f2f5f8; border-color: #149E91; }
        </style>"""
    (OUT_DIR / 'name' / 'air.html').parent.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / 'name' / 'air.html').write_text(
        page("Air — a note from the creator",
             body,
             description="A personal note from the creator of NameCharted.",
             canonical=canonical),
        encoding='utf-8',
    )


def run_generators_for_active(compare_files_out: list[str]) -> None:
    cc = ACTIVE_CC
    print(f"--- Generating [{cc}] tree ({PREFIX or '/'}) ---")
    generate_homepage()
    generate_browse_index()
    # Pre-pass: render any missing per-name pins in parallel so the
    # name-page loop only does HTML work. PIL/Pillow releases the GIL
    # around the compression step, so threads give a real speed-up.
    _prerender_pins_parallel()
    print(f"  {len(pages_to_generate)} name pages…")
    for name in pages_to_generate:
        generate_name_page(name)
    print(f"  {len(pages_to_generate)} similar pages…")
    for name in pages_to_generate:
        generate_similar_page(name)
    (OUT_DIR / 'name-data').mkdir(parents=True, exist_ok=True)
    for name in pages_to_generate:
        generate_name_data_json(name)
    print(f"  {len(DECADES)} decade pages + hub…")
    for decade in DECADES:
        generate_decade_page(decade)
    generate_decades_hub()
    generate_trends_pages()
    generate_trends_hub()
    letter_count = 0
    for sex in ('F', 'M'):
        for letter in sorted(letter_names[sex].keys()):
            generate_letter_page(sex, letter)
            letter_count += 1
    print(f"  {letter_count} letter pages.")
    print(f"  {len(YEARS)} year pages…")
    for year in YEARS:
        generate_year_page(year)
    generate_year_in_review_page(LATEST_YEAR)

    if cc == 'US':
        generate_air_easter_egg()
        print("  compare pages (top 5 names)…")
        top5 = [name for name, _ in top_names[:5]]
        for i in range(len(top5)):
            for j in range(i + 1, len(top5)):
                generate_comparison_page(top5[i], top5[j])
                compare_files_out.append(f'{slugify(top5[i])}-vs-{slugify(top5[j])}.html')

    generate_rare_names_page()
    generate_favorites_page()
    if BLOG_POSTS_BY_CC.get(ACTIVE_CC):
        generate_blog_index()
        for post in BLOG_POSTS_BY_CC[ACTIVE_CC]:
            generate_blog_post(post)
    generate_compare_page()
    generate_works_with_page()
    generate_picker_page()
    generate_sibling_page()
    # Origins (Phase 6d) — no-op when enrichment data is missing
    by_origin = collect_origin_names_for_active()
    if by_origin:
        (OUT_DIR / 'origin').mkdir(parents=True, exist_ok=True)
        generate_origins_hub_page(by_origin)
        for origin, names in by_origin.items():
            generate_origin_page(origin, names)
        ORIGIN_TO_NAMES_BY_CC[ACTIVE_CC] = by_origin
        print(f"  origins: {len(by_origin)} pages")
    generate_initials_page()
    # Fiction (Phase 6h)
    if FICTION.get('franchises'):
        (OUT_DIR / 'fiction').mkdir(parents=True, exist_ok=True)
        generate_fiction_hub_page()
        for fr in FICTION['franchises']:
            generate_fiction_franchise_page(fr)
        print(f"  fiction: {len(FICTION['franchises'])} pages")
    # Saints calendar (Phase 6i FR; Phase 34 expanded to ES + IT)
    if cc in ('FR', 'ES', 'IT') and SAINTS_BY_CC.get(cc):
        _activate_saints_for(cc)
        (OUT_DIR / _saint_page_dir()).mkdir(parents=True, exist_ok=True)
        generate_saints_hub_page()
        for slug, dates in SAINT_TO_DATES.items():
            generate_saint_page(slug, dates)
        print(f"  saints: hub + {len(SAINT_TO_DATES)} saint pages")
    generate_name_index_json()
    generate_top_race_json()
    generate_name_meta_json()


def main():
    load_enrichment()
    load_fiction()
    load_saints_all()
    load_blog()
    for cc in COUNTRIES:
        build_country(cc)
    build_presence_indices()

    compare_files: list[str] = []
    urls_by_cc: dict[str, list[str]] = {}
    for cc in COUNTRIES:
        set_active(cc)
        run_generators_for_active(compare_files)
        urls_by_cc[cc] = collect_country_urls(cc, compare_files)

    # 404 + sitemap + robots — emit once at root. Render 404 under US nav.
    set_active("US")
    generate_404_page()
    write_sitemaps_and_robots(urls_by_cc)
    print("Done!")


if __name__ == '__main__':
    main()
