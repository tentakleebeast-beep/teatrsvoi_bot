"""Экран «Сервисы»: состояние приложений словами (docs/development/92 §2.5).

Проверяем то, из-за чего экран появился: админ не должен идти в терминал, чтобы
понять «бот молчит — это бот или база».
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import routes, services  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")


def test_страница_собрана_на_системе():
    page = services.SERVICES_PAGE
    assert page.startswith("<!doctype html>")
    assert 'class="ui-page"' in page
    assert "ui-appbar__back" in page and 'href="/webapp/more"' in page
    assert 'id="recheck"' in page and 'id="rows"' in page
    assert "Проверить снова" in page
    assert not EMOJI.search(page), "эмодзи на экране сервисов"
    assert "<table" not in page


def test_состояние_передаётся_словом():
    """Цвет помогает, но состояние читается и без него."""
    page = services.SERVICES_PAGE
    for word in ("работает", "внимание", "недоступен", "нет данных"):
        assert word in page, f"нет слова состояния «{word}»"


def test_маршрут_страницы_есть():
    router = routes.build_router(actor=lambda: {}, caps_for=lambda who: [])
    paths = [route.path for route in router.routes]
    assert "/webapp/services" in paths
