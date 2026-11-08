#!/usr/bin/env python3
"""
Дайджест обращений админу (решение V-005, шаг Ш-45).

Обращения больше не рассылаются сразу: раз в день в 11:00 админ получает
одно сообщение со списком открытых обращений. Скрипт только кладёт запись
в `notification`, отправляет её обычный `send_notifications.py`, поэтому
тихие часы и лимит в час соблюдаются.

  python scripts/feedback_digest.py             # показать, что будет создано
  python scripts/feedback_digest.py --apply     # поставить в очередь
"""

from __future__ import annotations

import argparse
from datetime import datetime

import psycopg
from _cli import apply_env, dsn, now_local
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

apply_env()
DB = dsn()
OPEN_STATUSES = ("new", "seen", "planned")
KINDS = {"bug": "Проблема", "wish": "Пожелание", "wish-fl": "Просьба о доработке",
         "question": "Вопрос", "praise": "Хорошо работает"}
LINE_LIMIT = 3500
#: Обращение без ответа старше этого срока попадает в раздел «требует вас».
ATTENTION_HOURS = 6


def open_feedback(conn) -> list[dict]:
    """Только открытые обращения: закрытые в дайджест не попадают.

    Возраст считается по `now()` базы, а не по моменту запуска: дайджест
    планируется заранее, и «старше 6 часов» — свойство обращения, а не запуска.
    """
    return conn.execute("""
        select f.id, f.kind, f.text, f.status,
               coalesce(p.display_name, 'участник') as author,
               (extract(epoch from now() - f.created_at) / 3600)::int as age_hours,
               not exists (select 1 from feedback_answer a
                            where a.feedback_id = f.id) as unanswered
        from feedback f left join participant p on p.id = f.participant_id
        where f.status = any(%s)
        order by f.id""", (list(OPEN_STATUSES),)).fetchall()


def recipients(conn) -> list[dict]:
    """Получатели дайджеста — только те, у кого есть feedback.triage."""
    return conn.execute("""
        select distinct p.id, p.tg_chat_id
        from participant p
        join v_participant_capabilities c on c.participant_id = p.id
        where c.capability = 'feedback.triage' and p.status = 'active'
          and p.tg_chat_id is not null
        order by p.id""").fetchall()


def _line(item: dict) -> str:
    first = " ".join(item["text"].split())[:90]
    kind = KINDS.get(item["kind"], item["kind"])
    return "№{} · {} · {}: {}".format(item["id"], kind, item["author"], first)


def needs_attention(item: dict) -> bool:
    """Обращение без ответа старше 6 часов: такое нельзя терять в общем списке."""
    return bool(item.get("unanswered")) and int(item.get("age_hours") or 0) >= ATTENTION_HOURS


def digest_text(items: list[dict], moment: datetime) -> tuple[str, str]:
    """Заголовок и тело: сначала «требует вас», затем остальные открытые.

    Раздел «требует вас» — обращения без строки в журнале старше 6 часов
    (критерий 4 карты t_17f137b8): серверный таймер их не закрыл, значит нужен
    человек. Молчащее обращение не должно растворяться в общем списке.
    """
    title = f"Обращения за день: {len(items)}"
    stale = [item for item in items if needs_attention(item)]
    others = [item for item in items if not needs_attention(item)]
    lines = []
    if stale:
        lines.append("Требует вас (без ответа больше 6 часов):")
        lines.extend(_line(item) for item in stale)
        if others:
            lines.append("")
            lines.append("Остальные открытые:")
    lines.extend(_line(item) for item in others)
    return title, "\n".join(lines)[:LINE_LIMIT]


def plan(conn, moment: datetime) -> list[dict]:
    """Записи очереди; пустой список — открытых обращений нет."""
    items = open_feedback(conn)
    if not items:
        return []
    title, body = digest_text(items, moment)
    attention = sum(1 for item in items if needs_attention(item))
    key = f"feedback-digest:{moment.date().isoformat()}"
    out = []
    for person in recipients(conn):
        out.append({"participant_id": person["id"], "chat_id": person["tg_chat_id"],
                    "kind": "feedback_digest", "dedupe_key": key,
                    "title": title, "body": body, "scheduled_at": moment,
                    "count": len(items), "attention": attention})
    return out


def apply(conn, rows: list[dict]) -> int:
    """Пишет записи; повторный запуск за тот же день ничего не дублирует."""
    inserted = 0
    for row in rows:
        cur = conn.execute("""
            insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at)
            values (%s, %s, %s, %s, %s)
            on conflict (dedupe_key, recipient_id) do nothing
            returning id""",
            (row["participant_id"], row["kind"],
             Jsonb({"title": row["title"], "body": row["body"],
                    "feedback_count": row["count"],
                    "requires_attention": row["attention"]}),
             row["dedupe_key"], row["scheduled_at"])).fetchone()
        inserted += 1 if cur else 0
    conn.commit()
    return inserted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Дайджест обращений админу")
    parser.add_argument("--apply", action="store_true", help="записать в очередь")
    args = parser.parse_args(argv)
    with psycopg.connect(DB, row_factory=dict_row) as conn:
        rows = plan(conn, now_local())
        if not rows:
            print("открытых обращений нет — дайджест не нужен")
            return 0
        for row in rows:
            print("{} -> chat {}: {}".format(row["dedupe_key"], row["chat_id"], row["title"]))
        if args.apply:
            print(f"поставлено записей: {apply(conn, rows)}")
        else:
            print("показать без записи: --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
