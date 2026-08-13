# Editorial updates for AdSense re-review (target: request review ~Aug 20, 2026)

Context: rejected twice for "Low value content" (Jun 28, Jul 17). Sitemap/link
cleanup shipped Jul 20 (commit 0f8475654af). Google needs 4+ weeks to recrawl —
do NOT request review before ~Aug 20. These editorial tasks fill that window.

Decisions (JP, Jul 21):
- Fabricated seed stories: **remove all 106**, keep real submission pipeline
- Author identity: **"JP" + honest role description** (semi-anonymous)
- Editorial copy scope: **top 300 US names**

## Phase A — Trust & honesty fixes (1 session)

- [x] A1. Removed fabricated stories 2026-08-11 (commit e4222ee92ef) —
      `data/name_stories.json` emptied to `{}`, deleting 217 invented stories
      across 106 names. Submission form + moderation pipeline untouched; the
      render is guarded by `if stories:` so the section disappears until real
      submissions arrive. Backup of the removed content kept out of the repo.
- [x] A2. About "Who runs NameCharted" shipped 2026-08-11, then revised
      2026-08-12 (commit c4d1f4b3843). First draft claimed the per-name
      analyses were "researched and edited by JP" — untrue, they're drafted
      from a generated fact sheet and gated by `editorial_qa.py`. JP's call:
      drop the authorship claim entirely rather than disclose or overstate.
      The page now says nothing about who writes the commentary.
- [x] A3. `/methodology.html` shipped 2026-08-11. Per-country provenance
      table, rank calculation, enrichment sourcing, update cadence,
      corrections contact. Footer link sitewide + About + sitemap-us.xml.
      Writing it surfaced real errors in the old About copy: AU credited to
      the ABS (actually NSW + VIC state registries), NL called a national
      registry (actually the Meertens Instituut name bank), and "data through
      2024" (NL stops at 2017, AU runs to 2025). Also now states plainly that
      only the US series is near-complete — GB/AU/ES are top-100/top-50 lists
      where an absent name is NOT evidence the name is unused.
- [x] A4. Fixed 2026-08-11 — 8 dead links in 6 posts (`/origins/*` →
      `/origin/*`; four `*-names.html` blog slugs → `*-origin-names.html`).
      Full crawl of all built pages reports zero dead internal links.

## Phase B — Unique editorial copy, top 300 US name pages (batched)

Design:
- Storage: `data/editorial/us_name_editorial.json` keyed by slug:
  `{ "olivia": { "text": "...", "updated": "2026-07-21" } }`
- Generator: new "Editor's analysis" block on the name page when an entry
  exists, attributed to NameCharted editorial (JP).
- Writing pipeline per batch (~25 names, 12 batches):
  1. Script extracts each name's hard facts first (peak year/decade, current
     rank, biggest rise/fall, rank trajectory, top famous bearers) so every
     claim is grounded in the real data — writer works FROM facts, not memory.
  2. Draft ~150 words per name: what the curve actually shows, why (cultural
     moments, sound trends, sibling names), who it suits stylistically.
     No boilerplate openers; vary structure between entries.
  3. QA gate before merge: automated n-gram overlap check across all written
     entries (flag any 6-gram appearing in 3+ entries) + numeric fact check
     against the dataset.
- [x] B1. Build facts-extraction + n-gram QA scripts — `editorial_facts.py`,
      `editorial_qa.py`, shipped 2026-07-21 (commit c3564ef601b)
- [x] B2. Generator: editorial block + JSON loader — shipped same commit,
      seeded with 1 QA-passing entry (olivia) as the style template
- [x] B3. Batch 1 (ranks #1-25) — done manually 2026-07-21 (commit
      c812ac79b24) instead of waiting for the 4:30am schedule, to test the
      pipeline live. Caught real errors in the process: two wrong numbers
      (James's multiple of Noah's total, a year-gap off by one) and two
      overclaimed superlatives (wrong name called "steepest decline").
      Also hardened `editorial_qa.py`'s number check, which was rejecting
      legitimate cross-name comparisons and rank-tier framing — now checks
      against the whole batch's facts, not just the entry's own name.
      Scheduled task `namecharted-editorial-batch-1` disabled (redundant).
