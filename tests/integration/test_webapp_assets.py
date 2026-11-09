"""Статика Mini App отдаётся браузеру: стили, оболочка, SDK Telegram.

Дефект 01.11.2026: после разреза tool-api на роутеры (`app/api/`) путь к статике
считался от каталога модуля — `/webapp/ui.css` и `/webapp/shell.js` отдавали 500.
Mini App открывался без CSS (текст наложением) и без оболочки (вечная загрузка).
Тесты держат контракт: файл существует, отдаётся с правильным типом и содержимым.

Правила и требования раздела: docs/development/81-webapp-browser-tests.md.
"""

from __future__ import annotations

import re


def test_стили_отдаются(client):
    r = client.get("/webapp/ui.css")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/css")
    assert len(r.text) > 5000, "CSS пришёл пустым или обрезанным"
    assert ".ui-" in r.text, "в CSS нет классов дизайн-системы"


def test_оболочка_отдаётся(client):
    r = client.get("/webapp/shell.js")
    assert r.status_code == 200, r.text
    assert "javascript" in r.headers["content-type"]
    assert len(r.text) > 5000, "оболочка пришла пустой"
    assert "SVOI" in r.text, "в оболочке нет глобального объекта SVOI"


def test_sdk_telegram_отдаётся(client):
    r = client.get("/webapp/tg.js")
    assert r.status_code == 200, r.text
    assert "javascript" in r.headers["content-type"]
    assert len(r.text) > 20000


def test_страница_ссылается_на_живые_адреса(client):
    """Ссылки в разметке и реальные маршруты статики должны совпадать."""
    page = client.get("/webapp/more")
    assert page.status_code == 200
    assert 'href="/webapp/ui.css"' in page.text
    assert client.get("/webapp/ui.css").status_code == 200


def test_знак_отдаётся(client):
    """К5: V-046 — знак студии отдаётся собственным маршрутом как PNG.

    Маска в CSS без живого файла не рисует ничего (знак прозрачный), поэтому
    проверяется не «маршрут есть», а «по адресу приезжает PNG» — как у стилей
    и оболочки после дефекта 01.11.2026.
    """
    response = client.get("/webapp/logo.png")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n"), "пришёл не PNG"
    assert len(response.content) > 10_000, "файл знака пустой или обрезан"
    assert response.headers["cache-control"] == "public, max-age=86400"


def test_маска_знака_отдаётся(client):
    """К6: V-046 — адрес из `url(...)` в CSS ведёт на живой маршрут.

    `test_static_contract.py` видит только `href|src` с `.css|.js` и PNG из CSS
    не ловит: без этой проверки адрес маски мог бы разойтись с маршрутом молча.
    """
    css = client.get("/webapp/ui.css").text
    urls = set(re.findall(r'url\("(/webapp/[^"]+)"\)', css))
    assert "/webapp/logo.png" in urls, f"в CSS нет адреса знака: {sorted(urls)}"
    for url in sorted(urls):
        assert client.get(url).status_code == 200, f"адрес из CSS не отдаётся: {url}"
