"""Extract grounded facts for US name editorial copy.

Standalone (does not import generate_site.py — that module has module-level
build order side effects). Reads the same source CSV/enrichment files.

Usage:
    python3 editorial_facts.py olivia noah liam        # print facts for names
    python3 editorial_facts.py --rank-range 1 25       # print facts for a
                                                        # popularity-rank slice
                                                        # (current US mixed-sex
                                                        # rank, most recent year)
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

US_CSV = Path('data/normalized/us.csv')
ENRICHMENT = Path('data/normalized/name_enrichment.json')


def load():
    counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # name -> sex -> year -> count
    years = set()
    with open(US_CSV, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['country'] != 'US':
                continue
            name, sex, year, cnt = row['name'], row['sex'], int(row['year']), int(row['count'])
            counts[name][sex][year] += cnt
            years.add(year)
    enrichment = json.load(open(ENRICHMENT, encoding='utf-8')) if ENRICHMENT.exists() else {}
    return counts, sorted(years), enrichment


def facts_for(name, counts, years, enrichment):
    if name not in counts:
        return None
    latest_year = years[-1]
    dom_sex = max(('F', 'M'), key=lambda s: sum(counts[name][s].values()))
    series = {y: counts[name][dom_sex].get(y, 0) for y in years}
    total = sum(sum(counts[name][s].values()) for s in ('F', 'M'))
    peak_year = max(series, key=series.get)
    peak_count = series[peak_year]
    peak_decade = (peak_year // 10) * 10

    # rank trajectory: rank within dominant sex, by year, for years the name appears
    rank_by_year = _rank_series(name, dom_sex, counts, years)

    latest_count = series.get(latest_year, 0)
    latest_rank = rank_by_year.get(latest_year)
    peak_rank = min((r for y, r in rank_by_year.items() if r), default=None)
    peak_rank_year = min(rank_by_year, key=lambda y: (rank_by_year[y] is None, rank_by_year.get(y, 1 << 30))) if rank_by_year else None

    ten_years_ago = latest_year - 10
    count_10y_ago = series.get(ten_years_ago, 0)
    if count_10y_ago > 0:
        pct_change_10y = round((latest_count - count_10y_ago) / count_10y_ago * 100)
    else:
        pct_change_10y = None

    first_year_seen = next((y for y in years if series.get(y, 0) > 0), None)
    decades_active = sorted({(y // 10) * 10 for y in years if series.get(y, 0) > 0})

    e = enrichment.get(name.lower(), {})
    famous = e.get('famous', [])[:5]
    origin = e.get('origin')

    return {
        'name': name.title(),
        'dominant_sex': dom_sex,
        'total_births_all_time': total,
        'peak_year': peak_year,
        'peak_count': peak_count,
        'peak_decade': peak_decade,
        'latest_year': latest_year,
        'latest_count': latest_count,
        'latest_rank': latest_rank,
        'peak_rank': peak_rank,
        'peak_rank_year': peak_rank_year,
        'pct_change_10y': pct_change_10y,
        'first_year_seen': first_year_seen,
        'decades_active': decades_active,
        'origin': origin,
        'famous': [{'name': f['name'], 'occupation': f.get('occupation'), 'born': f.get('born')} for f in famous],
    }


_RANK_CACHE = {}


def _rank_series(name, sex, counts, years):
    key = sex
    if key not in _RANK_CACHE:
        by_year = defaultdict(dict)
        for n, sexes in counts.items():
            for y, c in sexes.get(sex, {}).items():
                by_year[y][n] = c
        ranks = {}
        for y, name_counts in by_year.items():
            ordered = sorted(name_counts.items(), key=lambda x: (-x[1], x[0]))
            ranks[y] = {n: i + 1 for i, (n, _) in enumerate(ordered)}
        _RANK_CACHE[key] = ranks
    ranks_by_year = _RANK_CACHE[key]
    return {y: ranks_by_year.get(y, {}).get(name) for y in years}


def main():
    args = sys.argv[1:]
    counts, years, enrichment = load()

    if args and args[0] == '--rank-range':
        lo, hi = int(args[1]), int(args[2])
        latest_year = years[-1]
        totals = defaultdict(int)
        for n, sexes in counts.items():
            for s in ('F', 'M'):
                totals[n] += sexes.get(s, {}).get(latest_year, 0)
        ordered = sorted(totals.items(), key=lambda x: (-x[1], x[0]))
        names = [n for n, _ in ordered[lo - 1:hi]]
    else:
        names = args

    out = {}
    for n in names:
        key = n if n in counts else n.title() if n.title() in counts else n.capitalize()
        f = facts_for(key, counts, years, enrichment)
        if f is None:
            print(f"WARNING: no data for {n!r}", file=sys.stderr)
            continue
        out[key.lower()] = f

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
