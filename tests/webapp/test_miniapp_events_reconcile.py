"""Экран «Сверка календаря»: свежесть, шесть строк отчёта, окно событий (190 §5).

Проверки держат AC-11…AC-15 документа `docs/development/190`: экран пишет только
`dry_run`, «Сверить» шлёт `sources`/`dry_run`/`window`, счётчики отчёта совпадают
с ответом, при недоступном календаре прошлый снимок не подставляется, роль без
`schedule.read.all` читает фразу, а не кнопку.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

import json
import re

PARTICIPANT = {"X-SVOI-Role": "participant"}
STALE = {"X-Svoi-Fresh": "stale"}
BROKEN = {"X-Svoi-Fresh": "error"}

REFRESH = "/tools/google/refresh"


def _posts(page) -> list[dict]:
    """Тело каждого POST, ушедшего со страницы: чем экран говорит с сервером — факт."""
    calls: list[dict] = []
    page.on("request", lambda request: calls.append(
        {"url": request.url, "body": request.post_data or ""})
        if request.method == "POST" else None)
    return calls


def _refresh_body(calls: list[dict]) -> dict:
    """Тело единственного `refresh`: второй вызов означал бы двойную сверку."""
    hits = [call for call in calls if call["url"].endswith(REFRESH)]
    assert len(hits) == 1, f"«Сверить» отправила {len(hits)} запросов: {hits}"
    return json.loads(hits[0]["body"])


def test_экран_говорит_когда_календарь_читали(app_page) -> None:
    """Свежесть — время чтения, а не время сейчас: «Данные от HH:MM.» (186 §7)."""
    page = app_page("/webapp/events/reconcile")
    page.wait_for_load_state("networkidle")

    fresh = page.locator("#fresh").inner_text()
    assert re.fullmatch(r"Данные от \d{2}:\d{2}\.", fresh.strip()), fresh
    assert page.locator("#stale").is_hidden(), "плашка устаревания при свежих данных"
    assert page.locator("#check").is_visible(), "организатор потерял кнопку «Сверить»"
    assert page.svoi_errors == [], page.svoi_errors


def test_устаревшие_данные_помечены_плашкой(app_page) -> None:
    """`stale: true` от сервера — человек видит предупреждение, а не тишину."""
    page = app_page("/webapp/events/reconcile", headers=STALE)
    page.wait_for_load_state("networkidle")

    assert page.locator("#stale").is_visible(), "устаревшие данные не помечены"
    assert "устареть" in page.locator("#stale").inner_text()


def test_кнопка_сверить_шлёт_sources_dry_run_и_окно(app_page) -> None:
    """«Сверить» пишет только `dry_run`: источники, флаг и границы месяца (AC-11)."""
    page = app_page("/webapp/events/reconcile")
    calls = _posts(page)
    page.wait_for_load_state("networkidle")

    page.locator("#check").click()
    page.wait_for_selector("#report:not([hidden])", timeout=10_000)

    body = _refresh_body(calls)
    assert body["sources"] == ["calendar.read"], body
    assert body["dry_run"] is True, f"экран ушёл писать в календарь: {body}"
    window = body["window"]
    assert re.fullmatch(r"\d{4}-\d{2}-01", window["from"]), window
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", window["to"]), window


def test_отчёт_рисует_шесть_строк_и_счётчики(app_page) -> None:
    """Счётчики и пояснения — из ответа, по одной строке на предмет отчёта (§5.3)."""
    page = app_page("/webapp/events/reconcile")
    page.wait_for_load_state("networkidle")
    page.locator("#check").click()
    page.wait_for_selector("#report:not([hidden])", timeout=10_000)

    counts = {key: page.locator(f"#{key}").inner_text()
              for key in ("c-changed", "c-moved", "c-color", "c-label", "c-untyped")}
    assert counts == {"c-changed": "1", "c-moved": "1", "c-color": "1",
                      "c-label": "1", "c-untyped": "4"}, counts

    assert "19:00" in page.locator("#changed-note").inner_text()
    assert "20:00" in page.locator("#changed-note").inner_text()
    assert "цвет 8" in page.locator("#color-note").inner_text()
    assert "у типа — 11" in page.locator("#color-note").inner_text()
    assert "в заголовке [Р], тип — show" in page.locator("#label-note").inner_text()
    assert "событий без типа: 4" in page.locator("#untyped-note").inner_text()
    assert "людских — 3" in page.locator("#untyped-note").inner_text()
    assert page.locator("#error-row").is_hidden(), "успешная сверка пометила ошибку"


def test_недоступный_календарь_не_подставляет_прошлый_снимок(app_page) -> None:
    """Сбой чтения: ошибка словами, счётчики пусты — старый отчёт не выдаётся (AC-15)."""
    page = app_page("/webapp/events/reconcile", headers=BROKEN)
    page.wait_for_load_state("networkidle")
    page.locator("#check").click()
    page.wait_for_selector("#error-row:not([hidden])", timeout=10_000)

    assert "Календарь недоступен" in page.locator("#c-error").inner_text()
    for key in ("c-changed", "c-moved", "c-color", "c-label", "c-untyped"):
        assert page.locator(f"#{key}").inner_text() == "0", (
            f"при сбое показан прошлый снимок в {key}")


def test_роль_без_права_читает_фразу_вместо_кнопки(app_page) -> None:
    """Без `schedule.read.all` кнопки нет, а сказано, кто ведёт сверку (AC-16)."""
    page = app_page("/webapp/events/reconcile", headers=PARTICIPANT)
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#denied:not([hidden])", timeout=10_000)

    assert page.locator("#check").is_hidden(), "участнику показали кнопку «Сверить»"
    text = page.locator(".ui-screen").inner_text()
    assert "Сверку календаря ведёт организатор" in text, text
    assert page.svoi_errors == [], page.svoi_errors


def test_события_окна_листаются(app_page) -> None:
    """Первая страница и «Показать ещё»: вторая страница дописывается, кнопка уходит."""
    page = app_page("/webapp/events/reconcile")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#events:not([hidden])", timeout=10_000)

    assert page.locator("#event-list .ui-row").count() == 2
    assert page.locator("#shown").inner_text() == "показано 2"
    assert page.locator("#more").is_visible(), "есть вторая страница, а кнопки нет"

    page.locator("#more").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#event-list .ui-row').length === 3",
        timeout=10_000)

    assert page.locator("#shown").inner_text() == "показано 3"
    assert page.locator("#more").is_hidden(), "«Показать ещё» осталась без страниц"
