#!/usr/bin/env python3
"""Показывает, что уже стоит на текущую неделю в основном и черновом календарях."""
import os
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
READ = os.getenv("CALENDAR_ID_READ", "")
DRAFT = os.getenv("CALENDAR_ID_DRAFT", "")
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

cred = service_account.Credentials.from_service_account_file(
    SA, scopes=["https://www.googleapis.com/auth/calendar"])
svc = build("calendar", "v3", credentials=cred, cache_discovery=False)

today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
monday = today - timedelta(days=today.weekday())
end = monday + timedelta(days=7)

print(f"неделя: {monday:%d.%m} — {(end - timedelta(days=1)):%d.%m.%Y}\n")

for name, cid in (("ОСНОВНОЙ", READ), ("ЧЕРНОВОЙ", DRAFT)):
    items = svc.events().list(calendarId=cid, timeMin=monday.isoformat() + "Z",
                              timeMax=end.isoformat() + "Z", singleEvents=True,
                              orderBy="startTime", maxResults=100).execute().get("items", [])
    print(f"=== {name}: {len(items)} событий ===")
    for e in items:
        s = e["start"].get("dateTime") or e["start"].get("date")
        en = e["end"].get("dateTime") or e["end"].get("date")
        try:
            d = datetime.fromisoformat(s)
            label = f"{WD[d.weekday()]} {d:%d.%m} {d:%H:%M}"
        except ValueError:
            label = s
        print(f"  {label:<18} — {en[11:16]:<6} {e.get('summary','')[:52]}")
    print()
