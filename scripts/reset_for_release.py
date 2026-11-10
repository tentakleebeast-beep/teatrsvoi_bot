#!/usr/bin/env python3
"""
Сброс dev-данных перед релизом.

За четыре месяца разработки бот накопил тестовые операции, тестовую занятость
и тестовых людей. Настоящие данные — файлы в папке «Инфа СВОИ»; база должна
стать их производной, а не свалкой прогонов.

Что делает:
  * оставляет справочники (права, категории, залы, проекты, дисциплины, политики);
  * оставляет людей и их роли, удаляет только подтверждённые тестовые аккаунты;
  * обнуляет операционные таблицы целиком — и записи бота, и прошлый импорт;
  * ставит метку в `migration_marker`, чтобы повторный запуск не сработал
    (урок L-001: опасная операция обязана быть идемпотентной).

После сброса данные возвращаются импортом из файлов:
    scripts/import_finance.py
    scripts/import_students_schedule.py --month YYYY-MM --apply

  env -u PYTHONPATH .venv/bin/python scripts/reset_for_release.py            # показать план
  env -u PYTHONPATH .venv/bin/python scripts/reset_for_release.py --apply    # выполнить
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import psycopg
from _cli import dsn
from psycopg.rows import dict_row

DB = dsn()
MARKER = "release_reset"

# Справочники и настройки: переживают сброс, восстанавливаются кодом и миграциями.
KEEP_REFERENCE = (
    "rbac_capability",
    "rbac_role_capability",
    "participant_capability_override",
    "category",
    "article_map",
    "finance_granularity",
    "venue",
    "project",
    "discipline",
    "participant_discipline",
    "llm_policy",
    "app_setting",
    "migration_marker",
    "article_alias",
    "sheet_binding",
)

# Люди: остаются, чистятся только тестовые аккаунты (см. TEST_ACCOUNTS).
KEEP_PEOPLE = (
    "participant",
    "participant_role",
    "person_alias",
)

# Порядок важен: сначала зависимые таблицы, потом те, на которые они ссылаются.
WIPE = (
    "agent_proposal",
    "availability_template",
    "notification_pref",
    "notification",
    "availability_hour",
    "dues_charge",
    "budget_tx",
    "budget_month_total",
    "budget_period",
    "approval_request",
    "slot_proposal",
    "event_plan",
    "feedback_attachment",
    "feedback_answer",
    "feedback",
    "category_candidate",
    "intent_log",
    "llm_usage",
    "invite_attempt",
    "invite_code",
    "import_row",
    "import_batch",
    "finance_source_map",
    "row_map",
    "note",
    "access_grant",
    "agent_action",
    "file_tag",
    "file_folder",
    "file_text",
    "file_audio",
    "file_object",
    "tag",
    "folder",
    "budget_plan",
    "dues_plan",
    "job",
    "audit_event",
)

# Тестовые аккаунты: подтверждены заказчиком как нетестовые данные, а отладка.
TEST_CHAT_IDS = (555, 999)


def plan(conn) -> dict:
    """Что будет удалено и что останется. Ничего не меняет."""
    present = {
        row["relname"]
        for row in conn.execute(
            "select relname from pg_stat_user_tables").fetchall()
    }
    counts = {}
    for table in WIPE:
        if table in present:
            counts[table] = conn.execute(f'select count(*) as n from "{table}"').fetchone()["n"]
    test_people = conn.execute(
        "select id, display_name, tg_chat_id from participant where tg_chat_id = any(%s)",
        (list(TEST_CHAT_IDS),)).fetchall()
    kept = conn.execute(
        "select status, count(*) as n from participant group by 1 order by 1").fetchall()
    known = set(KEEP_REFERENCE) | set(KEEP_PEOPLE) | set(WIPE)
    return {
        "unclassified": sorted(present - known),
        "wipe": counts,
        "wipe_rows": sum(counts.values()),
        "test_people": [dict(row) for row in test_people],
        "people_after": {row["status"]: row["n"] for row in kept},
        "missing_tables": [table for table in WIPE if table not in present],
    }


def marker_exists(conn) -> bool:
    row = conn.execute("select 1 from migration_marker where key = %s", (MARKER,)).fetchone()
    return row is not None


def apply_reset(conn, data: dict) -> None:
    """Выполнить сброс по готовому плану. Проблемы — SystemExit, а не половина работы."""
    if marker_exists(conn):
        raise SystemExit(f"{MARKER} уже применён: повторный сброс запрещён")
    if not data["test_people"]:
        raise SystemExit("не найдено ни одного тестового аккаунта из списка — проверьте окружение")
    if data["unclassified"]:
        raise SystemExit("есть таблицы без решения, что с ними делать: "
                         + ", ".join(data["unclassified"]))

    stamp = datetime.now().isoformat(timespec="seconds")
    chats = [person["tg_chat_id"] for person in data["test_people"]]
    with conn.transaction():
        conn.execute("delete from participant where tg_chat_id = any(%s)", (chats,))
        for table in WIPE:
            if table in data["wipe"]:
                conn.execute(f'delete from "{table}" where true')
        conn.execute(
            "insert into migration_marker (key, applied_at, note) values (%s, now(), %s) "
            "on conflict (key) do nothing",
            (MARKER, f"сброс dev-данных перед релизом {stamp}; "
                     f"удалено строк: {data['wipe_rows']}; тестовых людей: {len(chats)}"))


def print_plan(data: dict) -> None:
    print("Будет обнулено:")
    for table, count in sorted(data["wipe"].items(), key=lambda item: -item[1]):
        print(f"  {table:<24} {count:>7}")
    print(f"  {'ИТОГО':<24} {data['wipe_rows']:>7}")
    print("\nТестовые аккаунты к удалению:")
    for person in data["test_people"]:
        print(f"  id={person['id']} «{person['display_name']}» chat={person['tg_chat_id']}")
    if not data["test_people"]:
        print("  — ни одного (это подозрительно: проверьте DATABASE_URL)")
    print("\nЛюди остаются:", ", ".join(f"{k}: {v}" for k, v in data["people_after"].items()))
    if data["missing_tables"]:
        print("\nТаблиц нет в базе (пропущены):", ", ".join(data["missing_tables"]))
    if data["unclassified"]:
        print("\nВНИМАНИЕ, таблицы без решения:", ", ".join(data["unclassified"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Сброс dev-данных перед релизом")
    parser.add_argument("--apply", action="store_true", help="выполнить, а не только показать")
    args = parser.parse_args()

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        conn.autocommit = False
        before = plan(conn)
        print_plan(before)
        if not args.apply:
            print("\nЭто план. Ничего не изменено. Выполнить: --apply")
            return

        deleted_people = len(before["test_people"])
        apply_reset(conn, before)
        after = plan(conn)
        print(f"\nУдалено: строк {before['wipe_rows']}, тестовых людей {deleted_people}.")
        print("После сброса: людей", sum(after["people_after"].values()),
              "| часов", after["wipe"].get("availability_hour", 0),
              "| операций", after["wipe"].get("budget_tx", 0),
              "| записей аудита", after["wipe"].get("audit_event", 0))
        print(f"Метка «{MARKER}» поставлена. Дальше — импорт из файлов «Инфа СВОИ».")


if __name__ == "__main__":
    sys.exit(main())
