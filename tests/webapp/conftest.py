"""Обвязка браузерных тестов Mini App: локальный стенд + эмуляция Telegram.

Тесты по умолчанию пропускаются: они требуют playwright с установленными движками.
Включаются явно:

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q
    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser webkit
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from urllib.parse import quote

import pytest

ENABLED = os.getenv("SVOI_WEBAPP_TESTS") == "1"

# Так Telegram передаёт данные Mini App веб-клиенту: SDK разбирает hash и заполняет
# window.Telegram.WebApp.initData. Сервер на стенде проверку подписи не делает —
# она покрыта отдельными тестами webapp.py.
INIT_DATA = "user=%7B%22id%22%3A30%2C%22first_name%22%3A%22Test%22%7D&auth_date=1793000000&hash=testhash"
TELEGRAM_HASH = (f"#tgWebAppData={quote(INIT_DATA, safe='')}"
                 f"&tgWebAppVersion=7.0&tgWebAppPlatform=desktop&tgWebAppThemeParams=%7B%7D")

VIEWPORT = {"width": 390, "height": 844}   # iPhone 14/15 в CSS-пикселях

# Профили устройств: размер, плотность пикселей, тач и user-agent берутся из
# описаний playwright. Движок у профиля свой: iPhone — WebKit, Android — Chromium,
# поэтому набор гоняется дважды, а лишние пары пропускаются.
DEVICES = {
    "iPhone 14": "webkit",
    "iPhone 15 Pro Max": "webkit",
    "Pixel 7": "chromium",
    "Galaxy S9+": "chromium",      # самый узкий: 320 px
}


def pytest_collection_modifyitems(config, items):
    if ENABLED:
        return
    skip = pytest.mark.skip(reason="браузерные тесты: включите SVOI_WEBAPP_TESTS=1")
    for item in items:
        if "tests/webapp" in str(item.fspath):
            item.add_marker(skip)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def webapp_server() -> Iterator[str]:
    """Поднимает стенд на свободном порту и отдаёт базовый адрес."""
    pytest.importorskip("uvicorn")
    import uvicorn

    from tests.webapp.server import app as stand

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(stand, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while time.time() < deadline:
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    else:  # pragma: no cover
        raise RuntimeError("стенд Mini App не поднялся")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def app_page(browser, webapp_server):
    """Открывает страницу как Telegram: свой контекст на схему (light/dark)."""
    contexts = []

    def open_page(path: str, color_scheme: str = "light", headers: dict | None = None):
        context = browser.new_context(viewport=VIEWPORT, color_scheme=color_scheme,
                                      extra_http_headers=headers or {})
        contexts.append(context)
        page = context.new_page()
        # JS-ошибки страницы: экран не должен «работать», падая в консоль. Список
        # вешается до перехода, иначе ошибки первого кадра теряются.
        page.svoi_errors = []
        page.on("pageerror", lambda exc: page.svoi_errors.append(str(exc)))
        page.goto(f"{webapp_server}{path}{TELEGRAM_HASH}", wait_until="load")
        # SDK создаёт window.Telegram; ждём, пока страница сама получит токен
        try:
            page.wait_for_function(
                "() => window.SVOI && (window.SVOI.token || !window.Telegram?.WebApp?.initData)",
                    timeout=10_000)
        except Exception:
            print("ДИАГНОСТИКА url:", page.url)
            print("ДИАГНОСТИКА html:", page.content()[:400].replace("\n", " "))
            print("ДИАГНОСТИКА svói:", page.evaluate("typeof window.SVOI"))
            print("ДИАГНОСТИКА telegram:", page.evaluate("typeof window.Telegram"))
            raise
        return page

    yield open_page
    for context in contexts:
        context.close()


@pytest.fixture(scope="session")
def device_descriptors(playwright) -> dict:
    """Описания устройств: viewport, dpr, touch, user-agent.

    Экземпляр playwright берём у pytest-playwright: свой запускать нельзя —
    sync API внутри его event loop падает.
    """
    return dict(playwright.devices)


@pytest.fixture
def device_page(browser, webapp_server, device_descriptors):
    """Открывает страницу в профиле устройства с его движком."""
    contexts = []

    def open_page(path: str, device: str, color_scheme: str = "light"):
        engine = DEVICES[device]
        current = browser.browser_type.name
        if engine != current:
            pytest.skip(f"{device} проверяется в {engine}, сейчас {current}")

        descriptor = dict(device_descriptors[device])
        descriptor.pop("default_browser_type", None)
        context = browser.new_context(color_scheme=color_scheme, **descriptor)
        contexts.append(context)
        page = context.new_page()
        page.svoi_errors = []
        page.on("pageerror", lambda exc: page.svoi_errors.append(str(exc)))
        page.goto(f"{webapp_server}{path}{TELEGRAM_HASH}", wait_until="load")
        page.wait_for_function(
            "() => window.SVOI && (window.SVOI.token || !window.Telegram?.WebApp?.initData)",
            timeout=10_000)
        return page

    yield open_page
    for context in contexts:
        context.close()
