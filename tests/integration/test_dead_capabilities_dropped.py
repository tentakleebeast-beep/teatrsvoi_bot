"""Мёртвые права сняты с ролей на живой схеме (V-121, docs/development/188 §6).

Проверка идёт на свежей базе, накатанной `alembic upgrade head` (фикстура
`test_db`), а не чтением SQL глазами: `delete … where` ломается молча, если его
только читать.

Что проверяется (критерии карты t_a487695a):
  * у `org` и `troupe` больше нет `system.health`, у `admin` — `quota.manage`;
  * составы прав ролей равны замеру после 071 (15 / 4 / 24), а не «примерно»;
  * `admin` сохраняет `system.health`: кнопка «Сервисы» и экран сервисов — его;
  * повторный накат `db/init/071_drop_dead_capabilities.sql` ничего не меняет;
  * голова Alembic — `071_drop_dead_capabilities`.

Обоснование: FR-A6 (права решает сервер), docs/development/188 §6, V-121 от 22.11.2026.
"""

from __future__ import annotations

import os

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Замер после 071. Списки повторены намеренно: тест должен падать, если состав
#: прав «уточнят» молча.
ORG_CAPS = {
    "budget.import", "budget.read", "budget.read.self", "budget.write", "calendar.write",
    "feedback.send", "files.access", "notes.write", "participants.contact",
    "participants.view", "poll.manage", "schedule.availability.self",
    "schedule.read.all", "schedule.slots", "schedule.write",
}
TROUPE_CAPS = {"budget.read.self", "feedback.send", "notes.write",
               "schedule.availability.self"}
ADMIN_CAPS = {
    "admin.panel", "audit.view", "budget.import", "budget.read", "budget.read.self",
    "budget.write", "calendar.write", "feedback.send", "feedback.triage",
    "files.access", "howto.read", "notes.write", "notify.send",
    "participants.contact", "participants.manage", "participants.view",
    "poll.manage", "poll.results.all", "registration.code.issue",
    "schedule.availability.self", "schedule.read.all", "schedule.slots",
    "schedule.write", "system.health",
}


def _caps(dsn: str, role: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        return {row[0] for row in conn.execute(
            "select capability from rbac_role_capability where role = %s", (role,)).fetchall()}


def test_у_организатора_нет_состояния_сервисов(test_db: str) -> None:
    """071 снимает `system.health` у `org`: вход «Сервисы» закрыт `admin.panel`."""
    caps = _caps(test_db, "org")
    assert "system.health" not in caps
    assert caps == ORG_CAPS, f"состав прав org разошёлся: {sorted(caps)}"


def test_у_труппы_нет_состояния_сервисов(test_db: str) -> None:
    """071 снимает `system.health` у `troupe`; четыре права остаются."""
    caps = _caps(test_db, "troupe")
    assert "system.health" not in caps
    assert caps == TROUPE_CAPS, f"состав прав troupe разошёлся: {sorted(caps)}"


def test_у_администратора_нет_лимитов_ai(test_db: str) -> None:
    """071 снимает `quota.manage` у `admin`; `system.health` у него остаётся."""
    caps = _caps(test_db, "admin")
    assert "quota.manage" not in caps
    assert "system.health" in caps, "кнопка «Сервисы» без права — панель с мёртвым входом"
    assert caps == ADMIN_CAPS, f"состав прав admin разошёлся: {sorted(caps)}"


def test_повторный_накат_071_ничего_не_меняет(test_db: str) -> None:
    """Идемпотентность: второй прогон файла не падает и чисел не меняет."""
    path = os.path.join(ROOT, "db", "init", "071_drop_dead_capabilities.sql")
    with open(path, encoding="utf-8") as handle:
        sql = handle.read()
    before = {role: _caps(test_db, role) for role in ("admin", "org", "troupe")}
    with psycopg.connect(test_db, autocommit=True) as conn:
        conn.execute(sql)
        conn.execute(sql)
    after = {role: _caps(test_db, role) for role in ("admin", "org", "troupe")}
    assert after == before


def test_голова_alembic_после_071(test_db: str) -> None:
    """Учёт версий дошёл до 071, а не остановился на предыдущей ревизии.

    Голова сдвигается каждой новой миграцией: E1-4 добавила 072 (календарь события),
    E4 — 073 (два расположения файла).
    """
    with psycopg.connect(test_db) as conn:
        head = conn.execute("select version_num from alembic_version").fetchone()[0]
    assert head == "074_calendar_event_types"
