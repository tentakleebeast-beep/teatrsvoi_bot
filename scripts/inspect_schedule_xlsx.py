#!/usr/bin/env python3
"""Скачивает «Расписание СВОИ.xlsx» с Drive и показывает, насколько он заполнен."""
import io
import os

import openpyxl
from google.oauth2 import service_account
from googleapiclient.discovery import build

SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
FILE_ID = "1kKH2Vdc5_phHVBQ-sd5ANP4U7aBfUz1J"      # «Расписание СВОИ.xlsx»

cred = service_account.Credentials.from_service_account_file(
    SA, scopes=["https://www.googleapis.com/auth/drive.readonly"])
drive = build("drive", "v3", credentials=cred, cache_discovery=False)
data = drive.files().get_media(fileId=FILE_ID).execute()
print("скачано байт:", len(data))

wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
print("листов:", len(wb.sheetnames))
for name in wb.sheetnames[:20]:
    ws = wb[name]
    filled = 0
    ones = 0
    zeros = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        for v in row[1:]:
            if v is None or v == "":
                continue
            filled += 1
            if isinstance(v, (int, float)):
                if v == 1:
                    ones += 1
                elif v == 0:
                    zeros += 1
    print(f"  {name!r:<26} строк={ws.max_row:<4} колонок={ws.max_column:<3} "
          f"заполнено={filled:<5} единиц={ones:<5} нулей={zeros}")

ws = wb[wb.sheetnames[1]] if len(wb.sheetnames) > 1 else wb[wb.sheetnames[0]]
print(f"\n=== шапка листа {ws.title!r} ===")
for row in ws.iter_rows(min_row=1, max_row=4, max_col=10, values_only=True):
    print("  ", [str(x)[:14] if x is not None else "" for x in row])

print("\n=== даты, по которым есть данные ===")
dates = []
for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
    if row[0]:
        dates.append(row[0])
print(f"  всего дат: {len(dates)}, первая {dates[0] if dates else '—'}, "
      f"последняя {dates[-1] if dates else '—'}")
