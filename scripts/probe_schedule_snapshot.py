#!/usr/bin/env python3
"""Снимок расписания: лист «Расписание студийцы» и календари, только чтение.

  python scripts/probe_schedule_snapshot.py

Печатает числа, на которые опирается `docs/development/178-google-schedule-research.md`:
структуру листа занятости, варианты текстов, заполнение по месяцам, сверку колонки
«Решение» с событиями календаря и разбивку событий по типам (по тексту заголовка).

Ничего не пишет ни в Google, ни в базу: только `values.get` и `events.list`.
Файлы берутся по имени через Drive (ID в коде и в документах не держим).
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

PROJECT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Samara")
DATE_RE = re.compile(r"(\d{1,2})[.,](\d{1,2})[.,](\d{2})")
NAME = "Расписание студийцы"


def project_root() -> Path:
    """Корень с .env: в worktree его нет, там берём корень общего checkout."""
    if (PROJECT / ".env").exists():
        return PROJECT
    import subprocess

    common = subprocess.run(["git", "-C", str(PROJECT), "rev-parse", "--path-format=absolute",
                             "--git-common-dir"], capture_output=True, text=True, check=False)
    if common.returncode == 0 and common.stdout.strip():
        return Path(common.stdout.strip()).parent
    return PROJECT


def services() -> tuple:
    load_dotenv(project_root() / ".env")
    sa = os.environ.get("GOOGLE_SA_JSON", str(project_root() / "secrets/google-sa.json"))
    if not Path(sa).is_absolute():
        sa = str(project_root() / sa)

    def build_one(api: str, ver: str, scope: str):
        cred = service_account.Credentials.from_service_account_file(sa, scopes=[scope])
        return build(api, ver, credentials=cred, cache_discovery=False)

    return (
        build_one("drive", "v3", "https://www.googleapis.com/auth/drive.readonly"),
        build_one("sheets", "v4", "https://www.googleapis.com/auth/spreadsheets.readonly"),
        build_one("calendar", "v3", "https://www.googleapis.com/auth/calendar.readonly"),
    )


def sheet_rows(drive, sheets) -> list[list[str]]:
    files = drive.files().list(q=f"name = '{NAME}' and trashed = false",
                               fields="files(id,name)").execute().get("files", [])
    if not files:
        sys.exit(f"Файл «{NAME}» не найден: сервисная учётка его не видит")
    return sheets.spreadsheets().values().get(
        spreadsheetId=files[0]["id"], range="A1:AI200").execute().get("values", [])


def calendar_events(cal, calendar_id: str, tmin: str) -> list[dict]:
    items, page = [], None
    while True:
        resp = cal.events().list(calendarId=calendar_id, timeMin=tmin,
                                 timeMax="2027-01-01T00:00:00+04:00", singleEvents=True,
                                 maxResults=2500, pageToken=page).execute()
        items.extend(resp.get("items", []))
        page = resp.get("nextPageToken")
        if not page:
            return items


def event_kind(summary: str) -> str:
    text = summary.lower()
    if "аренда" in text or "валерий" in text:
        return "аренда"
    if "реп" in text or "прогон" in text or "читк" in text:
        return "репетиция"
    if "тренинг" in text or "актёрск" in text or "актерск" in text or "музык" in text:
        return "тренинг"
    if "показ" in text or "спектакл" in text or "вечер" in text:
        return "спектакль"
    return "не классифицируется"


def report_sheet(vals: list[list[str]]) -> dict[date, str]:
    """Печатает структуру листа, возвращает «день → текст решения»."""
    header = vals[0]
    print("=== Лист занятости ===")
    print("колонок в шапке:", len(header), "| людей:", len([c for c in header[3:] if c.strip()]))
    print("колонка «Решение»:", header[2])

    per_month_total: dict[str, int] = defaultdict(int)
    per_month_filled: dict[str, int] = defaultdict(int)
    per_month_decision: dict[str, int] = defaultdict(int)
    per_person: Counter = Counter()
    texts: list[str] = []
    decisions: dict[date, str] = {}
    days = 0
    for row in vals[1:]:
        label = " ".join((c or "").strip() for c in row[:2])
        m = DATE_RE.search(label)
        if not m:
            continue
        try:
            day = date(int(m.group(3)) + 2000, int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        days += 1
        month = day.strftime("%Y-%m")
        per_month_total[month] += 1
        decision = (row[2] if len(row) > 2 else "").strip()
        if decision:
            decisions[day] = decision
            per_month_decision[month] += 1
        for idx, name in enumerate(header[3:], start=3):
            value = (row[idx] if len(row) > idx else "").strip()
            if value:
                per_person[name] += 1
                texts.append(value)
                per_month_filled[month] += 1

    print("дней в листе:", days, "| первый/последний:",
          min(decisions) if decisions else "—", "/", max(decisions) if decisions else "—")
    print("людей с отметками:", len(per_person), "| пустых колонок:",
          len([c for c in header[3:] if c.strip() and c not in per_person]))
    print("непустых ячеек:", len(texts), "| уникальных текстов:", len(set(texts)))
    print("частые тексты:", Counter(texts).most_common(12))
    print("дней с решением:", len(decisions), "| из них со временем:",
          sum(1 for t in decisions.values() if re.search(r"\d{1,2}[:.]\d{2}|\bс \d{1,2}\b", t)))
    print("по месяцам (дней / ячеек / решений):",
          {k: (per_month_total[k], per_month_filled[k], per_month_decision[k])
           for k in sorted(per_month_total)})
    print("кто сколько отметил:", per_person.most_common())
    return decisions


def report_calendar(cal, name: str, env_key: str, tmin: str) -> dict[date, list[str]]:
    calendar_id = os.environ.get(env_key, "")
    if not calendar_id:
        print(f"\n=== {name} ===\n{env_key} не задан")
        return {}
    items = calendar_events(cal, calendar_id, tmin)
    by_day: dict[date, list[str]] = defaultdict(list)
    for event in items:
        start = event["start"].get("dateTime") or event["start"].get("date")
        day = (datetime.fromisoformat(start).astimezone(TZ).date()
               if "T" in start else date.fromisoformat(start))
        by_day[day].append(event.get("summary", ""))
    kinds = Counter(event_kind(e.get("summary") or "") for e in items)
    print(f"\n=== {name} ({len(items)} событий) ===")
    print("с цветом:", sum(1 for e in items if e.get("colorId")),
          "| с меткой [X]:", sum(1 for e in items if (e.get("summary") or "").startswith("[")),
          "| с участниками:", sum(1 for e in items if e.get("attendees")))
    print("типы по тексту:", kinds.most_common())
    print("дней:", len(by_day), "| событий на день:",
          sorted(Counter(len(v) for v in by_day.values()).items()))
    return by_day


def main() -> None:
    drive, sheets, cal = services()
    vals = sheet_rows(drive, sheets)
    decisions = report_sheet(vals)
    live_days = report_calendar(cal, "Боевой календарь", "CALENDAR_ID_READ",
                                "2026-09-01T00:00:00+04:00")
    report_calendar(cal, "Черновой календарь", "CALENDAR_ID_DRAFT", "2026-01-01T00:00:00+04:00")

    if decisions and live_days:
        period = [d for d in live_days if min(decisions) <= d <= max(decisions)]
        both = [d for d in decisions if live_days.get(d)]
        only_sheet = [d for d in decisions if not live_days.get(d)]
        only_cal = [d for d in period if d not in decisions]
        multi = [d for d in both if len(live_days[d]) > 1]
        print("\n=== Лист против календаря (внутри периода решений) ===")
        print("дней с решением и событиями:", len(both),
              "| только решение:", len(only_sheet),
              "| только события:", len(only_cal),
              "| в совпавших днях событий больше одного:", len(multi))


if __name__ == "__main__":
    main()
