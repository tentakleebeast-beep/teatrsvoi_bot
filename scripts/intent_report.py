#!/usr/bin/env python3
"""
Отчёт по каскаду разбора (docs/development/21).

  python scripts/intent_report.py                  # пробелы за 7 дней
  python scripts/intent_report.py --days 30
  python scripts/intent_report.py --candidates     # очередь новых категорий
  python scripts/intent_report.py --approve "Бутафория"
  python scripts/intent_report.py --reject "Бутафория"
  python scripts/intent_report.py --purge          # удалить записи старше 90 дней

Отчёт считает каскад разбора: сессии агента (`agent_status is not null`, A-5) живут
в той же таблице `intent_log`, но категорий не выбирают — их деградация к правилам
не должна попадать в «правила разобрали» и «потеряно». Их разбирает экран
`/webapp/admin/t/agent`.
"""

from __future__ import annotations

import psycopg
from _cli import apply_env, dsn, parser
from psycopg.rows import dict_row

apply_env()

DSN = dsn()
RETENTION_DAYS = 90


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


def show_gaps(days: int) -> None:
    with connect() as conn:
        print(f"=== пробелы разбора, последние {days} дней (без сессий агента) ===")
        rows = conn.execute("""
            select date_trunc('day', ts)::date as day,
                   count(*) as total,
                   count(*) filter (where source = 'rules') as by_rules,
                   count(*) filter (where source = 'llm') as by_llm,
                   count(*) filter (where source in ('buttons', 'human_new')) as by_human,
                   count(*) filter (where not resolved) as lost
            from intent_log
            where ts > now() - (%s || ' days')::interval
              and agent_status is null
            group by 1 order by 1 desc""", (days,)).fetchall()
        if not rows:
            print("  записей нет")
        for r in rows:
            share = (100 * r["by_rules"] / r["total"]) if r["total"] else 0
            print(f"  {r['day']}: всего {r['total']}, правила {r['by_rules']} ({share:.0f}%), "
                  f"модель {r['by_llm']}, человек {r['by_human']}, потеряно {r['lost']}")

        print("\n=== топ фраз, где правила молчат ===")
        top = conn.execute("""
            select text, count(*) as times from intent_log
            where source in ('buttons', 'human_new', 'failed')
              and ts > now() - (%s || ' days')::interval
            group by text order by times desc, max(ts) desc limit 20""", (days,)).fetchall()
        if not top:
            print("  таких фраз нет")
        for t in top:
            print(f"  {t['times']:>3}× {t['text'][:90]}")


def show_candidates() -> None:
    with connect() as conn:
        rows = conn.execute("""select name, seen_count, approved, first_seen
                               from category_candidate
                               order by approved nulls first, seen_count desc""").fetchall()
        print("=== кандидаты в категории ===")
        if not rows:
            print("  очередь пуста")
        for r in rows:
            status = {None: "ждёт решения", True: "утверждена", False: "отклонена"}[r["approved"]]
            print(f"  {r['name']:<28} {r['seen_count']:>3}×  {status}")


def decide(name: str, approve: bool) -> None:
    with connect() as conn:
        row = conn.execute("select id from category_candidate where name = %s", (name,)).fetchone()
        if not row:
            print(f"кандидат «{name}» не найден")
            return
        conn.execute("update category_candidate set approved = %s, approved_at = now() "
                     "where id = %s", (approve, row["id"]))
        if approve:
            direction = "expense"
            conn.execute("""insert into category (name, direction) values (%s, %s)
                            on conflict (name) do nothing""", (name, direction))
            print(f"«{name}» утверждена и добавлена в справочник ({direction})")
        else:
            print(f"«{name}» отклонена")


def purge() -> None:
    with connect() as conn:
        deleted = conn.execute(
            "delete from intent_log where ts < now() - (%s || ' days')::interval returning 1",
            (RETENTION_DAYS,)).fetchall()
    if deleted:                      # в cron молчим, когда чистить нечего
        print(f"журнал интентов: удалено {len(deleted)} записей старше {RETENTION_DAYS} дней"
              " (вместе с шагами агента)")


def main() -> None:
    ap = parser(__doc__ or "Отчёт по каскаду разбора")
    ap.add_argument("--days", type=int, default=7, help="за сколько дней показывать пробелы")
    ap.add_argument("--candidates", action="store_true", help="очередь новых категорий")
    ap.add_argument("--approve", metavar="КАТЕГОРИЯ", help="утвердить новую категорию")
    ap.add_argument("--reject", metavar="КАТЕГОРИЯ", help="отклонить новую категорию")
    ap.add_argument("--purge", action="store_true",
                    help=f"удалить записи старше {RETENTION_DAYS} дней")
    args = ap.parse_args()

    if args.purge:
        purge()
        return
    if args.candidates:
        show_candidates()
        return
    if args.approve:
        decide(args.approve, True)
        return
    if args.reject:
        decide(args.reject, False)
        return
    show_gaps(args.days)


if __name__ == "__main__":
    main()
