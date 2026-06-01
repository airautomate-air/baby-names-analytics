"""Normalize existing SSA national files (yob<year>.txt at repo root) to data/normalized/us.csv.

We already have the raw data downloaded; this just rewrites it into the unified schema.
"""
from pathlib import Path
from _common import ROOT, write_normalized


def rows():
    for f in sorted(ROOT.glob('yob*.txt')):
        try:
            year = int(f.stem.removeprefix('yob'))
        except ValueError:
            continue
        with f.open(encoding='utf-8') as fh:
            for line in fh:
                parts = line.strip().split(',')
                if len(parts) != 3:
                    continue
                name, sex, count = parts
                yield (year, sex, name, count)


def main():
    print("[US] reading SSA national files...")
    write_normalized('US', rows())


if __name__ == '__main__':
    main()
