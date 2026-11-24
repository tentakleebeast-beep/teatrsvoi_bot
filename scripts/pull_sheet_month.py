#!/usr/bin/env python3
"""
Перенести месяц занятости из общего листа «Расписание студийцы» в базу.

  python scripts/pull_sheet_month.py --month 2026-12            # показать, ничего не писать
  python scripts/pull_sheet_month.py --month 2026-12 --apply    # загрузить расходящиеся дни

Путь тот же, что у кнопки «Загрузить» в Mini App: `schedule._sheet_load_preview` и
`_sheet_load_apply`, поэтому экран и скрипт считают расхождение одинаково, а запись идёт
одной транзакцией на неделю с записью в аудит `availability.sheet.pull`. В лист не пишем.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

ROOT = os.environ.get("PROJECT_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services", "tool-api"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from _cli import apply_env  # noqa: E402

apply_env()

from app import core  # noqa: E402
from app.api import schedule  # noqa: E402

#: Состояния дня, которые надо перенести: в листе есть, в базе нет или иначе.
WRITE_STATES = ("new", "differs")


def month_bounds(text: str) -> tuple[date, date]:
    year, month = (int(part) for part in text.split("-"))
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def weeks(first: date, last: date) -> list[date]:
    start = first - timedelta(days=first.weekday())
    out = []
    while start <= last:
        out.append(start)
        start += timedelta(days=core.WEEK_DAYS)
    return out


def people() -> list[tuple[int, str]]:
    with core.pool.connection() as conn:
        rows = conn.execute(
            "select id, display_name from participant where is_active"
            " order by display_name").fetchall()
    return [(row["id"], row["display_name"]) for row in rows]


def actor_id() -> int:
    with core.pool.connection() as conn:
        row = conn.execute(
            "select id from participant where display_name ilike %s order by id limit 1",
            ("%Мамлютов%",)).fetchone()
    if row is None:
        raise SystemExit("не нашёл владельца в participant — укажите --actor")
    return row["id"]


def cache_sheet() -> None:
    """Читать лист один раз за прогон: Google даёт 60 чтений в минуту на проект.

    Предпросмотр и загрузка сами читают лист на каждый вызов, и на 12 людях × 5 неделях
    это упирается в 429 (замер 24.11.2026: перенеслось 87 дней, семь человек получили
    `sheet_unavailable`). Значения за прогон не меняются, поэтому кэшируем источник.
    """
    original = schedule._sheet_load_values
    cached: dict[str, tuple[list[list] | None, str]] = {}

    def once() -> tuple[list[list] | None, str]:
        if "values" not in cached:
            cached["values"] = original()
        return cached["values"]

    schedule._sheet_load_values = once


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="месяц как 2026-12")
    ap.add_argument("--apply", action="store_true", help="записать в базу")
    ap.add_argument("--actor", type=int, default=0, help="кто в аудите (id человека)")
    args = ap.parse_args()

    first, last = month_bounds(args.month)
    cache_sheet()
    core.pool.open()
    try:
        who = args.actor or actor_id()
        rows = people()
        total = 0
        for person_id, name in rows:
            line = [f"{name}"]
            for start in weeks(first, last):
                preview = schedule._sheet_load_preview(person_id, start)
                if not preview["ok"]:
                    line.append(f"{start:%d.%m}: {preview['error']}"
                                f" ({preview.get('detail') or ''})".strip())
                    continue
                days = [date.fromisoformat(item["day"]) for item in preview["days"]
                        if item["state"] in WRITE_STATES and first <= date.fromisoformat(item["day"]) <= last]
                if not days:
                    continue
                if not args.apply:
                    line.append(f"{start:%d.%m}: к переносу {len(days)}")
                    total += len(days)
                    continue
                answer = schedule._sheet_load_apply(person_id, start, days, who)
                if not answer.get("ok"):
                    line.append(f"{start:%d.%m}: не вышло — {answer.get('error')}")
                    continue
                total += len(answer["days"])
                line.append(f"{start:%d.%m}: {len(answer['days'])}")
            if len(line) > 1:
                print(" | ".join(line))
        print(f"\n{'перенесено' if args.apply else 'к переносу'}: {total} дней")
    finally:
        core.pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
