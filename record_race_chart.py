"""Record the homepage race chart as a webm video, one per locale.

The animated SVG chart is great on desktop but laggy on mid-range phones
(78 paths x frame redraws is too much for slower GPUs). We pre-record one
full loop per locale and serve the video on mobile via CSS media query.

Run after generate_site.py:
    python3 record_race_chart.py

Outputs: docs/{prefix}/top-race.webm for each locale.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / 'docs'
PORT = 8848  # local server port

LOCALES = [
    ('US', ''),
    ('FR', '/fr'),
    ('GB', '/uk'),
    ('AU', '/au'),
    ('CA', '/ca'),
    ('ES', '/es'),
    ('IT', '/it'),
    ('NL', '/nl'),
]

# Chart playback: 145 yrs * 90ms/frame ~ 13s. We give it 14s + 1s buffer.
RECORD_SECONDS = 15

VIEWPORT_W = 1200
VIEWPORT_H = 760


def start_http_server() -> subprocess.Popen:
    return subprocess.Popen(
        ['python3', '-m', 'http.server', str(PORT), '--bind', '127.0.0.1'],
        cwd=str(DOCS),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# JS snippet that strips the page down to only the race-stage element so the
# recorded video frame contains ONLY the chart, no surrounding chrome.
# Uses safe DOM methods instead of setting innerHTML.
STRIP_JS = r"""
() => {
    const stage = document.querySelector('.race-stage');
    if (!stage) throw new Error('no race-stage');
    while (document.body.firstChild) document.body.removeChild(document.body.firstChild);
    document.body.appendChild(stage);
    document.body.style.cssText = 'margin:0;padding:0;background:#F7F8FA;';
    stage.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;margin:0;border-radius:0;border:0;box-shadow:none;';
}
"""


def record_one(pw, cc: str, path_prefix: str) -> None:
    print(f"recording {cc}...", flush=True)
    out_dir = DOCS / path_prefix.lstrip('/') if path_prefix else DOCS
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        browser = pw.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled'],
        )
        context = browser.new_context(
            viewport={'width': VIEWPORT_W, 'height': VIEWPORT_H},
            device_scale_factor=1,
            record_video_dir=tmp,
            record_video_size={'width': VIEWPORT_W, 'height': VIEWPORT_H},
        )
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{PORT}{path_prefix}/index.html",
                  wait_until='domcontentloaded')

        page.evaluate(STRIP_JS)
        page.wait_for_function(
            "document.getElementById('raceScrub') && parseInt(document.getElementById('raceScrub').max,10) > 0",
            timeout=15_000,
        )
        # Restart so recording begins at year 0.
        page.evaluate("() => { const r = document.getElementById('raceRestart'); if (r) r.click(); }")

        page.wait_for_timeout(RECORD_SECONDS * 1000)

        video_path = page.video.path() if page.video else None
        context.close()
        browser.close()

        if not video_path:
            print(f"  {cc}: no video produced", flush=True)
            return
        dst = out_dir / 'top-race.webm'
        shutil.move(video_path, dst)
        size_kb = dst.stat().st_size / 1024
        print(f"  {cc}: {dst.relative_to(ROOT)}  ({size_kb:.0f} KB)", flush=True)


def main() -> None:
    server = start_http_server()
    try:
        with sync_playwright() as pw:
            for cc, prefix in LOCALES:
                record_one(pw, cc, prefix)
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == '__main__':
    main()
