#!/usr/bin/env python3
"""Снимок расписания: лист «Расписание студийцы» и календари, только чтение.

  env -u PYTHONPATH .venv/bin/python scripts/probes/schedule_snapshot.py

Печатает числа, на которые опирается `docs/development/178-google-schedule-research.md`:
структуру листа занятости, варианты текстов (уникальные и одиночные), заполнение по месяцам,
сверку колонки «Решение» с событиями календаря, разбивку событий по типам (по тексту заголовка)
и полный срез боевого календаря за 2026 год (страница `docs/development/160` §3.4).

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

PROJECT = Path(__file__).resolve().parents[2]
TZ = ZoneInfo("Europe/Samara")
DATE_RE = re.compile(r"(\d{1,2})[.,](\d{1,2})[.,](\d{2})")
NAME = "Расписание студийцы"
PERIOD_MIN = "2026-09-01T00:00:00+04:00"
YEAR_MIN = "2026-01-01T00:00:00+04:00"
YEAR_MAX = "2027-01-01T00:00:00+04:00"


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


def calendar_events(cal, calendar_id: str, tmin: str, tmax: str) -> list[dict]:
    items, page = [], None
    while True:
        resp = cal.events().list(calendarId=calendar_id, timeMin=tmin, timeMax=tmax,
                                 singleEvents=True, maxResults=2500, pageToken=page).execute()
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


def event_day(event: dict) -> date:
    start = event["start"].get("dateTime") or event["start"].get("date")
    return (datetime.fromisoformat(start).astimezone(TZ).date()
            if "T" in start else date.fromisoformat(start))


def report_sheet(vals: list[list[str]]) -> dict[date, str]:
    """Печатает структуру листа, возвращает «день → текст решения»."""
    header = vals[0]
    names = [c for c in header[3:] if c.strip()]
    nonempty = [c for c in header if c.strip()]
    print("=== Лист занятости ===")
    print("колонок в шапке (непустых ячеек):", len(nonempty),
          "| из них служебных (дата, день, «Решение»):", len(nonempty) - len(names),
          "| людей:", len(names), "| ширина сетки листа (A..AI):", 35)
    print("шапка целиком:", [c for c in header if c.strip()])
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

    counts = Counter(texts)
    singles = [t for t, n in counts.items() if n == 1]
    singles_ci = Counter(t.casefold() for t in texts)
    print("дней в листе:", days, "| первый/последний:",
          min(decisions) if decisions else "—", "/", max(decisions) if decisions else "—")
    print("людей с отметками:", len(per_person), "| пустых колонок:",
          len([c for c in names if c not in per_person]))
    print("непустых ячеек:", len(texts), "| уникальных текстов:", len(counts),
          "| из них одиночных:", len(singles),
          "| одиночных без учёта регистра:", sum(1 for n in singles_ci.values() if n == 1))
    print("частые тексты:", counts.most_common(12))
    print("дней с решением:", len(decisions), "| из них со временем:",
          sum(1 for t in decisions.values() if re.search(r"\d{1,2}[:.]\d{2}|\bс \d{1,2}\b", t)))
    print("по месяцам (дней / ячеек / решений):",
          {k: (per_month_total[k], per_month_filled[k], per_month_decision[k])
           for k in sorted(per_month_total)})
    print("кто сколько отметил:", per_person.most_common())
    return decisions


def report_period(cal, name: str, env_key: str) -> dict[date, list[str]]:
    """Срез «период решений» 01.09–31.12: сверка с листом."""
    calendar_id = os.environ.get(env_key, "")
    if not calendar_id:
        print(f"\n=== {name} ===\n{env_key} не задан")
        return {}
    items = calendar_events(cal, calendar_id, PERIOD_MIN, YEAR_MAX)
    by_day: dict[date, list[str]] = defaultdict(list)
    for event in items:
        by_day[event_day(event)].append(event.get("summary", ""))
    kinds = Counter(event_kind(e.get("summary") or "") for e in items)
    print(f"\n=== {name}, период {PERIOD_MIN[:10]}—31.12 ({len(items)} событий) ===")
    print("с цветом:", sum(1 for e in items if e.get("colorId")),
          "| с меткой [X]:", sum(1 for e in items if (e.get("summary") or "").startswith("[")),
          "| с участниками:", sum(1 for e in items if e.get("attendees")))
    print("типы по тексту:", kinds.most_common())
    print("дней:", len(by_day), "| событий на день:",
          sorted(Counter(len(v) for v in by_day.values()).items()))
    return by_day


def report_year(cal, name: str, env_key: str) -> list[dict]:
    """Полный 2026 год: числа для `docs/development/160` §3.4."""
    calendar_id = os.environ.get(env_key, "")
    if not calendar_id:
        print(f"\n=== {name}, 2026 ===\n{env_key} не задан")
        return []
    items = calendar_events(cal, calendar_id, YEAR_MIN, YEAR_MAX)
    months = Counter(event_day(e).strftime("%Y-%m") for e in items)
    durations = Counter()
    for event in items:
        start, end = event["start"], event["end"]
        if "dateTime" in start and "dateTime" in end:
            hours = (datetime.fromisoformat(end["dateTime"])
                     - datetime.fromisoformat(start["dateTime"])).total_seconds() / 3600
            durations[round(hours, 1)] += 1
    words = Counter()
    for event in items:
        for word in re.findall(r"[А-Яа-яЁёA-Za-z]{3,}", event.get("summary") or ""):
            words[word] += 1
    print(f"\n=== {name}, 2026 год ({len(items)} событий) ===")
    print("по месяцам:", {k: months[k] for k in sorted(months)})
    print("пусто у всех:", {field: sum(1 for e in items if not e.get(field))
                            for field in ("colorId", "attendees", "location",
                                          "description", "recurrence")})
    print("частые слова:", words.most_common(10))
    print("длительность (ч):", durations.most_common(6))
    return items


def main() -> None:
    drive, sheets, cal = services()
    vals = sheet_rows(drive, sheets)
    decisions = report_sheet(vals)
    live_days = report_period(cal, "Боевой календарь", "CALENDAR_ID_READ")
    report_year(cal, "Боевой календарь", "CALENDAR_ID_READ")
    report_period(cal, "Черновой календарь", "CALENDAR_ID_DRAFT")
    report_year(cal, "Черновой календарь", "CALENDAR_ID_DRAFT")

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
