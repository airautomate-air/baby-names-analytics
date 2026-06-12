"""Build a Pinterest upload package: pins_queue.csv with 100 rows ready to
post to 5 themed boards, paired with the pre-rendered images in docs/pin/.

Run:  python3 pinterest_export.py
Out:  pinterest_queue/pins_queue.csv
      pinterest_queue/images/<board>/<slug>.png   (copies for easy upload)

CSV columns:
  board, slug, image_path, pinterest_title, pinterest_description, target_url

The free Pinterest scheduler holds up to 100 future-scheduled pins, so 100
total is the practical batch size before the first refill cycle.
"""
from __future__ import annotations
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
DOCS_PIN = ROOT / "docs" / "pin"
OUT = ROOT / "pinterest_queue"
OUT_IMG = OUT / "images"

BASE_URL = "https://namecharted.com/name"

# Boards: (board name, source year file, gender filter, slice).
# Vintage decades: SSA released 1920 as their oldest "modern" decade with
# robust counts. Pull from yob1920.txt + yob1930.txt + yob1940.txt union.
BOARDS = [
    ("Trending Baby Girl Names 2024", ["yob2024.txt"], "F", 0, 20),
    ("Trending Baby Boy Names 2024",  ["yob2024.txt"], "M", 0, 20),
    ("Vintage Baby Girl Names",       ["yob1920.txt", "yob1930.txt", "yob1940.txt"], "F", 0, 20),
    ("Vintage Baby Boy Names",        ["yob1920.txt", "yob1930.txt", "yob1940.txt"], "M", 0, 20),
    ("Unique Baby Names",             ["yob2024.txt"], None, 200, 20),  # rank 200+ from 2024
]


def load_names(files: list[str], gender: str | None) -> list[tuple[str, str, int]]:
    """Return [(name, gender, births)] sorted by births desc.
    When multiple year files are given, sum births per (name, gender)."""
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for f in files:
        with open(ROOT / f, encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split(",")
                if len(parts) != 3:
                    continue
                name, g, births = parts
                if gender and g != gender:
                    continue
                totals[(name, g)] += int(births)
    return sorted(
        [(n, g, b) for (n, g), b in totals.items()],
        key=lambda x: -x[2],
    )


def main() -> None:
    meanings = json.load(open(ROOT / "data" / "name_meanings.json"))
    OUT.mkdir(exist_ok=True)
    OUT_IMG.mkdir(exist_ok=True)

    rows: list[dict] = []
    used: set[str] = set()  # avoid the same name appearing in two boards

    for board, sources, gender, skip, take in BOARDS:
        ranked = load_names(sources, gender)
        picked = 0
        for name, g, _births in ranked[skip:]:
            if picked >= take:
                break
            slug = name.lower()
            if slug in used:
                continue
            pin_png = DOCS_PIN / f"{slug}.png"
            if not pin_png.exists():
                continue
            used.add(slug)
            picked += 1

            gender_word = "Girl" if g == "F" else "Boy"
            meaning_blurb = meanings.get(slug, "")

            # Pinterest titles: max 100 chars, lead with the name + hook.
            title = f"{name} — {gender_word} Name Meaning, Popularity & Trends"
            if len(title) > 100:
                title = f"{name} — {gender_word} Name Trends"

            # Pinterest descriptions: ~400 chars sweet spot; include 2–3 hashtags
            # at end. Pinterest's algorithm uses both title and description as
            # keyword signals, so we mention name + gender + key intent words
            # (popularity, meaning, history, origin).
            parts = [
                f"Explore the name {name}.",
                f"See how popular it is, when it peaked, and what it means.",
            ]
            if meaning_blurb:
                parts.append(f"Origin: {meaning_blurb}")
            parts.append(
                f"Full popularity chart, decade rankings, and similar names "
                f"on NameCharted."
            )
            tags = f"#babynames #{gender_word.lower()}names #namemeaning"
            description = " ".join(parts) + " " + tags
            description = description[:480]  # safety margin under 500

            url = f"{BASE_URL}/{slug}"

            # Copy the image into a per-board folder so uploads are organised.
            board_dir = OUT_IMG / board.replace(" ", "_")
            board_dir.mkdir(exist_ok=True)
            shutil.copy(pin_png, board_dir / f"{slug}.png")

            rows.append({
                "board": board,
                "slug": slug,
                "image_path": str((board_dir / f"{slug}.png").relative_to(ROOT)),
                "pinterest_title": title,
                "pinterest_description": description,
                "target_url": url,
            })

    out_csv = OUT / "pins_queue.csv"
    with open(out_csv, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "board", "slug", "image_path",
            "pinterest_title", "pinterest_description", "target_url",
        ])
        w.writeheader()
        w.writerows(rows)

    by_board = defaultdict(int)
    for r in rows:
        by_board[r["board"]] += 1
    print(f"Wrote {len(rows)} rows -> {out_csv}")
    for b, n in by_board.items():
        print(f"  {n:3d}  {b}")


if __name__ == "__main__":
    main()
