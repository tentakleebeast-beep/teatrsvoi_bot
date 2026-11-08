"""Mini App в браузере: контраст темы, вёрстка на iPhone и живучесть токена.

Эти тесты ловят ровно тот класс дефектов, который проявился на iPhone 30.10.2026:
тёмный фон с тёмным текстом, пустые экраны и «сессия истекла» на первом действии.

Правила и требования раздела: docs/development/81-webapp-browser-tests.md.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q
    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser webkit
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.webapp.helpers import contrast as _contrast
from tests.webapp.helpers import luminance as _luminance

SCREENS = pathlib.Path(__file__).resolve().parent / "screenshots"
BASELINE = pathlib.Path(__file__).resolve().parent / "baseline"

PAGES = {
    "tx": "/webapp/tx",
    "week": "/webapp/week",
    "dues": "/webapp/dues",
    "person": "/webapp/person?id=30",
    "profile": "/webapp",
}

# Схему задаём на уровне контекста браузера: themeParams в SDK — свойство только
# для чтения, подменить его из теста нельзя. Именно так ведёт себя WebView: схема
# приходит из системы, а цвета — из themeParams, которых может не быть.
SCHEMES = ("light", "dark")

@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("screen", list(PAGES))
def test_тема_читаема(app_page, screen, scheme):
    """Текст обязан контрастировать с фоном, и тема обязана реально применяться."""
    page = app_page(PAGES[screen], color_scheme=scheme)

    background = page.evaluate("getComputedStyle(document.body).backgroundColor")
    foreground = page.evaluate("getComputedStyle(document.body).color")
    ratio = _contrast(foreground, background)
    lum = _luminance(background)

    if scheme == "dark":
        assert lum < 0.25, f"{screen}/{scheme}: фон не тёмный ({background})"
    else:
        assert lum > 0.7, f"{screen}/{scheme}: фон не светлый ({background})"
    assert ratio >= 4.5, (f"{screen}/{scheme}: контраст {ratio:.1f} — "
                          f"текст {foreground} на фоне {background}")

    browser = page.context.browser.browser_type.name
    target = SCREENS / browser / f"{screen}-{scheme}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target), full_page=True)


def test_тёмный_фон_без_цвета_текста(app_page):
    """iOS присылает bg_color, но не text_color — текст обязан остаться светлым.

    Именно этот случай дал «чёрный фон, не читаемо» на iPhone 30.10.2026.
    """
    page = app_page("/webapp/week")
    page.evaluate("window.SVOI.applyTheme({bg_color: '#000000'})")

    background = page.evaluate("getComputedStyle(document.body).backgroundColor")
    foreground = page.evaluate("getComputedStyle(document.body).color")
    assert _luminance(background) < 0.1, f"фон не чёрный: {background}"
    assert _luminance(foreground) > 0.7, f"текст остался тёмным: {foreground}"
    assert _contrast(foreground, background) >= 4.5


@pytest.mark.parametrize("screen", list(PAGES))
def test_нет_горизонтальной_прокрутки(app_page, screen):
    """На iPhone 390px страница не должна уезжать вбок."""
    page = app_page(PAGES[screen])
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"{screen}: содержимое шире экрана на {overflow}px"


@pytest.mark.parametrize("screen,marker", [
    ("tx", "option"),          # категории подставлены из API
    ("week", "Отмечено"),      # неделя отрисована
    ("dues", "осталось"),      # взносы посчитаны
    ("person", "Тестовый"),    # карточка участника
])
def test_данные_пришли_и_отрисованы(app_page, screen, marker):
    page = app_page(PAGES[screen])
    page.wait_for_timeout(700)
    if marker == "option":
        assert page.locator("#tx-category option").count() > 0, "список категорий пуст"
    else:
        assert marker.lower() in page.inner_text("body").lower(), \
            f"{screen}: нет «{marker}» на странице"


def test_протухший_токен_обновляется_сам(app_page):
    """Токен живёт 30 минут; старый в localStorage не должен давать «сессия истекла».

    Стенд отвечает 401 на любой чужой Bearer, поэтому страница обязана тихо
    перевыпустить токен через initData и повторить запрос.
    """
    page = app_page("/webapp/tx")          # сперва обычная загрузка
    page.evaluate("localStorage.setItem('svoi_token', 'stale-token')")
    page.goto(page.url.split("#")[0] + "#", wait_until="load")
    page.reload(wait_until="load")
    page.wait_for_timeout(1200)

    text = page.inner_text("body")
    assert "Сессия истекла" not in text, "страница не обновила токен"
    assert page.locator("#tx-category option").count() > 0, \
        "категории не загрузились после перевыпуска"


def test_ошибка_валидации_читаема(app_page):
    """Ответ 422 не должен показываться как [object Object]."""
    page = app_page("/webapp/tx")
    page.wait_for_timeout(500)
    page.select_option("#tx-category", index=1)   # без категории страница не отправит форму
    page.fill("#tx-amount", "100")
    page.fill("#tx-purpose", "__422__")     # стенд отвечает 422 со списком detail
    page.click("#tx-save")
    page.wait_for_timeout(800)
    status = page.inner_text("#tx-alert")
    field_error = page.inner_text("#err-amount")
    assert "[object Object]" not in status + field_error, "сырой объект в тексте ошибки"
    assert "сумма" in field_error.lower() or "сумма" in status.lower(), \
        f"нет имени поля в тексте ошибки: {status!r} / {field_error!r}"


def test_экран_папки_без_номера_не_тупик(app_page):
    """Пусто — не тупик (docs/development/91 §1 п.9, отчёт 142 §3 строка 17).

    `/webapp/folder` без `?id=` показывал только «Папка не выбрана» — человек
    не понимал, откуда в папку попасть. Пустое состояние обязано назвать
    источник и дать живой путь в «Файлы»; адрес при этом остаётся открывающимся
    (редиректа нет, 141 §2.4).
    """
    page = app_page("/webapp/folder")
    page.wait_for_selector("#no-folder:not([hidden])")

    text = page.inner_text("#no-folder")
    assert "Папки открываются из «Файлов»" in text, f"нет пути: {text!r}"

    link = page.locator('#no-folder a[href="/webapp/files"]')
    assert link.count() == 1, "в пустом состоянии нет ссылки на «Файлы»"
    assert link.is_visible()

    # Снимок состояния — для разбора карты и сравнения с 142 §3 строка 17.
    browser = page.context.browser.browser_type.name
    target = SCREENS / browser / "folder-empty-light.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target), full_page=True)

    link.click()
    page.wait_for_url("**/webapp/files")
    assert page.title() == "Файлы"