- [x] B4. Batch 2 (ranks #26-50) — done manually 2026-07-21 (commit
      23922bf22f8), same as batch 1. Pre-verified two superlative claims
      against the real numbers before writing (this batch's steepest
      decline is Logan at -54%, biggest gain is Luca at +245%) and still
      caught one factual overcount in a first draft (Avery's "only two
      names" claim — actually three: Ethan, Sofia, Avery). Main QA churn
      was the n-gram gate catching reused skeleton phrasing across many
      entries once digits are stripped ("a rise over the past decade",
      "been present in SSA data since", etc.) — fixed by rewording, not
      by weakening the check. All 50 entries (batches 1+2) pass QA.
      Scheduled task `namecharted-editorial-batch-2` disabled (redundant).
- [x] B5. Batch 3 (ranks #51-75) — done manually 2026-07-21 (commit
      ae7de71ac42). Pre-verified superlatives again (Maverick +252% is
      the batch's biggest gain; Mason and Jacob are *tied* for steepest
      decline at -62% each — correctly described as a tie, not forced
      into a single winner). Heaviest QA round yet: several entries used
      the shape "X's rank peaked at #NN in YYYY", which all collapse to
      the identical token skeleton "peaked at in" once digits are
      stripped for n-gram comparison regardless of the real numbers —
      a 4-way collision (aiden/gabriel/grayson/isaac) that needed full
      sentence restructures, not just reworded transitions, to break.
      Also extended `editorial_qa.py` to abs() negative BCE birth years
      (needed for Jacob's biblical namesake, born -1790). All 75 entries
      (batches 1-3) pass QA. Scheduled task `namecharted-editorial-batch-3`
      disabled (redundant) — none of the 3 scheduled batches ended up
      firing unattended; all were run manually instead.
- [x] B6–B14. Batches 4–12 (ranks #76-300) — complete. Lesson learned across
      the run: open with more structurally varied sentences from the start
      (vary "X peaked at #N in Y" / "X's count hit Z in Y" / "Y was X's best
      year" etc.) to cut down the QA-rework loop — every batch needed a second
      pass to break self-similar openings within its own 25 entries, on top of
      avoiding all prior batches.
- [x] B15. Full QA over all 300 passes (no repeated n-grams, all numbers
      traced, lengths OK). 300/300 editorial blocks render, 34,793 words, none
      noindexed. Shipped and pushed 2026-08-12.

## Phase C — Ship & request review

- [x] C1. Regenerate + link/sitemap sweep done 2026-08-11/12. Full crawl of
      all built pages: zero dead internal links.
- [x] C2. Pushed 2026-08-12 (c4d1f4b3843). Confirmed live: /methodology.html
      serving, editorial blocks live on name pages.
      NOTE: sitemap total dropped 44,758 → 16,853 URLs. This is intended, from
      commits b15a2cd51c0 (all /similar/ pages noindex + out of sitemaps) and
      fb1d900d6e7 (NOINDEX_MIN_TOTAL raised 1,000 → 2,000) — not a regression.
      Fewer, thicker indexable pages is the point.
- [ ] C3. Wait until ~Aug 20 (≥4 weeks after Jul 17 rejection AND ≥2 weeks
      after Phase B ships → Phase B landed Aug 12, so ~Aug 26 is the safer
      date), then check "I have fixed the issues" → Request review in AdSense.
      Before requesting: resolve the GA anomaly below — a reviewer landing on
      a site with 2s average engagement is being shown a bad signal.
- [ ] C5. Investigate GA traffic quality (raised 2026-08-12). The Jul 15–Aug 11
      GA report shows 20.6K active users at **2s average engagement** and
      20.8K new vs 20.6K active — i.e. almost no returning users and near-zero
      dwell. That pattern reads as bot/crawler traffic being counted, not
      readers. Users are also **down 48%** period-over-period, so the premise
      that traffic is growing does not hold. Check GA4 for referral spam and
      unfiltered bot traffic before drawing any monetisation conclusions.
- [ ] C4. If rejected a 3rd time: stop iterating on AdSense; shift to
      affiliate monetization + traffic building until organic traffic exists

## Review

**2026-08-11/12 — Phases A and B shipped.**

The headline problem was not that content was missing, it was that finished
content was never published. All 200 written analyses (batches 1–8) existed
only in `data/editorial/us_name_editorial.json`; `main` was 5 commits ahead of
`origin/main` and the built `docs/` regen was entirely uncommitted. Google had
never seen any of it. Batches 9–12 landed alongside, so 300 names now carry
34,793 words of unique, fact-checked commentary.

Two judgement calls worth remembering:

1. **The fabricated stories had to go before anything else shipped.** 217
   invented "reader stories" were rendering as genuine UGC. Publishing more
   content on top of that would have made a manual review worse, not better.

2. **The first About draft overstated authorship** ("researched and edited by
   JP") and the methodology page said the analyses were "written by a human".
   Both were false. Shipping that on the same pages that promise honest
   sourcing — immediately after deleting fabricated content for being
   misleading — would have been the same mistake in a different costume. JP
   chose to drop the claim rather than disclose AI assistance. Worth noting for
   next time: Google does not penalise AI-assisted content, it penalises
   low-value content, and the automated fact-tracing gate is the part that
   actually carries credibility.

Writing the methodology page also caught four inaccurate data claims that had
been live on the About page for months (see A3). Being forced to document
provenance precisely is what exposed them.

(fill in after completion)
