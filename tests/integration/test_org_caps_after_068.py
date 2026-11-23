"""Права роли `org` на живой схеме после миграции 068 (решение 19.11.2026).

Проверка идёт на свежей базе `svoi_test`, накатанной `alembic upgrade head`
(фикстура `test_db`), а не чтением SQL глазами: `delete ... where`,
`on conflict` и учёт версий ломаются молча, если их только читать.

Что проверяется (критерии карты t_848c60ed, пункты 4–5; после миграций 070 и 071):
  * у `org` нет `registration.approve` (068), и 070 сняла его у всех ролей;
  * у `org` ровно 15 возможностей, и это именно ожидаемый набор (071 сняла
    `system.health`, V-121 — см. `test_dead_capabilities_dropped.py`);
  * повторный накат `db/init/068_org_without_approve.sql` не падает и чисел не меняет;
  * голова Alembic — `071_drop_dead_capabilities`.

Обоснование: FR-A6 (права решает сервер), FR-A2 (код приглашения),
docs/architecture/02-access-control.md §1, карта t_848c60ed.
"""

from __future__ import annotations

import os

import psycopg

# Замер после 071: 15 возможностей роли `org`. Список повторён намеренно — тест
# должен падать, если набор прав «уточнят» молча (как было с `budget.approve`).
ORG_CAPS = {
    "budget.import", "budget.read", "budget.read.self", "budget.write", "calendar.write",
    "feedback.send", "files.access", "notes.write", "participants.contact",
    "participants.view", "poll.manage", "schedule.availability.self",
    "schedule.read.all", "schedule.slots", "schedule.write",
}

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _caps(dsn: str, role: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        return {row[0] for row in conn.execute(
            "select capability from rbac_role_capability where role = %s", (role,)).fetchall()}


def test_у_организатора_нет_права_на_заявки(test_db: str) -> None:
    """068 снимает `registration.approve` у `org`: заявки — работа администратора."""
    assert "registration.approve" not in _caps(test_db, "org")


def test_у_администратора_права_на_заявки_больше_нет(test_db: str) -> None:
    """070 снимает `registration.approve` у всех ролей: заявок в боте нет (§4.1)."""
    caps = _caps(test_db, "admin")
    assert "registration.approve" not in caps
    assert {"registration.code.issue", "participants.manage"} <= caps


def test_набор_прав_организатора_ровно_измеренный(test_db: str) -> None:
    """Остальные права `org` не тронуты: 15 возможностей, ровно из замера."""
    caps = _caps(test_db, "org")
    assert len(caps) == 15, f"у org {len(caps)} возможностей: {sorted(caps)}"
    assert caps == ORG_CAPS
    assert {"budget.write", "schedule.slots", "files.access", "feedback.send"} <= caps


def test_повторный_накат_068_ничего_не_меняет(test_db: str) -> None:
    """Идемпотентность: второй прогон файла не падает и чисел не меняет."""
    path = os.path.join(ROOT, "db", "init", "068_org_without_approve.sql")
    with open(path, encoding="utf-8") as handle:
        sql = handle.read()
    before = _caps(test_db, "org")
    with psycopg.connect(test_db, autocommit=True) as conn:
        conn.execute(sql)
        conn.execute(sql)
    assert _caps(test_db, "org") == before


def test_голова_alembic_после_071(test_db: str) -> None:
    """Учёт версий совпадает с файлом: накат дошёл до головы 071, а не остановился раньше.

    Голова сдвигается каждой новой миграцией: E1-4 добавила 072 (календарь события),
    E4 — 073 (два расположения файла).
    """
    with psycopg.connect(test_db) as conn:
        head = conn.execute("select version_num from alembic_version").fetchone()[0]
    assert head == "073_files_two_locations"
