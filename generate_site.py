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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
COUNTRIES = ["US", "FR", "GB", "AU"]
# URL slug per country. US is empty (root). GB exposes /uk/ for branding.
COUNTRY_SLUG = {"US": "", "FR": "fr", "GB": "uk", "AU": "au"}
COUNTRY_LABEL = {"US": "US", "FR": "FR", "GB": "UK", "AU": "AU"}
COUNTRY_NAME = {"US": "United States", "FR": "France", "GB": "United Kingdom", "AU": "Australia"}
FLAG = {"US": "🇺🇸", "FR": "🇫🇷", "GB": "🇬🇧", "AU": "🇦🇺"}
# Country names rendered in each UI language (for the homepage cross-country callout).
COUNTRY_NAMES_EN = {"US": "United States", "FR": "France", "GB": "UK", "AU": "Australia"}
COUNTRY_NAMES_FR = {"US": "États-Unis", "FR": "France", "GB": "Royaume-Uni", "AU": "Australie"}
COUNTRY_NAMES_IN_UI = {"US": COUNTRY_NAMES_EN, "FR": COUNTRY_NAMES_FR,
                       "GB": COUNTRY_NAMES_EN, "AU": COUNTRY_NAMES_EN}
DATA_SOURCE_FULL = {
    "US": "U.S. Social Security Administration",
    "FR": "INSEE (France)",
    "GB": "UK Office for National Statistics",
    "AU": "NSW BDM + VIC BDM (Australia)",
}
DATA_SOURCE_SHORT = {
    "US": "official SSA",
    "FR": "official INSEE",
    "GB": "official ONS",
    "AU": "official NSW & VIC BDM",
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
decades_by_country: dict[str, list[int]] = {}
decade_sex_counts_by_country: dict[str, dict] = {}
variants_of_by_country: dict[str, dict] = {}   # canonical_name -> [(variant, total), ...]
canonical_of_by_country: dict[str, dict] = {}  # variant_name -> canonical_name

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
    for n in pages_to_generate:
        dom = _dom(n)
        series = counts[n][dom]
        peak_year = max(series, key=series.get)
        low = n.lower()
        name_meta[n] = {
            'dom': dom,
            'first': low[0],
            'last': low[-1],
            'last2': low[-2:],
            'len': len(n),
            'syll': count_syllables(n),
            'peak_dec': (peak_year // 10) * 10,
            'latest_rank': rank_by_year_sex.get((latest_year, dom), {}).get(n),
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
decade_sex_counts: dict = {}
top_names: list = []
VARIANTS_OF: dict = {}
CANONICAL_OF: dict = {}

ALL_SITEMAP_URLS: list[str] = []

# Presence indices populated once after all build_country calls. Used by
# hreflang helpers to decide which countries to cross-link from a page.
SLUGS_WITH_PAGE_BY_CC: dict[str, set[str]] = {}
YEARS_SET_BY_CC: dict[str, set[int]] = {}
DECADES_SET_BY_CC: dict[str, set[int]] = {}
LETTERS_BY_CC: dict[str, set[tuple[str, str]]] = {}  # (sex_code, uppercase_letter)

HREFLANG = {"US": "en-US", "FR": "fr-FR", "GB": "en-GB", "AU": "en-AU"}


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
    g['decade_sex_counts'] = decade_sex_counts_by_country[cc]
    g['VARIANTS_OF'] = variants_of_by_country[cc]
    g['CANONICAL_OF'] = canonical_of_by_country[cc]
    g['top_names'] = sorted(
        g['name_total'].items(), key=lambda x: (-x[1], x[0]))[:TOP_N_NAMES]

    out = g['OUT_DIR']
    for sub in ('name', 'year', 'similar', 'decade', 'letter', 'trends'):
        (out / sub).mkdir(parents=True, exist_ok=True)
    if cc == 'US':
        (out / 'compare').mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers (read active-country globals)
# ---------------------------------------------------------------------------
def dominant_sex(name: str) -> str:
    return 'F' if name_sex_total[(name, 'F')] >= name_sex_total[(name, 'M')] else 'M'


def similar_names(name, k=24):
    m = name_meta[name]
    dom = m['dom']
    scored = []
    for other in same_sex_ranked[dom]:
        if other == name:
            continue
        o = name_meta[other]
        s = 0
        if o['first'] == m['first']:
            s += 3
        if o['last2'] == m['last2']:
            s += 3
        elif o['last'] == m['last']:
            s += 1
        if abs(o['len'] - m['len']) <= 1:
            s += 2
        if abs(o['peak_dec'] - m['peak_dec']) <= 10:
            s += 2
        if m['latest_rank'] and o['latest_rank'] and abs(o['latest_rank'] - m['latest_rank']) <= 75:
            s += 1
        if s >= 4:
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
    "fav_h1": "Your saved names",
    "fav_title": "Your saved names — NameCharted",
    "fav_desc": "Your personal shortlist of saved names.",
    "fav_intro": "Names you've saved. Stored only in your browser — clearing your site data removes them.",
    "fav_empty": "No saved names yet. Tap the heart on any name page to add it here.",
    "fav_share_btn": "Copy shareable link",
    "fav_share_done": "Link copied!",
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
    "home_title": "NameCharted — {country} Name Popularity & Trends",
    "home_desc": ("Explore {country} name popularity and trends from {range}. "
                  "Search {n}+ names for yearly counts, rankings, gender split, "
                  "and interactive trend charts."),

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
    "sibling_intro": ("Give us one child's name and we'll suggest names that "
                      "share a similar era and rhythm — without rhyming or "
                      "starting with the same letter."),
    "sibling_input": "Existing child's name",
    "sibling_target_sex": "Next baby",
    "sibling_go": "Suggest names",
    "sibling_empty": "Type a name above to see sibling suggestions.",
    "sibling_unknown": ("We don't have data for that name, so we'll just match "
                        "on rhythm. For best results pick a name with its own "
                        "popularity page."),
    "sibling_result_for": "Names that pair well with {name}",
    "sibling_show_more": "Show more",
    "sibling_desc": ("Find sibling names that pair well with a child you've "
                     "already named. We match on peak era, syllable rhythm and "
                     "complementary starting letters."),

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
    "name_famous_h2": "Famous people named {name}",
    "name_famous_occ_sep": " · ",
    "name_famous_born": "b. {year}",
}

STRINGS_FR: dict[str, str] = {
    "nav_home": "Accueil",
    "nav_browse": "Parcourir A–Z",
    "nav_trends": "Tendances",
    "nav_decades": "Décennies",
    "nav_rankings": "Classement {year}",
    "nav_favorites": "Favoris",
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
    "fav_h1": "Vos prénoms enregistrés",
    "fav_title": "Vos prénoms enregistrés — NameCharted",
    "fav_desc": "Votre liste personnelle de prénoms favoris.",
    "fav_intro": "Les prénoms que vous avez enregistrés. Conservés uniquement dans votre navigateur — effacer les données du site les supprime.",
    "fav_empty": "Aucun prénom enregistré. Touchez le cœur sur une page de prénom pour l'ajouter ici.",
    "fav_share_btn": "Copier le lien à partager",
    "fav_share_done": "Lien copié !",
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
    "home_title": "NameCharted — Prénoms en France : popularité et tendances",
    "home_desc": ("Explorez la popularité et les tendances des prénoms en France "
                  "de {range}. Recherchez parmi plus de {n} prénoms : effectifs "
                  "annuels, classements, répartition par sexe et graphiques interactifs."),

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
    "sibling_intro": ("Donnez-nous le prénom d'un premier enfant : nous "
                      "proposons des prénoms d'époque et de rythme similaires, "
                      "sans rimer ni commencer par la même lettre."),
    "sibling_input": "Prénom du premier enfant",
    "sibling_target_sex": "Prochain bébé",
    "sibling_go": "Suggérer",
    "sibling_empty": "Saisissez un prénom pour voir des idées.",
    "sibling_unknown": ("Nous n'avons pas de données pour ce prénom : nous "
                        "ne ferons que la correspondance de rythme. Pour de "
                        "meilleurs résultats, choisissez un prénom ayant sa "
                        "propre page de popularité."),
    "sibling_result_for": "Prénoms qui vont bien avec {name}",
    "sibling_show_more": "Voir plus",
    "sibling_desc": ("Trouvez des prénoms pour la fratrie qui s'accordent avec "
                     "le prénom d'un enfant déjà choisi. Score basé sur "
                     "l'époque, le nombre de syllabes et l'initiale."),

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
    "name_famous_h2": "Personnalités prénommées {name}",
    "name_famous_occ_sep": " · ",
    "name_famous_born": "né en {year}",
}

STRINGS = {"US": STRINGS_EN, "FR": STRINGS_FR, "GB": STRINGS_EN, "AU": STRINGS_EN}

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
GENDERED = {"US": GENDERED_EN, "FR": GENDERED_FR, "GB": GENDERED_EN, "AU": GENDERED_EN}

# Origin-slug → display label per UI language. Slugs come from
# data/normalized/name_enrichment.json (built by fetchers/enrich_wikidata.py).
# Keep this in sync with that file; unknown slugs fall back to title-cased slug.
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
                 "GB": ORIGIN_LABELS_EN, "AU": ORIGIN_LABELS_EN}


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

        // Favorites page
        var ul = document.getElementById('fav-list');
        if (ul) {
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
    })();
    </script>"""


def favorites_script() -> str:
    return (FAVORITES_SCRIPT
            .replace('__ACTIVE_CC__', ACTIVE_CC)
            .replace('__PREFIX__', PREFIX)
            .replace('__FAV_REMOVE_TIP__', S("fav_remove_tip"))
            .replace('__FAV_REMOVE__', S("fav_remove")))


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

        // Order matches the python emit: [first, last2, syll, dom, peak_dec, rank]
        var IDX_FIRST = 0, IDX_LAST2 = 1, IDX_SYLL = 2, IDX_DOM = 3, IDX_RANK = 5;
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

        function score(name, m, lastFold, lastFirst, lastLast2, lastSyll) {
            // m = [first, last2, syll, dom, peak_dec]
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

                var rows = [];
                for (var slug in meta) {
                    var m = meta[slug];
                    if (activeSex !== 'all' && m[IDX_DOM] !== activeSex) continue;
                    if (slug === lastFold) continue;
                    rows.push([score(slug, m, lastFold, lastFirst, lastLast2, lastSyll), slug, m]);
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

        var IDX_FIRST = 0, IDX_SYLL = 2, IDX_DOM = 3, IDX_PEAK = 4, IDX_RANK = 5;

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
        var L_UNKNOWN = __L_UNKNOWN__;
        var L_PEAK = __L_PEAK__;
        var L_SHOW_MORE = __L_SHOW_MORE__;
        var PAGE_SIZE = 12;

        var IDX_FIRST = 0, IDX_LAST2 = 1, IDX_SYLL = 2, IDX_DOM = 3, IDX_PEAK = 4, IDX_RANK = 5;

        var input = document.getElementById('sib-input');
        var ac = document.getElementById('sib-ac');
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

        // Autocomplete (mirrors compare/works-with pattern)
        (function() {
            var sel = -1, items = [];
            function render(matches) {
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
            function search() {
                var q = slugify(input.value);
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
            input.addEventListener('input', function() { loadData().then(search); });
            input.addEventListener('focus', function() { loadData().then(search); });
            input.addEventListener('blur', function() { setTimeout(function() { ac.style.display = 'none'; }, 150); });
            input.addEventListener('keydown', function(e) {
                if (ac.style.display === 'none') return;
                if (e.key === 'ArrowDown') { sel = (sel + 1) % items.length; render(items); e.preventDefault(); }
                else if (e.key === 'ArrowUp') { sel = (sel - 1 + items.length) % items.length; render(items); e.preventDefault(); }
                else if (e.key === 'Enter' && sel >= 0) { input.value = display(items[sel]); ac.style.display = 'none'; e.preventDefault(); run(); }
                else if (e.key === 'Escape') { ac.style.display = 'none'; }
            });
        })();

        function score(cand, m, ref) {
            // ref: {first, last2, syll, peak}, m = candidate meta array
            var s = 0;
            var reasons = [];

            // Era match
            var peakDiff = Math.abs(m[IDX_PEAK] - ref.peak);
            if (peakDiff === 0) { s += 22; reasons.push('era'); }
            else if (peakDiff <= 10) { s += 14; reasons.push('era'); }
            else if (peakDiff <= 20) s += 6;

            // Syllable similarity
            var syllDiff = Math.abs(m[IDX_SYLL] - ref.syll);
            if (syllDiff === 0) { s += 8; reasons.push('rhythm'); }
            else if (syllDiff === 1) { s += 5; reasons.push('rhythm'); }

            // Penalty: same starting letter (less variety) — unless both vowels match
            if (m[IDX_FIRST] === ref.first) s -= 10;
            else { s += 3; reasons.push('contrast'); }

            // Penalty: rhyming endings
            if (m[IDX_LAST2] === ref.last2) s -= 14;

            // Penalty: candidate is sibling itself
            // handled by caller (skips slug === refSlug)

            // Familiarity bonus — break ties toward currently-popular names.
            // Use rank-band rather than raw rank so we don't overweight the very top.
            var r = m[IDX_RANK];
            if (r) {
                if (r <= 50) s += 5;
                else if (r <= 200) s += 4;
                else if (r <= 1000) s += 2;
                else s += 1;
            }

            return [s, reasons];
        }

        function run() {
            var rawName = input.value.trim();
            if (!rawName) { emptyEl.style.display = ''; resultEl.style.display = 'none'; noteEl.style.display = 'none'; return; }
            emptyEl.style.display = 'none';
            loadData().then(function() {
                var slug = slugify(rawName);
                var ref;
                if (META[slug]) {
                    var rm = META[slug];
                    ref = { first: rm[IDX_FIRST], last2: rm[IDX_LAST2], syll: rm[IDX_SYLL], peak: rm[IDX_PEAK] };
                    noteEl.style.display = 'none';
                } else {
                    // Fallback: derive what we can from raw text. No era match possible.
                    var folded = rawName.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/[^a-z]/g, '');
                    ref = { first: folded[0] || 'a', last2: folded.slice(-2) || '', syll: countSyllables(rawName), peak: null };
                    noteEl.style.display = '';
                }

                var rows = [];
                for (var s in META) {
                    if (s === slug) continue;
                    var m = META[s];
                    if (targetSex !== 'all' && m[IDX_DOM] !== targetSex) continue;
                    var sc;
                    if (ref.peak === null) {
                        // Without era data, just score syllables + initial
                        var local = 0;
                        var sd = Math.abs(m[IDX_SYLL] - ref.syll);
                        if (sd === 0) local += 8;
                        else if (sd === 1) local += 5;
                        if (m[IDX_FIRST] !== ref.first) local += 3; else local -= 10;
                        if (m[IDX_LAST2] === ref.last2) local -= 14;
                        sc = [local, []];
                    } else {
                        sc = score(s, m, ref);
                    }
                    rows.push([sc[0], s, m, sc[1]]);
                }
                rows.sort(function(a, b) {
                    if (b[0] !== a[0]) return b[0] - a[0];
                    var ra = a[2][IDX_RANK] || 99999, rb = b[2][IDX_RANK] || 99999;
                    if (ra !== rb) return ra - rb;
                    return a[1] < b[1] ? -1 : 1;
                });
                currentResults = rows.slice(0, 60);
                shown = 0;

                headerEl.textContent = L_RESULT_FOR.replace('{name}', display(slug));
                clear(listEl);
                renderMore();
                resultEl.style.display = '';
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
                meta.textContent = (m[IDX_DOM] === 'F' ? L_GIRLS : L_BOYS) + ' · ' + L_PEAK.replace('{d}', m[IDX_PEAK]);
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
                if (input.value.trim()) run();
            });
        });
        form.addEventListener('submit', function(e) { e.preventDefault(); ac.style.display = 'none'; run(); });

        // Pre-fill from ?name=
        var qs = new URLSearchParams(window.location.search);
        var qn = qs.get('name');
        if (qn) { input.value = qn; loadData().then(run); }
    })();
    </script>"""


