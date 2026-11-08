"""Приглашения: без права формы нет, а пустое состояние не зовёт к несуществующей кнопке.

Отчёт 142 §3 строка 24, принципы 8, 9 и Б3 из docs/development/91, карта
t_343d42fe. У `troupe` нет `registration.code.issue` (`db/init/008`) и закрыта
сама панель (403 `admin_panel_forbidden`), но форма «Выпустить код» стояла в
разметке и гасилась только после ответа — на экране одновременно были и форма, и
отказ «Раздел доступен организатору и администратору». У `org` и `master` панель
есть, а раздела `invites` в ответе нет: пустое состояние звало нажать «Выпустить
код» — кнопку, которой у них нет.

Теперь форма отдаётся скрытой и показывается только по явному «да» сервера
(`allowed` и `actions.issue_codes` ответа `/webapp/admin/overview`). Без права
пустое состояние говорит «Коды выпускает администратор». У `troupe` панель
закрыта целиком — там, как и на экране 21, причину печатает отказ, а списка
нет.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_invites_rights.py \\
        -q --browser chromium
"""

from __future__ import annotations

DENIED = "Раздел доступен организатору и администратору."
NO_RIGHT = "Коды выпускает администратор"

ORG = {"X-SVOI-Role": "org"}
TROUPE = {"X-SVOI-Role": "troupe"}


def _settled(page) -> None:
    """Ждём, пока страница получила ответ панели (запросы после входа стихли)."""
    page.wait_for_load_state("networkidle")


def _screen_text(page) -> str:
    return page.locator(".ui-screen").inner_text()


def test_org_без_права_не_видит_формы_и_зовёт_не_к_кнопке(app_page) -> None:
    """`registration.code.issue` у org нет (`db/init/008`): формы выпуска быть не может."""
    page = app_page("/webapp/admin/t/invites", headers=ORG)
    _settled(page)

    text = _screen_text(page)
    assert not page.locator("#invite-new").is_visible(), "у org снова видна форма выпуска"
    assert "Выпустить код" not in text, f"у org кнопка без права: {text}"
    assert "Выпустите код кнопкой выше" not in text, "экран зовёт к несуществующей кнопке"
    assert NO_RIGHT in text, text
    assert page.locator("#status").inner_text().strip() == DENIED
    assert page.svoi_errors == [], page.svoi_errors


def test_troupe_не_видит_ни_формы_ни_кнопки(app_page) -> None:
    """У troupe панель закрыта (403): форма не должна мелькать рядом с отказом."""
    page = app_page("/webapp/admin/t/invites", headers=TROUPE)
    _settled(page)

    text = _screen_text(page)
    assert not page.locator("#invite-new").is_visible(), "у troupe снова видна форма выпуска"
    assert "Выпустить код" not in text, f"у troupe кнопка без права: {text}"
    assert page.locator("#status").inner_text().strip() == DENIED
    assert page.svoi_errors == [], page.svoi_errors


def test_admin_видит_форму_и_пустое_состояние_с_кнопкой(app_page) -> None:
    """У администратора право есть: форма на месте, пустое состояние указывает на неё."""
    page = app_page("/webapp/admin/t/invites")
    _settled(page)

    assert page.locator("#invite-new").is_visible(), "у администратора пропала форма выпуска"
    issue = page.locator("#invite-issue")
    assert issue.count() == 1 and issue.is_visible()
    text = _screen_text(page)
    assert "Активных кодов нет" in text
    assert "Выпустите код кнопкой выше" in text
    assert NO_RIGHT not in text
    assert page.locator("#status").inner_text().strip() == ""
    assert page.svoi_errors == [], page.svoi_errors
