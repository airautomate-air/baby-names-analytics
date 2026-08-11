# Editorial updates for AdSense re-review (target: request review ~Aug 20, 2026)

Context: rejected twice for "Low value content" (Jun 28, Jul 17). Sitemap/link
cleanup shipped Jul 20 (commit 0f8475654af). Google needs 4+ weeks to recrawl —
do NOT request review before ~Aug 20. These editorial tasks fill that window.

Decisions (JP, Jul 21):
- Fabricated seed stories: **remove all 106**, keep real submission pipeline
- Author identity: **"JP" + honest role description** (semi-anonymous)
- Editorial copy scope: **top 300 US names**

## Phase A — Trust & honesty fixes (1 session)

- [ ] A1. Remove fabricated stories: empty `data/name_stories.json` to `{}`,
      keep the submission form + moderation pipeline untouched. Regenerate,
      verify story sections show only the "share your story" form.
- [ ] A2. About page: add "Who runs NameCharted" section — JP, what he does,
      why the site exists, how it's funded/maintained. Honest, no fake persona.
      Draft goes to JP for approval before shipping.
- [ ] A3. New `/methodology.html` (EN, US tree first): how each country's data
      is ingested (SSA, ONS, INSEE, ABS, StatCan, ES/IT/NL registries), how
      ranks are computed, the <5-births suppression rule, where origins/
      meanings/famous-bearer data comes from (Wikipedia/Wikidata), update
      cadence, and a corrections contact. Link from footer + About; add to
      sitemap.
- [ ] A4. Fix 14 dead `/name/` links in 6 hand-written blog posts (also exists
      as a spawned background task — do whichever lands first, skip the other).

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
- [ ] B6–B14. Batches 4–12 (ranks #76-300) — not yet scheduled. Lesson
      for future batches: open with more structurally varied sentences
      from the start (vary "X peaked at #N in Y" / "X's count hit Z in Y"
      / "Y was X's best year" etc.) to cut down on the QA-rework loop —
      each batch so far has needed a second pass to break up self-similar
      openings within its own 25 entries, on top of avoiding prior batches.
- [ ] B15. Full QA pass over all 300; regenerate; commit/push

## Phase C — Ship & request review

- [ ] C1. Final regenerate + link/sitemap verification sweep (reuse Jul 20 checks)
- [ ] C2. Push; confirm live
- [ ] C3. Wait until ~Aug 20 (≥4 weeks after Jul 17 rejection AND ≥2 weeks
      after Phase B ships), then check "I have fixed the issues" → Request
      review in AdSense
- [ ] C4. If rejected a 3rd time: stop iterating on AdSense; shift to
      affiliate monetization + traffic building until organic traffic exists

## Review

(fill in after completion)
