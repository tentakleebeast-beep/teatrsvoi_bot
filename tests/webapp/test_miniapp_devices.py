"""Mini App на профилях устройств: вёрстка, тема, медленная сеть.

Проверяем то, что нельзя увидеть на одном вьюпорте: узкий экран 320 px (Galaxy S9+),
крупный 430 px (iPhone 15 Pro Max), плотность пикселей и тач. Движок у профиля свой:
iPhone — WebKit, Android — Chromium, поэтому набор гоняется дважды.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser webkit
    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium

Правила и требования раздела: docs/development/81-webapp-browser-tests.md.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.webapp.helpers import contrast, luminance
from tests.webapp.test_miniapp_visual import PAGES

SCREENS = pathlib.Path(__file__).resolve().parent / "screenshots"
DEVICES = ("iPhone 14", "iPhone 15 Pro Max", "Pixel 7", "Galaxy S9+")
KEY_SCREENS = ("tx", "week", "person")


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize("screen", KEY_SCREENS)
def test_вёрстка_на_устройстве(device_page, device, screen):
    """На реальном размере экрана ничего не уезжает вбок и не исчезает."""
    page = device_page(PAGES[screen], device)
    page.wait_for_timeout(400)

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"{device}/{screen}: содержимое шире экрана на {overflow}px"

    # основные органы управления на месте и не нулевого размера
    assert page.locator("body").bounding_box()["height"] > 200, f"{device}/{screen}: пустая страница"

    target = SCREENS / page.context.browser.browser_type.name / f"{device.replace(' ', '-')}-{screen}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target), full_page=True)


@pytest.mark.parametrize("device", DEVICES)
def test_тема_на_устройстве(device_page, device):
    """Тёмная тема читаема и на 320 px, и на 430 px."""
    page = device_page("/webapp/week", device, color_scheme="dark")
    background = page.evaluate("getComputedStyle(document.body).backgroundColor")
    foreground = page.evaluate("getComputedStyle(document.body).color")

    assert luminance(background) < 0.25, f"{device}: фон не тёмный ({background})"
    assert contrast(foreground, background) >= 4.5, f"{device}: контраст ниже нормы"


@pytest.mark.parametrize("device", ("iPhone 14", "Pixel 7"))
def test_медленная_сеть(device_page, device):
    """На 3G страница обязана показать данные, а не вечный «загружаю».

    Задержка ставится на ответы API: 700 мс на запрос — примерно как мобильный
    интернет в зале, где репетируют.
    """
    page = device_page("/webapp/week", device)

    def slow(route):
        page.wait_for_timeout(700)      # задержка ответа API
        route.continue_()

    page.route("**/webapp/availability/**", slow)

    page.reload(wait_until="load")
    page.wait_for_timeout(2500)

    text = page.inner_text("body").lower()
    assert "отмечено" in text or "октября" in text, f"{device}: страница не дождалась данных"
