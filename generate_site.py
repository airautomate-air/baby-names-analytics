#!/usr/bin/env python3
"""
Generate static site for baby names analytics.

Data: U.S. Social Security Administration national data (yob<year>.txt),
one row per (name, sex, count) per year. NOTE: each name can appear twice in a
year file -- once for F and once for M -- so counts MUST be tracked per sex and
summed, never overwritten.
"""
import json
import re
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR = Path('.')          # where the yob<year>.txt files live
OUTPUT_DIR = Path('docs')     # site output (served by Vercel)
YEARS = [y for y in range(1880, 2025) if (DATA_DIR / f'yob{y}.txt').exists()]
TOP_N_NAMES = 1000            # how many names get their own page
DATA_RANGE = f"{YEARS[0]}–{YEARS[-1]}" if YEARS else ""
LATEST_YEAR = YEARS[-1] if YEARS else 2024
BASE_URL = "https://baby-names-analytics.vercel.app"

OUTPUT_DIR.mkdir(exist_ok=True)
(OUTPUT_DIR / 'name').mkdir(exist_ok=True)
(OUTPUT_DIR / 'year').mkdir(exist_ok=True)
(OUTPUT_DIR / 'compare').mkdir(exist_ok=True)


def slugify(name: str) -> str:
    """Consistent URL slug used for every internal link and file name."""
    s = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return s or 'name'


# ---------------------------------------------------------------------------
# Load data  ->  counts[name][sex][year] = count   (summed correctly)
# ---------------------------------------------------------------------------
counts = defaultdict(lambda: {'F': {}, 'M': {}})
rank_by_year_sex = {}   # (year, sex) -> {name: rank}

