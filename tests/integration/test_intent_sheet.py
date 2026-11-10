"""Лист «Чего не умеем»: маршрут чтения (V-035, docs/development/156).

Обоснование: V-035 — строки листа тянутся с прода на локальную машину, чтобы по каждой
завести карточку на спецификацию. Маршрут `GET /tools/intent/sheet` — первое звено
конвейера, поэтому проверяем ровно то, на что опирается скрипт выгрузки:

  * отказ агента из бота попадает в лист целиком (фраза, разы, люди, ответ, токены);
  * отвеченное фактом и разобранное каскадом в лист не идут;
  * прогон без `chat_id` — не сообщение бота, его нет (V-034);
  * `phrase` нормализована на проде (`lower` + схлопывание пробелов + `btrim`),
    потому что по ней считается `row_id` карточки в скрипте-сестре;
  * `last_answer` — из самой свежей строки;
  * право только `admin.panel`, маршрут ничего не пишет.
"""

from __future__ import annotations

import inspect

import psycopg
import pytest
from app.api import admin

from tests.conftest import TOKEN, _test_dsn

REFUSAL = "Пока не умею разобрать этот вопрос свободным текстом."
ADMIN_CHAT = 786000


def _headers(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    """Участник с ролью и строкой `participant_role`: возможности берутся из неё."""
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status)"
        " values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, f"лист-{chat_id}", role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _ask(conn, chat_id, participant_id, text: str, *, answer: str | None = REFUSAL,
         agent_status: str | None = "refusal", resolved: bool = False,
         tokens: int | None = 0, ago_days: int | None = None) -> int:
    """Строка журнала вопроса. `ago_days` — сколько дней назад, чтобы задать давность."""
    row_id = conn.execute(
        "insert into intent_log (ts, chat_id, participant_id, text, source, resolved,"
        " answer, tokens, agent_status)"
        " values (case when %s::int is null then now()"
        "              else now() - (%s::int || ' days')::interval end,"
        "         %s,%s,%s,'llm',%s,%s,%s,%s) returning id",
        (ago_days, ago_days, chat_id, participant_id, text, resolved, answer, tokens,
         agent_status)).fetchone()[0]
    conn.commit()
    return row_id


@pytest.fixture()
def admin_headers(client) -> dict:
    """Заголовки настоящего администратора: маршрут закрыт правом `admin.panel`.

    Фикстура `auth` здесь не годится — это организатор `org` с `audit.view`
    (tests/conftest.py), а `admin.panel` выдаётся только роли `admin` (db/init/043).
    """
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, ADMIN_CHAT, "Лист Админ", role="admin")
    return _headers(ADMIN_CHAT)


def _rows(client, headers: dict, **params) -> list[dict]:
    response = client.get("/tools/intent/sheet", headers=headers, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == len(body["rows"]), "count — длина rows"
    return body["rows"]


def test_отказ_из_бота_попадает_в_лист(client, admin_headers) -> None:
    """V-035: отказ агента — строка листа со фразой, людьми, ответом и токенами."""
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 785001, "Иванов Виктор")
        _ask(conn, 785001, pid, "кто завтра свободен?", tokens=0)

    rows = _rows(client, admin_headers)
    assert len(rows) == 1
    row = rows[0]
    assert row["phrase"] == "кто завтра свободен?"
    assert row["times"] == 1
    assert row["people"] == 1
    assert row["who"] == "Иванов Виктор"
    assert row["last_answer"] == REFUSAL
    assert row["tokens"] == 0
    assert row["first_seen"] == row["last_seen"]


def test_отвеченное_фактом_не_попадает(client, admin_headers) -> None:
    """V-035: ответ фактом и разобранное каскадом в лист «не умеем» не идут."""
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 785001, "Петров Пётр")
        _ask(conn, 785001, pid, "сколько денег в бюджете", answer="120 тыс.",
             agent_status="answer")
        _ask(conn, 785001, pid, "когда репетиция", answer="завтра в 19:00",
             agent_status=None, resolved=True)

    assert _rows(client, admin_headers) == []


