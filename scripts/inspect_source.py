#!/usr/bin/env python3
"""Все таблицы/документы на Drive + содержимое «Занятости»."""
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
cred = service_account.Credentials.from_service_account_file(
    SA, scopes=["https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/spreadsheets.readonly"])
drive = build("drive", "v3", credentials=cred, cache_discovery=False)
sheets = build("sheets", "v4", credentials=cred, cache_discovery=False)

print("=== таблицы и документы ===")
q = ("(mimeType = 'application/vnd.google-apps.spreadsheet' "
     "or mimeType = 'application/vnd.google-apps.document' "
     "or mimeType contains 'spreadsheetml' or mimeType contains 'wordprocessingml') "
     "and trashed = false")
files = drive.files().list(q=q, fields="files(id,name,mimeType,modifiedTime)",
                           pageSize=100, supportsAllDrives=True,
                           includeItemsFromAllDrives=True).execute().get("files", [])
for f in sorted(files, key=lambda x: x["name"]):
    print(f"  {f['name']!r:<42} {f['mimeType'].split('.')[-1]:<14} {f.get('modifiedTime','')[:10]}  {f['id']}")

SID = os.getenv("SCHEDULE_SPREADSHEET_ID", "")
print(f"\n=== «Занятость» ({SID}) ===")
meta = sheets.spreadsheets().get(spreadsheetId=SID).execute()
for s in meta["sheets"]:
    title = s["properties"]["title"]
    vals = sheets.spreadsheets().values().get(
        spreadsheetId=SID, range=f"'{title}'!A1:H15").execute().get("values", [])
    print(f"--- {title}: непустых строк в первых 15: {len(vals)}")
    for row in vals[:5]:
        print("   ", " | ".join(str(x)[:24] for x in row))
