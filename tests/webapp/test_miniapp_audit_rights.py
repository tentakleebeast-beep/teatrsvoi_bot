"""Аудит: без `audit.view` экран говорит о праве, а не о пустом журнале.

Отчёт 142 §3 строка 28, принципы 91: 9, Б3, карта t_8212983f. У `org` и
`master` права `audit.view` нет: `/webapp/admin/overview` отдаёт разделы
списком `allowed`, и блока `audit` в нём нет (`ADMIN_BLOCKS`, docs/architecture/85).
Экран читал отсутствующий блок как пустой список и писал «Событий нет» — как
будто журнал пуст, а не закрыт. У `troupe` в том же месте 403 и честное
«Раздел доступен организатору и администратору», то есть три роли без права
видели два разных ответа. Теперь все — одно сообщение из общего словаря ядра
(`ERRORS.admin_panel_forbidden`).

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_audit_rights.py \\
        -q --browser chromium
"""

from __future__ import annotations

import pytest

DENIED = "Раздел доступен организатору и администратору."

ORG = {"X-SVOI-Role": "org"}
MASTER = {"X-SVOI-Role": "master"}
TROUPE = {"X-SVOI-Role": "troupe"}


def _settled(page) -> None:
    """Ждём, пока страница получила ответ панели (запросы после входа стихли)."""
    page.wait_for_load_state("networkidle")


def _screen_text(page) -> str:
    return page.locator(".ui-screen").inner_text()


@pytest.mark.parametrize("headers,role", [(ORG, "org"), (MASTER, "master")])
def test_роль_без_audit_view_читает_отказ_а_не_пустоту(app_page, headers, role) -> None:
    """Блока `audit` нет в ответе — экран объясняет право, а не «Событий нет»."""
    page = app_page("/webapp/admin/t/audit", headers=headers)
    _settled(page)

    text = _screen_text(page)
    assert "Событий нет" not in text, f"у {role} снова молчаливая пустота: {text}"
    assert DENIED in text, text
    assert page.locator("#status").inner_text().strip() == DENIED
    assert page.locator("#audit-list").inner_text().strip() == "", \
        "под отказом остался журнал"
    assert not page.locator("#audit-more").is_visible(), "кнопка «Показать ещё» без права"
    assert page.svoi_errors == [], page.svoi_errors


def test_troupe_видит_то_же_сообщение(app_page) -> None:
    """403 и «блока нет» — одно состояние: все роли без права видят одно и то же."""
    page = app_page("/webapp/admin/t/audit", headers=TROUPE)
    _settled(page)

    assert page.locator("#status").inner_text().strip() == DENIED
    assert "Событий нет" not in _screen_text(page)
    assert page.svoi_errors == [], page.svoi_errors


def test_с_правом_журнал_рисуется(app_page) -> None:
    """Администратор не теряет журнал: проверка права не режет данные с правом."""
    page = app_page("/webapp/admin/t/audit")

    def with_events(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=(
            '{"ok": true, "allowed": ["people", "audit"], "actions": {},'
            ' "blocks": {"audit": [{"action": "invite.create", "ts": "2026-11-08T10:00:00",'
            ' "actor_type": "human", "actor_name": "Виктор", "entity_type": "invite_code",'
            ' "entity_id": "7"}]}}'))

    page.route("**/webapp/admin/overview", with_events)
    page.reload(wait_until="load")
    _settled(page)

    text = _screen_text(page)
    assert "Код выпущен" in text, f"журнал с правом не отрисован: {text}"
    assert DENIED not in text
    assert page.svoi_errors == [], page.svoi_errors
