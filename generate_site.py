#!/usr/bin/env python3
"""
Generate static site for baby names analytics.

Data: U.S. Social Security Administration national data (yob<year>.txt),
one row per (name, sex, count) per year. NOTE: each name can appear twice in a
year file -- once for F and once for M -- so counts MUST be tracked per sex and
summed, never overwritten.
"""
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

print(f"Total unique names: {len(name_total):,}")
print(f"Top {len(top_names)} all-time + yearly top-50 = {len(pages_to_generate)} name pages.")

# ---------------------------------------------------------------------------
# Shared markup
# ---------------------------------------------------------------------------
BASE_CSS = """
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
            margin: 0;
            padding: 2rem;
            background-color: #f5f5f5;
            color: #333;
        }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { color: #2c3e50; }
        .nav { margin-bottom: 2rem; }
        .nav a { color: #3498db; text-decoration: none; }
        .nav a:hover { text-decoration: underline; }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        .stat {
            background: #fff; padding: 1.5rem; border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1); text-align: center;
        }
        .stat-value { font-size: 2rem; font-weight: bold; color: #2c3e50; }
        .stat-label { color: #7f8c8d; margin-top: 0.5rem; }
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
        .footer { text-align: center; margin-top: 3rem; color: #7f8c8d; font-size: 0.9rem; }
        a { color: #3498db; }
"""

FOOTER = f"""
        <div class="footer">
            <p>Data source: U.S. Social Security Administration national data ({DATA_RANGE}).</p>
            <p>&copy; 2026 Baby Names Analytics</p>
        </div>"""


def page(title, body):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>{BASE_CSS}</style>
</head>
<body>
    <div class="container">
{body}
{FOOTER}
    </div>
</body>
</html>"""


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
        <p>Explore the popularity and trends of U.S. baby names from {DATA_RANGE}.</p>

        <div class="search-box" style="margin:2rem 0; text-align:center;">
            <input type="text" id="searchInput" placeholder="Enter a name to explore..."
                   style="padding:0.75rem; width:70%; max-width:400px; border:1px solid #ddd; border-radius:4px; font-size:1rem;">
            <p>Try names like Olivia, Liam, Emma, Noah, James (top {len(top_names)} all-time)</p>
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
    (OUTPUT_DIR / 'index.html').write_text(page("Baby Names Analytics", body), encoding='utf-8')


# ---------------------------------------------------------------------------
# Name page
# ---------------------------------------------------------------------------
def generate_name_page(name):
    dom = dominant_sex(name)
    series = counts[name][dom]                 # {year: count} for the dominant sex
    years = sorted(series.keys())
    ft = name_sex_total[(name, 'F')]
    mt = name_sex_total[(name, 'M')]
    total = ft + mt
    peak = max(series.values()) if series else 0
    f_pct = round(100 * ft / total) if total else 0

    if ft and mt and min(ft, mt) / total >= 0.10:
        gender_text = f"Unisex — {f_pct}% girls / {100 - f_pct}% boys"
    else:
        gender_text = f"{f_pct}% girls" if dom == 'F' else f"{100 - f_pct}% boys"

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

    body = f"""        <nav class="nav"><a href="/">← Back to all names</a></nav>
        <h1>{name}</h1>
        <p style="color:#7f8c8d; margin-top:-0.5rem;">Primarily a {sex_label(dom)[:-1]}'s name &middot; {gender_text}</p>

        <div class="stats">
            <div class="stat"><div class="stat-value">{total:,}</div><div class="stat-label">Total babies (all years)</div></div>
            <div class="stat"><div class="stat-value">{len(years)}</div><div class="stat-label">Years in the data</div></div>
            <div class="stat"><div class="stat-value">{peak:,}</div><div class="stat-label">Peak in a single year</div></div>
        </div>

        <h2>Popularity Over Time — {sex_label(dom).capitalize()}</h2>
        <p style="color:#7f8c8d; font-size:0.9rem;">Rank is among all {sex_label(dom)}' names registered that year.</p>
        <table>
            <thead><tr>
                <th class="year-column">Year</th>
                <th class="count-column">Babies</th>
                <th class="rank-column">Rank</th>
            </tr></thead>
            <tbody>
{rows}            </tbody>
        </table>"""
    (OUTPUT_DIR / 'name' / f'{slugify(name)}.html').write_text(
        page(f"{name} - Baby Name Popularity & Trends", body), encoding='utf-8')


# ---------------------------------------------------------------------------
# Year page  (separate top-50 girls and boys lists = conventional + correct)
# ---------------------------------------------------------------------------
def generate_year_page(year):
    def table_for(sex):
        ranked = sorted(rank_by_year_sex[(year, sex)].items(), key=lambda x: x[1])[:50]
        rows = ""
        for name, rank in ranked:
            c = counts[name][sex][year]
            rows += (
                f'                <tr><td class="rank-column">{rank}</td>'
                f'<td><a href="/name/{slugify(name)}.html">{name}</a></td>'
                f'<td class="count-column">{c:,}</td></tr>\n'
            )
        return rows

    prev_link = f'<a href="/year/{year-1}.html">← {year-1}</a>' if (year - 1) in YEARS else ''
    next_link = f'<a href="/year/{year+1}.html">{year+1} →</a>' if (year + 1) in YEARS else ''
    body = f"""        <nav class="nav"><a href="/">← All names</a> &nbsp; {prev_link} &nbsp; {next_link}</nav>
        <h1>Top Baby Names of {year}</h1>
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
    (OUTPUT_DIR / 'year' / f'{year}.html').write_text(
        page(f"Top Baby Names of {year} - Rankings & Counts", body), encoding='utf-8')


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
    body = f"""        <nav class="nav"><a href="/">← Back to all names</a></nav>
        <h1>{name1} vs {name2}</h1>
        <div style="display:flex; gap:2rem; flex-wrap:wrap;">
            <div style="flex:1; min-width:280px;">
                <h2 style="color:#3498db;">{name1} <span style="font-size:0.7em; color:#7f8c8d;">({sex_label(dom1)})</span></h2>
                <table><thead><tr><th class="year-column">Year</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{rows1}                </tbody></table>
            </div>
            <div style="flex:1; min-width:280px;">
                <h2 style="color:#3498db;">{name2} <span style="font-size:0.7em; color:#7f8c8d;">({sex_label(dom2)})</span></h2>
                <table><thead><tr><th class="year-column">Year</th><th class="count-column">Babies</th></tr></thead>
                <tbody>
{rows2}                </tbody></table>
            </div>
        </div>"""
    fname = f'{slugify(name1)}-vs-{slugify(name2)}.html'
    (OUTPUT_DIR / 'compare' / fname).write_text(
        page(f"{name1} vs {name2} - Baby Name Comparison", body), encoding='utf-8')


# ---------------------------------------------------------------------------
def main():
    print("Generating homepage...")
    generate_homepage()

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
    for i in range(len(top5)):
        for j in range(i + 1, len(top5)):
            generate_comparison_page(top5[i], top5[j])

    print("Done!")


if __name__ == '__main__':
    main()
