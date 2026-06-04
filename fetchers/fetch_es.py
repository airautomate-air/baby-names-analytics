"""Fetch Spanish national name data from INE (Instituto Nacional de Estadística).

Source XLS publishes the top 50 names per *decade* (1930s through 2020s, plus
a pre-1930 bucket) for each sex, drawn from the Padrón Continuo population
register. The rest of the build assumes yearly granularity, so we spread each
decade's count across its 10 years equally — the trend line will look stair-
stepped within decades, but the totals stay faithful to the source.
"""
import sys
import urllib.request
from pathlib import Path

import xlrd

sys.path.insert(0, str(Path(__file__).parent))
from _common import RAW_DIR, write_normalized

URL = "https://www.ine.es/en/daco/daco42/nombyapel/nombres_por_fecha_en.xls"
LOCAL_XLS = RAW_DIR / 'es' / 'nombres_por_fecha.xls'

# Decade buckets in the XLS — column anchors are (start_col_of_block,
# decade_start_year, year_span). Each block spans 3 cols: NOMBRE, FRECUENCIA,
# Por 1.000. The pre-1930 bucket starts at col 1; subsequent decades step by
# 3 cols. The 2020s bucket is currently a partial decade (2020-2024), so we
# cap its span to avoid emitting rows for future years.
LATEST_YEAR = 2024
DECADES: list[tuple[int, int, int]] = []
# pre-1930: single year anchor at 1924 (so build sees a clean 1924-2024 range)
DECADES.append((1, 1924, 1))
for i in range(1, 11):
    col = 1 + i * 3
    year = 1930 + (i - 1) * 10
    span = min(10, LATEST_YEAR - year + 1)
    DECADES.append((col, year, span))


def download() -> None:
    LOCAL_XLS.parent.mkdir(parents=True, exist_ok=True)
    if LOCAL_XLS.exists() and LOCAL_XLS.stat().st_size > 100_000:
        return
    print(f"  [ES] downloading {URL}")
    req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=60) as r, LOCAL_XLS.open('wb') as f:
        f.write(r.read())


def rows():
    wb = xlrd.open_workbook(LOCAL_XLS)
    for sex, sheet_name in (('M', 'ESPAÑA_hombres'), ('F', 'ESPAÑA_mujeres')):
        sh = wb.sheet_by_name(sheet_name)
        # Data starts at row 5 (after title + header). Names are 50 per decade.
        for r in range(5, min(sh.nrows, 55)):
            for col, year_anchor, span in DECADES:
                if col >= sh.ncols or span <= 0:
                    continue
                name_cell = sh.cell_value(r, col)
                count_cell = sh.cell_value(r, col + 1) if col + 1 < sh.ncols else ''
                if not isinstance(name_cell, str) or not name_cell.strip():
                    continue
                name = name_cell.strip()
                try:
                    total = int(float(count_cell))
                except (ValueError, TypeError):
                    continue
                if total <= 0:
                    continue
                # Spread the decadal total evenly across the bucket's years —
                # the within-decade distribution isn't published, so even-
                # spread is the honest default. Remainder lands on the
                # earliest years.
                per_year = max(1, total // span)
                remainder = total - per_year * span
                for offset in range(span):
                    y = year_anchor + offset
                    c = per_year + (1 if offset < remainder else 0)
                    yield (y, sex, name, c)


def main():
    print("[ES] INE Padrón names by decade 1924–2024 (decadal → yearly expansion)")
    download()
    write_normalized('ES', rows())


if __name__ == '__main__':
    main()
