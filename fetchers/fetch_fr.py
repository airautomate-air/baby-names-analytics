"""Fetch French national name data from INSEE (1900–2024)."""
import csv
import io
import urllib.request
import zipfile
from _common import RAW_DIR, write_normalized

URL = "https://www.insee.fr/fr/statistiques/fichier/8595130/prenoms-2024-nat_csv.zip"
LOCAL_ZIP = RAW_DIR / 'fr' / 'prenoms-2024-nat_csv.zip'


def download():
    LOCAL_ZIP.parent.mkdir(parents=True, exist_ok=True)
    if LOCAL_ZIP.exists() and LOCAL_ZIP.stat().st_size > 100_000:
        return
    print(f"  [FR] downloading {URL}")
    req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=60) as r, LOCAL_ZIP.open('wb') as f:
        f.write(r.read())


def rows():
    with zipfile.ZipFile(LOCAL_ZIP) as zf:
        member = next(n for n in zf.namelist() if n.endswith('.csv'))
        with zf.open(member) as fh:
            reader = csv.reader(io.TextIOWrapper(fh, encoding='utf-8'), delimiter=';')
            header = next(reader)
            try:
                i_sex = header.index('sexe')
                i_name = header.index('prenom')
                i_year = header.index('periode')
                i_count = header.index('valeur')
            except ValueError:
                i_sex, i_name, i_year, i_count = 0, 1, 2, 3
            for row in reader:
                if len(row) < 4:
                    continue
                year_raw = row[i_year]
                if not year_raw.isdigit():
                    continue
                yield (year_raw, row[i_sex], row[i_name], row[i_count])


def main():
    print("[FR] INSEE national names 1900–2024")
    download()
    write_normalized('FR', rows())


if __name__ == '__main__':
    main()
