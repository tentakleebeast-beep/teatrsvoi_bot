#!/usr/bin/env python3
"""
Печатает календари, которые видит сервисная учётка, и их ID.

  python scripts/calendar_list.py

ID берётся отсюда же: колонка «ID». Для чтения в .env → CALENDAR_ID_READ,
для обкатки → CALENDAR_ID_DRAFT.
"""

from __future__ import annotations

import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")


def main() -> None:
    cred = service_account.Credentials.from_service_account_file(
        SA, scopes=["https://www.googleapis.com/auth/calendar.readonly"])
    svc = build("calendar", "v3", credentials=cred, cache_discovery=False)
    items = svc.calendarList().list(maxResults=50).execute().get("items", [])
    if not items:
        print("Сервисная учётка не видит ни одного календаря.")
        print("Расшарь календарь на", os.getenv("GOOGLE_SA_EMAIL", "<email сервисной учётки>"),
              "с правом «Внесение изменений» (или «Просмотр» для чтения).")
        return
    print(f"{'ID':<70} {'Название':<28} {'Роль':<12} Часовой пояс")
    print("-" * 130)
    for c in items:
        print(f"{c.get('id',''):<70} {c.get('summary',''):<28} "
              f"{c.get('accessRole',''):<12} {c.get('timeZone','')}")


if __name__ == "__main__":
    main()
