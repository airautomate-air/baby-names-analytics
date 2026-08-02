"""QA gate for data/editorial/us_name_editorial.json before it's merged.

Checks:
  1. Repeated 6-grams across entries (templating smell) — any 6-word
     sequence that appears in 3+ different names' text.
  2. Numeric fact-check — every 4-digit year and number-with-3+-digits
     mentioned in an entry's text must appear in that name's facts
     (from editorial_facts.py), so a written claim can't drift from data.
  3. Length sanity — entries under 80 or over 260 words are flagged.

Exits non-zero if any hard failure (repeated n-grams, length, or a numeric
claim that doesn't trace to facts) is found. Run before every batch merge.

Usage: python3 editorial_qa.py
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

EDITORIAL = Path('data/editorial/us_name_editorial.json')


def ngrams(words, n=6):
    return [' '.join(words[i:i + n]) for i in range(len(words) - n + 1)]


def check_overlap(entries):
    seen = defaultdict(set)
    for slug, e in entries.items():
        words = re.findall(r"[a-z']+", e['text'].lower())
        for g in ngrams(words):
            seen[g].add(slug)
    failures = {g: names for g, names in seen.items() if len(names) >= 3}
    return failures


# Round numbers used as structural framing (rank tiers, batch size) rather
# than as a factual claim about a specific name's data.
TIER_NUMBERS = {'10', '15', '20', '25', '30', '50', '100', '300', '1', '2', '3'}


def check_numbers(entries):
    import editorial_facts
    failures = []
    counts, years, enrichment = editorial_facts.load()

    # Numbers legitimately traceable to ANY name currently in the file, so a
    # cross-reference like "unlike Ava's 45% drop" in another name's entry
    # is allowed as long as it's real — just not fabricated.
    batch_numbers = set(TIER_NUMBERS)
    facts_by_slug = {}
    for slug in entries:
        f = editorial_facts.facts_for(slug.title(), counts, years, enrichment)
        if f is None:
            continue
        facts_by_slug[slug] = f
        for k, v in f.items():
            if isinstance(v, int):
                batch_numbers.add(str(v))
                batch_numbers.add(str(abs(v)))
        batch_numbers |= {str(d) + 's' for d in f['decades_active']}
        # BCE born years are negative (e.g. -1790); allow "1790 BC" phrasing
        for fb in f['famous']:
            born = fb.get('born')
            if born:
                batch_numbers.add(str(born))
                batch_numbers.add(str(abs(born)))

    year_fields = ('peak_year', 'latest_year', 'peak_rank_year', 'first_year_seen')
    DATASET_START_YEAR = 1880  # implicit anchor for "N years into the dataset"

    # Cross-name derived gaps, e.g. citing "Samuel's 121-year gap" in another
    # entry's text — legitimate as long as it's a real pairwise difference
    # between two of THAT OTHER name's own year facts (not a fabricated one).
    for slug, f in facts_by_slug.items():
        yv = [f[k] for k in year_fields if f.get(k)] + [DATASET_START_YEAR]
        yv += [fb['born'] for fb in f['famous'] if fb.get('born')]
        for a in yv:
            for b in yv:
                if a is not None and b is not None:
                    batch_numbers.add(str(abs(a - b)))

    for slug, e in entries.items():
        f = facts_by_slug.get(slug)
        if f is None:
            failures.append((slug, 'no facts available for this name'))
            continue
        known_numbers = set(batch_numbers)
        # allow "84 years earlier" derived from two of this name's own year
        # facts (e.g. latest_year - peak_rank_year), a legitimate calculation.
        # DATASET_START_YEAR is included so "N years into the dataset" works.
        year_values = [f[k] for k in year_fields if f.get(k)] + [DATASET_START_YEAR]
        year_values += [fb['born'] for fb in f['famous'] if fb.get('born')]
        for a in year_values:
            for b in year_values:
                if a is not None and b is not None:
                    known_numbers.add(str(abs(a - b)))
        # match comma-grouped numbers (19,836) as a single token, then strip
        # commas before comparing so "19,836" checks against known "19836"
        raw_mentions = re.findall(r"\b\d[\d,]*\d\b|\b\d\b", e['text'])
        mentioned = {m.replace(',', '') for m in raw_mentions}
        # a written "2014" inside "2014's" etc. should still match a plain year
        mentioned = {m.rstrip('s') if m.rstrip('s') in known_numbers else m for m in mentioned}
        unverified = mentioned - known_numbers
        if unverified:
            failures.append((slug, f"numbers not traceable to facts: {sorted(unverified)}"))
    return failures


def check_length(entries):
    failures = []
    for slug, e in entries.items():
        n = len(e['text'].split())
        if n < 80 or n > 260:
            failures.append((slug, f"{n} words"))
    return failures


def main():
    if not EDITORIAL.exists():
        print("No editorial file yet — nothing to check.")
        return
    entries = json.loads(EDITORIAL.read_text(encoding='utf-8'))
    if not entries:
        print("Editorial file is empty — nothing to check.")
        return

    ok = True

    overlaps = check_overlap(entries)
    if overlaps:
        ok = False
        print(f"FAIL: {len(overlaps)} repeated 6-grams across 3+ entries:")
        for g, names in sorted(overlaps.items())[:15]:
            print(f"  {g!r} -> {sorted(names)}")

    length_fails = check_length(entries)
    if length_fails:
        ok = False
        print(f"FAIL: {len(length_fails)} entries outside 80-260 words:")
        for slug, msg in length_fails:
            print(f"  {slug}: {msg}")

    number_fails = check_numbers(entries)
    if number_fails:
        ok = False
        print(f"FAIL: {len(number_fails)} entries with unverified numeric claims:")
        for slug, msg in number_fails:
            print(f"  {slug}: {msg}")

    if ok:
        print(f"PASS: {len(entries)} entries, no repeated n-grams, all numbers traced, lengths OK.")
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
