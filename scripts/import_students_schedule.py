#!/usr/bin/env python3
"""
Импорт занятости из файла «Расписание студийцы» (xlsx, люди в колонках).

  python scripts/import_students_schedule.py --month 2026-11
  python scripts/import_students_schedule.py --month 2026-11 --apply

Что делает:
  * читает лист, находит шапку с именами в первой строке;
  * строки — дни (дата в первом столбце с датой);
  * текст в клетке переводит в почасовые статусы (schedule_grid.hours_from_text);
  * пишет в availability_hour с source='sheet_import', не трогая отметки бота (source='bot').

Файл — источник истины, поэтому повторный импорт за тот же месяц перезаписывает
только строки, пришедшие из файла.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import openpyxl
import psycopg
from _cli import dsn
from psycopg.rows import dict_row

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "services" / "tool-api"))

from app.schedule_grid import hours_from_text, parse_day  # noqa: E402

DB = dsn()
DEFAULT_FILE = PROJECT / ".hermes" / "desktop-attachments" / "Расписание студийцы.xlsx"


def find_header(sheet) -> tuple[int, dict[int, str]]:
    """Ищет строку, где в колонках >= 3 стоят имена, и возвращает {колонка: имя}."""
    for row_index in range(1, 6):
        names = {}
        for column in range(3, sheet.max_column + 1):
            value = sheet.cell(row_index, column).value
            if isinstance(value, str) and len(value.strip()) > 3 and not value.strip().isdigit():
                names[column] = value.strip()
        if len(names) >= 3:
            return row_index, names
    raise SystemExit("не нашёл шапку с именами участников")


def resolve_people(conn, names: dict[int, str]) -> dict[int, int | None]:
    """Имя из шапки → participant.id. Сопоставляем по словам: «Дарья Смирнова» ≈ «Смирнова Дарья»."""
    rows = conn.execute("select id, display_name, person_key from participant "
                        "where status <> 'archived'").fetchall()
    mapping: dict[int, int | None] = {}
    for column, raw in names.items():
        words = {w.lower() for w in raw.replace(".", " ").split() if len(w) > 2}
        best, best_score = None, 0
        for row in rows:
            candidate = {w.lower() for w in (row["display_name"] or "").replace(".", " ").split()
                         if len(w) > 2}
            keys = {row["person_key"].lower()} if row["person_key"] else set()
            score = len(words & candidate) + (2 if words & keys else 0)
            if score > best_score:
                best, best_score = row["id"], score
        mapping[column] = best if best_score else None
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(DEFAULT_FILE))
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--create-missing", action="store_true",
                        help="завести в базе людей из файла, которых ещё нет (status=pending)")
    args = parser.parse_args()

    year, month = (int(x) for x in args.month.split("-"))
    sheet_path = Path(args.file)
    if not sheet_path.exists():
        raise SystemExit(f"файл не найден: {sheet_path}")

    workbook = openpyxl.load_workbook(sheet_path, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    header_row, names = find_header(sheet)

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        people = resolve_people(conn, names)
        print(f"файл: {sheet_path.name}, лист «{sheet.title}», шапка в строке {header_row}")
        print(f"людей в шапке: {len(names)}, найдено в базе: "
              f"{sum(1 for v in people.values() if v)}")
        for column, name in names.items():
            if people[column] is None:
                print(f"  НЕ НАЙДЕН в базе: «{name}» — колонка {column} пропущена")

        if args.create_missing and args.apply:
            for column, name in names.items():
                if people[column] is not None or name.lower().startswith("решение"):
                    continue
                created = conn.execute(
                    """insert into participant (display_name, person_key, role, status)
                       values (%s, %s, 'troupe', 'pending') returning id""",
                    (name, " ".join(name.lower().split()))).fetchone()
                people[column] = created["id"]
                print(f"  заведён новый участник: «{name}» (id {created['id']}, status=pending)")

        per_person: Counter = Counter()
        statuses: Counter = Counter()
        days_seen: set[date] = set()
        rows_to_write: list[tuple[int, date, int, str, str | None]] = []

        for row_index in range(header_row + 1, sheet.max_row + 1):
            raw_date = sheet.cell(row_index, 2).value
            day = parse_day(raw_date) if raw_date else None
            if day is None or day.year != year or day.month != month:
                continue
            days_seen.add(day)
            for column, participant_id in people.items():
                if participant_id is None:
                    continue
                value = sheet.cell(row_index, column).value
                marks = hours_from_text(value)
                if not marks:
                    continue
                # исходный текст клетки храним: в нём бывают минуты («до 18:30»),
                # которые часовая сетка округлит — без `raw_text` карточка дня
                # вернулась бы как «до 19:00», то есть шире, чем человек написал
                text = " ".join(str(value).split()) or None
                for hour, status in marks.items():
                    rows_to_write.append((participant_id, day, hour, status, text))
                per_person[participant_id] += 1
                statuses.update(marks.values())

        print(f"\nдней в месяце {args.month}: {len(days_seen)}, "
              f"часовых отметок: {len(rows_to_write)}")
        for status, count in sorted(statuses.items()):
            print(f"  {status}: {count}")

        if not args.apply:
            print("\n(dry-run: ничего не записано)")
            return

        with conn.cursor() as cur:
            cur.execute("delete from availability_hour where source = 'sheet_import' "
                        "and day >= %s and day < %s",
                        (date(year, month, 1),
                         date(year + (month == 12), month % 12 + 1, 1)))
            for participant_id, day, hour, status, text in rows_to_write:
                cur.execute("""insert into availability_hour
                                 (participant_id, day, hour, is_free, kind, source, raw_text, updated_at)
                               values (%s,%s,%s,%s,%s,'sheet_import',%s, now())
                               on conflict (participant_id, day, hour) do update
                                 set is_free = excluded.is_free,
                                     kind = excluded.kind,
                                     source = 'sheet_import',
                                     raw_text = excluded.raw_text,
                                     updated_at = now()
                                 where availability_hour.source <> 'bot'""",
                            (participant_id, day, hour, status == "free", status, text))
        conn.commit()
        print(f"\nзаписано: {len(rows_to_write)} часовых отметок "
              f"по {len(per_person)} людям (source=sheet)")


if __name__ == "__main__":
    main()
