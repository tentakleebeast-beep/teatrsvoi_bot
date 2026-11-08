#!/usr/bin/env python3
"""
Планирование напоминаний (docs/infrastructure/36).

Пишет в таблицу `notification` (существует с миграции 001):
recipient_id → participant, payload — текст, dedupe_key — защита от дублей,
scheduled_at — когда отправлять, status='scheduled'.

  python scripts/plan_reminders.py            # показать, что будет создано
  python scripts/plan_reminders.py --apply    # поставить в очередь

Что планируем:
  * dues — у кого остался долг по взносам за текущий месяц;
  * availability — у кого не заполнена занятость на следующую неделю.
Отправляет отдельный процесс: scripts/send_notifications.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg
from _cli import add_tool_api_path, add_write_mode, apply_env, dsn, env, now_local, parser, writes
from psycopg.rows import dict_row

apply_env()
add_tool_api_path()

from app.notification_store import load_defaults  # noqa: E402
from app.notifications import (  # noqa: E402
    availability_deadline,
    availability_text,
    dedup_key,
    dues_text,
    plan_due_at,
    resolve_settings,
    should_skip,
    week_start,
)

DB = dsn()
TZ_OFFSET = float(env("TZ_OFFSET_HOURS", "4"))

#: Поля частной записи, которые перекрывают общую настройку (FR-G17).
PERSONAL_FIELDS = ("quiet_from", "quiet_to", "kinds_muted", "digest_enabled")


def people_with_prefs(conn) -> list[dict]:
    """Активные люди с действующими настройками: частные важнее общих (FR-G17).

    Запись `notification_pref` есть только у того, кто настраивал уведомления сам.
    Остальные получают общую настройку студии — иначе «типы по умолчанию» из
    админ-панели не доходили бы до планировщика.
    """
    defaults = load_defaults(conn)
    rows = conn.execute("""
        select p.id, p.tg_chat_id, p.display_name,
               prefs.participant_id as pref_id,
               prefs.quiet_from, prefs.quiet_to, prefs.kinds_muted, prefs.digest_enabled
        from participant p
        left join notification_pref prefs on prefs.participant_id = p.id
        where p.status = 'active' and p.tg_chat_id is not null
        order by p.id""").fetchall()

    people = []
    for row in rows:
        person = dict(row)
        personal = ({field: person[field] for field in PERSONAL_FIELDS}
                    if person.pop("pref_id") is not None else None)
        person.update(resolve_settings(personal, defaults))
        people.append(person)
    return people


def plan_dues(conn, people: list[dict], moment: datetime) -> list[dict]:
    period = moment.strftime("%Y-%m")
    balances = {r["participant_id"]: r for r in conn.execute(
        "select participant_id, amount_due, amount_paid from v_dues_balance "
        "where period = %s", (period,))}

    desired = moment.replace(hour=10, minute=0, second=0)
    out = []
    for person in people:
        if should_skip("dues", person):
            continue
        row = balances.get(person["id"])
        if not row:
            continue
        due, paid = float(row["amount_due"] or 0), float(row["amount_paid"] or 0)
        if due - paid < 1:
            continue
        title, body = dues_text(period, due, paid)
        out.append({
            "participant_id": person["id"], "chat_id": person["tg_chat_id"],
            "kind": "dues",
            "dedupe_key": dedup_key("dues", person["tg_chat_id"], period),
            "title": title, "body": body,
            "scheduled_at": plan_due_at(moment, desired),
        })
    return out


def plan_availability(conn, people: list[dict], moment: datetime) -> list[dict]:
    monday = week_start(moment.date()) + timedelta(days=7)
    deadline = availability_deadline(moment.date(), tz=moment.tzinfo)
    if moment > deadline:
        return []                                   # срок прошёл, напоминать поздно

    out = []
    for person in people:
        if should_skip("availability", person):
            continue
        filled = conn.execute("""
            select count(distinct day) as days from availability_hour
            where participant_id = %s and day >= %s and day < %s""",
            (person["id"], monday, monday + timedelta(days=7))).fetchone()["days"]
        title, body = availability_text(monday, filled_days=filled)
        if filled >= 5:
            continue                                # почти заполнено — не тревожим
        out.append({
            "participant_id": person["id"], "chat_id": person["tg_chat_id"],
            "kind": "availability",
            "dedupe_key": dedup_key("availability", person["tg_chat_id"], monday.isoformat()),
            "title": title, "body": body,
            "scheduled_at": plan_due_at(moment, deadline),
        })
    return out


def save(conn, items: list[dict]) -> int:
    created = 0
    for item in items:
        row = conn.execute("""
            insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at)
            values (%s, %s, %s::jsonb, %s, %s)
            on conflict (dedupe_key, recipient_id) do nothing
            returning id""",
            (item["participant_id"], item["kind"],
             psycopg.types.json.Jsonb({"title": item["title"], "body": item["body"],
                                       "chat_id": item["chat_id"]}),
             item["dedupe_key"], item["scheduled_at"])).fetchone()
        if row:
            created += 1
    return created


def main() -> None:
    ap = parser(__doc__ or "План напоминаний о взносах и занятости")
    add_write_mode(ap)
    apply_changes = writes(ap.parse_args())
    moment = now_local()

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        people = people_with_prefs(conn)
        dues = plan_dues(conn, people, moment)
        availability = plan_availability(conn, people, moment)

        print(f"сейчас: {moment:%d.%m.%Y %H:%M} (UTC+{int(TZ_OFFSET)}), "
              f"активных с telegram: {len(people)}")
        print(f"\nвзносы: {len(dues)}")
        for item in dues[:6]:
            print(f"  {item['participant_id']} {item['title']} → {item['scheduled_at']:%d.%m %H:%M}")
        print(f"\nзанятость: {len(availability)}")
        for item in availability[:6]:
            print(f"  {item['participant_id']} {item['title']} → {item['scheduled_at']:%d.%m %H:%M}")

        if apply_changes:
            created = save(conn, dues + availability)
            conn.commit()
            print(f"\nпоставлено в очередь: {created} (дубли отсечены dedupe_key)")
        else:
            print("\n(dry-run: ничего не записано)")


if __name__ == "__main__":
    main()
