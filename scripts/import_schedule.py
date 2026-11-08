#!/usr/bin/env python3
"""
Импорт занятости из «Расписание СВОИ.xlsx» (часовая матрица 0/1) в Postgres
и в Google-таблицу «Занятость» в виде интервалов.

  python scripts/import_schedule.py --dry-run
  python scripts/import_schedule.py

Матрица: строки = даты (кол. B), колонки с 3-й = часы 08:00…; 1 = свободен, 0 = занят.
В БД пишем почасово (availability_hour), в таблицу — сжатыми интервалами.

Кто есть кто: лист человека ищется в данных (`person_alias`, `person_key`, `display_name`),
а не по словарю в коде — после обезличивания имён (Ш-03 плана 129) словарь указывал на
вымышленные имена. Никнейм, которого в БД нет, дописывается в `config/participant-names.yaml`;
после первого импорта скрипт сам заводит алиас «никнейм листа → человек».
"""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime, time

import openpyxl
import psycopg
from _cli import dsn
from _people import person_index, report_gaps, unmatched_sheets
from google.oauth2 import service_account
from googleapiclient.discovery import build
from psycopg.rows import dict_row

SOURCE = "/tmp/svoi_src/Инфа СВОИ/Админ/Расписание СВОИ.xlsx"
DB = dsn()
SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
SPREADSHEET = os.getenv("SCHEDULE_SPREADSHEET_ID") or "1XZAr6P5QrvAx4wboJeFe0vXV5SKkhJo6op4oGtNhKTU"
DATE_FROM, DATE_TO = date(2026, 7, 1), date(2026, 10, 31)

