"""Снимок текущей главной (E2): «было» рядом с `e2_home_shot.py` («стало»).

    env -u PYTHONPATH ../../.venv/bin/python scripts/probes/e2_home_before.py http://127.0.0.1:8831 /tmp/e2-before

Адреса экранов не содержит: главная — из `TABS`.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from e2_home_layout import HOME_URL  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from tests.webapp.conftest import TELEGRAM_HASH  # noqa: E402

VIEWPORTS = {"390": {"width": 390, "height": 844},
             "320": {"width": 320, "height": 568}}


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8831"
    prefix = sys.argv[2] if len(sys.argv) > 2 else "/tmp/e2-before"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for label, viewport in VIEWPORTS.items():
            page = browser.new_page(viewport=viewport, color_scheme="light",
                                    device_scale_factor=2)
            page.goto(base + HOME_URL + TELEGRAM_HASH, wait_until="load")
            page.wait_for_function("() => !!document.querySelector('.ui-home')", timeout=10_000)
            page.wait_for_timeout(300)
            page.screenshot(path=f"{prefix}-{label}.png", full_page=True)
            print(f"{prefix}-{label}.png")
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
