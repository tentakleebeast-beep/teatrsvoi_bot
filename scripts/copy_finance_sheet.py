"""Копия листа «Финансы 2026 от 07» из рабочей таблицы студии в таблицу бюджета бота.

Источник — нативная Google-таблица «СВОИ 2025» (самая свежая версия, ведётся руками).
Задача: новый файл бюджета должен выглядеть и считаться так же, чтобы переход не ломал
привычную работу.

Что переносится: значения и формулы, объединения, ширины столбцов и высоты строк,
фон, шрифт, выравнивание и перенос, числовые форматы, границы.

Запуск:
  env -u PYTHONPATH .venv/bin/python scripts/copy_finance_sheet.py --dry-run
  env -u PYTHONPATH .venv/bin/python scripts/copy_finance_sheet.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys

from _cli import ROOT, apply_env

apply_env()

SOURCE_ID = os.getenv("FINANCE_SOURCE_ID", "1JdSISyD2ye3RMcg_mwwjEEqKPn_6jyr39LeL7FCq7ac")
SHEET = "Финансы 2026 от 07"
FIELDS = ("userEnteredFormat.backgroundColor,"
          "userEnteredFormat.textFormat,"
          "userEnteredFormat.numberFormat,"
          "userEnteredFormat.horizontalAlignment,"
          "userEnteredFormat.verticalAlignment,"
          "userEnteredFormat.wrapStrategy,"
          "userEnteredFormat.borders")


def client():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    key = ROOT / os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
    creds = service_account.Credentials.from_service_account_file(
        str(key), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def source_tab(service, spreadsheet_id: str, title: str):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in meta["sheets"]:
        if sheet["properties"]["title"] == title:
            return sheet["properties"]["sheetId"]
    raise SystemExit(f"Нет листа {title!r} в источнике")


def read_source(service, spreadsheet_id: str, title: str) -> dict:
    data = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        ranges=[f"'{title}'"],
        includeGridData=True,
        fields="sheets(merges,data(rowData(values(userEnteredValue,effectiveFormat)),"
               "rowMetadata(pixelSize),columnMetadata(pixelSize)))").execute()
    sheet = data["sheets"][0]
    grid = sheet["data"][0]
    return {"merges": sheet.get("merges", []),
            "rows": grid.get("rowData", []),
            "columns": grid.get("columnMetadata", [])}


def used_size(rows: list[dict]) -> tuple[int, int]:
    last_row, last_col = 0, 0
    for index, row in enumerate(rows):
        values = row.get("values") or []
        if values:
            last_row = index + 1
            last_col = max(last_col, len(values))
    return last_row, last_col


def values_matrix(rows: list[dict], height: int, width: int) -> list[list]:
    matrix = []
    for row_index in range(height):
        row = rows[row_index] if row_index < len(rows) else {}
        values = row.get("values") or []
        line = []
        for col_index in range(width):
            cell = values[col_index] if col_index < len(values) else {}
            entered = cell.get("userEnteredValue") or {}
            if "formulaValue" in entered:
                line.append(entered["formulaValue"])          # уже с «=»
            elif "numberValue" in entered:
                line.append(entered["numberValue"])
            elif "stringValue" in entered:
                line.append(entered["stringValue"])
            elif "boolValue" in entered:
                line.append(entered["boolValue"])
            else:
                line.append("")
        matrix.append(line)
    return matrix


def ensure_target_sheet(service, spreadsheet_id: str, title: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in meta["sheets"]:
        if sheet["properties"]["title"] == title:
            sheet_id = sheet["properties"]["sheetId"]
            service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id, range=f"'{title}'").execute()
            return sheet_id
    reply = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": title, "index": 0}}}]}).execute()
    return reply["replies"][0]["addSheet"]["properties"]["sheetId"]


def format_requests(rows: list[dict], target_id: int, height: int, width: int) -> list[dict]:
    requests: list[dict] = []
    for row_index in range(height):
        row = rows[row_index] if row_index < len(rows) else {}
        values = row.get("values") or []
        for col_index in range(width):
            cell = values[col_index] if col_index < len(values) else {}
            effective = cell.get("effectiveFormat") or {}
            if not effective:
                continue
            user_format = {}
            if effective.get("backgroundColor"):
                user_format["backgroundColor"] = effective["backgroundColor"]
            text = effective.get("textFormat") or {}
            if text:
                allowed = {key: text[key] for key in
                           ("bold", "italic", "fontSize", "foregroundColor", "fontFamily")
                           if key in text}
                if allowed:
                    user_format["textFormat"] = allowed
            if effective.get("numberFormat"):
                user_format["numberFormat"] = effective["numberFormat"]
            for key in ("horizontalAlignment", "verticalAlignment", "wrapStrategy"):
                if effective.get(key):
                    user_format[key] = effective[key]
            if effective.get("borders"):
                user_format["borders"] = effective["borders"]
            if not user_format:
                continue
            requests.append({"repeatCell": {
                "range": {"sheetId": target_id, "startRowIndex": row_index,
                          "endRowIndex": row_index + 1,
                          "startColumnIndex": col_index, "endColumnIndex": col_index + 1},
                "cell": {"userEnteredFormat": user_format},
                "fields": ",".join("userEnteredFormat." + key for key in user_format)}})
    return requests


def dimension_requests(source: dict, target_id: int) -> list[dict]:
    requests = []
    for index, column in enumerate(source["columns"][:60]):
        size = column.get("pixelSize")
        if size:
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": target_id, "dimension": "COLUMNS",
                          "startIndex": index, "endIndex": index + 1},
                "properties": {"pixelSize": size}, "fields": "pixelSize"}})
    for index, row in enumerate(source["rows"][:400]):
        size = (row.get("rowMetadata") or {}).get("pixelSize")
        if size:
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": target_id, "dimension": "ROWS",
                          "startIndex": index, "endIndex": index + 1},
                "properties": {"pixelSize": size}, "fields": "pixelSize"}})
    return requests


def merge_requests(merges: list[dict], target_id: int) -> list[dict]:
    requests = []
    for merge in merges:
        start_row = merge.get("startRowIndex", 0)
        end_row = merge.get("endRowIndex", start_row + 1)
        start_col = merge.get("startColumnIndex", 0)
        end_col = merge.get("endColumnIndex", start_col + 1)
        requests.append({"mergeCells": {
            "range": {"sheetId": target_id, "startRowIndex": start_row, "endRowIndex": end_row,
                      "startColumnIndex": start_col, "endColumnIndex": end_col},
            "mergeType": "MERGE_ALL"}})
    return requests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    service = client()
    source_tab(service, SOURCE_ID, SHEET)   # проверяем, что лист есть
    source = read_source(service, SOURCE_ID, SHEET)
    height, width = used_size(source["rows"])
    formulas = sum(1 for row in source["rows"] for cell in (row.get("values") or [])
                   if (cell.get("userEnteredValue") or {}).get("formulaValue"))
    print(f"источник: «{SHEET}» {height}×{width}, формул {formulas}, "
          f"объединений {len(source['merges'])}")

    if not args.apply:
        print("DRY-RUN: в целевой файл ничего не записано")
        return 0

    target_id_spreadsheet = os.environ["BUDGET_SPREADSHEET_ID"]
    sheet_id = ensure_target_sheet(service, target_id_spreadsheet, SHEET)

    letters = _letters(width)
    matrix = values_matrix(source["rows"], height, width)
    service.spreadsheets().values().update(
        spreadsheetId=target_id_spreadsheet,
        range=f"'{SHEET}'!A1:{letters}{height}",
        valueInputOption="USER_ENTERED",
        body={"values": matrix}).execute()

    requests = (merge_requests(source["merges"], sheet_id)
                + dimension_requests(source, sheet_id)
                + format_requests(source["rows"], sheet_id, height, width))
    for start in range(0, len(requests), 200):
        service.spreadsheets().batchUpdate(
            spreadsheetId=target_id_spreadsheet,
            body={"requests": requests[start:start + 200]}).execute()

    print(f"готово: значений {height}×{width}, запросов форматирования {len(requests)}")
    return 0


def _letters(count: int) -> str:
    result = ""
    while count > 0:
        count, rem = divmod(count - 1, 26)
        result = chr(65 + rem) + result
    return result


if __name__ == "__main__":
    sys.exit(main())
