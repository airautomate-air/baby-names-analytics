# NameCharted — handover

Quick-reference prompt for the next session, scoped to **work not yet done**.

## What just shipped this session (Phases 14–22)

- **14** Pinterest per-name pins (1000×1500 palettized PNG, top 1000 names per country)
- **14b** Curated famous-bearer overrides (`data/famous_overrides.json`) — patches Wikidata's praenomen-vs-nomen gap (Julius Caesar etc.)
- **15** Favorites page: Print-as-PDF button + per-row meta enrichment (`/favorites.html`)
- **16** Pin v2: Meaning blurb + numerology card row; on-page red "Save to Pinterest" pill
- **17** Sibling tool accepts 1–3 names (`/sibling.html?names=a,b,c`)
- **18** Pin v3: cleaner meaning (strip non-Latin scripts, single ellipsis), trait descriptions on numerology cards
- **19** Blog framework (`/blog/`) + 3 seed posts from real SSA data
- **20** PWA wrapper: `manifest.webmanifest`, service worker (`docs/sw.js`), app icons, installable
- **21** Spain (ES) as 6th country (`/es/`, 260 name pages from INE Padrón decadal data, English UI) + Spanish/French fiction franchises (Les Misérables, Le Petit Prince, Don Quijote, La Casa de Papel)

---

## Outstanding tasks

### Country expansion (highest user-visible payoff)

- **Germany (DE)**: Destatis publishes only top-10 per year as press releases. Knud Bielefeld (`beliebte-vornamen.de`) has comprehensive data but it's copyrighted — need to verify license before use. **Suggested first step:** WebSearch for an open German national name dataset; if nothing usable, skip and document.
- **Italy (IT)**: ISTAT has open data. Likely follows a similar XLS/CSV pattern to Spain. ~1–2 hr.
- **Netherlands (NL)**: Meertens Instituut publishes the Nederlandse Voornamenbank. Format is per-name lookups via web pages, so a fetcher needs to scrape ~5K names. ~2–3 hr.
- **Per-country sub-tasks for each new country:**
  1. Source the data, write `fetchers/fetch_<cc>.py`, run it, verify `data/normalized/<cc>.csv`.
  2. Add the country code to: `COUNTRIES`, `COUNTRY_SLUG`, `COUNTRY_LABEL`, `COUNTRY_NAME`, `FLAG`, `COUNTRY_NAMES_EN`, `COUNTRY_NAMES_FR`, `COUNTRY_NAMES_IN_UI`, `DATA_SOURCE_FULL`, `DATA_SOURCE_SHORT`, `STRINGS`, `GENDERED`, `ORIGIN_LABELS`, `NUMEROLOGY_TRAITS`, `HREFLANG`, `SURNAMES_BY_CC` in `generate_site.py`. Use the **CA / ES pattern** (point to `*_EN` dicts) unless localising the UI.
  3. Run `python3 generate_site.py` and verify the new `/{cc}/` tree builds without errors.
  4. Push (use the large-push command in `CLAUDE.md`).

### Localised UI strings

- The Spain tree currently uses English UI strings (same as CA/AU/UK). For true Spanish UI, write a `STRINGS_ES` dict (~150 entries) translated from `STRINGS_EN`, plus `GENDERED_ES`, `ORIGIN_LABELS_ES`, `NUMEROLOGY_TRAITS_ES`. Repeat for IT/DE/NL when those land.
- The pin renderer currently emits English numerology labels (`DESTINY` / `SOUL` / `PERSONALITY`) regardless of country — needs per-CC label dictionaries when localising.

### Saints calendars beyond France

- France has `data/saints_fr.json` (366-day Catholic calendar) wired through `load_saints_fr()` → SAINTS_FR / SAINT_TO_DATES → `/jour-de-fete.html` + `/saint/<slug>.html`.
- Spain and Italy use the same Catholic calendar with localised name spellings (Pierre → Pedro / Pietro). Need `data/saints_es.json` and `data/saints_it.json`, then mirror the `load_saints_fr` / `generate_saint_pages` pattern. Don't rely on automated translation — verify with a published Catholic calendar.

