#!/usr/bin/env python3
"""
Заполняет ЧЕРНОВОЙ календарь неделей из основного — по правилам проекта.

Правила:
  config/calendar-rules.yaml    как оформляются события (маска «Тип - Название», 23:55 и т.д.)
  config/calendar-palette.yaml  цвет и «доходность» по типу события

Что делает:
  1) читает события недели из основного календаря (инстансы регулярных серий тоже попадают);
  2) приводит названия к маске «Тип - Название»;
  3) ставит цвет по палитре и помечает доходные события (аренда, показ арендатора);
  4) удаляет прошлые копии (метка source=main_copy) и создаёт заново — идемпотентно.

  python scripts/calendar_fill_draft.py [--dry-run] [--week-offset 0]
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import yaml
from _cli import apply_env, parser
from google.oauth2 import service_account
from googleapiclient.discovery import build

apply_env()

SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
READ = os.getenv("CALENDAR_ID_READ", "")
DRAFT = os.getenv("CALENDAR_ID_DRAFT", "")
TZ = "Europe/Samara"
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

PALETTE = yaml.safe_load(open("config/calendar-palette.yaml", encoding="utf-8"))
TYPES = PALETTE["types"]

# проекты и их человеческие названия
PROJECTS = {
    "жн": "Жизнь насекомых",
    "жизнь насекомых": "Жизнь насекомых",
    "чп": "Чёрная пурга",
    "пурга": "Чёрная пурга",
    "бунин": "И всё же...",
    "и всё же": "И всё же...",
    "литрейв": "ЛитРейв",
}

# служебные названия, которые не репетиция, а обслуживание сцены
SERVICE_WORDS = ("ремонт", "музыка", "подготовка", "свет", "звук", "уборка")


def project_of(text: str, fallback: str | None = None) -> str:
    """Приводит «ЖН», «ЛитРейву», «бунин» к человеческому названию проекта."""
    low = (text or "").lower().replace("ё", "е")
    for key, val in PROJECTS.items():
        if low.startswith(key) or key in low:
            return val
    return fallback or text


def classify(title: str) -> tuple[str, str]:
    """Возвращает (тип, название) по текущему названию события."""
    t = (title or "").strip()
    low = t.lower().replace("ё", "е")

    # арендатор: «Валерий Аренда», «Аренда Валерий», «Показ аренда Добряки»
    if "аренда" in low:
        is_show = "показ" in low
        tenant = low
        for word in ("показ", "аренда", "?"):
            tenant = tenant.replace(word, "")
        tenant = " ".join(tenant.split())
        name = tenant[:1].upper() + tenant[1:] if tenant else "Арендатор"
        return ("Показ" if is_show else "Аренда"), name

    if "показ" in low or "вечер" in low or "спектакл" in low:
        name = t
        for word in ("Показ аренда", "Показ", "Спектакль"):
            name = name.replace(word, "").strip()
        return "Показ", name or t

    if "курс" in low:
        return "Тренинг", "Курс"

    if any(w in low for w in SERVICE_WORDS):
        name = t
        for word in ("КОСМИЧЕСКИЙ", "Подготовка к"):
            name = name.replace(word, "").strip()
        if low.startswith("подготовка"):
            return "Репетиция", project_of(name)
        return "Тренинг" if "музык" in low else "Служебное", name

    if low.startswith("прогон"):
        name = t.split(" ", 1)[1] if " " in t else t
        return "Показ", project_of(name)

    if "репа" in low:
        rest = low.split("репа", 1)[1].strip()
        tokens = [x for x in rest.split() if x]
        if tokens:
            head = tokens[0]
            if project_of(head) != head:
                return "Репетиция", project_of(head)
            return "Репетиция", head.capitalize()
        return "Репетиция", t

    if low in PROJECTS:
        return "Репетиция", PROJECTS[low]
    for key, val in PROJECTS.items():
        if key in low:
            return "Репетиция", val

    if low == "свои":
        return "Репетиция", "СВОИ"

    return "Служебное", t


def main() -> None:
    ap = parser(__doc__ or "Заполнение чернового календаря")
    ap.add_argument("--dry-run", action="store_true", help="показать, ничего не создавать")
    ap.add_argument("--week-offset", type=int, default=0,
                    help="сдвиг недели от текущей: 0 — эта, 1 — следующая")
    args = ap.parse_args()
    dry = args.dry_run
    offset = args.week_offset

    cred = service_account.Credentials.from_service_account_file(
        SA, scopes=["https://www.googleapis.com/auth/calendar"])
    svc = build("calendar", "v3", credentials=cred, cache_discovery=False)

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    week_end = monday + timedelta(days=7)

    src = svc.events().list(calendarId=READ, timeMin=monday.isoformat() + "Z",
                            timeMax=week_end.isoformat() + "Z", singleEvents=True,
                            orderBy="startTime", maxResults=150).execute().get("items", [])

    # старые копии в черновике убираем, чтобы не плодить дубли при смене названий
    old = svc.events().list(calendarId=DRAFT, timeMin=monday.isoformat() + "Z",
                            timeMax=week_end.isoformat() + "Z", singleEvents=True,
                            maxResults=150).execute().get("items", [])
    removed = 0
    for e in old:
        if (e.get("extendedProperties", {}).get("private", {}).get("source") == "main_copy"):
            print(f"  − удаляю старую копию: {WD[datetime.fromisoformat(e['start']['dateTime']).weekday()]} "
                  f"{e['start']['dateTime'][11:16]} {e.get('summary')}")
            if not dry:
                svc.events().delete(calendarId=DRAFT, eventId=e["id"]).execute()
            removed += 1

    created = 0
    for e in src:
        s = e["start"].get("dateTime")
        if not s:
            continue
        start = datetime.fromisoformat(s)
        end = datetime.fromisoformat(e["end"]["dateTime"])
        # «до конца дня»: не уходим в следующие сутки
        if (end.date() > start.date()) or (end.hour == 0 and end.minute == 0 and end.date() > start.date()):
            end = start.replace(hour=23, minute=55, second=0)
        if end.hour == 0 and end.minute == 0:
            end = end.replace(hour=23, minute=55)

        kind, name = classify(e.get("summary", ""))
        title = f"{kind} - {name}"
        meta = TYPES.get(kind, {})
        body = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": TZ},
            "end": {"dateTime": end.isoformat(), "timeZone": TZ},
            "colorId": str(meta.get("color_id", 8)),
            "description": (f"{kind} · {'доход студии' if meta.get('revenue') else 'своё событие'} · "
                            f"черновик недели, снят с боевого календаря {datetime.now():%d.%m.%Y}"),
            "extendedProperties": {"private": {
                "source": "main_copy",
                "kind": kind,
                "revenue": "1" if meta.get("revenue") else "0",
            }},
        }
        recurring = e.get("recurringEventId")
        print(f"  + {WD[start.weekday()]} {start:%d.%m %H:%M}–{end:%H:%M}  {title:<38} "
              f"[{kind}, цвет {body['colorId']}, {'доход' if meta.get('revenue') else '—'}]"
              f"{' (из серии)' if recurring else ''}")
        if dry:
            continue
        svc.events().insert(calendarId=DRAFT, body=body).execute()
        created += 1

    print(f"\n{'dry-run: ' if dry else ''}удалено старых {removed}, создано {created}")


if __name__ == "__main__":
    main()
