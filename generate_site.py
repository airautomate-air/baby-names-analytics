#!/usr/bin/env python3
"""
Generate static site for baby names analytics.

Reads normalized per-country CSVs from data/normalized/<cc>.csv with schema
(country, year, sex, name, count). All derived structures are country-scoped
internally; the active build country is controlled by the COUNTRY constant.
"""
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
COUNTRY = "US"                          # active build country (ISO-2)
DATA_DIR = Path('data/normalized')      # where <cc>.csv files live
OUTPUT_DIR = Path('docs')               # site output (served by Vercel)
TOP_N_NAMES = 1000                      # how many names get their own page
BASE_URL = "https://namecharted.com"

OUTPUT_DIR.mkdir(exist_ok=True)
(OUTPUT_DIR / 'name').mkdir(exist_ok=True)
(OUTPUT_DIR / 'year').mkdir(exist_ok=True)
(OUTPUT_DIR / 'compare').mkdir(exist_ok=True)
(OUTPUT_DIR / 'similar').mkdir(exist_ok=True)
(OUTPUT_DIR / 'decade').mkdir(exist_ok=True)
(OUTPUT_DIR / 'letter').mkdir(exist_ok=True)
(OUTPUT_DIR / 'trends').mkdir(exist_ok=True)


def slugify(name: str) -> str:
    """Consistent URL slug used for every internal link and file name."""
    s = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return s or 'name'


# ---------------------------------------------------------------------------
# Country-scoped data structures. Every dict is keyed by ISO-2 country code so
# the build can support multiple countries; today only COUNTRY is populated.
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

PAGE_MIN_TOTAL = 500


def build_country(cc: str) -> None:
    """Load data/normalized/<cc>.csv and populate every *_by_country dict for cc."""
    csv_path = DATA_DIR / f'{cc.lower()}.csv'
    print(f"Reading {csv_path}...")

    counts = defaultdict(lambda: {'F': {}, 'M': {}})
    per_year_rows: dict[tuple[int, str], list] = defaultdict(list)
    years_seen: set[int] = set()

    with csv_path.open(encoding='utf-8') as f:
        r = csv.reader(f)
        next(r, None)   # header
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
    has_page = set(pages_to_generate)

    print(f"  Total unique names: {len(name_total):,}")
    print(f"  Top {len(top_names_local)} all-time + yearly top-50 = {len(pages_to_generate)} name pages.")

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


build_country(COUNTRY)

# ---------------------------------------------------------------------------
# Active-country aliases — the generator functions below all read these names.
# ---------------------------------------------------------------------------
YEARS = years_by_country[COUNTRY]
YEARS_SET = set(YEARS)
DATA_RANGE = f"{YEARS[0]}–{YEARS[-1]}" if YEARS else ""
LATEST_YEAR = YEARS[-1] if YEARS else 2024
DECADES = decades_by_country[COUNTRY]

counts = counts_by_country[COUNTRY]
rank_by_year_sex = rank_by_year_sex_by_country[COUNTRY]
name_sex_total = name_sex_total_by_country[COUNTRY]
name_total = name_total_by_country[COUNTRY]
pages_to_generate = pages_to_generate_by_country[COUNTRY]
HAS_PAGE = has_page_by_country[COUNTRY]
same_sex_ranked = same_sex_ranked_by_country[COUNTRY]
same_sex_index = same_sex_index_by_country[COUNTRY]
latest_year_ranked = latest_year_ranked_by_country[COUNTRY]
latest_year_index = latest_year_index_by_country[COUNTRY]
by_initial = by_initial_by_country[COUNTRY]
letter_names = letter_names_by_country[COUNTRY]
name_meta = name_meta_by_country[COUNTRY]
decade_sex_counts = decade_sex_counts_by_country[COUNTRY]
top_names = sorted(name_total.items(), key=lambda x: (-x[1], x[0]))[:TOP_N_NAMES]


def dominant_sex(name: str) -> str:
    return 'F' if name_sex_total[(name, 'F')] >= name_sex_total[(name, 'M')] else 'M'


def similar_names(name, k=24):
    """Heuristic name similarity among page-names of the same dominant sex."""
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
        if s >= 4:   # require a real signal, not a single coincidence
            popdiff = abs(name_total[other] - name_total[name])
            scored.append((-s, popdiff, other))
    scored.sort()
    return [o for _, _, o in scored[:k]]


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
"""

FOOTER = f"""
        <div class="footer">
            <p>&copy; 2026 NameCharted</p>
            <p style="font-size:0.75rem; color:#8a93a3; margin-top:0.25rem;">Data: U.S. Social Security Administration ({DATA_RANGE})</p>
        </div>"""

SITE_NAV = f"""
    <div class="sitenav"><div class="sitenav-inner">
        <a class="brand" href="/"><svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true"><rect x="1" y="1" width="30" height="30" rx="7" fill="#149E91"/><polyline points="6,22 12,17 17,20 24,10" fill="none" stroke="#FFFFFF" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="24" cy="10" r="3" fill="#FF6B5C"/></svg><span>Name<span class="wm-teal">Charted</span></span></a>
        <a href="/">Home</a>
        <a href="/names.html">Browse A–Z</a>
        <a href="/trends.html">Trends</a>
        <a href="/decades.html">Decades</a>
        <a href="/year/{LATEST_YEAR}.html">{LATEST_YEAR} Rankings</a>
    </div></div>"""


def page(title, body, description="", canonical="", extra_head=""):
    desc_tag = f'\n    <meta name="description" content="{description}">' if description else ""
    canon_tag = f'\n    <link rel="canonical" href="{canonical}">' if canonical else ""
    og = ""
    if description:
        og = (
            f'\n    <meta property="og:title" content="{title}">'
            f'\n    <meta property="og:description" content="{description}">'
            f'\n    <meta property="og:type" content="website">'
            f'\n    <meta property="og:site_name" content="NameCharted">'
            f'\n    <meta property="og:image" content="{BASE_URL}/og-default.png">'
            f'\n    <meta property="og:image:width" content="1200">'
            f'\n    <meta property="og:image:height" content="630">'
            f'\n    <meta name="twitter:card" content="summary_large_image">'
            f'\n    <meta name="twitter:title" content="{title}">'
            f'\n    <meta name="twitter:description" content="{description}">'
            f'\n    <meta name="twitter:image" content="{BASE_URL}/og-default.png">'
        )
        if canonical:
            og += f'\n    <meta property="og:url" content="{canonical}">'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>{desc_tag}{canon_tag}{og}
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <meta name="theme-color" content="#149E91">
    <style>{BASE_CSS}</style>{extra_head}
</head>
<body>{SITE_NAV}
    <div class="container">
{body}
{FOOTER}
    </div>
</body>
</html>"""