### Fiction expansion

- Three Spanish/French entries added this session (Les Misérables, Le Petit Prince, Don Quijote, La Casa de Papel). The data lives in `data/fiction.json` — schema is `{ slug, title, kind, year, blurb, names: [{ name, role }] }`. Pure JSON, no code change needed to add more.
- Worth adding next: classic Italian (Pinocchio, Promessi Sposi), more Spanish-language TV (Élite, Narcos), more French film (Amélie, Asterix). Each entry should have 4–10 character names that map to actual SSA-rankable first names.

### Affiliate revenue (blocked — needs your input)

- Pick an affiliate program (Amazon Associates, Etsy, Bookshop.org, monogram-gift vendors).
- Decide placement: per-name page sidebar? Per-decade page? Footer? Each is a separate code change.
- The site already collects no personal data and doesn't run analytics — make sure the affiliate links don't break that promise.

### Blog content plan

- Posts live at `data/blog/<date>-<slug>.md`. Drop a file, rebuild, push.
- Three seed posts done. Recommended next batch (all free, data-derived):
  - **Top 10 falling-fastest** boys + girls (mirror of the rising posts)
  - **Names that peaked exactly 100 years ago** — annual evergreen
  - **What name did Olivia replace at #1?** — history of US #1 girls
  - **Origin spotlight: Greek names** (then Hebrew, Irish, Italian, etc.)
  - **Country crossover** — names hot in France that haven't crossed to the US
- **Cadence**: weekly for 8–12 weeks to build a content moat, then biweekly.
- **Open architectural decision**: turn rising/falling posts into auto-regenerating templates so they auto-refresh each year against the latest SSA data. Same URL, fresh content forever. Requires moving the post body into Python (or Jinja) instead of static `.md`.

### Smaller papercuts

- The pin renderer's meaning extractor has ~20–30% miss rate on names like Ava, Arthur, Elijah where the Wikipedia text has no etymology keywords. The pin still renders fine (no meaning row) but better extraction would help. Possible fix: hand-curate a small `data/name_meanings.json` override.
- Spain's data is **decadal**, expanded to yearly by even-spread inside `fetch_es.py`. The trend chart for Spanish names will look stair-stepped within each decade. Not wrong, just less granular than US/FR. A real yearly dataset would replace this (INE doesn't publish one).
- Pin v3 still folds POPULARITY + PEAK ERA into the hero subtitle when both are present, but if one is missing it can look unbalanced. Acceptable for v1.

### New ideas surfaced this session

- **Two-format pin strategy** (deferred): keep the 2:3 pin at `/pin/<name>.png` for Pinterest, restore a 1.91:1 social card at `/og/<name>.png` for FB/Twitter previews. Each name page would link both (`og:image` → social, Pinterest share button → pin).
- **Year-in-review automation**: refresh `/year-in-review-<latest>.html` each January when SSA drops new data. Currently regenerates on every build; just needs a fresh blog post + social push to drive traffic.
- **Twin-name share URL pre-fill**: the multi-name sibling tool reads `?names=a,b,c` but doesn't have a "copy share link" button. One-line addition.

---

## Build / push reference

```bash
# Full rebuild (~5 min for code, ~25 min for code+pins regeneration):
python3 generate_site.py

# Regenerate just the PWA icons (only when logo changes):
python3 generate_pwa_assets.py

# Large pushes (workflow file changes need SSH, see CLAUDE.md):
git -c http.version=HTTP/1.1 -c http.postBuffer=524288000 \
    -c http.lowSpeedLimit=0 -c http.lowSpeedTime=999999 \
    push origin HEAD:main
```

Vercel auto-deploys on push to `main`. Deploys take ~10 min for full-pin builds; just HTML changes deploy in 1–2 min.
