"""Сеть в оболочке: баннер «сайт недоступен» — по таймауту, по 5xx и его снятие.

Обращение №40: человек открывал Mini App, экран оставался в «Считаю…» и через
12 секунд появлялось сообщение про связь с сервером. Просили другие слова
(«сайт недоступен, попробуйте ещё раз») и порог 10 секунд.

Тесты проверяют поведение, а не строку в файле:

* запрос висит дольше 10 с — к баннеру приходит текст из обращения и «Повторить».
  Заодно замеряется момент появления баннера: при старом пороге 12 с тест падал бы,
  при слишком раннем — тоже;
* сервер ответил 5xx — тот же баннер, а экран не пустой (статичные блоки на месте);
* успешный ответ баннер снимает, а при обычном открытии его нет вовсе;
* ответ соседнего запроса (маяк загрузки, тихий вход) аварию не отменяет: иначе
  баннер жил миллисекунды и человек видел пустой экран.

Запуск:

    SVOI_WEBAPP_TESTS=1 env -u PYTHONPATH .venv/bin/python -m pytest tests/webapp \\
        -q --browser chromium
"""

from __future__ import annotations

import json
import time

# Слова из обращения №40. В коде сообщение — предложение: с заглавной буквы и
# точкой на конце, поэтому сравнение идёт по этой же фразе без регистра и точки.
MESSAGE = "сайт недоступен, попробуйте ещё раз"
RETRY = "Повторить"
DATA_URL = "**/webapp/home/data"


def _message(page) -> str:
    return page.inner_text("#net-error span").strip().lower().rstrip(".")


def test_баннер_по_таймауту(app_page):
    """Сервер молчит: через 10 с виден текст из обращения и кнопка «Повторить»."""
    page = app_page("/webapp/home")
    # Обработчик, который ничего не отвечает: запрос остаётся висеть, как при
    # мёртвом сервере. Страница при этом грузится — висит только запрос данных.
    page.route(DATA_URL, lambda route: None)

    started = time.monotonic()
    page.reload(wait_until="load")
    page.wait_for_selector("#net-error", state="visible", timeout=15_000)
    elapsed = time.monotonic() - started

    assert _message(page) == MESSAGE, page.inner_text("#net-error")
    assert page.locator("#net-error button").inner_text().strip() == RETRY
    # Порог из обращения: 10 с. Верхняя граница 11 с отсекает прежние 12 с,
    # нижняя 9 с — слишком ранний обрыв рабочего запроса.
    assert 9.0 <= elapsed <= 11.0, f"баннер через {elapsed:.1f} с, ожидалось 10 с"


def test_успешный_ответ_не_показывает_баннер(app_page):
    """Сайт ответил: сообщения нет, экран показывает данные."""
    page = app_page("/webapp/home")
    page.wait_for_function(
        "() => (document.getElementById('name').textContent || '') === 'Тестовый Админ'",
        timeout=10_000)

    assert page.locator("#net-error").count() == 0, "баннер при живом сервере"


def test_удачный_фоновый_запрос_не_снимает_баннер(app_page):
    """Баннер про данные экрана не стирается удачным запросом по другому адресу.

    Подпись четвёртой вкладки едет отдельным запросом `/webapp/nav`. Пока баннер
    снимался любым ответом ниже 500, этот фоновый запрос гасил «Сайт недоступен»,
    хотя данные экрана так и не пришли.
    """
    page = app_page("/webapp/home")
    page.route(DATA_URL, lambda route: route.fulfill(
        status=503, content_type="application/json",
        body=json.dumps({"ok": False, "detail": "service unavailable"})))

    page.reload(wait_until="load")
    page.wait_for_selector("#net-error", state="visible", timeout=10_000)

    # Фоновый запрос по другому адресу отвечает 200 — баннер обязан остаться.
    assert page.evaluate("() => fetch('/webapp/nav').then(r => r.status)") == 200
    page.wait_for_timeout(300)
    assert page.locator("#net-error").count() == 1, "удачный фоновый запрос снял баннер"


def test_ответ_5xx_показывает_баннер_и_экран_не_пустой(app_page):
    """Ошибка сервера — тот же баннер; статичные блоки экрана остаются, не пустота."""
    page = app_page("/webapp/home")
    page.route(DATA_URL, lambda route: route.fulfill(
        status=503, content_type="application/json",
        body=json.dumps({"ok": False, "detail": "service unavailable"})))

    page.reload(wait_until="load")
    page.wait_for_selector("#net-error", state="visible", timeout=10_000)

    assert _message(page) == MESSAGE, page.inner_text("#net-error")
    assert page.locator("#net-error button").inner_text().strip() == RETRY
    # Экран не пустой: шапка и карточка недели — статичные, они не зависят от ответа.
    # V-046: знак — маска слова, поэтому проверяется компонент, а не видимый текст.
    assert page.locator("#logo .ui-logo__mark").get_attribute("aria-label") == "СВОИ"
    assert page.locator("#week").is_visible()

    # Живой ответ снимает баннер: сообщение не «прилипает» до перезагрузки.
    page.unroute(DATA_URL)
    assert page.evaluate("() => fetch('/webapp/home/data').then(r => r.status)") == 200
    page.wait_for_selector("#net-error", state="detached", timeout=5_000)


def test_ответ_соседнего_запроса_аварию_не_отменяет(app_page):
    """503 по данным не снимается успехом чужого запроса.

    На каждой странице идёт больше одного запроса: маяк загрузки (`/webapp/boot`)
    и тихий вход (`/webapp/auth`) отвечают сразу после данных. Пока оболочка
    снимала баннер на любой ответ ниже 500, их 200/404 гасили сообщение через
    миллисекунды после появления — снаружи это и был «баннера нет при 503»
    (красные job'ы webapp/webapp-webkit, 01.11.2026).
    """
    page = app_page("/webapp/home")
    page.route(DATA_URL, lambda route: route.fulfill(
        status=503, content_type="application/json",
        body=json.dumps({"ok": False, "detail": "service unavailable"})))

    page.reload(wait_until="load")
    page.wait_for_selector("#net-error", state="visible", timeout=10_000)

    # Соседний запрос жив: ответ 200 приходит уже после баннера и не должен его снять.
    assert page.evaluate("() => fetch('/webapp/profile').then(r => r.status)") == 200
    page.wait_for_timeout(300)
    assert page.locator("#net-error").is_visible(), "чужой успешный ответ снял баннер"
    assert _message(page) == MESSAGE

    # А ответ на тот самый запрос аварию закрывает.
    page.unroute(DATA_URL)
    assert page.evaluate("() => fetch('/webapp/home/data').then(r => r.status)") == 200
    page.wait_for_selector("#net-error", state="detached", timeout=5_000)
