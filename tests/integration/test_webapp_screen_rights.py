"""Прямой адрес экрана без права: 302 на «Главную», а не 200 с пустышкой.

Правило владельца 08.11.2026 — «не показываем вход туда, куда действие
недоступно» — держалось только на хабе: плитки отбирал `webapp.sections_for()`,
а сам экран открывался по прямому адресу любому, кто знал ссылку. Права на данные
проверяются в эндпоинтах, поэтому человек видел экран и отказ 403 после нажатия.

Здесь проверяется поведение на живой схеме и живых ролях: возможности берутся из
`v_participant_capabilities` (тот же источник, что у сервера), а не из списка в
тесте. Страницы остаются публичными: без заголовка `Authorization` маршрут отдаёт
HTML как раньше (оболочка решает по `/webapp/nav`), с токеном решает сервер.

Обоснование: FR-H9, FR-A6 (права выдаются человеку, интерфейс их показывает),
docs/development/92 §9, карта t_8873ed85,
`docs/reports/146-screen-rights-matrix.md`.
"""

from __future__ import annotations

import psycopg
import pytest
from app.person_key import make_key
from app.webapp import SCREEN_CAPS, screen_visible

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data

ROLES = ("admin", "org", "troupe", "master")
SCREENS = sorted(SCREEN_CAPS)


def _person(conn, chat_id: int, name: str, role: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _caps(pid: int) -> list[str]:
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select capability from v_participant_capabilities where participant_id = %s",
            (pid,)).fetchall()
    return sorted(row[0] for row in rows)


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def roles(client) -> dict[str, dict]:
    """По человеку на каждую из четырёх ролей: chat_id, заголовки, возможности из базы."""
    _api()
    people = {}
    for index, role in enumerate(ROLES):
        chat_id = 790100 + index
        with psycopg.connect(_test_dsn()) as conn:
            pid = _person(conn, chat_id, f"Права {role}", role)
        people[role] = {"chat_id": chat_id, "pid": pid,
                        "headers": _token(client, chat_id), "caps": _caps(pid)}
    return people


def test_без_токена_страницы_остаются_публичными(client, roles):
    """Страница — публичная константа: без `Authorization` её отдают как раньше.

    Правило из `docs/reports/117`: HTML не несёт данных, поэтому доступ к нему не
    запирается. Иначе сломался бы первый кадр и переходы в WebView без токена.
    """
    _api()
    for url in SCREENS:
        answer = client.get(url, follow_redirects=False)
        assert answer.status_code == 200, (url, answer.status_code)


@pytest.mark.parametrize("role", ROLES)
def test_прямой_адрес_без_права_уводит_на_главную(client, roles, role: str):
    """(б) экран без права не отдаёт 200: 302 на `/webapp/home` этой роли."""
    person = roles[role]
    wrong = []
    for url in SCREENS:
        expected = screen_visible(url, person["caps"])
        answer = client.get(url, headers=person["headers"], follow_redirects=False)
        if expected and answer.status_code != 200:
            wrong.append(f"{url}: право есть, ответ {answer.status_code}")
        elif not expected:
            if answer.status_code != 302:
                wrong.append(f"{url}: без права ответ {answer.status_code}, ждали 302")
            elif answer.headers.get("location") != "/webapp/home":
                wrong.append(f"{url}: уводит в {answer.headers.get('location')}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("role", ROLES)
def test_признак_экранов_в_nav_совпадает_с_правами_из_базы(client, roles, role: str):
    """`/webapp/nav` — единственный серверный признак прав; он не врёт."""
    person = roles[role]
    body = client.get("/webapp/nav", headers=person["headers"]).json()
    assert body["ok"] is True
    assert sorted(body["caps"]) == person["caps"]
    assert body["screens"] == {url: screen_visible(url, person["caps"]) for url in SCREENS}


@pytest.mark.parametrize("role", ROLES)
def test_хаб_и_управление_не_шире_прав(client, roles, role: str):
    """(а) в хабе и «Управлении» нет пунктов, которых у роли нет."""
    person = roles[role]
    body = client.get("/webapp/home/data", headers=person["headers"]).json()
    assert body["ok"] is True
    entries = list((body.get("more") or {}).get("management") or [])
    entries += list((body.get("more") or {}).get("personal") or [])
    entries += list(body.get("attention") or [])
    over = [row["url"] for row in entries if not screen_visible(row["url"], person["caps"])]
    assert not over, f"роль {role} видит входы без права: {over}"


def test_право_сверки_и_сервисов_только_у_тех_у_кого_есть(client, roles):
    """Пример иска из карты: сверка и сервисы — не для студийца и не для педагога."""
    for role in ("troupe", "master"):
        person = roles[role]
        screens = client.get("/webapp/nav", headers=person["headers"]).json()["screens"]
        assert screens["/webapp/reconcile"] is False
        assert screens["/webapp/services"] is False
        assert screens["/webapp/broadcast"] is False
    org = client.get("/webapp/nav", headers=roles["org"]["headers"]).json()["screens"]
    assert org["/webapp/reconcile"] is True
    # Рассылка — только администратору (db/init/057, FR-G2).
    assert org["/webapp/broadcast"] is False
    admin = client.get("/webapp/nav", headers=roles["admin"]["headers"]).json()["screens"]
    assert admin["/webapp/broadcast"] is True
