#!/usr/bin/env python3
"""Что теперь видит сервисная учётка: ищем расписание студийцев и записную книжку."""
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
cred = service_account.Credentials.from_service_account_file(
    SA, scopes=["https://www.googleapis.com/auth/drive.readonly"])
drive = build("drive", "v3", credentials=cred, cache_discovery=False)

for needle in ("асписан", "аписн"):
    print(f"=== поиск: {needle} ===")
    res = drive.files().list(
        q=f"name contains '{needle}' and trashed = false",
        fields="files(id,name,mimeType,modifiedTime,owners(displayName,emailAddress))",
        pageSize=50, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    for f in res.get("files", []):
        owner = (f.get("owners") or [{}])[0].get("emailAddress", "?")
        print(f"  {f['name']!r}")
        print(f"    id={f['id']}  type={f['mimeType']}  изменён={f.get('modifiedTime','')[:16]}")
        print(f"    владелец={owner}")
    if not res.get("files"):
        print("  ничего")
