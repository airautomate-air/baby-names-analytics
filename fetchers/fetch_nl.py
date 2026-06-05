"""Fetch Dutch national name data from Meertens Voornamenbank (NVB).

NVB has no bulk download and no JSON API, so we scrape two layers:

  1. **List** — for each starting letter A..Z, walk
     /naam/begintmet/{L} then /naam/pagina{N}/begintmet/{L} pages
     until the "next page" link disappears. Each row gives a name and the
     aggregated (man, vrouw) totals across eerste + volg name usage.

  2. **Detail** — per name, fetch
     /populariteit/absoluut/{man|vrouw}/eerstenaam/{Name}
     for whichever sex has a non-zero list total. Inside the page,
     the histogram script embeds plain JS arrays:

        var year_list = new Array(1880,1881,…,2017);
        var value_list = new Array(0,0,…,317,0,0,0);   // BRP actuals
        var approximation_list = new Array(4.43,4.41,…); // pre-1880 est.

     We use value_list (modern actuals) and ignore pre-1880 approximations
     so the data is comparable to the other countries.

Polite spacing (~0.5s) is applied. Both layers cache to data/raw/nl/.
First names only (eerstenaam) — volgnaam is the "follow-up name" slot in
Dutch composite names and isn't directly comparable to the first-name
rankings of other countries.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import RAW_DIR, write_normalized

BASE = "https://nvb.meertens.knaw.nl"
RAW_NL = RAW_DIR / 'nl'
RAW_DETAILS = RAW_NL / 'details'
INDEX_FILE = RAW_NL / '_index.json'
HEADERS = {'User-Agent': 'Mozilla/5.0 (NameCharted research scraper)'}
SLEEP_LIST = 0.2
SLEEP_DETAIL = 0.15
DETAIL_WORKERS = 6  # modest concurrency; server tolerates this fine
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Stop pagination if N consecutive pages add zero new names. Defends
# against the "next" link continuing past the end of the dataset.
PAGINATION_MAX_DRY = 2


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode('utf-8', errors='replace')


_ROW_RE = re.compile(
    r"<tr class=\"data\"[^>]*>\s*"
    r"<td><a href=\"/populariteit/naam/([^\"]+)\">([^<]+)</a></td>.*?"
    r"<td class=\"number\">(-?\d+)</td>\s*"
    r"<td class=\"number\">(-?\d+)</td>",
    re.DOTALL,
)
_NEXT_RE = re.compile(r"/naam/pagina(\d+)/begintmet/")


def _parse_list_page(html: str) -> list[tuple[str, str, int, int]]:
    """Return list of (name, url_slug, total_m, total_f) on this page."""
    out = []
    for m in _ROW_RE.finditer(html):
        slug = m.group(1)
        name = m.group(2).strip()
        try:
            tm = int(m.group(3))
            tf = int(m.group(4))
        except ValueError:
            continue
        out.append((name, slug, tm, tf))
    return out


def crawl_list() -> list[dict]:
    """Walk every letter's pagination, deduplicate, return the index."""
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding='utf-8'))

    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    seen: dict[str, dict] = {}
    for letter in LETTERS:
        page = 1
        dry = 0
        while True:
            url = (
                f"{BASE}/naam/begintmet/{letter}"
                if page == 1
                else f"{BASE}/naam/pagina{page}/begintmet/{letter}"
            )
            html = _get(url)
            rows = _parse_list_page(html)
            added = 0
            for name, slug, tm, tf in rows:
                key = slug
                if key in seen:
                    continue
                seen[key] = {
                    'name': name,
                    'slug': slug,
                    'total_m': tm,
                    'total_f': tf,
                }
                added += 1
            # If the page links to a "next", continue — but stop after
            # PAGINATION_MAX_DRY pages add zero new names (recycled tail).
            has_next = bool(_NEXT_RE.search(html))
            dry = dry + 1 if added == 0 else 0
            print(f"  [NL] {letter} p{page}: {len(rows)} rows, +{added} new "
                  f"(total {len(seen):,})")
            if not has_next or dry >= PAGINATION_MAX_DRY:
                break
            page += 1
            time.sleep(SLEEP_LIST)
        time.sleep(SLEEP_LIST)

    index = sorted(seen.values(), key=lambda d: d['slug'])
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False), encoding='utf-8')
    print(f"  [NL] index: {len(index):,} unique names")
    return index


_YEAR_LIST_RE = re.compile(r"var year_list\s*=\s*new Array\(([^)]*)\)")
_VALUE_LIST_RE = re.compile(r"var value_list\s*=\s*new Array\(([^)]*)\)")


def _parse_detail(html: str) -> list[tuple[int, int]]:
    """Extract (year, count) pairs from the histogram JS arrays."""
    ym = _YEAR_LIST_RE.search(html)
    vm = _VALUE_LIST_RE.search(html)
    if not ym or not vm:
        return []
    try:
        years = [int(x) for x in ym.group(1).split(',') if x.strip()]
        values = [int(float(x)) for x in vm.group(1).split(',') if x.strip()]
    except ValueError:
        return []
    if len(years) != len(values):
        return []
    return [(y, v) for y, v in zip(years, values) if v > 0]


def fetch_detail(slug: str, sex: str) -> list[tuple[int, int]]:
    """sex is 'm' or 'f' → 'man' or 'vrouw' on the NVB."""
    nvb_sex = 'man' if sex == 'm' else 'vrouw'
    cache = RAW_DETAILS / f"{slug}_{sex}.html"
    if cache.exists() and cache.stat().st_size > 500:
        return _parse_detail(cache.read_text(encoding='utf-8'))
    # The URL slug is already URL-encoded in the list-page anchor.
    url = f"{BASE}/populariteit/absoluut/{nvb_sex}/eerstenaam/{slug}"
    try:
        html = _get(url)
    except Exception as e:
        print(f"    [NL] {slug} {sex}: fetch failed ({e})")
        return []
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(html, encoding='utf-8')
    time.sleep(SLEEP_DETAIL)
    return _parse_detail(html)


def rows():
    index = crawl_list()
    # Build the work list (slug, sex, display_name) for every needed fetch.
    work: list[tuple[str, str, str]] = []
    for e in index:
        display = e['name'] or urllib.parse.unquote(e['slug'])
        if e['total_m'] > 0:
            work.append((e['slug'], 'm', display))
        if e['total_f'] > 0:
            work.append((e['slug'], 'f', display))
    print(f"  [NL] scraping {len(work):,} detail pages "
          f"({DETAIL_WORKERS} workers)…")

    done = 0
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        futures = {
            pool.submit(fetch_detail, slug, sex): (slug, sex, display)
            for slug, sex, display in work
        }
        for fut in as_completed(futures):
            slug, sex, display = futures[fut]
            done += 1
            if done % 500 == 0:
                print(f"    [NL] {done:,}/{len(work):,} ({display})")
            try:
                pairs = fut.result()
            except Exception as ex:
                print(f"    [NL] {slug} {sex}: error ({ex})")
                continue
            sexcode = 'M' if sex == 'm' else 'F'
            for year, count in pairs:
                yield (year, sexcode, display, count)


def main():
    print("[NL] Meertens NVB — first-name yearly registrations (BRP, 1880–)")
    RAW_DETAILS.mkdir(parents=True, exist_ok=True)
    write_normalized('NL', rows())


if __name__ == '__main__':
    main()