def sibling_script() -> str:
    def js(v: str) -> str:
        return json.dumps(v)
    return (SIBLING_SCRIPT
            .replace('__PREFIX__', js(PREFIX))
            .replace('__L_GIRLS__', js(loc_label_cap('F')))
            .replace('__L_BOYS__', js(loc_label_cap('M')))
            .replace('__L_RESULT_FOR__', js(S("sibling_result_for", name='{name}')))
            .replace('__L_UNKNOWN__', js(S("sibling_unknown")))
            .replace('__L_PEAK__', js(S("picker_peak_decade", d='{d}')))
            .replace('__L_SHOW_MORE__', js(S("sibling_show_more"))))


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
        .container { max-width: 900px; margin: 0 auto; padding: 2rem; }
        h1, h2, h3, h4 { font-family: 'Poppins', 'Inter', sans-serif; color: #1B2440; }
        h1 { color: #1B2440; }
        .sitenav {
            background: #1B2440; padding: 0.9rem 2rem;
        }
        .sitenav-inner { max-width: 900px; margin: 0 auto; display: flex; gap: 1.5rem; align-items: center; flex-wrap: wrap; }
        .sitenav a { color: #EEF2F4; text-decoration: none; font-weight: 500; }
        .sitenav a:hover { color: #fff; text-decoration: underline; }
        .sitenav .brand { font-family: 'Poppins', 'Inter', sans-serif; font-weight: 700; color: #fff; margin-right: auto; display: inline-flex; align-items: center; gap: 0.55rem; font-size: 1.05rem; }
        .sitenav .brand svg { display: block; }
        .sitenav .brand .wm-teal { color: #149E91; }
        .sitenav .brand-cc { color: #c8cfdb; font-weight: 500; font-size: 0.95rem; margin-left: 0.15rem; display: inline-flex; align-items: center; gap: 0.3rem; }
        .sitenav .brand-cc-dash { color: #4a5269; }
        .sitenav .brand-cc .flag { font-size: 1.05rem; line-height: 1; }
        .h1-flag { margin: 0 0.15rem; font-size: 0.8em; vertical-align: 0.1em; }
        @media (max-width: 560px) { .sitenav .brand-cc { display: none; } }
        .ccswitch { margin-left: auto; font-size: 0.95rem; color: #8a93a3; display: inline-flex; gap: 0.45rem; align-items: center; }
        .ccswitch a { color: #c8cfdb; text-decoration: none; }
        .ccswitch a:hover { color: #fff; }
        .ccswitch strong { color: #fff; font-weight: 700; }
        .ccswitch .flag { font-size: 1.05rem; line-height: 1; margin-right: 0.18rem; }
        .ccswitch .sep { color: #4a5269; }
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
        .fav-list { list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 0.75rem; margin: 1.5rem 0; }
        .fav-list li { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.75rem 1rem; display: flex; align-items: center; justify-content: space-between; }
        .fav-list a { color: #1B2440; text-decoration: none; font-weight: 600; flex: 1; }
        .fav-list a:hover { color: #149E91; }
        .fav-remove-btn { background: none; border: 0; cursor: pointer; color: #c0392b; font-size: 1.2rem; line-height: 1; padding: 0 0.25rem; }
        .fav-remove-btn:hover { color: #7a1f12; }
        .fav-actions { display: flex; gap: 0.75rem; align-items: center; margin: 1rem 0 2rem; }
        .fav-share-btn { background: #149E91; color: #fff; border: 0; border-radius: 6px; padding: 0.55rem 1rem; font-weight: 600; cursor: pointer; font-size: 0.92rem; }
        .fav-share-btn:hover { background: #117f74; }
        .fav-share-done { color: #27ae60; font-size: 0.9rem; }
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
        .famous-list { list-style: none; padding: 0; margin: 1rem 0 2rem; display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem; }
        @media (max-width: 600px) { .famous-list { grid-template-columns: 1fr; } }
        .famous-item { background: #fff; border: 1px solid #d6dde2; border-radius: 8px; padding: 0.65rem 0.9rem; display: flex; flex-direction: column; gap: 0.2rem; }
        .famous-name a { color: #1B2440; text-decoration: none; font-weight: 600; }
        .famous-name a:hover { color: #149E91; text-decoration: underline; }
        .famous-sub { color: #5B6678; font-size: 0.82rem; }
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
        .sib-form { display: flex; gap: 0.6rem; margin: 1.5rem 0 1rem; flex-wrap: wrap; }
        .sib-form .ac-wrap { position: relative; flex: 1; min-width: 220px; }
        .sib-form input { width: 100%; box-sizing: border-box; padding: 0.7rem 0.9rem; font-size: 1rem; border: 1px solid #d6dde2; border-radius: 6px; background: #fff; }
        .sib-form button { background: #149E91; color: #fff; border: 0; border-radius: 6px; padding: 0.7rem 1.3rem; font-weight: 600; cursor: pointer; font-size: 1rem; }
        .sib-form button:hover { background: #117f74; }
        #sib-ac { position: absolute; left: 0; right: 0; top: 100%; background: #fff; border: 1px solid #d6dde2; border-top: 0; border-radius: 0 0 6px 6px; max-height: 260px; overflow-y: auto; z-index: 10; }
        #sib-ac div { padding: 0.5rem 0.9rem; cursor: pointer; }
        #sib-ac div:hover, #sib-ac div.sel { background: #EEF2F4; }
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
"""


def country_switcher_html() -> str:
    parts = []
    for c in COUNTRIES:
        href = home_path(c)
        flag = f'<span class="flag" aria-hidden="true">{FLAG[c]}</span>'
        label = f'{flag}{COUNTRY_LABEL[c]}'
        if c == ACTIVE_CC:
            parts.append(f'<strong>{label}</strong>')
        else:
            parts.append(f'<a href="{href}">{label}</a>')
    sep = '<span class="sep">·</span>'
    return '<span class="ccswitch">' + sep.join(parts) + '</span>'


def site_nav_html() -> str:
    p = PREFIX
    return f"""
    <div class="sitenav"><div class="sitenav-inner">
        <a class="brand" href="{home_path()}"><svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true"><rect x="1" y="1" width="30" height="30" rx="7" fill="#149E91"/><polyline points="6,22 12,17 17,20 24,10" fill="none" stroke="#FFFFFF" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="24" cy="10" r="3" fill="#FF6B5C"/></svg><span>Name<span class="wm-teal">Charted</span></span><span class="brand-cc"><span class="brand-cc-dash">—</span> <span class="flag" aria-hidden="true">{FLAG[ACTIVE_CC]}</span> {COUNTRY_NAMES_IN_UI[ACTIVE_CC][ACTIVE_CC]}</span></a>
        <a href="{home_path()}">{S("nav_home")}</a>
        <a href="{p}/names.html">{S("nav_browse")}</a>
        <a href="{p}/trends.html">{S("nav_trends")}</a>
        <a href="{p}/decades.html">{S("nav_decades")}</a>
        <a href="{p}/year/{LATEST_YEAR}.html">{S("nav_rankings", year=LATEST_YEAR)}</a>
        <a href="{p}/compare.html">{S("nav_compare")}</a>
        <a href="{p}/works-with.html">{S("nav_works_with")}</a>
        <a href="{p}/picker.html">{S("nav_picker")}</a>
        <a href="{p}/sibling.html">{S("nav_sibling")}</a>
        <a href="{p}/origins.html">{S("nav_origins")}</a>
        <a href="{p}/favorites.html">{S("nav_favorites")}<span class="fav-nav-count"></span></a>
        {country_switcher_html()}
    </div></div>"""


def footer_html() -> str:
    return f"""
        <div class="footer">
            <p>&copy; 2026 NameCharted</p>
            <p style="font-size:0.75rem; color:#8a93a3; margin-top:0.25rem;">{S("footer_data", source=data_source_full(), range=DATA_RANGE)}</p>
        </div>"""


def page(title, body, description="", canonical="", extra_head=""):
    desc_tag = f'\n    <meta name="description" content="{description}">' if description else ""
    canon_tag = f'\n    <link rel="canonical" href="{canonical}">' if canonical else ""
    og = ""
    if description:
        # Country flag in social-card title — cheapest way to make /fr/ vs /
        # vs /uk/ vs /au/ visually distinct in Twitter/Facebook previews,
        # without needing four separate OG images.
        og_title = f"{FLAG[ACTIVE_CC]} {title}"
        og = (
            f'\n    <meta property="og:title" content="{og_title}">'
            f'\n    <meta property="og:description" content="{description}">'
            f'\n    <meta property="og:type" content="website">'
            f'\n    <meta property="og:site_name" content="NameCharted">'
            f'\n    <meta property="og:locale" content="{"fr_FR" if ACTIVE_CC == "FR" else ("en_GB" if ACTIVE_CC == "GB" else ("en_AU" if ACTIVE_CC == "AU" else "en_US"))}">'
            f'\n    <meta property="og:image" content="{BASE_URL}/og-default.png">'
            f'\n    <meta property="og:image:width" content="1200">'
            f'\n    <meta property="og:image:height" content="630">'
            f'\n    <meta name="twitter:card" content="summary_large_image">'
            f'\n    <meta name="twitter:title" content="{og_title}">'
            f'\n    <meta name="twitter:description" content="{description}">'
            f'\n    <meta name="twitter:image" content="{BASE_URL}/og-default.png">'
        )
        if canonical:
            og += f'\n    <meta property="og:url" content="{canonical}">'
    return f"""<!DOCTYPE html>
<html lang="{lang_attr()}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>{desc_tag}{canon_tag}{og}
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <meta name="theme-color" content="#149E91">
    <style>{BASE_CSS}</style>{extra_head}
</head>
<body>{site_nav_html()}
    <div class="container">
{body}
{footer_html()}
    </div>{lang_banner_script()}{favorites_script()}{compare_script()}{works_with_script()}{picker_script()}{sibling_script()}
</body>
</html>"""


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
    body = f"""        <h1>NameCharted — <span class="h1-flag" aria-hidden="true">{FLAG[ACTIVE_CC]}</span> {COUNTRY_NAME[ACTIVE_CC]}</h1>
        <p style="color:#5B6678; font-size:1.05rem; margin-top:-0.25rem;">{S("home_tagline")}</p>
        <p>{S("home_intro", range=DATA_RANGE)}</p>
{homepage_cc_callout()}
        <div class="search-box" style="margin:2rem 0; text-align:center;">
            <input type="text" id="searchInput" placeholder="{S("home_search_placeholder")}"
                   style="padding:0.75rem; width:70%; max-width:400px; border:1px solid #ddd; border-radius:4px; font-size:1rem;">
            <p>{S("home_try", samples=samples)}
            <a href="{p}/names.html">{S("home_browse_link", n=fmt(n_pages))}</a></p>
        </div>

        <div class="trending">
            <h2 style="color:#149E91; border-bottom:2px solid #EEF2F4; padding-bottom:0.5rem;">{S("home_top_h2")}</h2>
            <ul class="trending-list" style="display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:1rem; list-style:none; padding:0;">
{items}            </ul>
        </div>

        <script>
        var PAGE_SLUGS = null;
        var SSA_SLUGS = null;
        function loadIndex() {{
            if (PAGE_SLUGS) return Promise.resolve();
            return fetch('{p}/name-index.json').then(function(r) {{ return r.json(); }})
                .then(function(d) {{ PAGE_SLUGS = new Set(d.pages); SSA_SLUGS = new Set(d.ssa); }});
        }}
        function route(slug) {{
            if (!slug) return;
            if (PAGE_SLUGS.has(slug)) {{ window.location.href = '{p}/name/' + slug + '.html'; return; }}
            if (SSA_SLUGS.has(slug)) {{ window.location.href = '{p}/rare-names.html?q=' + encodeURIComponent(slug); return; }}
            window.location.href = '/404.html';
        }}
        document.getElementById('searchInput').addEventListener('keypress', function(e) {{
            if (e.key === 'Enter') {{
                var slug = this.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
                if (!slug) return;
                loadIndex().then(function() {{ route(slug); }});
            }}
        }});
        loadIndex();
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
    extra_head = breadcrumb_jsonld([
        (S("crumb_home"), home_url()),
        (S("crumb_names"), f"{BASE_URL}{p}/names.html"),
        (name, canonical),
    ]) + chart_js + hreflang_for_name(slugify(name))

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
    famous = enrich.get('famous', [])
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
                bits.append(S("name_famous_born", year=born))
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
        <p style="color:#7f8c8d; margin-top:-0.5rem;">{S("name_primarily", singular=loc_singular(dom), of_singular=singular_of)} &middot; {gender_text}</p>{variants_line}
        {origin_badge_html}

        <div class="insight">{insight}</div>

        <div class="stats">
            <div class="stat"><div class="stat-value">{fmt(total)}</div><div class="stat-label">{S("stat_total")}</div></div>
            <div class="stat"><div class="stat-value">{len(years)}</div><div class="stat-label">{S("stat_years")}</div></div>
            <div class="stat"><div class="stat-value">{fmt(peak)}</div><div class="stat-label">{S("stat_peak")}</div></div>
        </div>

        <h2>{S("name_popularity_h2", label_cap=loc_label_cap(dom))}</h2>
        <div class="chart-wrap"><canvas id="trendChart" height="120"></canvas></div>

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
    (OUT_DIR / 'name' / f'{slugify(name)}.html').write_text(
        page(S("name_title", name=name), body,
             description=desc, canonical=canonical, extra_head=extra_head),
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
        ranked = sorted(rank_by_year_sex[(year, sex)].items(), key=lambda x: x[1])[:50]
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
    top_girl = sorted(rank_by_year_sex[(year, 'F')].items(), key=lambda x: x[1])[0][0]
    top_boy = sorted(rank_by_year_sex[(year, 'M')].items(), key=lambda x: x[1])[0][0]
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {year}</div>
        <nav class="nav">{prev_link} &nbsp; {next_link}</nav>
        <h1>{S("year_h1", year=year)}</h1>
        <p>{S("year_intro", year=year, g=top_girl, b=top_boy, source=data_source_label())}</p>
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
    extra_head = breadcrumb_jsonld([
        (S("crumb_home"), home_url()),
        (str(year), canonical),
    ]) + hreflang_for_year(year)
    (OUT_DIR / 'year' / f'{year}.html').write_text(
        page(S("year_title", year=year), body,
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
    extra_head = breadcrumb_jsonld([
        (S("crumb_home"), home_url()),
        (S("crumb_decades"), f"{BASE_URL}{p}/decades.html"),
        (label, canonical),
    ]) + hreflang_for_decade(decade)
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
    pages = sorted({slugify(n) for n in pages_to_generate})
    ssa = sorted({slugify(n) for n in name_total if n not in HAS_PAGE})
    (OUT_DIR / 'name-index.json').write_text(
        json.dumps({"pages": pages, "ssa": ssa}, separators=(',', ':')),
        encoding='utf-8')


def generate_name_meta_json():
    """Compact per-name metadata feeding works-with-surname (6c) and future
    picker/sibling tools (6e/6f). Array form keeps the file small enough to
    fetch lazily on the client (~150–250 KB per country, gzip ~50 KB)."""
    out: dict[str, list] = {}
    for n in pages_to_generate:
        m = name_meta[n]
        out[slugify(n)] = [m['first'], m['last2'], m['syll'], m['dom'], m['peak_dec'],
                            m['latest_rank'] or 0]
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
                <span class="ac-wrap">
                    <input type="text" id="sib-input" placeholder="{S("sibling_input")}" aria-label="{S("sibling_input")}">
                    <div id="sib-ac" style="display:none;"></div>
                </span>
                <button type="submit">{S("sibling_go")}</button>
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
# Single 404 (country-neutral), single sitemap + robots (root)
# ---------------------------------------------------------------------------
def generate_favorites_page():
    """Empty shell — JS fills in the list from localStorage on load.
    noindex so search engines don't try to crawl an empty page."""
    p = PREFIX
    body = f"""        <div class="breadcrumb"><a href="{home_path()}">{S("crumb_home")}</a> &rsaquo; {S("nav_favorites")}</div>
        <h1>{S("fav_h1")}</h1>
        <p>{S("fav_intro")}</p>
        <div class="fav-actions" id="fav-actions" style="display:none;">
            <button class="fav-share-btn" id="fav-share">{S("fav_share_btn")}</button>
            <span class="fav-share-done" id="fav-share-done" style="display:none;">{S("fav_share_done")}</span>
        </div>
        <p id="fav-empty">{S("fav_empty")}</p>
        <ul class="fav-list" id="fav-list" style="display:none;"></ul>"""
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
            f"{BASE_URL}{p}/sibling.html"]
    if cc in ORIGIN_TO_NAMES_BY_CC and ORIGIN_TO_NAMES_BY_CC[cc]:
        urls.append(f"{BASE_URL}{p}/origins.html")
        urls += [f"{BASE_URL}{p}/origin/{o}.html"
                 for o in ORIGIN_TO_NAMES_BY_CC[cc]]
    urls += [f"{BASE_URL}{p}/name/{slugify(n)}.html" for n in pages_to_generate_by_country[cc]]
    urls += [f"{BASE_URL}{p}/similar/{slugify(n)}.html" for n in pages_to_generate_by_country[cc]]
    urls += [f"{BASE_URL}{p}/year/{y}.html" for y in years_by_country[cc]]
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
def run_generators_for_active(compare_files_out: list[str]) -> None:
    cc = ACTIVE_CC
    print(f"--- Generating [{cc}] tree ({PREFIX or '/'}) ---")
    generate_homepage()
    generate_browse_index()
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

    if cc == 'US':
        print("  compare pages (top 5 names)…")
        top5 = [name for name, _ in top_names[:5]]
        for i in range(len(top5)):
            for j in range(i + 1, len(top5)):
                generate_comparison_page(top5[i], top5[j])
                compare_files_out.append(f'{slugify(top5[i])}-vs-{slugify(top5[j])}.html')

    generate_rare_names_page()
    generate_favorites_page()
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
    generate_name_index_json()
    generate_name_meta_json()


def main():
    load_enrichment()
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
