"""Compress PNGs via https://www.iloveimg.com/compress-image (Playwright)."""

from __future__ import annotations

import shutil
import sys
import time
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "assets" / "ui_raw"
OUT = ROOT / "assets" / "ui"
WORK = ROOT / "assets" / "_ilove_work"


def main() -> int:
    files = sorted(RAW.glob("*.png"))
    if not files:
        print("no raw pngs", file=sys.stderr)
        return 1

    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in WORK.iterdir():
        if old.is_file():
            old.unlink()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.goto("https://www.iloveimg.com/compress-image", wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector('input[type="file"]', timeout=60_000)
        page.set_input_files('input[type="file"]', [str(f) for f in files])

        # Wait until Compress button is enabled / process starts.
        page.wait_for_timeout(1500)
        # Prefer clicking the primary process button when visible.
        for label in ("Compress images", "Compress IMAGE", "Compress"):
            btn = page.get_by_role("button", name=label)
            if btn.count():
                try:
                    btn.first.click(timeout=5_000)
                    break
                except Exception:
                    pass

        # Wait for download affordance.
        download = None
        deadline = time.time() + 180
        while time.time() < deadline:
            # Single-file download or zip for multiple.
            dl_btn = page.locator(
                'a#download, button#download, a[href*="download"], '
                'button:has-text("Download"), a:has-text("Download")'
            )
            if dl_btn.count():
                try:
                    with page.expect_download(timeout=15_000) as di:
                        dl_btn.first.click()
                    download = di.value
                    break
                except Exception:
                    pass
            page.wait_for_timeout(1000)

        if download is None:
            page.screenshot(path=str(WORK / "ilove_fail.png"))
            print("download not found; screenshot saved", file=sys.stderr)
            browser.close()
            return 2

        archive = WORK / download.suggested_filename
        download.save_as(str(archive))
        browser.close()

    # Unpack into OUT (flat).
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(WORK / "unzipped")
        produced = list((WORK / "unzipped").rglob("*.png"))
    else:
        produced = [archive]

    if not produced:
        print("no pngs in download", file=sys.stderr)
        return 3

    # Map by stem (ilove may keep names).
    by_stem = {p.stem.lower().replace("_compressed", "").replace("-compressed", ""): p for p in produced}
    for src in files:
        key = src.stem.lower()
        match = by_stem.get(key)
        if match is None:
            # fuzzy: startswith
            match = next((p for s, p in by_stem.items() if key in s or s in key), None)
        if match is None:
            print(f"missing compressed for {src.name}, copying raw", file=sys.stderr)
            shutil.copy2(src, OUT / src.name)
            continue
        dest = OUT / src.name
        shutil.copy2(match, dest)
        print(f"{src.name}: {src.stat().st_size}B → {dest.stat().st_size}B")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
