#!/usr/bin/env python3
"""Анализ основного календаря: какие события, как называются, когда и как оформлены."""
import collections
import os
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
CID = os.getenv("CALENDAR_ID_READ", "")
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

cred = service_account.Credentials.from_service_account_file(
    SA, scopes=["https://www.googleapis.com/auth/calendar"])
svc = build("calendar", "v3", credentials=cred, cache_discovery=False)

now = datetime.now()
items = []
page = None
while True:
    resp = svc.events().list(
        calendarId=CID,
        timeMin=(now - timedelta(days=120)).isoformat() + "Z",
        timeMax=(now + timedelta(days=60)).isoformat() + "Z",
        singleEvents=True, orderBy="startTime", maxResults=250, pageToken=page,
    ).execute()
    items += resp.get("items", [])
    page = resp.get("nextPageToken")
    if not page:
        break

print(f"событий: {len(items)}\n")
print("=== последние 25 ===")
for e in items[-25:]:
    s = e["start"].get("dateTime") or e["start"].get("date")
    en = e["end"].get("dateTime") or e["end"].get("date")
    print(f"{s[:16]:<17} → {en[11:16]:<6} | {e.get('summary','')[:48]:<48} | "
          f"loc={str(e.get('location'))[:22]:<22} color={e.get('colorId')} "
          f"rec={'да' if e.get('recurrence') else 'нет'} "
          f"att={len(e.get('attendees', []))}")

print("\n=== шаблоны (title → count, длительности) ===")
titles = collections.Counter(e.get("summary", "") for e in items)
for t, c in titles.most_common(20):
    durs = []
    for e in items:
        if e.get("summary") != t:
            continue
        s, en = e["start"].get("dateTime"), e["end"].get("dateTime")
        if s and en:
            d = (datetime.fromisoformat(en) - datetime.fromisoformat(s)).total_seconds() / 3600
            durs.append(round(d, 1))
    print(f"{c:>3}× {t[:46]:<46} длительности: {sorted(set(durs))[:6]}")

print("\n=== по дням недели ===")
byday = collections.Counter()
for e in items:
    s = e["start"].get("dateTime") or e["start"].get("date")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        continue
    byday[WD[d.weekday()]] += 1
print(dict(byday))

print("\n=== время начала (час) ===")
hours = collections.Counter()
for e in items:
    s = e["start"].get("dateTime")
    if s:
        hours[s[11:16]] += 1
print(dict(sorted(hours.items())))

print("\n=== примеры описаний/локаций ===")
for e in items[-8:]:
    print(f"- {e.get('summary')}\n  desc: {str(e.get('description'))[:180]}\n  loc: {e.get('location')}")
