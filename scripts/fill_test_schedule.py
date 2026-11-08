#!/usr/bin/env python3
"""
Наполняет тестовую таблицу «Занятость - тест (цвета)» данными по новой сетке
(часы в строках, даты в столбцах), на основе боевого «Расписание студийцы».

  python scripts/fill_test_schedule.py --list
  python scripts/fill_test_schedule.py --apply

Страницы: «11.2026 · Ник» или «12.2026 · Ник». Цвета: красный занят,
зелёный свободен, жёлтый не уверен. Плюс провокации: фиолетовая клетка
и объединённая ячейка — чтобы парсер не падал.
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import date

from _cli import ROOT, add_tool_api_path, add_write_mode, apply_env, parser, writes
from google.oauth2 import service_account
from googleapiclient.discovery import build

apply_env()
add_tool_api_path()

from app.schedule_grid import hours_from_text  # noqa: E402

SA = os.getenv("GOOGLE_SA_JSON", os.path.join(ROOT, "secrets/google-sa.json"))
TEST_SID = os.getenv("SCHEDULE_TEST_SPREADSHEET_ID", "1Jh6tfa_aCt00YhkhzOkEPYJqGd1qTMK3AjVzosL2KRk")
SOURCE = os.path.join(ROOT, ".hermes", "desktop-attachments", "Расписание студийцы.xlsx")

HOURS = list(range(8, 24))          # 08:00 … 23:00
COLORS = {"busy": {"red": 0.92, "green": 0.26, "blue": 0.21},
          "free": {"red": 0.20, "green": 0.66, "blue": 0.33},
          "uncertain": {"red": 0.98, "green": 0.74, "blue": 0.02}}
PROVOCATION = {"red": 0.48, "green": 0.12, "blue": 0.64}     # фиолетовый

PEOPLE = ["Дарья Смирнова", "Александра Орлова", "Алена Морозова",
          "Виктор Новиков", "Мария Лебедева"]
NICKS = {"Дарья Смирнова": "Дарья", "Александра Орлова": "Орлова",
         "Алена Морозова": "Морозова", "Виктор Новиков": "Новиков",
         "Мария Лебедева": "Лебедева"}


def client():
    cred = service_account.Credentials.from_service_account_file(
        SA, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=cred, cache_discovery=False)


def read_source() -> dict[str, dict[date, str]]:
    from openpyxl import load_workbook
    wb = load_workbook(SOURCE, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))

    header = rows[0]
    columns = {str(h).strip(): i for i, h in enumerate(header)
               if h and i >= 3 and str(h).strip() in PEOPLE}

    out: dict[str, dict[date, str]] = {name: {} for name in columns}
    for row in rows[2:]:
        day = row[1]
        if not isinstance(day, date):
            continue
        for name, idx in columns.items():
            if idx < len(row) and row[idx] not in (None, ""):
                out[name][day] = str(row[idx]).strip()
    return out


def build_grid(source: dict[str, dict[date, str]], name: str) -> list[list[str]]:
    """Строки сетки: [часы × даты] — статус на каждую клетку месяца."""
    days = sorted(d for d in source[name] if d.month == 11)
    grid = []
    for hour in HOURS:
        row = []
        for day in days:
            statuses = hours_from_text(source[name][day])
            row.append(statuses.get(hour, ""))
        grid.append(row)
    return grid, days


def ensure_sheets(sheets, titles: list[str]) -> dict[str, int]:
    meta = sheets.spreadsheets().get(spreadsheetId=TEST_SID).execute()
    have = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    requests, new = [], []
    for title in titles:
        if title not in have:
            requests.append({"addSheet": {"properties": {"title": title}}})
            new.append(title)
    if requests:
        res = sheets.spreadsheets().batchUpdate(spreadsheetId=TEST_SID,
                                                body={"requests": requests}).execute()
        for reply in res.get("replies", []):
            props = (reply.get("addSheet") or {}).get("properties") or {}
            if props:
                have[props["title"]] = props["sheetId"]
    return have


def write_sheet(sheets, sheet_id: int, title: str, days: list[date],
                grid: list[list[str]]) -> Counter:
    values = [["Час"] + [d.strftime("%d.%m.%Y") for d in days]]
    for _i, hour in enumerate(HOURS):
        values.append([f"{hour:02d}:00"] + ["" for _ in days])

    sheets.spreadsheets().values().update(
        spreadsheetId=TEST_SID, range=f"'{title}'!A1",
        valueInputOption="USER_ENTERED", body={"values": values}).execute()

    requests, counts = [], Counter()
    for r_i, hour_row in enumerate(grid):
        row_index = r_i
        run_start, run_status = 0, None
        def flush(end_col: int, start_col: int, status: str | None, row_index: int = row_index):
            if not status:
                return
            requests.append({"repeatCell": {
                "range": {"sheetId": sheet_id,
                          "startRowIndex": row_index + 1, "endRowIndex": row_index + 2,
                          "startColumnIndex": start_col + 1, "endColumnIndex": end_col + 1},
                "cell": {"userEnteredFormat": {"backgroundColor": COLORS[status]}},
                "fields": "userEnteredFormat.backgroundColor"}})
        for c_i, status in enumerate(hour_row):
            if status != run_status:
                flush(c_i, run_start, run_status, row_index)
                run_start, run_status = c_i, status
            if status:
                counts[status] += 1
        flush(len(hour_row), run_start, run_status, row_index)

    if requests:
        sheets.spreadsheets().batchUpdate(spreadsheetId=TEST_SID,
                                          body={"requests": requests}).execute()
    return counts


def add_provocations(sheets, sheet_ids: dict[str, int]) -> None:
    """Фиолетовая клетка и объединение — проверка, что парсер не падает."""
    titles = sorted(sheet_ids)
    if not titles:
        return
    first, second = titles[0], titles[-1]
    requests = [
        {"repeatCell": {"range": {"sheetId": sheet_ids[first], "startRowIndex": 3,
                                  "endRowIndex": 4, "startColumnIndex": 2, "endColumnIndex": 3},
                        "cell": {"userEnteredFormat": {"backgroundColor": PROVOCATION}},
                        "fields": "userEnteredFormat.backgroundColor"}},
        {"mergeCells": {"range": {"sheetId": sheet_ids[second], "startRowIndex": 5,
                                  "endRowIndex": 6, "startColumnIndex": 3, "endColumnIndex": 5},
                        "mergeType": "MERGE_ALL"}},
    ]
    sheets.spreadsheets().batchUpdate(spreadsheetId=TEST_SID,
                                      body={"requests": requests}).execute()


def main() -> None:
    ap = parser(__doc__ or "Заполнение тестовой таблицы занятости")
    add_write_mode(ap)
    apply_changes = writes(ap.parse_args())
    source = read_source()
    titles = [f"11.2026 · {NICKS[n]}" for n in PEOPLE]

    sheets = client()
    if not apply_changes:
        print("страницы, которые будут заполнены:", ", ".join(titles))
        for name in PEOPLE:
            grid, days = build_grid(source, name)
            counts = Counter(s for row in grid for s in row if s)
            print(f"  {name}: дней {len(days)}, занят {counts['busy']}, "
                  f"свободен {counts['free']}, не уверен {counts['uncertain']}")
        return

    sheet_ids = ensure_sheets(sheets, titles)
    for name, title in zip(PEOPLE, titles, strict=False):
        grid, days = build_grid(source, name)
        counts = write_sheet(sheets, sheet_ids[title], title, days, grid)
        print(f"{title}: клеток {sum(counts.values())} "
              f"(занят {counts['busy']}, свободен {counts['free']}, не уверен {counts['uncertain']})")
    add_provocations(sheets, {t: sheet_ids[t] for t in titles})
    print("\nдобавлены провокации: фиолетовая клетка и объединённая ячейка")


if __name__ == "__main__":
    main()
