"""Generate pinterest_queue/helper.html — a one-page upload helper for the
95 remaining pins. Self-contained (no server needed): just open in a browser.
Each row shows a thumbnail, copy buttons for title/description/url, board,
suggested schedule slot, and a checkbox persisted in localStorage.
"""
from __future__ import annotations
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
CSV_IN = ROOT / "pinterest_queue" / "pins_queue.csv"
OUT = ROOT / "pinterest_queue" / "helper.html"

COVERS_DONE = {"olivia", "liam", "mary", "robert", "audrey"}
TIMES = ["09:00", "12:00", "15:00", "18:00", "21:00"]


def main() -> None:
    rows = list(csv.DictReader(open(CSV_IN)))
    pending = [r for r in rows if r["slug"] not in COVERS_DONE]

    by_board: dict[str, list[dict]] = defaultdict(list)
    for r in pending:
        by_board[r["board"]].append(r)

    # Sort each board's pins alphabetically by slug — easier to find a name in
    # the helper when matching against Pinterest's draft list.
    for b in by_board:
        by_board[b].sort(key=lambda r: r["slug"])

    boards = list(by_board.keys())
    max_per_board = max(len(v) for v in by_board.values())

    # Group by board for the upload workflow: user drags 20 pins into one
    # board, fills each draft, sets the schedule, repeats per board. Each
    # board's 20 pins get spread one-per-day so Pinterest sees a steady
    # cadence; each board has its own time-of-day slot so they don't all
    # fire at once.
    today = datetime.now().date()
    scheduled = []
    for i, b in enumerate(boards):
        for day_idx, r in enumerate(by_board[b]):
            r = dict(r)
            slot = today + timedelta(days=day_idx)
            r["schedule_day"] = slot.strftime("%a %b %d")
            r["schedule_time"] = TIMES[i]
            r["thumb_url"] = f"https://namecharted.com/pin/{r['slug']}.png"
            scheduled.append(r)

    payload = json.dumps(scheduled, ensure_ascii=False)

    # Note: the page builds its DOM with createElement + textContent (no innerHTML)
    # so titles/descriptions cannot inject markup even though they come from a
    # trusted source.
    html = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>NameCharted — Pinterest Upload Helper</title>
<style>
  :root { --ink:#1B2440; --teal:#149E91; --coral:#FF6B5C; --canvas:#F7F8FA; --muted:#5B6678; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, system-ui, sans-serif; background: var(--canvas); color: var(--ink); }
  header { position: sticky; top: 0; background: var(--ink); color: #fff; padding: 16px 24px; z-index: 10; display: flex; gap: 24px; align-items: center; }
  header h1 { font-size: 18px; margin: 0; }
  header .progress { margin-left: auto; font-size: 14px; opacity: 0.9; }
  header .progress strong { color: var(--teal); font-size: 18px; }
  header button { background: transparent; color: #fff; border: 1px solid #fff5; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  header button:hover { background: #fff2; }
  main { max-width: 900px; margin: 0 auto; padding: 24px; }
  .card { background: #fff; border-radius: 12px; padding: 16px; margin-bottom: 12px; display: grid; grid-template-columns: 80px 1fr auto; gap: 16px; box-shadow: 0 1px 3px #0001; transition: opacity .2s; }
  .card.done { opacity: 0.4; }
  .card img { width: 80px; height: 120px; object-fit: cover; border-radius: 6px; background: #eee; }
  .meta { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .schedule { font-size: 12px; color: var(--coral); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .board { font-size: 13px; color: var(--teal); font-weight: 600; }
  .field { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .field label { font-weight: 600; color: var(--muted); font-size: 11px; text-transform: uppercase; min-width: 50px; }
  .field .val { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); }
  .field button { background: var(--teal); color: #fff; border: 0; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 11px; }
  .field button:hover { background: #0f7c72; }
  .field button.copied { background: var(--coral); }
  .check { display: flex; align-items: center; }
  .check input { width: 24px; height: 24px; cursor: pointer; }
  .day-header { font-weight: 700; margin: 24px 0 8px; color: var(--muted); text-transform: uppercase; font-size: 12px; letter-spacing: 1px; }
</style></head><body>
<header>
  <h1>NameCharted Pinterest Upload Helper</h1>
  <div class="progress"><strong id="done">0</strong> / __TOTAL__ scheduled</div>
  <button id="toggle-done">Toggle done</button>
  <button id="reset">Reset</button>
</header>
<main id="root"></main>
<script>
const data = __PAYLOAD__;
let hideDone = false;
const done = JSON.parse(localStorage.getItem('npins') || '{}');

document.getElementById('toggle-done').onclick = () => { hideDone = !hideDone; render(); };
document.getElementById('reset').onclick = () => {
  if (confirm('Reset all checkboxes?')) { localStorage.removeItem('npins'); for (const k in done) delete done[k]; render(); }
};

function save() { localStorage.setItem('npins', JSON.stringify(done)); }

function copy(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'Copied';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1200);
  });
}

// Build a single field row with a copy button (uses textContent — no HTML).
function field(label, value) {
  const row = document.createElement('div');
  row.className = 'field';
  const l = document.createElement('label');
  l.textContent = label;
  const v = document.createElement('span');
  v.className = 'val';
  v.textContent = value;
  const b = document.createElement('button');
  b.textContent = 'Copy';
  b.onclick = () => copy(value, b);
  row.append(l, v, b);
  return row;
}

function render() {
  const root = document.getElementById('root');
  root.replaceChildren();
  let lastSection = '';
  let doneCount = 0;
  for (const r of data) {
    if (done[r.slug]) doneCount++;
    if (hideDone && done[r.slug]) continue;
    if (r.board !== lastSection) {
      const h = document.createElement('div');
      h.className = 'day-header';
      h.textContent = r.board;
      root.appendChild(h);
      lastSection = r.board;
    }
    const card = document.createElement('div');
    card.className = 'card' + (done[r.slug] ? ' done' : '');

    const img = document.createElement('img');
    img.src = r.thumb_url;
    img.alt = r.slug;
    img.loading = 'lazy';

    const meta = document.createElement('div');
    meta.className = 'meta';
    const sched = document.createElement('div');
    sched.className = 'schedule';
    sched.textContent = r.schedule_day + '  ·  ' + r.schedule_time;
    meta.appendChild(sched);
    meta.appendChild(field('Title', r.pinterest_title));
    meta.appendChild(field('Desc',  r.pinterest_description));
    meta.appendChild(field('Link',  r.target_url));

    const check = document.createElement('div');
    check.className = 'check';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = !!done[r.slug];
    cb.onchange = () => { done[r.slug] = cb.checked; save(); render(); };
    check.appendChild(cb);

    card.append(img, meta, check);
    root.appendChild(card);
  }
  document.getElementById('done').textContent = doneCount;
}
render();
</script>
</body></html>"""
    html = html.replace("__PAYLOAD__", payload).replace("__TOTAL__", str(len(scheduled)))
    OUT.write_text(html)
    print(f"Wrote {OUT}")
    print(f"  {len(scheduled)} pins grouped by board ({len(boards)} boards × ~{max_per_board} pins each)")


if __name__ == "__main__":
    main()
