# Rollover plan — NameCharted rebrand

Handoff for continuing the **branding + design** work on another laptop with Claude Code.
Created 2026-06-01. Pick up here.

---

## 0. Resume context first
- Repo: `github.com/airautomate-air/baby-names-analytics` (branch `main`)
- Clone, then read project memory: `.claude/.../memory/project_baby_names_initiative.md` (if synced) and this file.
- Tell the new Claude session: *"Continue the NameCharted rebrand — read HANDOFF_REBRAND.md and apply steps 2–4."*

## 1. What's decided (locked)
- **Brand name:** NameCharted
- **Domain:** `namecharted.com` — PURCHASED at **Exabytes.sg** (not Vercel).
- **Positioning:** data/analytics on names (charts, trends, rankings) — "names, charted."

### Proposed brand spec (adjust freely — this is the starting point)
| Role | Hex |
|---|---|
| Ink (deep indigo) | `#1B2440` |
| Brand Teal (primary) | `#149E91` |
| Coral (secondary) | `#FF6B5C` |
| Canvas (bg) | `#F7F8FA` |
| Surface (cards) | `#FFFFFF` |
| Muted text | `#5B6678` |

- **Type:** Poppins 600/700 (headings + brand) + Inter (body + data tables, tabular numerals). Both free Google Fonts.
- **Logo:** inline SVG — rounded teal badge with a rising line-chart glyph ending in a coral dot; doubles as favicon. Wordmark: **Name** (ink) + **Charted** (teal). Build the brand guide / final logo on the other laptop.

## 2. Domain → Vercel DNS (do in your accounts)
1. Vercel project → **Settings → Domains** → add `namecharted.com` (+ `www`).
2. In **Exabytes DNS panel**: `A  @  → 76.76.21.21` and `CNAME  www → cname.vercel-dns.com` (use the exact values Vercel shows).
3. Wait for propagation, confirm HTTPS issues, then the new `BASE_URL` resolves.

## 3. Code change map — `generate_site.py`
Old palette to retire: `#2c3e50` (navy), `#3498db` (blue), `#ecf0f1`, `#f5f5f5`, chart `#e84393`/`#0984e3`.

| What | Where (approx line) | Change |
|---|---|---|
| BASE_URL | 24 | `"https://baby-names-analytics.vercel.app"` → `"https://namecharted.com"` |
| Google Fonts import | top of `BASE_CSS` (~200) | add `@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Poppins:wght@600;700&display=swap');` |
| body font-family | 202 | → `'Inter', -apple-system, ...` |
| h1 / headings color | 209, 236 (`#2c3e50`) | → Ink `#1B2440`, set headings/brand to Poppins |
| .sitenav bg | 211 (`#2c3e50`) | → Ink `#1B2440` |
| accent blue everywhere | 218,221,223,255,258,260 + inline 353,605,707,713,945 (`#3498db`) | → Teal `#149E91` |
| stat-value color | 236 (`#2c3e50`) | → Ink `#1B2440` |
| th bg | 245 (`#ecf0f1`) | → light tint, e.g. `#EEF2F4` |
| page bg | 205 (`#f5f5f5`) | → Canvas `#F7F8FA` |
| FOOTER text | 263–267 | `Baby Names Analytics` → `NameCharted`; keep SSA data-source line |
| SITE_NAV brand | 271 | replace text with inline SVG logo + `NameCharted` wordmark |
| Chart.js colors | 526–527 | girls `#e84393`→ Teal `#149E91`; boys `#0984e3`→ Coral `#FF6B5C` (update rgba fills to match) |
| `<title>` suffix | per-page `title=` calls | swap any "Baby Names Analytics" brand suffix → "NameCharted" |

Tip: `grep -n "3498db\|2c3e50\|e84393\|0984e3\|Baby Names Analytics" generate_site.py` to catch every spot.
Add a favicon: write the badge SVG to `docs/favicon.svg` and link it in the `<head>` template.

## 4. Build, verify, deploy
```bash
cd baby-names-analytics
python generate_site.py            # regenerates docs/ (outputDirectory=docs)
# quick broken-link sanity check (no committed checker): confirm every internal href
# resolves to a file under docs/ before deploying.
git add -A
git commit -m "Rebrand to NameCharted: domain, palette, fonts, logo"
# large pushes 408 over HTTP/2 — use:
git -c http.version=HTTP/1.1 -c http.postBuffer=524288000 \
    -c http.lowSpeedLimit=0 -c http.lowSpeedTime=999999 push origin HEAD:main
```
Vercel auto-deploys `docs/` on push to `main`. Verify live on `namecharted.com` once DNS is live (until then, the `*.vercel.app` URL still serves the rebuilt site).

## 5. Don't forget (post-rebrand, still pending from project roadmap)
- Submit `sitemap.xml` to Google Search Console + Bing (starts 3–6mo indexing ramp).
- Add GA4/Plausible analytics ID.
- Apply for AdSense once `namecharted.com` is live with content.
