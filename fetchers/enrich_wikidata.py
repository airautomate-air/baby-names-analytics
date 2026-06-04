#!/usr/bin/env python3
"""Enrich names with origin language (P5219 / P407) and famous bearers
(P735, sorted by sitelink count) from Wikidata.

Resumable: every API hit is cached so re-running picks up where it left off.

Outputs:
  data/cache/wikidata_origins.json   per-name raw origin records
  data/cache/wikidata_famous.json    per-name-Qid famous-people records
  data/normalized/name_enrichment.json   merged, what the site generator reads

Run:
  python3 fetchers/enrich_wikidata.py            # all page-eligible names
  python3 fetchers/enrich_wikidata.py --limit 200
  python3 fetchers/enrich_wikidata.py --names olivia,emma
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NORMALIZED = ROOT / 'data' / 'normalized'
CACHE = ROOT / 'data' / 'cache'
CACHE.mkdir(parents=True, exist_ok=True)

SPARQL = 'https://query.wikidata.org/sparql'
USER_AGENT = 'NameChartedBot/1.0 (https://namecharted.com; air.automate@gmail.com) Python/urllib'

# Roll-up: many historical variants get merged into the modern label so we
# don't fragment "Old English" / "Middle English" into thin pages.
CANONICALISE = {
    'old-english': 'english',
    'middle-english': 'english',
    'old-french': 'french',
    'old-high-german': 'german',
    'middle-high-german': 'german',
    'old-norse': 'scandinavian',
    'ancient-greek': 'greek',
    'koine-greek': 'greek',
    'biblical-hebrew': 'hebrew',
    'scottish-gaelic': 'scottish',
    'classical-arabic': 'arabic',
    'church-slavonic': 'slavic',
    'old-church-slavonic': 'slavic',
    'proto-germanic': 'germanic',
    'proto-indo-european': 'indo-european',
    'late-latin': 'latin',
    'medieval-latin': 'latin',
    'vulgar-latin': 'latin',
    'classical-latin': 'latin',
}

# Origin slugs we will NOT publish pages for (too generic, language-family
# rather than origin, or only useful as internal labels).
SKIP_ORIGIN_SLUGS = {
    'multiple-languages',
    'indo-european',
    'germanic',
    'romance',
    'celtic',
    'slavic',  # too generic — prefer specific (russian, polish, …)
}


def slugify(name: str) -> str:
    folded = unicodedata.normalize('NFD', name.lower())
    folded = ''.join(c for c in folded if unicodedata.category(c) != 'Mn')
    s = re.sub(r'[^a-z0-9]+', '-', folded).strip('-')
    return s or 'name'


def load_cache(path: Path) -> dict:
    if path.exists():
        with path.open() as f:
            return json.load(f)
    return {}


def save_cache(path: Path, data: dict) -> None:
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(data, f, separators=(',', ':'))
    tmp.replace(path)


def sparql(query: str, retries: int = 4) -> dict:
    """POST a SPARQL query and return the parsed JSON. Retries on transient
    errors with exponential backoff."""
    data = urllib.parse.urlencode({'query': query}).encode('utf-8')
    req = urllib.request.Request(
        SPARQL,
        data=data,
        headers={
            'User-Agent': USER_AGENT,
            'Accept': 'application/sparql-results+json',
            'Accept-Encoding': 'gzip',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    )
    backoff = 2.0
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if resp.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                print(f'  SPARQL retry {attempt + 1}: {e}', file=sys.stderr)
                time.sleep(backoff)
                backoff *= 1.8
            else:
                raise
    raise RuntimeError(f'SPARQL failed after {retries} retries: {last_err}')


def page_eligible_names() -> list[str]:
    """Collect names that have a dedicated page (≥ PAGE_MIN_TOTAL lifetime
    births) in any country. This is the universe we want enriched."""
    PAGE_MIN_TOTAL = 500
    total: dict[str, int] = defaultdict(int)
    for cc in ('us', 'fr', 'gb', 'au'):
        p = NORMALIZED / f'{cc}.csv'
        if not p.exists():
            continue
        with p.open() as f:
            r = csv.DictReader(f)
            for row in r:
                total[row['name']] += int(row['count'])
    return sorted({n for n, t in total.items() if t >= PAGE_MIN_TOTAL})


# ---------------------------------------------------------------------------
# Origins
# ---------------------------------------------------------------------------
ORIGIN_QUERY = """
SELECT ?nameLabel ?name ?origin WHERE {
  VALUES ?nameLabel { %(values)s }
  ?name rdfs:label ?nameLabel ;
        wdt:P31/wdt:P279* wd:Q202444 .
  OPTIONAL { ?name wdt:P5219 ?origin }
  OPTIONAL { ?name wdt:P407  ?origin }
  FILTER(LANG(?nameLabel) = "en")
}
"""


def fetch_origins(names: list[str], cache_path: Path) -> dict:
    cache = load_cache(cache_path)
    todo = [n for n in names if n not in cache]
    if not todo:
        print(f'  origins: 0 to fetch ({len(cache)} cached)')
        return cache

    print(f'  origins: {len(todo)} names to fetch ({len(cache)} cached)')
    BATCH = 60
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        values = ' '.join(f'"{n}"@en' for n in batch)
        q = ORIGIN_QUERY % {'values': values}
        try:
            res = sparql(q)
        except Exception as e:
            print(f'  batch {i}: skip ({e})', file=sys.stderr)
            continue
        # Aggregate by name label so we capture multiple Q-IDs / origins per name
        per_name: dict[str, dict] = {n: {'qids': [], 'origin_qids': []} for n in batch}
        for b in res.get('results', {}).get('bindings', []):
            lbl = b.get('nameLabel', {}).get('value', '')
            if lbl not in per_name:
                continue
            qid = b.get('name', {}).get('value', '').rsplit('/', 1)[-1]
            origin = b.get('origin', {}).get('value', '').rsplit('/', 1)[-1] if b.get('origin') else None
            if qid and qid not in per_name[lbl]['qids']:
                per_name[lbl]['qids'].append(qid)
            if origin and origin not in per_name[lbl]['origin_qids']:
                per_name[lbl]['origin_qids'].append(origin)
        for n in batch:
            cache[n] = per_name[n]
        if (i // BATCH) % 5 == 4:
            save_cache(cache_path, cache)
            print(f'    saved at {i + BATCH}/{len(todo)}')
        time.sleep(0.4)  # be polite to the public endpoint
    save_cache(cache_path, cache)
    return cache


# ---------------------------------------------------------------------------
# Famous bearers
# ---------------------------------------------------------------------------
FAMOUS_QUERY = """
SELECT ?nameQid ?person ?personLabel ?occupationLabel ?birth ?article ?sitelinks WHERE {
  VALUES ?nameQid { %(values)s }
  ?person wdt:P735 ?nameQid ;
          wikibase:sitelinks ?sitelinks .
  FILTER(?sitelinks >= 25)
  OPTIONAL { ?person wdt:P106 ?occupation }
  OPTIONAL { ?person wdt:P569 ?birth }
  OPTIONAL { ?article schema:about ?person ;
                       schema:isPartOf <https://en.wikipedia.org/> }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY DESC(?sitelinks)
LIMIT 4000
"""


def fetch_famous(name_qids: list[str], cache_path: Path) -> dict:
    """Query top-known people for each name Q-ID. Returns map qid → list of
    {label, occupation, url, born, sitelinks}, top 5 each."""
    cache = load_cache(cache_path)
    todo = [q for q in name_qids if q not in cache]
    if not todo:
        print(f'  famous: 0 to fetch ({len(cache)} cached)')
        return cache

    print(f'  famous: {len(todo)} name-Qids to fetch ({len(cache)} cached)')
    BATCH = 40
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        values = ' '.join(f'wd:{q}' for q in batch)
        q = FAMOUS_QUERY % {'values': values}
        try:
            res = sparql(q)
        except Exception as e:
            print(f'  batch {i}: skip ({e})', file=sys.stderr)
            continue
        by_qid: dict[str, list] = {q: [] for q in batch}
        for b in res.get('results', {}).get('bindings', []):
            nq = b.get('nameQid', {}).get('value', '').rsplit('/', 1)[-1]
            if nq not in by_qid:
                continue
            label = b.get('personLabel', {}).get('value', '')
            if not label or label.startswith('Q'):  # unlabeled fallback
                continue
            occ = b.get('occupationLabel', {}).get('value', '')
            url = b.get('article', {}).get('value', '')
            sl = int(b.get('sitelinks', {}).get('value', '0') or 0)
            born = b.get('birth', {}).get('value', '')
            born_year = None
            if born and len(born) >= 4:
                try:
                    born_year = int(born[:4]) if born[0] != '-' else -int(born[1:5])
                except ValueError:
                    born_year = None
            by_qid[nq].append({
                'name': label,
                'occupation': occ,
                'url': url,
                'born': born_year,
                'sitelinks': sl,
            })
        # Keep top 5 per qid, dedupe by url
        for nq in batch:
            ppl = by_qid[nq]
            seen = set()
            uniq = []
            for p in sorted(ppl, key=lambda x: -x['sitelinks']):
                key = p['url'] or p['name']
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(p)
                if len(uniq) >= 15:
                    break
            cache[nq] = uniq
        if (i // BATCH) % 5 == 4:
            save_cache(cache_path, cache)
            print(f'    saved at {i + BATCH}/{len(todo)}')
        time.sleep(0.5)
    save_cache(cache_path, cache)
    return cache


# ---------------------------------------------------------------------------
# Language label lookup (resolves Q-IDs to display slugs at runtime)
# ---------------------------------------------------------------------------
LANG_LABEL_QUERY = """
SELECT ?lang ?langLabel WHERE {
  VALUES ?lang { %(values)s }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""


def fetch_language_labels(qids: list[str], cache_path: Path) -> dict:
    cache = load_cache(cache_path)
    todo = [q for q in qids if q not in cache]
    if not todo:
        return cache
    print(f'  language labels: {len(todo)} new Q-IDs to resolve')
    BATCH = 100
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        values = ' '.join(f'wd:{q}' for q in batch)
        try:
            res = sparql(LANG_LABEL_QUERY % {'values': values})
        except Exception as e:
            print(f'  lang batch {i}: skip ({e})', file=sys.stderr)
            continue
        for b in res.get('results', {}).get('bindings', []):
            qid = b['lang']['value'].rsplit('/', 1)[-1]
            label = b.get('langLabel', {}).get('value', '')
            if label and not label.startswith('Q'):
                cache[qid] = label
        time.sleep(0.3)
    save_cache(cache_path, cache)
    return cache


def label_to_slug(label: str) -> str:
    """Turn a language label like 'Old French' into 'old-french'.
    Drops trailing 'language' suffix and parenthetical disambiguators."""
    s = re.sub(r'\s*\([^)]*\)\s*', ' ', label).strip()
    s = re.sub(r'\s+language$', '', s, flags=re.IGNORECASE).strip()
    return slugify(s)


# ---------------------------------------------------------------------------
# Wikipedia article + first-paragraph extracts (lightweight name meanings)
# ---------------------------------------------------------------------------
WP_ARTICLE_QUERY = """
SELECT ?item ?en_url ?fr_url WHERE {
  VALUES ?item { %(values)s }
  OPTIONAL { ?en_url schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }
  OPTIONAL { ?fr_url schema:about ?item; schema:isPartOf <https://fr.wikipedia.org/>. }
}
"""


def fetch_wp_articles(qids: list[str], cache_path: Path) -> dict:
    """Resolve each name Q-ID to its en + fr Wikipedia article title."""
    cache = load_cache(cache_path)
    todo = [q for q in qids if q not in cache]
    if not todo:
        print(f'  wp articles: 0 to fetch ({len(cache)} cached)')
        return cache
    print(f'  wp articles: {len(todo)} Q-IDs to resolve ({len(cache)} cached)')
    BATCH = 80
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        values = ' '.join(f'wd:{q}' for q in batch)
        try:
            res = sparql(WP_ARTICLE_QUERY % {'values': values})
        except Exception as e:
            print(f'  wp-art batch {i}: skip ({e})', file=sys.stderr)
            continue
        for q in batch:
            cache[q] = {'en': None, 'fr': None}
        for b in res.get('results', {}).get('bindings', []):
            qid = b['item']['value'].rsplit('/', 1)[-1]
            if qid not in cache:
                continue
            for lang in ('en', 'fr'):
                u = b.get(f'{lang}_url', {}).get('value', '')
                if u and not cache[qid].get(lang):
                    title = urllib.parse.unquote(u.rsplit('/', 1)[-1]).replace('_', ' ')
                    cache[qid][lang] = title
        if (i // BATCH) % 5 == 4:
            save_cache(cache_path, cache)
            print(f'    saved at {i + BATCH}/{len(todo)}')
        time.sleep(0.4)
    save_cache(cache_path, cache)
    return cache


def fetch_wp_extracts(host: str, titles: list[str], cache_path: Path) -> dict:
    """MediaWiki API for first-paragraph plain-text extracts.
    Up to 20 titles per call (API caps extracts at 20)."""
    cache = load_cache(cache_path)
    todo = sorted({t for t in titles if t and t not in cache})
    if not todo:
        print(f'  {host} extracts: 0 to fetch ({len(cache)} cached)')
        return cache
    print(f'  {host} extracts: {len(todo)} titles to fetch ({len(cache)} cached)')
    BATCH = 20
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        params = urllib.parse.urlencode({
            'action': 'query', 'prop': 'extracts', 'exintro': 1,
            'explaintext': 1, 'redirects': 1, 'format': 'json',
            'titles': '|'.join(batch),
        })
        url = f'https://{host}/w/api.php?{params}'
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                                   'Accept-Encoding': 'gzip'})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if resp.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                data = json.loads(raw)
        except Exception as e:
            print(f'  {host} batch {i}: skip ({e})', file=sys.stderr)
            continue
        # Map MediaWiki's returned title back to our requested title (redirects
        # resolved). Use the 'normalized' + 'redirects' arrays to track.
        norm_map = {n['from']: n['to'] for n in
                    data.get('query', {}).get('normalized', [])}
        redir_map = {r['from']: r['to'] for r in
                     data.get('query', {}).get('redirects', [])}
        pages = data.get('query', {}).get('pages', {})
        # First collect by final title
        by_title = {p.get('title'): p.get('extract', '') for p in pages.values()}
        for t in batch:
            final = redir_map.get(norm_map.get(t, t), norm_map.get(t, t))
            cache[t] = by_title.get(final, '')
        if (i // BATCH) % 10 == 9:
            save_cache(cache_path, cache)
            print(f'    saved at {i + BATCH}/{len(todo)}')
        time.sleep(0.3)
    save_cache(cache_path, cache)
    return cache


_PAREN_RE = re.compile(r'\s*\([^()]*\)')
_PRON_RE = re.compile(r'(?:pronounced|prononcé|/[^/]+/)[^.]*\.\s*', re.IGNORECASE)


def first_sentences(text: str, max_chars: int = 280) -> str:
    """Trim a Wikipedia extract down to its first 1-2 sentences.
    Strips IPA/pronunciation parentheticals which read poorly out of context."""
    if not text:
        return ''
    # Drop nested parentheticals (often pronunciation/IPA)
    cleaned = text
    for _ in range(3):
        new = _PAREN_RE.sub('', cleaned)
        if new == cleaned:
            break
        cleaned = new
    cleaned = _PRON_RE.sub('', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # Take whole sentences up to max_chars
    out = ''
    for sent in re.split(r'(?<=[.!?])\s+', cleaned):
        if not sent:
            continue
        if out and len(out) + 1 + len(sent) > max_chars:
            break
        out = (out + ' ' + sent).strip() if out else sent
    return out


def looks_like_disambiguation(text: str) -> bool:
    """Disambiguation pages start with 'X may refer to:' (en) or 'X est un
    prénom' followed by very short list-y content."""
    if not text:
        return True
    head = text[:200].lower()
    if 'may refer to' in head or 'peut faire référence' in head:
        return True
    if len(text.strip()) < 40:
        return True
    return False


# ---------------------------------------------------------------------------
# Merge into final enrichment file
# ---------------------------------------------------------------------------
def canonicalise_origin(qid: str, lang_labels: dict) -> str | None:
    label = lang_labels.get(qid)
    if not label:
        return None
    slug = label_to_slug(label)
    slug = CANONICALISE.get(slug, slug)
    if slug in SKIP_ORIGIN_SLUGS:
        return None
    # Reject anything that doesn't look like a language (multi-word labels
    # that include 'dynasty', 'kingdom', etc. are noise — Wikidata sometimes
    # returns the wrong entity class).
    if any(stop in slug for stop in ('dynasty', 'kingdom', 'period', 'era', 'q-')):
        return None
    return slug


def write_enrichment(origins: dict, famous: dict, lang_labels: dict,
                     wp_articles: dict, wp_en: dict, wp_fr: dict,
                     out_path: Path) -> None:
    enriched: dict[str, dict] = {}
    for name, rec in origins.items():
        slug = slugify(name)
        origin_qids = rec.get('origin_qids', [])
        # Score each candidate origin: pick the most specific/oldest known etymology
        # over generic modern languages. Lower score = preferred.
        ORIGIN_PRIORITY = [
            'hebrew', 'arabic', 'aramaic', 'persian', 'sanskrit',
            'greek', 'latin',
            'irish', 'welsh', 'scottish',
            'old-norse', 'scandinavian',
            'old-english', 'old-french', 'old-high-german',
            'german', 'french', 'italian', 'spanish', 'portuguese',
            'russian', 'polish', 'czech',
            'japanese', 'chinese', 'korean',
            'english',  # fall-back; many names get tagged English just because
        ]
        ranked: list[tuple[int, str]] = []
        for q in origin_qids:
            o = canonicalise_origin(q, lang_labels)
            if not o:
                continue
            rank = ORIGIN_PRIORITY.index(o) if o in ORIGIN_PRIORITY else 999
            ranked.append((rank, o))
        ranked.sort()
        origin_slug = ranked[0][1] if ranked else None
        # Pick the first name Q-ID's famous list (most names map to one Q-ID;
        # if multiple, prefer one that has bearers).
        bearers: list = []
        for qid in rec.get('qids', []):
            cand = famous.get(qid)
            if cand and (not bearers or len(cand) > len(bearers)):
                bearers = cand
        # Meanings: pick the first name Q-ID with a usable Wikipedia extract.
        meaning_en = ''
        meaning_fr = ''
        for qid in rec.get('qids', []):
            arts = wp_articles.get(qid) or {}
            if not meaning_en and arts.get('en'):
                raw = wp_en.get(arts['en'], '')
                if not looks_like_disambiguation(raw):
                    meaning_en = first_sentences(raw)
            if not meaning_fr and arts.get('fr'):
                raw = wp_fr.get(arts['fr'], '')
                if not looks_like_disambiguation(raw):
                    meaning_fr = first_sentences(raw)
            if meaning_en and meaning_fr:
                break
        entry = {}
        if origin_slug:
            entry['origin'] = origin_slug
        if bearers:
            entry['famous'] = bearers
        if meaning_en:
            entry['meaning_en'] = meaning_en
        if meaning_fr:
            entry['meaning_fr'] = meaning_fr
        if entry:
            enriched[slug] = entry
    with out_path.open('w') as f:
        json.dump(enriched, f, separators=(',', ':'), ensure_ascii=False)
    print(f'  wrote {out_path}: {len(enriched):,} entries with data')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, help='Process only the first N names')
    ap.add_argument('--names', help='Comma-separated list of names to process')
    ap.add_argument('--skip-famous', action='store_true')
    ap.add_argument('--skip-meanings', action='store_true')
    args = ap.parse_args()

    if args.names:
        names = [n.strip() for n in args.names.split(',') if n.strip()]
    else:
        names = page_eligible_names()
        if args.limit:
            names = names[:args.limit]
    print(f'Universe: {len(names):,} names')

    origin_cache = CACHE / 'wikidata_origins.json'
    origins = fetch_origins(names, origin_cache)

    # Resolve every language Q-ID we saw to a human label.
    all_origin_qids = sorted({q for rec in origins.values() for q in rec.get('origin_qids', [])})
    lang_labels = fetch_language_labels(all_origin_qids, CACHE / 'wikidata_lang_labels.json')

    qids: list[str] = []
    seen = set()
    for rec in origins.values():
        for q in rec.get('qids', []):
            if q not in seen:
                seen.add(q)
                qids.append(q)
    print(f'Discovered {len(qids):,} given-name Q-IDs')

    if not args.skip_famous:
        famous_cache = CACHE / 'wikidata_famous.json'
        famous = fetch_famous(qids, famous_cache)
    else:
        famous = load_cache(CACHE / 'wikidata_famous.json')

    if not args.skip_meanings:
        wp_articles = fetch_wp_articles(qids, CACHE / 'wikipedia_articles.json')
        en_titles = sorted({a['en'] for a in wp_articles.values() if a.get('en')})
        fr_titles = sorted({a['fr'] for a in wp_articles.values() if a.get('fr')})
        wp_en = fetch_wp_extracts('en.wikipedia.org', en_titles,
                                  CACHE / 'wikipedia_extracts_en.json')
        wp_fr = fetch_wp_extracts('fr.wikipedia.org', fr_titles,
                                  CACHE / 'wikipedia_extracts_fr.json')
    else:
        wp_articles = load_cache(CACHE / 'wikipedia_articles.json')
        wp_en = load_cache(CACHE / 'wikipedia_extracts_en.json')
        wp_fr = load_cache(CACHE / 'wikipedia_extracts_fr.json')

    write_enrichment(origins, famous, lang_labels,
                     wp_articles, wp_en, wp_fr,
                     NORMALIZED / 'name_enrichment.json')
    print('Done.')


if __name__ == '__main__':
    main()
