"""Shared helpers for country fetchers.

Unified normalized schema (one row per country/year/sex/name):
    country : ISO-2 code (US, FR, GB, AU, ...)
    year    : int
    sex     : 'F' or 'M'
    name    : str, title-cased (Olivia, Jean-Pierre, Mary Beth)
    count   : int, number of registrations
"""
from __future__ import annotations
import csv
import re
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / 'data' / 'raw'
NORMALIZED_DIR = ROOT / 'data' / 'normalized'
NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)

HEADER = ('country', 'year', 'sex', 'name', 'count')


def titlecase_name(raw: str) -> str:
    """Normalize a single name: 'JEAN-PIERRE' -> 'Jean-Pierre', 'mary beth' -> 'Mary Beth'."""
    s = raw.strip()
    if not s:
        return s
    parts = re.split(r"([\-' ])", s.lower())
    return ''.join(p.capitalize() if not re.match(r"[\-' ]", p) else p for p in parts)


def normalize_sex(raw) -> str | None:
    """Map common sex encodings to 'F' or 'M'. Returns None if unrecognized."""
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if s in ('F', 'FEMALE', 'GIRL', 'GIRLS', 'W', 'FEMME', 'F.', '2'):
        return 'F'
    if s in ('M', 'MALE', 'BOY', 'BOYS', 'H', 'HOMME', 'M.', '1'):
        return 'M'
    return None


def write_normalized(country: str, rows: Iterable[tuple[int, str, str, int]]) -> Path:
    """Write a normalized CSV. `rows` yields (year, sex, name, count) tuples for one country.

    Aggregates duplicate (year,sex,name) triples by summing counts. Skips bad rows.
    Returns the output path.
    """
    out = NORMALIZED_DIR / f"{country.lower()}.csv"
    agg: dict[tuple[int, str, str], int] = {}
    skipped = 0
    for row in rows:
        try:
            year, sex, name, count = row
            year = int(year)
            sex = normalize_sex(sex)
            name = titlecase_name(name)
            count = int(count)
            if not sex or not name or count <= 0:
                skipped += 1
                continue
            key = (year, sex, name)
            agg[key] = agg.get(key, 0) + count
        except (TypeError, ValueError):
            skipped += 1
            continue
    with out.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for (year, sex, name), count in sorted(agg.items()):
            w.writerow((country, year, sex, name, count))
    print(f"  [{country}] wrote {len(agg):,} rows -> {out.relative_to(ROOT)}"
          + (f" (skipped {skipped:,} bad rows)" if skipped else ""))
    return out


def read_normalized(country: str) -> Iterator[tuple[str, int, str, str, int]]:
    p = NORMALIZED_DIR / f"{country.lower()}.csv"
    with p.open(encoding='utf-8') as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            yield (row[0], int(row[1]), row[2], row[3], int(row[4]))


def stats(country: str) -> dict:
    rows = list(read_normalized(country))
    if not rows:
        return {'country': country, 'rows': 0}
    years = sorted({r[1] for r in rows})
    by_sex_name: dict[tuple[str, str], int] = {}
    for _, _, sex, name, count in rows:
        k = (sex, name)
        by_sex_name[k] = by_sex_name.get(k, 0) + count
    top_f = sorted(((c, n) for (s, n), c in by_sex_name.items() if s == 'F'), reverse=True)[:3]
    top_m = sorted(((c, n) for (s, n), c in by_sex_name.items() if s == 'M'), reverse=True)[:3]
    return {
        'country': country,
        'rows': len(rows),
        'years': f"{years[0]}–{years[-1]}",
        'unique_names': len({(s, n) for _, _, s, n, _ in rows}),
        'top_girls': [n for _, n in top_f],
        'top_boys': [n for _, n in top_m],
    }
