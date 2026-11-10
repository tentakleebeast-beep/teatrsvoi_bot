"""Оболочка сводки занятости по праву: строки нет без права, отказ честный.

`GET /webapp/grid` требует `schedule.read.all` (FR-H9, docs/development/92 §7,
карта t_52bf3106). Значит и строка «Сводка занятости» на `/webapp/week` должна
появляться только у того, кому сводка открыта, а прямой заход на
`/webapp/week/summary` участником должен говорить про отсутствие права, а не
про сеть (docs/architecture/25 §9.2: оболочка может только не показывать
кнопку — решает сервер).

Карта t_70a6d8e5. Роль на стенде задаётся заголовком `X-SVOI-Role`:
`participant` — без `schedule.read.all`, иначе организатор (tests/webapp/server.py).

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations

PARTICIPANT = {"X-SVOI-Role": "participant"}


def _week_loaded(page) -> None:
    """Ждём, пока страница недели отрисовала первый день (стенд отдаёт 26.10)."""
    page.wait_for_function(
        "() => document.querySelector('[data-day-slot=\"0\"]')"
        " && document.querySelector('[data-day-slot=\"0\"]').dataset.state === 'busy'",
        timeout=10_000)


def test_fr_h9_участник_не_видит_строку_сводки(app_page) -> None:
    """FR-H9, t_70a6d8e5: без `schedule.read.all` строки «Сводка занятости» нет.

    Строка остаётся в разметке (страница — публичная константа), но оболочка
    прячет её по `data-svoi-cap` из ответа `/webapp/nav` — до того, как снимется
    скелет загрузки прав (карта t_f279a0eb), поэтому нажать её участник не успевает
    и не видит её даже на кадр.
    """
    page = app_page("/webapp/week", headers=PARTICIPANT)
    _week_loaded(page)

    assert page.locator("#to-summary").count() == 1, "строка исчезла из разметки"
    assert not page.locator("#to-summary").is_visible(), (
        "участнику без schedule.read.all показана строка «Сводка занятости»")
    assert "Сводка занятости" not in page.locator(".ui-screen").inner_text()


def test_fr_h9_организатор_видит_строку_сводки(app_page) -> None:
    """FR-H9, t_70a6d8e5: у организатора строка сводки на месте и ведёт на экран."""
    page = app_page("/webapp/week")
    _week_loaded(page)

    row = page.locator("#to-summary")
    assert row.is_visible(), "организатор потерял строку «Сводка занятости»"
    assert "Сводка занятости" in row.inner_text()
    href = row.get_attribute("href") or ""
    assert href.startswith("/webapp/week/summary"), href


def test_fr_h9_страница_сводки_у_участника_говорит_о_праве(app_page) -> None:
    """FR-H9, t_70a6d8e5: 403 — это не сеть; экран называет причину отказа.

    Прямой заход участником на `/webapp/week/summary` (закладка, старая ссылка)
    не оставляет пустой экран и не советует «проверить сеть».
    """
    page = app_page("/webapp/week/summary", headers=PARTICIPANT)
    page.wait_for_function(
        "() => { var s = document.getElementById('status');"
        " return s && s.textContent.trim().length > 0; }",
        timeout=10_000)

    text = page.locator("#status").inner_text().strip()
    assert "у организатора" in text, text
    assert "сеть" not in text.lower(), f"экран врёт про сеть: {text}"
