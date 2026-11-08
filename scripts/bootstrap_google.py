#!/usr/bin/env python3
"""
bootstrap_google.py — проверка доступа и достройка структуры Google для бота СВОИ.

Идемпотентен: повторный запуск ничего не дублирует.
  python scripts/bootstrap_google.py --dry-run   # только проверки
  python scripts/bootstrap_google.py             # создать недостающее

Нужны права service account: Editor на корневой папке SVOI,
Editor на таблицах бюджета/занятости (или наследование от SVOI),
«Make changes to events» на календаре SVOI.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    sys.exit("Нет зависимостей: uv pip install google-api-python-client google-auth google-auth-httplib2")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
]

TX_HEADERS = ["tx_id", "Дата", "Тип", "Сумма", "Категория", "Проект", "Контрагент",
              "Назначение", "Способ", "Кто внёс", "Основание", "Ревизия", "Статус"]
REF_HEADERS = ["категория", "направление", "синонимы"]
DUES_HEADERS = ["Участник", "Период", "Начислено", "Оплачено", "Дата", "tx_id", "Статус"]
AVAIL_HEADERS = ["Дата", "Участник", "С", "По", "Тип", "Источник", "Обновлено"]

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


def env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        sys.exit(f"{FAIL} {name} не задан в окружении/.env")
    return v


def creds():
    path = Path(os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json"))
    if not path.exists():
        sys.exit(f"{FAIL} нет файла ключа: {path} (см. docs/infrastructure/09-google-setup.md)")
    return service_account.Credentials.from_service_account_file(str(path), scopes=SCOPES)


def ensure_folder(drive, name: str, parent: str, dry: bool) -> str | None:
    q = (f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' "
         f"and '{parent}' in parents and trashed = false")
    found = drive.files().list(q=q, fields="files(id,name)", pageSize=5).execute().get("files", [])
    if found:
        print(f"{OK} папка {name} уже есть ({found[0]['id']})")
        return found[0]["id"]
    if dry:
        print(f"{WARN} папки {name} нет — создалась бы")
        return None
    created = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent]},
        fields="id",
    ).execute()
    print(f"{OK} создана папка {name} ({created['id']})")
    return created["id"]


def ensure_sheet(sheets, spreadsheet_id: str, title: str, headers: list[str], dry: bool) -> None:
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id,
                                     fields="sheets(properties(title))").execute()
    names = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if title not in names:
        if dry:
            print(f"{WARN} листа «{title}» нет — создался бы")
            return
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()
        print(f"{OK} создан лист «{title}»")

    cur = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{title}!A1:M1"
    ).execute().get("values", [[]])
    row = cur[0] if cur else []
    if [c.strip() for c in row][:len(headers)] == headers:
        print(f"{OK} шапка «{title}» соответствует контракту")
        return
    if row:
        print(f"{WARN} шапка «{title}» отличается: {row[:6]}… — не перезаписываю, проверь вручную")
        return
    if dry:
        print(f"{WARN} шапка «{title}» пустая — записалась бы")
        return
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{title}!A1",
        valueInputOption="RAW", body={"values": [headers]},
    ).execute()
    print(f"{OK} записана шапка «{title}»")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="только проверки, без записи")
    args = ap.parse_args()
    dry = args.dry_run

    sa_path = Path(os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json"))
    cred = creds()
    with open(sa_path) as f:
        import json
        client_email = json.load(f)["client_email"]
    print(f"service account: {client_email}")
    print(f"режим: {'DRY-RUN' if dry else 'ЗАПИСЬ'}\n")

    drive = build("drive", "v3", credentials=cred, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=cred, cache_discovery=False)
    calendar = build("calendar", "v3", credentials=cred, cache_discovery=False)

    # 1. корень
    root = env("DRIVE_ROOT_ID")
    try:
        meta = drive.files().get(fileId=root, fields="id,name,capabilities(canEdit)").execute()
        can_edit = meta.get("capabilities", {}).get("canEdit")
        print(f"{OK if can_edit else WARN} доступ к корню «{meta.get('name')}» "
              f"({'редактор' if can_edit else 'только чтение'})")
    except HttpError as e:
        sys.exit(f"{FAIL} нет доступа к DRIVE_ROOT_ID={root}: {e}. Расшарь папку на {client_email}")

    # 2. _shared и подпапки
    shared = ensure_folder(drive, "_shared", root, dry)
    if shared:
        for name in ("budget", "schedule", "media", "notes"):
            ensure_folder(drive, name, shared, dry)

    # 3. таблицы
    budget = env("BUDGET_SPREADSHEET_ID")
    try:
        info = sheets.spreadsheets().get(spreadsheetId=budget,
                                         fields="properties(title)").execute()
        print(f"{OK} бюджет доступен: «{info['properties']['title']}»")
        for title, headers in (("Транзакции", TX_HEADERS), ("Справочники", REF_HEADERS),
                               ("Взносы", DUES_HEADERS)):
            ensure_sheet(sheets, budget, title, headers, dry)
    except HttpError as e:
        print(f"{FAIL} бюджет недоступен ({e}) — расшарь на {client_email} как Editor")

    sched = env("SCHEDULE_SPREADSHEET_ID")
    try:
        info = sheets.spreadsheets().get(spreadsheetId=sched,
                                         fields="properties(title)").execute()
        print(f"{OK} занятость доступна: «{info['properties']['title']}»")
        ensure_sheet(sheets, sched, "Занятость", AVAIL_HEADERS, dry)
    except HttpError as e:
        print(f"{FAIL} занятость недоступна ({e})")

    # 4. календарь
    cal_id = env("CALENDAR_ID")
    try:
        cal = calendar.calendars().get(calendarId=cal_id).execute()
        print(f"{OK} календарь доступен: «{cal.get('summary')}»")
        if not dry:
            ev = calendar.events().insert(
                calendarId=cal_id,
                body={"summary": "Проверка доступа бота — можно удалить",
                      "start": {"dateTime": "2030-01-01T10:00:00+04:00"},
                      "end": {"dateTime": "2030-01-01T10:15:00+04:00"}},
            ).execute()
            calendar.events().delete(calendarId=cal_id, eventId=ev["id"]).execute()
            print(f"{OK} запись в календарь проверена (тестовое событие создано и удалено)")
    except HttpError as e:
        print(f"{FAIL} календарь недоступен ({e}) — дай {client_email} право "
              f"«Make changes to events»")

    print("\nГотово. Дальше: запусти tool-api с этими переменными и `/start` в боте.")


if __name__ == "__main__":
    main()
