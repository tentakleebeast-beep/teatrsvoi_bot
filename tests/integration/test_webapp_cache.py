"""Кэширование Mini App: HTML не кэшируется, ассеты — своими заголовками.

Обоснование: обращение №41. 02.11.2026 клиент показывал старую страницу из кэша WebView:
оболочку чинили, а на телефоне оставался прошлый HTML, в журнале сервера не было ни одного
запроса с устройства. Страница должна приезжать заново при каждом открытии.
"""

from __future__ import annotations


def test_страницы_mini_app_не_кэшируются(api):
    for path in ("/webapp/home", "/webapp/week", "/webapp"):
        r = api.get(path)
        assert r.status_code == 200, path
        assert "no-store" in (r.headers.get("cache-control") or ""), path


def test_ассеты_не_получают_no_store(api):
    """У статики свои заголовки: оболочку кэшировать нельзя, SDK можно — но не запретом."""
    for path in ("/webapp/shell.js", "/webapp/ui.css"):
        r = api.get(path)
        assert r.status_code == 200, path
        assert "no-store" not in (r.headers.get("cache-control") or ""), path