def test_прогон_без_chat_id_не_попадает(client, admin_headers) -> None:
    """V-035 (правило V-034): строка без `chat_id` — прогон, а не сообщение бота."""
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 785001, "Сидоров Семён")
        _ask(conn, None, pid, "кто завтра свободен?")
        _ask(conn, 785002, pid, "кто завтра занят?")

    rows = _rows(client, admin_headers)
    assert [row["phrase"] for row in rows] == ["кто завтра занят?"]


def test_одна_фраза_в_разном_регистре_и_пробелах_одна_строка(client, admin_headers) -> None:
    """V-035: нормализация `phrase` — часть контракта, по ней считается `row_id`."""
    with psycopg.connect(_test_dsn()) as conn:
        first = _person(conn, 785001, "Иванов Виктор")
        second = _person(conn, 785003, "Смирнова Анна")
        _ask(conn, 785001, first, "кто завтра свободен?")
        _ask(conn, 785001, first, "Кто  завтра   свободен?")
        _ask(conn, 785003, second, "КТО ЗАВТРА СВОБОДЕН?")

    rows = _rows(client, admin_headers)
    assert len(rows) == 1
    assert rows[0]["phrase"] == "кто завтра свободен?"
    assert rows[0]["times"] == 3
    assert rows[0]["people"] == 2


def test_последний_ответ_из_самой_свежей_строки(client, admin_headers) -> None:
    """V-035: `last_answer` — ответ строки с большим `ts`, при равных — с большим `id`."""
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 785001, "Иванов Виктор")
        _ask(conn, 785001, pid, "кто завтра свободен?", answer="старый ответ",
             ago_days=2)
        _ask(conn, 785001, pid, "кто завтра свободен?", answer="свежий ответ",
             ago_days=1)

    assert _rows(client, admin_headers)[0]["last_answer"] == "свежий ответ"

    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute("select id from participant where tg_chat_id = 785001").fetchone()[0]
        _ask(conn, 785001, pid, "кто завтра свободен?", answer="ровесник свежего",
             ago_days=1)
    assert _rows(client, admin_headers)[0]["last_answer"] == "ровесник свежего"


def test_права_только_admin_panel(client, auth, admin_headers) -> None:
    """V-035: право `admin.panel`, а не `audit.view`, — в ответе имена и тексты людей."""
    assert client.get("/tools/intent/sheet").status_code == 401
    assert client.get("/tools/intent/sheet", headers=auth).status_code == 403
    assert client.get("/tools/intent/sheet", headers=admin_headers).status_code == 200


def test_маршрут_только_читает(client) -> None:
    """V-035: в теле маршрута нет записи — механизм листа в прод не пишет."""
    source = inspect.getsource(admin.intent_sheet).lower()
    assert "select" in source
    for word in ("insert", "update", "delete"):
        assert word not in source, f"маршрут листа пишет в прод: {word}"


def test_days_и_limit_ограничены(client, admin_headers) -> None:
    """V-035: окно дней и размер листа ограничены объявлением маршрута."""
    for params in ({"days": 0}, {"days": 400}, {"limit": 0}, {"limit": 501}):
        response = client.get("/tools/intent/sheet", headers=admin_headers, params=params)
        assert response.status_code == 422, params


def test_ответ_содержит_ровно_поля_строки(client, admin_headers) -> None:
    """V-035: набор полей строки — контракт для скрипта выгрузки, лишнего не отдаём."""
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 785001, "Иванов Виктор")
        _ask(conn, 785001, pid, "кто завтра свободен?")

    response = client.get("/tools/intent/sheet", headers=admin_headers).json()
    assert set(response) == {"ok", "days", "count", "rows"}
    assert set(response["rows"][0]) == {"phrase", "times", "people", "who", "first_seen",
                                        "last_seen", "last_answer", "tokens"}
