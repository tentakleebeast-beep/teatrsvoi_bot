#!/usr/bin/env python3
"""
Чтение раскрашенной сетки занятости из Google Sheets (docs/development/22).

  python scripts/parse_colored_schedule.py --sheet-id <ID> --list
  python scripts/parse_colored_schedule.py --sheet-id <ID> --sheet "11.2026 · Иванов" --dry-run
  python scripts/parse_colored_schedule.py --sheet-id <ID> --all --apply

Без --apply ничего не пишет в БД: сначала показываем, что поняли.
"""

from __future__ import annotations

import os
import re
from collections import Counter

import psycopg
from _cli import ROOT, add_tool_api_path, add_write_mode, apply_env, dsn, parser, writes
from google.oauth2 import service_account
from googleapiclient.discovery import build

apply_env()
add_tool_api_path()

from app.schedule_grid import (  # noqa: E402
    build_slots,
    merge_intervals,
    parse_day,
    parse_filled_until,
    parse_vertical_grid,
    split_by_marker,
    summary,
)

SA = os.getenv("GOOGLE_SA_JSON", os.path.join(ROOT, "secrets/google-sa.json"))
DB = dsn()
SHEET_NAME_RE = re.compile(r"^\s*(\d{2})\.(\d{4})\s*[·\-–|]\s*(.+?)\s*$")

FIELDS = ("sheets(properties(title,gridProperties),data(rowData(values("
          "formattedValue,userEnteredFormat.backgroundColor,effectiveFormat.backgroundColor))))")


def client():
    cred = service_account.Credentials.from_service_account_file(
        SA, scopes=["https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive.readonly"])
    return build("sheets", "v4", credentials=cred, cache_discovery=False)


def drive_client():
    cred = service_account.Credentials.from_service_account_file(
        SA, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=cred, cache_discovery=False)


def is_office_file(spreadsheet_id: str) -> bool:
    meta = drive_client().files().get(fileId=spreadsheet_id, fields="mimeType").execute()
    return "officedocument" in meta.get("mimeType", "") or "ms-excel" in meta.get("mimeType", "")


def read_xlsx(sheets, spreadsheet_id: str) -> dict[str, tuple[list, list[list]]]:
    """
    Office-файл (.xlsx): Sheets API форматирование не отдаёт, поэтому скачиваем
    и читаем заливку через openpyxl. ARGB 'FFEA4335' → '#EA4335'.
    """
    import io

    from openpyxl import load_workbook

    raw = drive_client().files().get_media(fileId=spreadsheet_id).execute()
    wb = load_workbook(io.BytesIO(raw), data_only=True)

    def fill_of(cell) -> str | None:
        fill = cell.fill
        if fill is None or fill.patternType != "solid":
            return None
        color = fill.start_color
        rgb = getattr(color, "rgb", None)
        if isinstance(rgb, str) and len(rgb) == 8:
            return "#" + rgb[2:]
        if isinstance(rgb, str) and len(rgb) == 6:
            return "#" + rgb
        return None

    out: dict[str, tuple[list, list[list]]] = {}
    for ws in wb.worksheets:
        grid = []
        for row in ws.iter_rows():
            grid.append([{"value": cell.value, "color": fill_of(cell)} for cell in row])
        out[ws.title] = (grid[0] if grid else [], grid[1:])
    return out


def read_sheet(sheets, spreadsheet_id: str, title: str) -> tuple[list, list[list]]:
    """Возвращает (шапка, строки) в виде {'value': ..., 'color': {...}}."""
    res = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id, includeGridData=True, fields=FIELDS,
        ranges=[title]).execute()
    sheet = next(s for s in res["sheets"] if s["properties"]["title"] == title)
    row_data = (sheet.get("data") or [{}])[0].get("rowData", [])

    grid = []
    for row in row_data:
        cells = []
        for cell in row.get("values", []):
            fmt = cell.get("effectiveFormat") or cell.get("userEnteredFormat") or {}
            cells.append({"value": cell.get("formattedValue"),
                          "color": fmt.get("backgroundColor")})
        grid.append(cells)
    return (grid[0] if grid else []), grid[1:]


def parse_title(title: str) -> tuple[str | None, str | None]:
    m = SHEET_NAME_RE.match(title)
    if not m:
        return None, None
    return f"{m.group(2)}-{m.group(1)}", m.group(3)          # месяц, ник


def resolve_participant(conn, nickname: str) -> int | None:
    key = nickname.strip().lower()
    row = conn.execute(
        "select participant_id from person_alias where lower(alias) = %s", (key,)).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "select id from participant where lower(person_key) = %s", (key,)).fetchone()
    return row[0] if row else None


