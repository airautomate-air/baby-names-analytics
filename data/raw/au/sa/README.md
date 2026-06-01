# South Australia baby names — manual download

SA's open data portal (`data.sa.gov.au`) sits behind a WAF that returns
403 to scripted requests. To include SA data, download CSVs manually
from the dataset page and drop them in this folder:

**Dataset:** https://data.sa.gov.au/data/dataset/popular-baby-names

**Files to grab:**
- "Most popular Baby Names (1944-2013)" — ZIP, unpack and drop the CSVs in
- "Baby Names <YEAR> - Male" + "Baby Names <YEAR> - Female" for each year 2014–latest

Save with any filename ending in `.csv`. The fetcher will parse them
automatically on the next `python3 fetchers/fetch_au.py` run.
