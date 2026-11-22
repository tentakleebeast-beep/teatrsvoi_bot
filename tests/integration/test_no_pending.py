"""Заявок нет ни в одной ветке: код привязывает и сразу делает активным.

docs/development/182 §4.1: ветка `pending` удалена из онбординга целиком —
`register` удалён, единственный вход — `attach`, он ставит `status='active'`;
право `registration.approve` снято миграцией 069 у всех ролей; раздел и счётчик
`pending` сняты из реестра Mini App. Миграции данных нет: на 21.11.2026
`participant.status = 'pending'` = 0 (§4.2), а если запись останется — её
переводит выпуск персонального кода и вход им.

Обоснование: FR-A2, FR-G16, docs/development/182 §4.1, §4.2, §6.2 п. 8.
"""

from __future__ import annotations

import psycopg
from app.api.webapp_api import attention_rows
from app.person_key import make_key
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(chat_id: int | None, name: str, status: str = "active",
            role: str = "troupe") -> int:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s,%s,%s,%s,%s) returning id",
            (chat_id, name, make_key(name), role, status)).fetchone()["id"]
        conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                     (pid, role))
        conn.commit()
    return pid


def _issue(person_id: int, code: str) -> str:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into invite_code (code, role, participant_id, created_by, expires_at, max_uses)
               values (%s, 'troupe', %s, 0, now() + interval '7 days', 1)""",
            (code, person_id))
    return code


def test_attach_не_создаёт_pending(client, auth) -> None:
    person = _person(None, "Без Заявки")
    code = _issue(person, "NOPEND01")
    body = client.post("/tools/participants/attach",
                       json={"chat_id": 796001, "code": code}, headers=auth).json()
    assert body["ok"] is True
    assert body["participant"]["status"] == "active"
    with psycopg.connect(_test_dsn()) as conn:
        status = conn.execute("select status from participant where id = %s",
                              (person,)).fetchone()[0]
        pending = conn.execute("select count(*) from participant where status = 'pending'"
                               ).fetchone()[0]
    assert status == "active"
    assert pending == 0, "ветка заявки вернулась"


def test_уведомлений_о_заявке_нет(client, auth) -> None:
    """Вход по коду не ставит в очередь ни одной строки «заявка»."""
    person = _person(None, "Без Уведомления")
    code = _issue(person, "NOPEND02")
    client.post("/tools/participants/attach",
                json={"chat_id": 796002, "code": code}, headers=auth)
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        kinds = [row["kind"] for row in conn.execute("select kind from notification")]
    assert all("pending" not in (kind or "").lower() for kind in kinds), kinds
    assert all("request" not in (kind or "").lower() for kind in kinds), kinds


def test_ни_у_одной_роли_нет_права_подтверждения(client, auth) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        roles = [row[0] for row in conn.execute(
            "select distinct role from rbac_role_capability "
            "where capability = 'registration.approve'")]
    assert roles == [], f"право подтверждения осталось у {roles}"


def test_attention_rows_без_ждут_решения() -> None:
    rows = attention_rows(["*"], {"feedback": 3})
    urls = {row["url"] for row in rows}
    assert all("/pending" not in url for url in urls), urls
    assert all(row["title"] != "Заявки" for row in rows), rows
    assert all(row["meta"] != "ждут решения" for row in rows), rows


def test_в_реестре_разделов_нет_pending() -> None:
    from app.webapp import SECTIONS, allowed_admin_blocks

    names = {section.key for section in SECTIONS}
    assert "pending" not in names, "раздел «Заявки» вернулся в реестр"
    assert "pending" not in allowed_admin_blocks(["*"])


def test_экрана_заявок_нет_и_в_miniapp(client) -> None:
    _api()
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        admin_id = conn.execute("select id from participant where tg_chat_id = %s",
                                (123456789,)).fetchone()["id"]
        conn.execute("insert into participant_role (participant_id, role) values (%s, 'admin') "
                     "on conflict do nothing", (admin_id,))
        conn.commit()
    token = client.post("/webapp/auth", json={"init_data": init_data(123456789)}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    overview = client.get("/webapp/admin/overview", headers=headers).json()
    assert "pending" not in overview["blocks"]
    assert "pending" not in overview["allowed"]
    assert client.get("/webapp/admin/t/pending").status_code == 404
