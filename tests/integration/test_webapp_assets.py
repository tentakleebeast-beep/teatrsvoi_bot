"""Статика Mini App отдаётся браузеру: стили, оболочка, SDK Telegram.

Дефект 01.11.2026: после разреза tool-api на роутеры (`app/api/`) путь к статике
считался от каталога модуля — `/webapp/ui.css` и `/webapp/shell.js` отдавали 500.
Mini App открывался без CSS (текст наложением) и без оболочки (вечная загрузка).
Тесты держат контракт: файл существует, отдаётся с правильным типом и содержимым.

Правила и требования раздела: docs/development/81-webapp-browser-tests.md.
"""

from __future__ import annotations


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
