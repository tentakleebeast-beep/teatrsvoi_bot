"""Выдача роли «педагог» в Mini App после V-037: без дисциплины роль выдаётся (Э1, Г16).

Критерий карты Э1 в редакции V-037 (решение владельца, карта `t_6e49e144`):
«Given администратор, When выдаёт в карточке «Педагог» без дисциплины, Then роль
выдаётся, запрос уходит, а ответ несёт предупреждение `master_without_discipline`».
Прежняя формулировка («без дисциплины запрос не уходит», `master_discipline_required`)
снята вместе с серверным запретом — тест на неё держал бы поведение, которого больше
нет.

Почему браузером: решение принимает скрипт `confirmMaster`/`changeRole`
(`pages/admin_disciplines.py`, `pages/admin_people.py`) — он собирает тело
`POST /webapp/admin/grant` и показывает предупреждение из ответа в карточке.
Проверка «кнопка есть в разметке» не видит ни тела запроса, ни того, что текст
предупреждения пришёл с сервера, а не написан в скрипте.

Стенд `tests/webapp/server.py` отвечает по правилам `api/admin.py` после V-037:
роль педагога выдаётся без дисциплин, состояние «нет дисциплины» приходит
предупреждением (`warnings`), тела запросов пишутся в `GRANT_EVENTS`.

Спека роли: docs/architecture/140-master-role.md (Э1, критерий Г16, §2.2, §4.3).

Запуск:

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest \
        tests/webapp/test_miniapp_master_grant.py -q --browser chromium
"""

from __future__ import annotations

import pytest

from tests.webapp import server as stand

ADMIN_WARNING = stand.WARNING_TEXTS["admin"]
GRANT_URL = "/webapp/admin/grant"


@pytest.fixture(autouse=True)
def _reset_stand(webapp_server):
    """Дисциплины и роли — к исходному состоянию до и после сценария.

    После тоже: состояние стенда живёт в модуле, и чужие сценарии ниже по
    алфавиту не должны видеть выданную здесь роль педагога.
    """
    def baseline() -> None:
        stand.DISCIPLINES_STATE.clear()
        stand.ROLES_STATE.clear()
        stand.ROLES_STATE.update({1: ["admin"], 2: ["troupe"], 30: ["troupe"]})
        stand.GRANT_EVENTS.clear()
        stand.DISCIPLINE_EVENTS.clear()

    baseline()
    yield
    baseline()


def _open_person_card(page) -> None:
    """Открыть карточку человека из списка «Люди» и лист выдачи педагога."""
    page.locator("#people-list .ui-row", has_text="Валерий").first.click()
    page.wait_for_selector(".ui-sheet", timeout=10_000)
    page.locator(".ui-sheet button", has_text="Выдать педагога").first.click()
    page.wait_for_selector(".ui-sheet .ui-btn--primary", timeout=10_000)


def _grant(page, issue) -> tuple[dict, dict, int]:
    """Нажать «Выдать роль педагога» и вернуть тело запроса, тело ответа и статус."""
    with page.expect_response(lambda response: response.url.endswith(GRANT_URL)) as info:
        issue.click()
    response = info.value
    return response.request.post_data_json, response.json(), response.status


def test_без_дисциплины_роль_выдаётся_и_ответ_несёт_предупреждение(app_page) -> None:
    """Г16 по V-037: кнопка активна, запрос уходит без дисциплин, в ответе `warnings`."""
    page = app_page("/webapp/admin/t/people")
    page.wait_for_load_state("networkidle")
    _open_person_card(page)

    issue = page.locator(".ui-sheet .ui-btn--primary", has_text="Выдать роль педагога")
    assert issue.is_enabled(), "кнопка выдачи заблокирована без дисциплины (V-037 снял запрет)"
    assert "Без дисциплины роль выдаётся" in page.locator(".ui-sheet").inner_text()

    payload, body, status = _grant(page, issue)

    assert status == 200, f"выдача без дисциплины не прошла: {status} {body}"
    assert payload["role"] == "master" and payload["enabled"] is True, payload
    assert "disciplines" not in payload, \
        f"без выбора в запрос уехал пустой список дисциплин: {payload!r}"
    assert stand.GRANT_EVENTS and stand.GRANT_EVENTS[-1] == payload, stand.GRANT_EVENTS

    warnings = body.get("warnings") or []
    assert [item.get("code") for item in warnings] == ["master_without_discipline"], \
        f"ответ без предупреждения `master_without_discipline`: {warnings!r}"
    assert warnings[0].get("text") == ADMIN_WARNING

    # Карточка перечиталась после выдачи: предупреждение видно словами сервера
    page.wait_for_function("() => document.querySelectorAll('.ui-sheet').length === 0",
                           timeout=10_000)
    page.locator("#people-list .ui-row", has_text="Валерий").first.click()
    page.wait_for_selector(".ui-sheet .ui-dl", timeout=10_000)
    assert ADMIN_WARNING in page.locator(".ui-sheet").inner_text()
    assert page.svoi_errors == [], page.svoi_errors


def test_выбранная_дисциплина_уезжает_в_запрос_без_предупреждения(app_page) -> None:
    """Г16: выбранная дисциплина едет в `disciplines`, предупреждения нет."""
    page = app_page("/webapp/admin/t/people")
    page.wait_for_load_state("networkidle")
    _open_person_card(page)

    page.locator(".ui-sheet .ui-row", has_text="Музыка").first.click()
    issue = page.locator(".ui-sheet .ui-btn--primary", has_text="Выдать роль педагога")
    payload, body, status = _grant(page, issue)

    assert status == 200, f"выдача с дисциплиной не прошла: {status} {body}"
    assert payload.get("disciplines") == ["music"], \
        f"дисциплина не уехала в запрос: {payload.get('disciplines')!r}"
    assert body.get("warnings") == [], f"предупреждение при выбранной дисциплине: {body!r}"
    assert stand.DISCIPLINES_STATE.get(2) == ["music"], stand.DISCIPLINES_STATE
    assert page.svoi_errors == [], page.svoi_errors