def breadcrumb_jsonld(items):
    """items = [(name, url_or_None), ...]  -> JSON-LD BreadcrumbList script."""
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


def sex_label(sex: str) -> str:
    return 'girls' if sex == 'F' else 'boys'


# ---------------------------------------------------------------------------
# Homepage
# ---------------------------------------------------------------------------
def generate_homepage():
    items = ""
    for name, total in top_names[:20]:
        dom = dominant_sex(name)
        items += (
            f'                <li><a href="/name/{slugify(name)}.html"><h3>{name}</h3></a>'
            f'<p>{total:,} total babies</p><p style="font-size:0.8rem">mostly {sex_label(dom)}</p></li>\n'
        )
    body = f"""        <h1>NameCharted</h1>
        <p style="color:#5B6678; font-size:1.05rem; margin-top:-0.25rem;">Names, charted.</p>
        <p>Explore the popularity and trends of names from {DATA_RANGE}. Search any name
        to see its yearly counts, popularity rank, gender split, and an interactive trend chart.</p>

        <div class="search-box" style="margin:2rem 0; text-align:center;">
            <input type="text" id="searchInput" placeholder="Enter a name to explore..."
                   style="padding:0.75rem; width:70%; max-width:400px; border:1px solid #ddd; border-radius:4px; font-size:1rem;">
            <p>Try names like Olivia, Liam, Emma, Noah, James &middot; or
            <a href="/names.html">browse all {len(pages_to_generate):,} names A–Z</a></p>
        </div>

        <div class="trending">
            <h2 style="color:#149E91; border-bottom:2px solid #EEF2F4; padding-bottom:0.5rem;">Top Names of All Time (by total usage)</h2>
            <ul class="trending-list" style="display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:1rem; list-style:none; padding:0;">
{items}            </ul>
        </div>

        <script>
        var PAGE_SLUGS = null;
        var SSA_SLUGS = null;
        function loadIndex() {{
            if (PAGE_SLUGS) return Promise.resolve();
            return fetch('/name-index.json').then(function(r) {{ return r.json(); }})
                .then(function(d) {{ PAGE_SLUGS = new Set(d.pages); SSA_SLUGS = new Set(d.ssa); }});
        }}
        function route(slug) {{
            if (!slug) return;
            if (PAGE_SLUGS.has(slug)) {{ window.location.href = '/name/' + slug + '.html'; return; }}
            if (SSA_SLUGS.has(slug)) {{ window.location.href = '/rare-names.html?q=' + encodeURIComponent(slug); return; }}
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
    desc = (f"Explore name popularity and trends from {DATA_RANGE}. "
            f"Search {len(pages_to_generate):,}+ names for yearly counts, "
            f"rankings, gender split, and interactive trend charts.")
    (OUTPUT_DIR / 'index.html').write_text(
        page("NameCharted — Name Popularity & Trends", body,
             description=desc, canonical=f"{BASE_URL}/"),
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
    links = "".join(f'<a href="/name/{slugify(n)}.html">{n}</a>' for n in names)
    return f'        <h3 style="margin-bottom:0.25rem;">{label}</h3>\n        <div class="related">{links}</div>\n'


# ---------------------------------------------------------------------------
# Name page
# ---------------------------------------------------------------------------
def generate_name_page(name):
    dom = dominant_sex(name)
    series = counts[name][dom]                 # {year: count} for the dominant sex
    years = sorted(series.keys())
    chart_counts = [series[y] for y in years]
    ft = name_sex_total[(name, 'F')]
    mt = name_sex_total[(name, 'M')]
    total = ft + mt
    peak = max(series.values()) if series else 0
    peak_year = max(series, key=series.get) if series else None
    f_pct = round(100 * ft / total) if total else 0
    label = sex_label(dom)            # 'girls' / 'boys'
    singular = label[:-1]             # 'girl' / 'boy'

    if ft and mt and min(ft, mt) / total >= 0.10:
        gender_text = f"Unisex — {f_pct}% girls / {100 - f_pct}% boys"
    else:
        gender_text = f"{f_pct}% girls" if dom == 'F' else f"{100 - f_pct}% boys"

    # --- data-driven insight prose (truthful, derived from SSA counts) ---
    first_year = years[0] if years else None
    latest_rank = rank_by_year_sex.get((LATEST_YEAR, dom), {}).get(name)
    latest_count = series.get(LATEST_YEAR)
    insight_parts = []
    if first_year is not None:
        insight_parts.append(
            f"<strong>{name}</strong> first appears in the data in "
            f"<strong>{first_year}</strong> and has been recorded in {len(years)} different years "
            f"as a {singular}'s name.")
    if peak_year is not None:
        peak_rank = rank_by_year_sex.get((peak_year, dom), {}).get(name)
        rank_txt = f" (rank #{peak_rank:,} that year)" if peak_rank else ""
        insight_parts.append(
            f"Its single biggest year was <strong>{peak_year}</strong> with "
            f"<strong>{peak:,}</strong> babies{rank_txt}.")
    if latest_count and latest_rank:
        insight_parts.append(
            f"In {LATEST_YEAR} it was given to {latest_count:,} {label} (rank "
            f"<strong>#{latest_rank:,}</strong>).")
    elif latest_count:
        insight_parts.append(f"In {LATEST_YEAR} it was given to {latest_count:,} {label}.")
    else:
        insight_parts.append(f"It was not in the {LATEST_YEAR} data.")

    # rising / declining over the last decade of available data
    recent = [series.get(y, 0) for y in range(LATEST_YEAR - 4, LATEST_YEAR + 1)]
    prior = [series.get(y, 0) for y in range(LATEST_YEAR - 9, LATEST_YEAR - 4)]
    ra, pa = sum(recent) / 5, sum(prior) / 5
    if pa > 0:
        change = (ra - pa) / pa
        if change > 0.15:
            insight_parts.append("The name has been <strong>rising</strong> over the last five years.")
        elif change < -0.15:
            insight_parts.append("The name has been <strong>declining</strong> over the last five years.")
        else:
            insight_parts.append("Its popularity has been <strong>fairly steady</strong> recently.")

    insight = " ".join(insight_parts)

    rows = ""
    for year in years:
        count = series[year]
        rank = rank_by_year_sex.get((year, dom), {}).get(name)
        rank_disp = f"#{rank:,}" if rank else "–"
        rows += (
            f'                <tr><td class="year-column">{year}</td>'
            f'<td class="count-column">{count:,}</td>'
            f'<td class="rank-column">{rank_disp}</td></tr>\n'
        )

    # --- related-name sections ---
    peak_dec = name_meta[name]['peak_dec']
    rel = (
        f'        <p style="margin:0.75rem 0 1.5rem;">'
        f'<a href="/similar/{slugify(name)}.html"><strong>&rarr; See names similar to {name}</strong></a>'
        f' &nbsp;&middot;&nbsp; <a href="/decade/{peak_dec}s.html">Popular names of the {peak_dec}s</a>'
        f' &nbsp;&middot;&nbsp; <a href="/letter/{label}-{name[0].lower()}.html">'
        f'{label.capitalize()} names starting with {name[0].upper()}</a></p>\n'
    )
    rel += related_block("More popular " + label + "' names", related_more_popular(name, dom))
    rel += related_block(f"Names ranked near {name} in {LATEST_YEAR}",
                         related_latest_neighbors(name, dom))
    rel += related_block(f"Other {label}' names starting with {name[0].upper()}",
                         related_same_initial(name, dom))

    # --- Chart.js trend chart (built via concatenation to avoid f-string braces) ---
    canonical = f"{BASE_URL}/name/{slugify(name)}.html"
    chart_id = "trendChart"
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
        "\n            label: 'Babies named " + name.replace("'", "\\'") + " per year (" + label + ")',"
        "\n            data: " + json.dumps(chart_counts) + ","
        "\n            borderColor: '" + ('#149E91' if dom == 'F' else '#FF6B5C') + "',"
        "\n            backgroundColor: '" + ('rgba(20,158,145,0.12)' if dom == 'F' else 'rgba(255,107,92,0.12)') + "',"
        "\n            fill: true, tension: 0.2, pointRadius: 0, borderWidth: 2"
        "\n          }]"
        "\n        },"
        "\n        options: {"
        "\n          responsive: true,"
        "\n          plugins: { legend: { display: true }, tooltip: { mode: 'index', intersect: false } },"
        "\n          scales: { y: { beginAtZero: true, title: { display: true, text: 'Babies per year' } } }"
        "\n        }"
        "\n      });"
        "\n    });"
        "\n    </script>"
    )
    extra_head = breadcrumb_jsonld([
        ("Home", BASE_URL + "/"),
        ("Names", BASE_URL + "/names.html"),
        (name, canonical),
    ]) + chart_js

    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; <a href="/names.html">Names</a> &rsaquo; {name}</div>
        <h1>{name}</h1>
        <p style="color:#7f8c8d; margin-top:-0.5rem;">Primarily a {singular}'s name &middot; {gender_text}</p>

        <div class="insight">{insight}</div>

        <div class="stats">
            <div class="stat"><div class="stat-value">{total:,}</div><div class="stat-label">Total babies (all years)</div></div>
            <div class="stat"><div class="stat-value">{len(years)}</div><div class="stat-label">Years in the data</div></div>
            <div class="stat"><div class="stat-value">{peak:,}</div><div class="stat-label">Peak in a single year</div></div>
        </div>

        <h2>Popularity Over Time — {label.capitalize()}</h2>
        <div class="chart-wrap"><canvas id="trendChart" height="120"></canvas></div>

{rel}
        <h2>Year-by-Year Detail</h2>
        <p style="color:#7f8c8d; font-size:0.9rem;">Rank is among all {label}' names registered that year.</p>
        <table>
            <thead><tr>
                <th class="year-column">Year</th>
                <th class="count-column">Babies</th>
                <th class="rank-column">Rank</th>
            </tr></thead>
            <tbody>
{rows}            </tbody>
        </table>"""

    desc = (f"{name} name popularity: {total:,} births recorded {DATA_RANGE}, "
            f"peaking in {peak_year} with {peak:,}. See yearly counts, rank, gender split and "
            f"an interactive trend chart.")
    (OUTPUT_DIR / 'name' / f'{slugify(name)}.html').write_text(
        page(f"{name} — Baby Name Popularity & Trends", body,
             description=desc, canonical=canonical, extra_head=extra_head),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# A-Z browse index
# ---------------------------------------------------------------------------
def generate_browse_index():
    letters = sorted(by_initial.keys())
    jump = '<div class="azindex">' + "".join(
        f'<a href="#letter-{l}">{l}</a>' for l in letters) + '</div>'
    sections = ""
    for l in letters:
        names = by_initial[l]
        links = "".join(
            f'<a href="/name/{slugify(n)}.html">{n}</a>' for n in names)
        sections += (
            f'        <h2 id="letter-{l}" style="border-bottom:2px solid #EEF2F4; padding-bottom:0.3rem;">{l} '
            f'<span style="font-size:0.6em; color:#7f8c8d;">({len(names)})</span></h2>\n'
            f'        <div class="related">{links}</div>\n')
    girl_letters = "".join(
        f'<a href="/letter/girls-{l.lower()}.html">{l}</a>'
        for l in sorted(letter_names['F'].keys()))
    boy_letters = "".join(
        f'<a href="/letter/boys-{l.lower()}.html">{l}</a>'
        for l in sorted(letter_names['M'].keys()))
    explore = f"""        <div style="background:#fff; padding:1.25rem 1.5rem; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.06); margin-bottom:2rem;">
            <h2 style="margin-top:0;">Explore by theme</h2>
            <p style="margin:0.25rem 0;"><a href="/trends.html"><strong>Trends</strong></a> &middot;
            <a href="/trends/rising.html">Rising names</a> &middot;
            <a href="/trends/falling.html">Falling names</a> &middot;
            <a href="/decades.html"><strong>Names by decade</strong></a></p>
            <p style="margin:0.75rem 0 0.25rem;"><strong>Girls' names by letter:</strong></p>
            <div class="azindex">{girl_letters}</div>
            <p style="margin:0.5rem 0 0.25rem;"><strong>Boys' names by letter:</strong></p>
            <div class="azindex">{boy_letters}</div>
        </div>"""
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; Browse A–Z</div>
        <h1>Browse All Names A–Z</h1>
        <p>All {len(pages_to_generate):,} names with a dedicated popularity page, grouped by first letter.
        Looking for a rarer name? See the full <a href="/rare-names.html">A–Z index of rare names</a>.</p>
{explore}
        <h2>All names</h2>
        {jump}
{sections}"""
    desc = (f"Browse all {len(pages_to_generate):,} names A–Z. Click any name for "
            f"popularity trends, rankings and yearly counts from {DATA_RANGE}.")
    (OUTPUT_DIR / 'names.html').write_text(
        page("Browse All Names A–Z", body,
             description=desc, canonical=f"{BASE_URL}/names.html"),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Year page  (separate top-50 girls and boys lists = conventional + correct)
# ---------------------------------------------------------------------------
def generate_year_page(year):
    def table_for(sex):
        ranked = sorted(rank_by_year_sex[(year, sex)].items(), key=lambda x: x[1])[:50]
        rows = ""
        for name, rank in ranked:
            c = counts[name][sex][year]
            link = (f'<a href="/name/{slugify(name)}.html">{name}</a>'
                    if name in HAS_PAGE else name)
            rows += (
                f'                <tr><td class="rank-column">{rank}</td>'
                f'<td>{link}</td>'
                f'<td class="count-column">{c:,}</td></tr>\n'
            )
        return rows

    prev_link = f'<a href="/year/{year-1}.html">← {year-1}</a>' if (year - 1) in YEARS else ''
    next_link = f'<a href="/year/{year+1}.html">{year+1} →</a>' if (year + 1) in YEARS else ''
    top_girl = sorted(rank_by_year_sex[(year, 'F')].items(), key=lambda x: x[1])[0][0]
    top_boy = sorted(rank_by_year_sex[(year, 'M')].items(), key=lambda x: x[1])[0][0]
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; {year}</div>
        <nav class="nav">{prev_link} &nbsp; {next_link}</nav>
        <h1>Top Names of {year}</h1>
        <p>The most popular names recorded in {year} were <strong>{top_girl}</strong> for girls
        and <strong>{top_boy}</strong> for boys. Full top-50 lists below, from official SSA data.</p>
        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:2rem;">
            <div>
                <h2 style="color:#149E91;">Girls</h2>
                <table><thead><tr><th class="rank-column">#</th><th>Name</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{table_for('F')}                </tbody></table>
            </div>
            <div>
                <h2 style="color:#FF6B5C;">Boys</h2>
                <table><thead><tr><th class="rank-column">#</th><th>Name</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{table_for('M')}                </tbody></table>
            </div>
        </div>"""
    desc = (f"Top 50 most popular names of {year} for girls and boys, with birth "
            f"counts from official Social Security Administration data. #1: {top_girl} and {top_boy}.")
    extra_head = breadcrumb_jsonld([
        ("Home", BASE_URL + "/"),
        (str(year), f"{BASE_URL}/year/{year}.html"),
    ])
    (OUTPUT_DIR / 'year' / f'{year}.html').write_text(
        page(f"Top Names of {year} — Rankings & Counts", body,
             description=desc, canonical=f"{BASE_URL}/year/{year}.html", extra_head=extra_head),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Comparison page
# ---------------------------------------------------------------------------
def generate_comparison_page(name1, name2):
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
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; Compare</div>
        <h1>{name1} vs {name2}</h1>
        <p>Side-by-side popularity comparison of <strong>{name1}</strong> and
        <strong>{name2}</strong> year by year ({DATA_RANGE}).</p>
        <div style="display:flex; gap:2rem; flex-wrap:wrap;">
            <div style="flex:1; min-width:280px;">
                <h2 style="color:#149E91;"><a href="/name/{slugify(name1)}.html">{name1}</a> <span style="font-size:0.7em; color:#7f8c8d;">({sex_label(dom1)})</span></h2>
                <table><thead><tr><th class="year-column">Year</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{rows1}                </tbody></table>
            </div>
            <div style="flex:1; min-width:280px;">
                <h2 style="color:#149E91;"><a href="/name/{slugify(name2)}.html">{name2}</a> <span style="font-size:0.7em; color:#7f8c8d;">({sex_label(dom2)})</span></h2>
                <table><thead><tr><th class="year-column">Year</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{rows2}                </tbody></table>
            </div>
        </div>"""
    desc = (f"{name1} vs {name2}: compare name popularity year by year using official "
            f"Social Security data from {DATA_RANGE}.")
    fname = f'{slugify(name1)}-vs-{slugify(name2)}.html'
    (OUTPUT_DIR / 'compare' / fname).write_text(
        page(f"{name1} vs {name2} — Baby Name Comparison", body,
             description=desc, canonical=f"{BASE_URL}/compare/{fname}"),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# "Names like X"  ->  /similar/<slug>.html
# ---------------------------------------------------------------------------
def generate_similar_page(name):
    dom = dominant_sex(name)
    label = sex_label(dom)
    sims = similar_names(name)
    cards = ""
    for n in sims:
        d = dominant_sex(n)
        lr = name_meta[n]['latest_rank']
        rank_txt = f"#{lr:,} in {LATEST_YEAR}" if lr else "rare today"
        cards += (
            f'            <li><a href="/name/{slugify(n)}.html"><h3 style="margin:0;">{n}</h3></a>'
            f'<p style="margin:0.2rem 0; color:#7f8c8d; font-size:0.85rem;">{name_total[n]:,} total &middot; {rank_txt}</p></li>\n'
        )
    canonical = f"{BASE_URL}/similar/{slugify(name)}.html"
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; <a href="/name/{slugify(name)}.html">{name}</a> &rsaquo; Similar names</div>
        <h1>Names Similar to {name}</h1>
        <p>If you like <a href="/name/{slugify(name)}.html"><strong>{name}</strong></a>, here are {len(sims)}
        {label}' names with a similar sound, length, or popularity era — ranked by how close they are.
        Data range: {DATA_RANGE}.</p>
        <ul class="trending-list" style="display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:1rem; list-style:none; padding:0;">
{cards}        </ul>"""
    desc = (f"{len(sims)} names similar to {name} — comparable {label}' names by sound, length and "
            f"popularity. See popularity for each.")
    extra_head = breadcrumb_jsonld([
        ("Home", BASE_URL + "/"),
        (name, f"{BASE_URL}/name/{slugify(name)}.html"),
        ("Similar names", canonical),
    ])
    (OUTPUT_DIR / 'similar' / f'{slugify(name)}.html').write_text(
        page(f"Names Similar to {name} — {label.capitalize()}' Name Ideas", body,
             description=desc, canonical=canonical, extra_head=extra_head),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Decade roundups  ->  /decade/<d>s.html   + hub /decades.html
# ---------------------------------------------------------------------------
def generate_decade_page(decade):
    label = f"{decade}s"
    yrs = [y for y in range(decade, decade + 10) if y in YEARS_SET]
    span = f"{yrs[0]}–{yrs[-1]}" if yrs else label

    def table_for(sex):
        items = sorted(decade_sex_counts[(decade, sex)].items(), key=lambda x: (-x[1], x[0]))[:50]
        rows = ""
        for i, (name, tot) in enumerate(items):
            link = (f'<a href="/name/{slugify(name)}.html">{name}</a>'
                    if name in HAS_PAGE else name)
            rows += (f'                <tr><td class="rank-column">{i+1}</td>'
                     f'<td>{link}</td><td class="count-column">{tot:,}</td></tr>\n')
        top = items[0][0] if items else ''
        return rows, top

    grows, gtop = table_for('F')
    brows, btop = table_for('M')
    idx = DECADES.index(decade)
    prev_link = f'<a href="/decade/{DECADES[idx-1]}s.html">← {DECADES[idx-1]}s</a>' if idx > 0 else ''
    next_link = f'<a href="/decade/{DECADES[idx+1]}s.html">{DECADES[idx+1]}s →</a>' if idx < len(DECADES) - 1 else ''
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; <a href="/decades.html">Decades</a> &rsaquo; {label}</div>
        <nav class="nav">{prev_link} &nbsp; {next_link}</nav>
        <h1>Most Popular Names of the {label}</h1>
        <p>The top names across the {label} ({span}), totaled over the whole decade.
        The decade's #1 names were <strong>{gtop}</strong> for girls and <strong>{btop}</strong> for boys.</p>
        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:2rem;">
            <div>
                <h2 style="color:#149E91;">Girls — Top 50</h2>
                <table><thead><tr><th class="rank-column">#</th><th>Name</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{grows}                </tbody></table>
            </div>
            <div>
                <h2 style="color:#FF6B5C;">Boys — Top 50</h2>
                <table><thead><tr><th class="rank-column">#</th><th>Name</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{brows}                </tbody></table>
            </div>
        </div>"""
    desc = (f"Most popular names of the {label} ({span}). Top 50 girls and boys by total "
            f"births over the decade, from official Social Security data. #1: {gtop} and {btop}.")
    canonical = f"{BASE_URL}/decade/{decade}s.html"
    extra_head = breadcrumb_jsonld([
        ("Home", BASE_URL + "/"),
        ("Decades", BASE_URL + "/decades.html"),
        (label, canonical),
    ])
    (OUTPUT_DIR / 'decade' / f'{decade}s.html').write_text(
        page(f"Most Popular Names of the {label}", body,
             description=desc, canonical=canonical, extra_head=extra_head),
        encoding='utf-8')


def generate_decades_hub():
    links = ""
    for d in reversed(DECADES):
        gtop = sorted(decade_sex_counts[(d, 'F')].items(), key=lambda x: (-x[1], x[0]))
        btop = sorted(decade_sex_counts[(d, 'M')].items(), key=lambda x: (-x[1], x[0]))
        g = gtop[0][0] if gtop else '—'
        b = btop[0][0] if btop else '—'
        links += (f'                <tr><td><a href="/decade/{d}s.html"><strong>{d}s</strong></a></td>'
                  f'<td>{g}</td><td>{b}</td></tr>\n')
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; Decades</div>
        <h1>Names by Decade</h1>
        <p>Explore the most popular names of each decade from the {DECADES[0]}s to the
        {DECADES[-1]}s, based on official Social Security Administration data.</p>
        <table>
            <thead><tr><th>Decade</th><th>#1 Girls' Name</th><th>#1 Boys' Name</th></tr></thead>
            <tbody>
{links}            </tbody>
        </table>"""
    desc = (f"Most popular names by decade, {DECADES[0]}s–{DECADES[-1]}s. See the top girls' "
            f"and boys' names of every decade from official Social Security data.")
    (OUTPUT_DIR / 'decades.html').write_text(
        page("Names by Decade — Top Names of Every Era", body,
             description=desc, canonical=f"{BASE_URL}/decades.html"),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# Trend pages: rising & falling  ->  /trends/rising.html, /trends/falling.html
#   + hub /trends.html
# ---------------------------------------------------------------------------
def _trend_windows(name):
    dom = name_meta[name]['dom']
    series = counts[name][dom]
    recent = sum(series.get(y, 0) for y in range(LATEST_YEAR - 2, LATEST_YEAR + 1)) / 3.0
    older = sum(series.get(y, 0) for y in range(LATEST_YEAR - 7, LATEST_YEAR - 4)) / 3.0
    return recent, older


def _trend_table(rows_data, sex):
    rows = ""
    for name, recent, older, pct in rows_data:
        arrow = "▲" if pct >= 0 else "▼"
        color = "#27ae60" if pct >= 0 else "#c0392b"
        rows += (f'                <tr><td><a href="/name/{slugify(name)}.html">{name}</a></td>'
                 f'<td class="count-column">{round(older):,}</td>'
                 f'<td class="count-column">{round(recent):,}</td>'
                 f'<td class="count-column" style="color:{color}; white-space:nowrap;">{arrow}&nbsp;{abs(pct):.0f}%</td></tr>\n')
    return rows


def generate_trends_pages():
    # Build rising / falling lists per sex among page-names.
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

    head = ('<thead><tr><th>Name</th><th class="count-column">~5 yrs ago</th>'
            f'<th class="count-column">{LATEST_YEAR}</th><th class="count-column">Change</th></tr></thead>')

    def two_col(data, n=30):
        return f"""        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:2rem;">
            <div><h2 style="color:#149E91;">Girls</h2><table>{head}<tbody>
{_trend_table(data['F'][:n], 'F')}            </tbody></table></div>
            <div><h2 style="color:#FF6B5C;">Boys</h2><table>{head}<tbody>
{_trend_table(data['M'][:n], 'M')}            </tbody></table></div>
        </div>"""

    # Rising
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; <a href="/trends.html">Trends</a> &rsaquo; Rising</div>
        <h1>Fastest-Rising Names ({LATEST_YEAR})</h1>
        <p>The {label_join()} names growing fastest in popularity — comparing average births around
        five years ago with the most recent years. Only names with
        meaningful current usage are included.</p>
{two_col(rising)}"""
    (OUTPUT_DIR / 'trends' / 'rising.html').write_text(
        page(f"Fastest-Rising Names of {LATEST_YEAR}", body,
             description=f"The fastest-rising names heading into {LATEST_YEAR}, for girls and "
                         f"boys, based on official Social Security birth data.",
             canonical=f"{BASE_URL}/trends/rising.html",
             extra_head=breadcrumb_jsonld([("Home", BASE_URL + "/"), ("Trends", BASE_URL + "/trends.html"),
                                           ("Rising", BASE_URL + "/trends/rising.html")])),
        encoding='utf-8')

    # Falling
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; <a href="/trends.html">Trends</a> &rsaquo; Falling</div>
        <h1>Fastest-Falling Names ({LATEST_YEAR})</h1>
        <p>Once-common names declining fastest in popularity — comparing average births around five
        years ago with the most recent years.</p>
{two_col(falling)}"""
    (OUTPUT_DIR / 'trends' / 'falling.html').write_text(
        page(f"Fastest-Falling Names of {LATEST_YEAR}", body,
             description=f"Names declining fastest in popularity heading into {LATEST_YEAR}, "
                         f"for girls and boys, from official Social Security birth data.",
             canonical=f"{BASE_URL}/trends/falling.html",
             extra_head=breadcrumb_jsonld([("Home", BASE_URL + "/"), ("Trends", BASE_URL + "/trends.html"),
                                           ("Falling", BASE_URL + "/trends/falling.html")])),
        encoding='utf-8')


def generate_trends_hub():
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; Trends</div>
        <h1>Baby Name Trends</h1>
        <p>Which names are heating up and which are fading? These rankings compare recent birth
        counts with five years earlier ({DATA_RANGE}).</p>
        <div class="stats">
            <a class="stat" style="text-decoration:none;" href="/trends/rising.html">
                <div class="stat-value" style="color:#27ae60;">▲</div>
                <div class="stat-label"><strong>Fastest-Rising Names</strong><br>Biggest gainers of {LATEST_YEAR}</div></a>
            <a class="stat" style="text-decoration:none;" href="/trends/falling.html">
                <div class="stat-value" style="color:#c0392b;">▼</div>
                <div class="stat-label"><strong>Fastest-Falling Names</strong><br>Biggest declines of {LATEST_YEAR}</div></a>
            <a class="stat" style="text-decoration:none;" href="/decades.html">
                <div class="stat-value" style="color:#149E91;">★</div>
                <div class="stat-label"><strong>Names by Decade</strong><br>Top names of every era</div></a>
        </div>"""
    (OUTPUT_DIR / 'trends.html').write_text(
        page("Name Trends — Rising & Falling Names", body,
             description=f"See which names are rising and falling in popularity heading into "
                         f"{LATEST_YEAR}, plus top names by decade, from official Social Security data.",
             canonical=f"{BASE_URL}/trends.html"),
        encoding='utf-8')


def label_join():
    return "girls' and boys'"


# ---------------------------------------------------------------------------
# Letter pages  ->  /letter/<girls|boys>-<a>.html
# ---------------------------------------------------------------------------
def generate_letter_page(sex, letter):
    label = sex_label(sex)
    names = letter_names[sex].get(letter, [])
    rows = ""
    for i, name in enumerate(names):
        lr = rank_by_year_sex.get((LATEST_YEAR, sex), {}).get(name)
        lr_disp = f"#{lr:,}" if lr else "–"
        rows += (f'                <tr><td class="rank-column">{i+1}</td>'
                 f'<td><a href="/name/{slugify(name)}.html">{name}</a></td>'
                 f'<td class="count-column">{name_sex_total[(name, sex)]:,}</td>'
                 f'<td class="rank-column">{lr_disp}</td></tr>\n')
    other_sex = 'M' if sex == 'F' else 'F'
    other_label = sex_label(other_sex)
    cross = (f'<a href="/letter/{other_label}-{letter.lower()}.html">{other_label.capitalize()} names starting with {letter}</a>'
             if letter in letter_names[other_sex] else '')
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; <a href="/names.html">Names</a> &rsaquo; {label.capitalize()} / {letter}</div>
        <h1>{label.capitalize()} Names Starting With {letter}</h1>
        <p>All {len(names)} {label}' names beginning with <strong>{letter}</strong> that have a popularity
        page, ranked by all-time births ({DATA_RANGE}). {('Looking for ' + cross + '?') if cross else ''}</p>
        <table>
            <thead><tr><th class="rank-column">#</th><th>Name</th>
                <th class="count-column">Total babies</th><th class="rank-column">{LATEST_YEAR} rank</th></tr></thead>
            <tbody>
{rows}            </tbody>
        </table>"""
    desc = (f"{label.capitalize()} names that start with {letter}: {len(names)} options ranked by "
            f"popularity, with total births and current rank from official Social Security data.")
    canonical = f"{BASE_URL}/letter/{label}-{letter.lower()}.html"
    extra_head = breadcrumb_jsonld([
        ("Home", BASE_URL + "/"),
        ("Names", BASE_URL + "/names.html"),
        (f"{label.capitalize()} {letter}", canonical),
    ])
    (OUTPUT_DIR / 'letter' / f'{label}-{letter.lower()}.html').write_text(
        page(f"{label.capitalize()} Names Starting With {letter} — Popularity Ranked", body,
             description=desc, canonical=canonical, extra_head=extra_head),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# sitemap.xml + robots.txt
# ---------------------------------------------------------------------------
def generate_sitemap(compare_files):
    urls = [f"{BASE_URL}/", f"{BASE_URL}/names.html",
            f"{BASE_URL}/trends.html", f"{BASE_URL}/decades.html",
            f"{BASE_URL}/trends/rising.html", f"{BASE_URL}/trends/falling.html"]
    urls += [f"{BASE_URL}/name/{slugify(n)}.html" for n in pages_to_generate]
    urls += [f"{BASE_URL}/similar/{slugify(n)}.html" for n in pages_to_generate]
    urls += [f"{BASE_URL}/year/{y}.html" for y in YEARS]
    urls += [f"{BASE_URL}/decade/{d}s.html" for d in DECADES]
    urls += [f"{BASE_URL}/letter/{sex_label(sex)}-{letter.lower()}.html"
             for sex in ('F', 'M') for letter in sorted(letter_names[sex].keys())]
    urls += [f"{BASE_URL}/compare/{f}" for f in compare_files]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n'
    body += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for u in urls:
        body += f'  <url><loc>{u}</loc></url>\n'
    body += '</urlset>\n'
    (OUTPUT_DIR / 'sitemap.xml').write_text(body, encoding='utf-8')

    robots = (
        "User-agent: *\n"
        "Allow: /\n\n"
        f"Sitemap: {BASE_URL}/sitemap.xml\n"
    )
    (OUTPUT_DIR / 'robots.txt').write_text(robots, encoding='utf-8')
    print(f"  sitemap.xml: {len(urls):,} URLs")


# ---------------------------------------------------------------------------
# Rare-names index + branded 404
# ---------------------------------------------------------------------------
def generate_rare_names_page():
    """One page listing every SSA name that does NOT have its own dedicated page,
    grouped A–Z with lifetime totals, so they remain discoverable."""
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
            items.append(
                f'<li id="n-{slugify(n)}" style="break-inside:avoid;">{n} '
                f'<span style="color:#5B6678; font-size:0.85em;">'
                f'({name_total[n]:,} · mostly {sex_label(dom)})</span></li>')
        sections.append(
            f'<details id="letter-{l}" style="margin:1rem 0; background:#fff; '
            f'border:1px solid #EEF2F4; border-radius:8px; padding:1rem 1.25rem;">'
            f'<summary style="cursor:pointer; font-family:\'Poppins\',sans-serif; '
            f'font-weight:600; color:#1B2440;">{l} '
            f'<span style="color:#5B6678; font-weight:400;">({len(by_letter[l]):,} names)</span>'
            f'</summary>'
            f'<ul style="columns:3; column-gap:1.5rem; list-style:none; padding:0; margin:1rem 0 0; font-size:0.92rem;">'
            + ''.join(items) + '</ul></details>')
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; <a href="/names.html">Browse A–Z</a> &rsaquo; Rare names</div>
        <h1>Rare Names — Full A–Z Index</h1>
        <p>These {len(rare):,} names appear in the records ({DATA_RANGE}) but
        have fewer than {PAGE_MIN_TOTAL:,} lifetime births, so they don't yet have their own
        dedicated trend page. They're listed here A–Z with lifetime totals.</p>
        <p style="color:#5B6678; font-size:0.9rem;">Tip: use <kbd>Ctrl</kbd>+<kbd>F</kbd> (or <kbd>⌘</kbd>+<kbd>F</kbd>) to search this page.</p>
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
    desc = (f"Index of {len(rare):,} rare names ({DATA_RANGE}) "
            f"with fewer than {PAGE_MIN_TOTAL} lifetime births, listed A–Z.")
    (OUTPUT_DIR / 'rare-names.html').write_text(
        page("Rare Names — Full A–Z Index", body,
             description=desc, canonical=f"{BASE_URL}/rare-names.html"),
        encoding='utf-8')


def generate_name_index_json():
    """Two slug arrays so the homepage search routes correctly:
       pages = names with a dedicated /name/<slug>.html page
       ssa   = names that exist in SSA data but only appear in /rare-names.html"""
    pages = sorted({slugify(n) for n in pages_to_generate})
    ssa = sorted({slugify(n) for n in name_total if n not in HAS_PAGE})
    (OUTPUT_DIR / 'name-index.json').write_text(
        json.dumps({"pages": pages, "ssa": ssa}, separators=(',', ':')),
        encoding='utf-8')


def generate_404_page():
    body = """        <div style="text-align:center; padding:2rem 0;">
        <h1>Name not found</h1>
        <p style="color:#5B6678; max-width:520px; margin:1rem auto;">
        We couldn't find a dedicated page for that name. NameCharted has full
        trend pages for every name with at least 500 lifetime births. Rarer
        names are listed in our complete A–Z index below.</p>
        <p style="margin-top:2rem;">
            <a href="/rare-names.html" style="display:inline-block; padding:0.75rem 1.5rem; background:#149E91; color:#fff; text-decoration:none; border-radius:6px; font-weight:600;">Browse rare names A–Z</a>
            &nbsp;&nbsp;
            <a href="/" style="display:inline-block; padding:0.75rem 1.5rem; background:#fff; color:#1B2440; border:1px solid #d6dde2; text-decoration:none; border-radius:6px; font-weight:600;">Back to search</a>
        </p>
        </div>"""
    (OUTPUT_DIR / '404.html').write_text(
        page("Name not found — NameCharted", body,
             description="The name you searched isn't in our top-1,000 index. See the full A–Z list of rarer SSA-recorded names."),
        encoding='utf-8')


# ---------------------------------------------------------------------------
def main():
    print("Generating homepage...")
    generate_homepage()

    print("Generating A–Z browse index...")
    generate_browse_index()

    print(f"Generating {len(pages_to_generate)} name pages...")
    for i, name in enumerate(pages_to_generate):
        if i % 200 == 0:
            print(f"  {i} names...")
        generate_name_page(name)

    print(f"Generating {len(pages_to_generate)} 'similar names' pages...")
    for i, name in enumerate(pages_to_generate):
        if i % 200 == 0:
            print(f"  {i} similar...")
        generate_similar_page(name)

    print(f"Generating {len(DECADES)} decade pages + hub...")
    for decade in DECADES:
        generate_decade_page(decade)
    generate_decades_hub()

    print("Generating trend pages (rising/falling) + hub...")
    generate_trends_pages()
    generate_trends_hub()

    print("Generating letter pages...")
    letter_count = 0
    for sex in ('F', 'M'):
        for letter in sorted(letter_names[sex].keys()):
            generate_letter_page(sex, letter)
            letter_count += 1
    print(f"  {letter_count} letter pages.")

    print(f"Generating {len(YEARS)} year pages...")
    for year in YEARS:
        generate_year_page(year)

    print("Generating comparison pages (top 5 names)...")
    top5 = [name for name, _ in top_names[:5]]
    compare_files = []
    for i in range(len(top5)):
        for j in range(i + 1, len(top5)):
            generate_comparison_page(top5[i], top5[j])
            compare_files.append(f'{slugify(top5[i])}-vs-{slugify(top5[j])}.html')

    print("Generating rare-names index + 404...")
    generate_rare_names_page()
    generate_404_page()
    generate_name_index_json()

    print("Generating sitemap.xml + robots.txt...")
    generate_sitemap(compare_files)

    print("Done!")


if __name__ == '__main__':
    main()