HEADERS = ["Дата", "Участник", "С", "По", "Тип", "Источник", "Обновлено"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def read_matrix(index):
    """Прочитать личные листы файла: кто есть в БД, того и читаем.

    Соответствие «имя листа → человек» приходит из данных (`_people.person_index`), а не
    из словаря в коде: после обезличивания (Ш-03) словарь указывал на вымышленные имена.
    Возвращает ячейки `(participant_id, date, hour, is_free)`, прочитанные листы и листы,
    похожие на личные, но без человека в БД (их оператору и надо назвать).
    """
    wb = openpyxl.load_workbook(SOURCE, data_only=True)
    try:
        person_sheets = [s for s in wb.sheetnames if index.resolve(s) is not None]
        unclaimed = unmatched_sheets(list(wb.sheetnames), index)
        out = []           # (participant_id, date, hour, is_free)
        hours_header = None
        for name in person_sheets:
            ws = wb[name]
            if hours_header is None:
                # шапка: кол.3..18 = 08:00…23:00, кол.19 = 00:00 (тот же последний слот
                # «до 24:00»), кол.20 = «Итог» (формула, не читаем)
                hours_header = []
                for c in range(3, ws.max_column + 1):
                    v = ws.cell(row=1, column=c).value
                    if isinstance(v, datetime):
                        v = v.time()
                    if isinstance(v, time):
                        hour = v.hour if v.hour != 0 else 23   # 00:00 = конец суток
                        hours_header.append((c, hour))
                # слот 23 объединяем: свободен, если свободен хотя бы один из двух столбцов
                merged = {}
                for c, h in hours_header:
                    merged.setdefault(h, []).append(c)
                hours_header = [(cols, h) for h, cols in merged.items()]
            participant_id = index.resolve(name)
            for r in range(2, ws.max_row + 1):
                d = ws.cell(row=r, column=2).value
                if not isinstance(d, datetime):
                    continue
                day = d.date()
                if not (DATE_FROM <= day <= DATE_TO):
                    continue
                for cols, hour in hours_header:
                    vals = [ws.cell(row=r, column=c).value for c in cols]
                    present = [v for v in vals if v is not None]
                    if not present:
                        continue
                    out.append((participant_id, day, hour,
                                any(bool(int(v)) for v in present)))
    finally:
        wb.close()
    return out, person_sheets, unclaimed


def save_aliases(conn, sheets, index) -> int:
    """Завести алиасы «никнейм листа → человек», чтобы со следующего прогона конфиг не нужен.

    Конфликт разрешается без указания цели (`on conflict do nothing`), а не по
    `(participant_id, alias)`: в схеме два уникальных ограничения — `(participant_id, alias)`
    (023) и `(participant_id, lower(alias))` (030). Второе ловит тот же никнейм в другом
    регистре, и адресный `on conflict (participant_id, alias)` на нём падал бы
    `InvalidColumnReference`/`UniqueViolation` — импорт обрывался бы целиком.
    Возвращает число листов, для которых алиас заведён (или уже был).
    """
    saved = 0
    for sheet in sheets:
        participant_id = index.resolve(sheet)
        if participant_id is None:
            continue
        conn.execute("""insert into person_alias (participant_id, alias, alias_kind, source)
                        values (%s,%s,'short','import') on conflict do nothing""",
                     (participant_id, sheet.strip().lower()))
        saved += 1
    return saved


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        index = person_index(conn)
    cells, sheets, unclaimed = read_matrix(index)
    print(f"прочитано личных листов: {len(sheets)} → {sheets}")
    for line in report_gaps(index, unclaimed):
        print(f"! {line}")
    print(f"ячеек занятости в диапазоне {DATE_FROM}…{DATE_TO}: {len(cells)}")

    days = sorted({c[1] for c in cells})
    people = sorted({c[0] for c in cells})
    if not cells:
        print("! в файле нет ни одной ячейки за период: писать нечего")
        return
    print(f"дней: {len(days)} ({days[0]} … {days[-1]}) | людей: {len(people)}")

    # сжатие в интервалы свободного времени
    intervals = []
    for participant_id in people:
        for day in days:
            row = {h: f for n, d, h, f in cells if n == participant_id and d == day}
            if not row:
                continue
            start = None
            for h in range(0, 24):
                free = row.get(h)
                if free and start is None:
                    start = h
                elif not free and start is not None:
                    intervals.append((participant_id, day, start, h))
                    start = None
            if start is not None:
                intervals.append((participant_id, day, start, 24))
    print(f"интервалов свободного времени: {len(intervals)}")

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        conn.execute("alter table participant alter column tg_chat_id drop not null")
        # никнейм листа становится алиасом человека: со следующего прогона он находится
        # уже без конфига, а оператору не нужно вести словарь руками
        save_aliases(conn, sheets, index)

        inserted = 0
        for participant_id, day, hour, free in cells:
            conn.execute("""
                insert into availability_hour (participant_id, day, hour, is_free, source)
                values (%s,%s,%s,%s,'sheet_import')
                on conflict (participant_id, day, hour) do update
                  set is_free = excluded.is_free
                where availability_hour.source <> 'bot'""",
                (participant_id, day, hour, free))
            inserted += 1

        conn.execute("""
            select day, count(*) filter (where is_free) free_cnt, count(*) known
            from availability_hour where day between %s and %s group by day order by day""",
            (DATE_FROM, DATE_TO)).fetchall()

        if args.dry_run:
            conn.rollback()
            print("DRY-RUN: откатано")
            return
        conn.commit()
        print(f"в БД записано почасовых значений: {inserted}")

    if args.dry_run:
        return

    # запись интервалов в Google-таблицу
    cred = service_account.Credentials.from_service_account_file(SA, scopes=SCOPES)
    sheets_api = build("sheets", "v4", credentials=cred, cache_discovery=False)
    meta = sheets_api.spreadsheets().get(
        spreadsheetId=SPREADSHEET, fields="sheets(properties(title))").execute()
    if "Занятость" not in [s["properties"]["title"] for s in meta.get("sheets", [])]:
        sheets_api.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET,
            body={"requests": [{"addSheet": {"properties": {"title": "Занятость"}}}]}).execute()
    sheets_api.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET, range="Занятость!A1", valueInputOption="RAW",
        body={"values": [HEADERS]}).execute()
    sheets_api.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET, range="Занятость!A2:G20000").execute()

    now = datetime.now().strftime("%d.%m %H:%M")
    rows = [[day.strftime("%d.%m.%Y"), index.display_name(participant_id),
             f"{s:02d}:00", f"{e:02d}:00" if e < 24 else "24:00",
             "свободен", "импорт (файл)", now]
            for participant_id, day, s, e in intervals]
    for i in range(0, len(rows), 2000):
        sheets_api.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET, range=f"Занятость!A{2 + i}",
            valueInputOption="USER_ENTERED", body={"values": rows[i:i + 2000]}).execute()
    print(f"в таблицу записано интервалов: {len(rows)}")
    print(f"https://docs.google.com/spreadsheets/d/{SPREADSHEET}/edit")


if __name__ == "__main__":
    main()