def main() -> None:
    ap = parser(__doc__ or "Чтение раскрашенной сетки занятости")
    ap.add_argument("--sheet-id", help="ID таблицы: Google Sheets или Office (.xlsx)")
    ap.add_argument("--list", action="store_true", help="только список страниц")
    ap.add_argument("--all", action="store_true", help="обработать все страницы по маске")
    ap.add_argument("--sheet", help="одна страница: «11.2026 · Ник»")
    add_write_mode(ap)
    args = ap.parse_args()
    if not args.sheet_id:
        print(__doc__)
        return
    sid = args.sheet_id
    sheets = client()

    office = is_office_file(sid)
    print(f"тип файла: {'Office (.xlsx) — читаем через openpyxl' if office else 'Google Sheets — читаем через API'}")

    if office:
        book = read_xlsx(sheets, sid)
        titles = list(book)
    else:
        meta = sheets.spreadsheets().get(spreadsheetId=sid,
                                         fields="sheets(properties(title))").execute()
        book = None
        titles = [s["properties"]["title"] for s in meta["sheets"]]

    if args.list:
        for title in titles:
            month, nick = parse_title(title)
            mark = f"{month} · {nick}" if month else "не по маске"
            print(f"  {title!r:<34} {mark}")
        return
    if not args.all:
        if not args.sheet:
            print("укажите --sheet \"11.2026 · Ник\" или --all")
            return
        titles = [args.sheet]

    apply_changes = writes(args)
    total = Counter()

    with psycopg.connect(DB) as conn:
        for title in titles:
            month, nick = parse_title(title)
            if not month:
                print(f"пропуск {title!r}: имя страницы не по маске «MM.YYYY · Ник»")
                continue
            pid = resolve_participant(conn, nick)
            if not pid:
                print(f"пропуск {title!r}: ник «{nick}» не найден в справочнике")
                continue

            if office:
                header, rows = book[title]
            else:
                header, rows = read_sheet(sheets, sid, title)

            # пометка «Заполнено до: 30.11.2026» — определяет границу достоверных данных
            filled_until = None
            for row in rows[:3]:
                for cell in row:
                    value = cell.get("value") if isinstance(cell, dict) else cell
                    marker = parse_filled_until(value)
                    if marker:
                        filled_until = marker
                        break
                if filled_until:
                    break
            if filled_until:
                print(f"   пометка «заполнено до»: {filled_until:%d.%m.%Y}")

            # ориентация: даты в столбцах (часы в строках) или наоборот
            vertical = bool(header) and len(header) > 1 and parse_day(
                header[1] if not isinstance(header[1], dict) else header[1].get("value")) is not None
            if vertical:
                slots = parse_vertical_grid(header, rows)
                print("   ориентация: даты в столбцах, часы в строках")
            else:
                slots = build_slots(header, rows, default_month=month)
            if filled_until:
                slots, beyond = split_by_marker(slots, filled_until)
                if beyond:
                    print(f"   за пределами пометки: {len(beyond)} клеток — считаю «не отмечено»")
                filled_until = None
            stats = summary(slots)
            print(f"\n{title}: клеток {stats['cells']}, дней {stats['days']}, "
                  f"занят {stats['busy']}, свободен {stats['free']}, не уверен {stats['uncertain']}")
            for interval in merge_intervals(slots)[:8]:
                print(f"   {interval['day'][8:10]}.{interval['day'][5:7]} "
                      f"{interval['from']:02d}:00–{interval['to']:02d}:00 {interval['title']}")

            if apply_changes:
                with conn.cursor() as cur:
                    # удаляем только строки, пришедшие из файла: ручные отметки бота
                    # (source='bot') и объявления остаются нетронутыми (FR-C4)
                    cur.execute("delete from availability_hour where participant_id = %s "
                                "and source = 'sheet_import' and day >= %s and day <= %s",
                                (pid, f"{month}-01", f"{month}-31"))
                    for s in slots:
                        cur.execute("""insert into availability_hour
                                         (participant_id, day, hour, is_free, kind, source)
                                       values (%s,%s,%s,%s,'declared','sheet_import')
                                       on conflict (participant_id, day, hour) do update
                                         set is_free = excluded.is_free,
                                             kind = 'declared',
                                             source = 'sheet_import',
                                             updated_at = now()
                                       where availability_hour.source <> 'bot'""",
                                    (pid, s.day, s.hour, s.status == "free"))
                conn.commit()
                print(f"   записано в БД: {len(slots)} слотов (source=sheet, "
                      f"ручные отметки бота не тронуты)")
            total.update({k: stats[k] for k in ("busy", "free", "uncertain")})

    print(f"\nитого: занят {total['busy']}, свободен {total['free']}, "
          f"не уверен {total['uncertain']}")


if __name__ == "__main__":
    main()
