"""Fetch Canadian baby names from Statistics Canada table 17-10-0147-01.

Single national CSV covering 1991-2024, source Canadian Vital Statistics +
Retraite Québec. Names are uppercased in the raw file; normalize_*() title-cases
and folds accents. Counts below 5 are suppressed in the source.

  https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1710014701
"""
import csv
import time
import urllib.request
import zipfile
from pathlib import Path
from _common import RAW_DIR, write_normalized

CA_RAW = RAW_DIR / 'ca'
ZIP_URL = "https://www150.statcan.gc.ca/n1/tbl/csv/17100147-eng.zip"
ZIP_FILE = CA_RAW / 'statcan_17100147.zip'
CSV_FILE = CA_RAW / '17100147.csv'
UA = {'User-Agent': 'Mozilla/5.0'}


def download():
    CA_RAW.mkdir(parents=True, exist_ok=True)
    if ZIP_FILE.exists() and ZIP_FILE.stat().st_size > 1_000_000:
        return
    print(f"  [CA] downloading {ZIP_URL}")
    req = urllib.request.Request(ZIP_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, ZIP_FILE.open('wb') as f:
        f.write(r.read())
    time.sleep(1)
    with zipfile.ZipFile(ZIP_FILE) as z:
        z.extractall(CA_RAW)


def rows():
    download()
    with CSV_FILE.open(encoding='utf-8-sig') as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get('Indicator') != 'Frequency':
                continue
            if row.get('GEO') != 'Canada':
                continue
            try:
                year = int(row['REF_DATE'])
                count = int(row['VALUE'])
            except (KeyError, ValueError, TypeError):
                continue
            name = row.get('First name at birth', '')
            sex = row.get('Sex at birth', '')
            if count > 0 and name and sex:
                yield (year, sex, name, count)


def main():
    print("[CA] Statistics Canada 17-10-0147-01 (Canada-wide 1991-2024)")
    write_normalized('CA', rows())


if __name__ == '__main__':
    main()
