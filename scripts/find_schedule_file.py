#!/usr/bin/env python3
"""Ищем на Drive файл «расписание студийцы» и смотрим, что внутри."""
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
cred = service_account.Credentials.from_service_account_file(
    SA, scopes=["https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/spreadsheets.readonly"])
drive = build("drive", "v3", credentials=cred, cache_discovery=False)

print("=== поиск по имени ===")
res = drive.files().list(
    q="name contains 'асписание' and trashed = false",
    fields="files(id,name,mimeType,modifiedTime,parents,size)",
    pageSize=50, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
files = res.get("files", [])
for f in files:
    print(f"- {f['name']!r}\n   id={f['id']} type={f['mimeType']} изменён={f.get('modifiedTime')}")

if not files:
    print("ничего не найдено; смотрим всё, что видит учётка")
    all_files = drive.files().list(fields="files(id,name,mimeType)", pageSize=50,
                                   supportsAllDrives=True,
                                   includeItemsFromAllDrives=True).execute().get("files", [])
    for f in all_files[:30]:
        print(f"  {f['name']!r} — {f['mimeType']} — {f['id']}")
