"""Fetch Italian national name data from ISTAT's contanomi web service.

The "Contanomi" tool on istat.it is backed by a JSONP endpoint that returns
the full ranking of first names registered at the anagrafe for each year
since 1999, broken down by sex. We hit it once per (year, gender) and cache
the raw JSON under data/raw/it/. Available years are discovered dynamically
from the type=years call (1999–most recent).

Source: https://www.istat.it/it/dati-analisi-e-prodotti/contenuti-interattivi/contanomi
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import RAW_DIR, write_normalized

WS = "https://www.istat.it/wp-content/themes/EGPbs5-child/contanomi/nati/index2022.php"
RAW_IT = RAW_DIR / 'it'
HEADERS = {'User-Agent': 'Mozilla/5.0'}
# ISTAT's endpoint has a server-side response-size cap that varies by year
# (older / smaller-cohort years return an empty body when too many rows are
# requested). Walk down from the desired ceiling until the server answers.
LIMIT_LADDER = [1000, 500, 300, 200, 150, 100, 50]


def _jsonp_get(params: dict):
    qs = '&'.join(f"{k}={v}" for k, v in params.items()) + '&callback=cb'
    req = urllib.request.Request(f"{WS}?{qs}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode('utf-8')
    # JSONP: cb(...) — strip the wrapper.
    m = re.match(r'[^(]+\((.*)\);?\s*$', body, re.DOTALL)
    if not m:
        raise ValueError(f"unexpected JSONP body: {body[:200]}")
    inner = m.group(1).strip()
    if not inner:
        return None
    return json.loads(inner)


def available_years() -> list[int]:
    return list(_jsonp_get({'type': 'years'}))


def fetch_year(year: int, gender: str) -> list[dict]:
    cache = RAW_IT / f"{year}_{gender}.json"
    if cache.exists() and cache.stat().st_size > 200:
        return json.loads(cache.read_text(encoding='utf-8'))
    data = None
    used = 0
    for lim in LIMIT_LADDER:
        data = _jsonp_get({'type': 'list', 'limit': lim, 'year': year, 'gender': gender})
        time.sleep(0.3)
        if data:
            used = lim
            break
    # Response shape: {"years":[YYYY], "0":[ {year,name,count,gender,percent}, ... ]}
    rows: list[dict] = []
    if not data:
        print(f"    [IT] {year} {gender}: empty response at every limit")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text('[]', encoding='utf-8')
        return rows
    for k, v in data.items():
        if k == 'years':
            continue
        if isinstance(v, list):
            rows.extend(v)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    print(f"    [IT] {year} {gender}: {len(rows)} names @ limit={used}")
    return rows


def rows():
    years = available_years()
    print(f"  [IT] ISTAT years {years[0]}–{years[-1]} ({len(years)} years)")
    for year in years:
        for gender in ('m', 'f'):
            sex = 'M' if gender == 'm' else 'F'
            entries = fetch_year(year, gender)
            for e in entries:
                name = (e.get('name') or '').strip()
                try:
                    count = int(e.get('count') or 0)
                except (TypeError, ValueError):
                    continue
                if not name or count <= 0:
                    continue
                yield (year, sex, name, count)


def main():
    print("[IT] ISTAT Contanomi — anagrafe registrations 1999–latest")
    write_normalized('IT', rows())


if __name__ == '__main__':
    main()
