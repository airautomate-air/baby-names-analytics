# Queensland baby names — manual download

QLD's open data portal (`data.qld.gov.au`) sits behind a CloudFront WAF
that blocks scripted downloads with a JS challenge. To include QLD data,
download CSVs manually from the dataset page and drop them in this folder:

**Dataset:** https://www.data.qld.gov.au/dataset/top-100-baby-names

**Files to grab:**
- "1960 to 2005 Top 100 Baby Names" (one combined CSV)
- "Top 100 Baby Names" for each year from 2008 onwards (separate CSVs)

Save with any filename ending in `.csv`. The fetcher will parse them
automatically on the next `python3 fetchers/fetch_au.py` run.