print("Reading data...")
for year in YEARS:
    rows = {'F': [], 'M': []}
    with open(DATA_DIR / f'yob{year}.txt', 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            name, sex, count = line.split(',')
            count = int(count)
            if sex not in ('F', 'M'):
                continue
            # += guards against any accidental duplicate rows; the key fix is
            # that F and M are stored separately and never overwrite each other.
            counts[name][sex][year] = counts[name][sex].get(year, 0) + count
            rows[sex].append((name, count))
    for sex in ('F', 'M'):
        rows[sex].sort(key=lambda x: (-x[1], x[0]))
        rank_by_year_sex[(year, sex)] = {n: i + 1 for i, (n, _) in enumerate(rows[sex])}
    print(f"  {year}: F={len(rows['F'])}, M={len(rows['M'])}")

# Per-name totals (per sex and combined)
name_sex_total = {}
name_total = {}
for name, d in counts.items():
    ft = sum(d['F'].values())
    mt = sum(d['M'].values())
    name_sex_total[(name, 'F')] = ft
    name_sex_total[(name, 'M')] = mt
    name_total[name] = ft + mt


def dominant_sex(name: str) -> str:
    return 'F' if name_sex_total[(name, 'F')] >= name_sex_total[(name, 'M')] else 'M'


top_names = sorted(name_total.items(), key=lambda x: (-x[1], x[0]))[:TOP_N_NAMES]

# Every name that gets its own page: the all-time top N, PLUS any name that ever
# cracked a yearly top-50 (so year-page links never 404 and trending modern
# names like Isla / Nova get pages even if their all-time total is modest).
pages_to_generate = {name for name, _ in top_names}
for (year, sex), ranks in rank_by_year_sex.items():
    for name, rank in ranks.items():
        if rank <= 50:
            pages_to_generate.add(name)
pages_to_generate = sorted(pages_to_generate, key=lambda n: (-name_total[n], n))
HAS_PAGE = set(pages_to_generate)

print(f"Total unique names: {len(name_total):,}")
print(f"Top {len(top_names)} all-time + yearly top-50 = {len(pages_to_generate)} name pages.")

# ---------------------------------------------------------------------------
# Precompute related-name structures (only names that actually have a page)
# ---------------------------------------------------------------------------
# Names with a page, grouped by dominant sex, sorted by all-time same-sex total.
same_sex_ranked = {'F': [], 'M': []}
for name in pages_to_generate:
    dom = dominant_sex(name)
    same_sex_ranked[dom].append(name)
for sex in ('F', 'M'):
    same_sex_ranked[sex].sort(key=lambda n: (-name_sex_total[(n, sex)], n))
same_sex_index = {
    sex: {n: i for i, n in enumerate(lst)} for sex, lst in same_sex_ranked.items()
}

# Names with a page that ranked in the latest year, by dominant sex, in rank order.
latest_year_ranked = {'F': [], 'M': []}
for sex in ('F', 'M'):
    ranks = rank_by_year_sex.get((LATEST_YEAR, sex), {})
    have = [(n, r) for n, r in ranks.items() if n in HAS_PAGE]
    have.sort(key=lambda x: x[1])
    latest_year_ranked[sex] = have
latest_year_index = {
    sex: {n: i for i, (n, _) in enumerate(lst)} for sex, lst in latest_year_ranked.items()
}

# Names with a page grouped by first letter (for "same initial" + A-Z index).
by_initial = defaultdict(list)
for name in pages_to_generate:
    by_initial[name[0].upper()].append(name)
for letter in by_initial:
    by_initial[letter].sort(key=lambda n: (-name_total[n], n))


# ---------------------------------------------------------------------------
# Shared markup
# ---------------------------------------------------------------------------
BASE_CSS = """
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
            margin: 0;
            padding: 0;
            background-color: #f5f5f5;
            color: #333;
        }
        .container { max-width: 900px; margin: 0 auto; padding: 2rem; }
        h1 { color: #2c3e50; }
        .sitenav {
            background: #2c3e50; padding: 0.9rem 2rem;
        }
        .sitenav-inner { max-width: 900px; margin: 0 auto; display: flex; gap: 1.5rem; align-items: center; flex-wrap: wrap; }
        .sitenav a { color: #ecf0f1; text-decoration: none; font-weight: 500; }
        .sitenav a:hover { color: #fff; text-decoration: underline; }
        .sitenav .brand { font-weight: 700; color: #fff; margin-right: auto; }
        .nav { margin-bottom: 1.5rem; font-size: 0.9rem; }
        .nav a { color: #3498db; text-decoration: none; }
        .nav a:hover { text-decoration: underline; }
        .breadcrumb { font-size: 0.85rem; color: #7f8c8d; margin-bottom: 1rem; }
        .breadcrumb a { color: #3498db; text-decoration: none; }
        .insight {
            background: #fff; border-left: 4px solid #3498db; padding: 1rem 1.25rem;
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
        .stat-value { font-size: 2rem; font-weight: bold; color: #2c3e50; }
        .stat-label { color: #7f8c8d; margin-top: 0.5rem; font-size: 0.9rem; }
        .chart-wrap { background:#fff; padding:1.25rem; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:2rem; }
        table {
            width: 100%; border-collapse: collapse; margin-bottom: 2rem;
            background: #fff; border-radius: 8px; overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        th, td { padding: 0.85rem 1rem; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #ecf0f1; font-weight: 600; }
        tr:hover { background-color: #f8f9fa; }
        .year-column { width: 12%; font-family: monospace; }
        .count-column { width: 18%; text-align: right; }
        .rank-column { width: 15%; text-align: right; }
        .related { display:flex; flex-wrap:wrap; gap:0.5rem; margin:0.5rem 0 1.75rem; }
        .related a {
            background:#fff; border:1px solid #d6dde2; border-radius:20px;
            padding:0.35rem 0.85rem; text-decoration:none; color:#2c3e50; font-size:0.9rem;
        }
        .related a:hover { background:#3498db; color:#fff; border-color:#3498db; }
        .azindex { display:flex; flex-wrap:wrap; gap:0.4rem; margin:1rem 0 2rem; }
        .azindex a { background:#fff; border:1px solid #d6dde2; border-radius:6px; padding:0.4rem 0.7rem; text-decoration:none; color:#2c3e50; font-weight:600; }
        .azindex a:hover { background:#3498db; color:#fff; }
        .footer { text-align: center; margin-top: 3rem; color: #7f8c8d; font-size: 0.9rem; }
        a { color: #3498db; }
"""

FOOTER = f"""
        <div class="footer">
            <p>Data source: U.S. Social Security Administration national data ({DATA_RANGE}).</p>
            <p>&copy; 2026 Baby Names Analytics</p>
        </div>"""

SITE_NAV = """
    <div class="sitenav"><div class="sitenav-inner">
        <a class="brand" href="/">Baby Names Analytics</a>
        <a href="/">Home</a>
        <a href="/names.html">Browse A–Z</a>
        <a href="/year/%d.html">%d Rankings</a>
    </div></div>""" % (LATEST_YEAR, LATEST_YEAR)


def page(title, body, description="", canonical="", extra_head=""):
    desc_tag = f'\n    <meta name="description" content="{description}">' if description else ""
    canon_tag = f'\n    <link rel="canonical" href="{canonical}">' if canonical else ""
    og = ""
    if description:
        og = (
            f'\n    <meta property="og:title" content="{title}">'
            f'\n    <meta property="og:description" content="{description}">'
            f'\n    <meta property="og:type" content="website">'
        )
        if canonical:
            og += f'\n    <meta property="og:url" content="{canonical}">'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>{desc_tag}{canon_tag}{og}
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
    body = f"""        <h1>Baby Names Analytics</h1>
        <p>Explore the popularity and trends of U.S. baby names from {DATA_RANGE}, based on
        official Social Security Administration records. Search any name to see its yearly
        counts, popularity rank, gender split, and an interactive trend chart.</p>

        <div class="search-box" style="margin:2rem 0; text-align:center;">
            <input type="text" id="searchInput" placeholder="Enter a name to explore..."
                   style="padding:0.75rem; width:70%; max-width:400px; border:1px solid #ddd; border-radius:4px; font-size:1rem;">
            <p>Try names like Olivia, Liam, Emma, Noah, James &middot; or
            <a href="/names.html">browse all {len(pages_to_generate):,} names A–Z</a></p>
        </div>

        <div class="trending">
            <h2 style="color:#3498db; border-bottom:2px solid #ecf0f1; padding-bottom:0.5rem;">Top Names of All Time (by total usage)</h2>
            <ul class="trending-list" style="display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:1rem; list-style:none; padding:0;">
{items}            </ul>
        </div>

        <script>
        document.getElementById('searchInput').addEventListener('keypress', function(e) {{
            if (e.key === 'Enter') {{
                var slug = this.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
                if (slug) window.location.href = '/name/' + slug + '.html';
            }}
        }});
        </script>"""
    desc = (f"Explore U.S. baby name popularity and trends from {DATA_RANGE} using official "
            f"Social Security data. Search {len(pages_to_generate):,}+ names for yearly counts, "
            f"rankings, and interactive charts.")
    (OUTPUT_DIR / 'index.html').write_text(
        page("Baby Names Analytics — U.S. Name Popularity & Trends", body,
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
            f"<strong>{name}</strong> first appears in the U.S. data in "
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
    rel = ""
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
        "\n            borderColor: '" + ('#e84393' if dom == 'F' else '#0984e3') + "',"
        "\n            backgroundColor: '" + ('rgba(232,67,147,0.1)' if dom == 'F' else 'rgba(9,132,227,0.1)') + "',"
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

    desc = (f"{name} baby name popularity: {total:,} U.S. babies recorded {DATA_RANGE}, "
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
            f'        <h2 id="letter-{l}" style="border-bottom:2px solid #ecf0f1; padding-bottom:0.3rem;">{l} '
            f'<span style="font-size:0.6em; color:#7f8c8d;">({len(names)})</span></h2>\n'
            f'        <div class="related">{links}</div>\n')
    body = f"""        <div class="breadcrumb"><a href="/">Home</a> &rsaquo; Browse A–Z</div>
        <h1>Browse All Baby Names A–Z</h1>
        <p>All {len(pages_to_generate):,} names with a dedicated popularity page, grouped by first letter.</p>
        {jump}
{sections}"""
    desc = (f"Browse all {len(pages_to_generate):,} U.S. baby names A–Z. Click any name for "
            f"popularity trends, rankings and yearly counts from {DATA_RANGE}.")
    (OUTPUT_DIR / 'names.html').write_text(
        page("Browse All Baby Names A–Z", body,
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
        <h1>Top Baby Names of {year}</h1>
        <p>The most popular U.S. baby names in {year} were <strong>{top_girl}</strong> for girls
        and <strong>{top_boy}</strong> for boys. Full top-50 lists below, from official SSA data.</p>
        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:2rem;">
            <div>
                <h2 style="color:#e84393;">Girls</h2>
                <table><thead><tr><th class="rank-column">#</th><th>Name</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{table_for('F')}                </tbody></table>
            </div>
            <div>
                <h2 style="color:#0984e3;">Boys</h2>
                <table><thead><tr><th class="rank-column">#</th><th>Name</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{table_for('M')}                </tbody></table>
            </div>
        </div>"""
    desc = (f"Top 50 most popular U.S. baby names of {year} for girls and boys, with birth "
            f"counts from official Social Security Administration data. #1: {top_girl} and {top_boy}.")
    extra_head = breadcrumb_jsonld([
        ("Home", BASE_URL + "/"),
        (str(year), f"{BASE_URL}/year/{year}.html"),
    ])
    (OUTPUT_DIR / 'year' / f'{year}.html').write_text(
        page(f"Top Baby Names of {year} — Rankings & Counts", body,
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
        <strong>{name2}</strong> using U.S. Social Security data ({DATA_RANGE}).</p>
        <div style="display:flex; gap:2rem; flex-wrap:wrap;">
            <div style="flex:1; min-width:280px;">
                <h2 style="color:#3498db;"><a href="/name/{slugify(name1)}.html">{name1}</a> <span style="font-size:0.7em; color:#7f8c8d;">({sex_label(dom1)})</span></h2>
                <table><thead><tr><th class="year-column">Year</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{rows1}                </tbody></table>
            </div>
            <div style="flex:1; min-width:280px;">
                <h2 style="color:#3498db;"><a href="/name/{slugify(name2)}.html">{name2}</a> <span style="font-size:0.7em; color:#7f8c8d;">({sex_label(dom2)})</span></h2>
                <table><thead><tr><th class="year-column">Year</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{rows2}                </tbody></table>
            </div>
        </div>"""
    desc = (f"{name1} vs {name2}: compare U.S. baby name popularity year by year using official "
            f"Social Security data from {DATA_RANGE}.")
    fname = f'{slugify(name1)}-vs-{slugify(name2)}.html'
    (OUTPUT_DIR / 'compare' / fname).write_text(
        page(f"{name1} vs {name2} — Baby Name Comparison", body,
             description=desc, canonical=f"{BASE_URL}/compare/{fname}"),
        encoding='utf-8')


# ---------------------------------------------------------------------------
# sitemap.xml + robots.txt
# ---------------------------------------------------------------------------
def generate_sitemap(compare_files):
    urls = [f"{BASE_URL}/", f"{BASE_URL}/names.html"]
    urls += [f"{BASE_URL}/name/{slugify(n)}.html" for n in pages_to_generate]
    urls += [f"{BASE_URL}/year/{y}.html" for y in YEARS]
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

    print("Generating sitemap.xml + robots.txt...")
    generate_sitemap(compare_files)

    print("Done!")


if __name__ == '__main__':
    main()
