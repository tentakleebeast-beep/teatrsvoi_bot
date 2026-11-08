#!/usr/bin/env python3
"""
Отчёты в Google-таблице «Бюджет» в том виде, к которому привыкли в студии.

  «07.2026», «08.2026» …   — бюджет месяца: блоки рядом, как в исходном файле
                              Взносы | Расходы студии | Показы (Доходы / Вложения) | Субаренда
  «Зан. 10.2026»           — матрица занятости: даты × часы, свободно/занято
  «Журнал бота»            — кто что сделал
  «Проверки»               — инварианты

  python scripts/build_reports.py
"""

from __future__ import annotations

import os
import re

import psycopg
from _cli import dsn
from google.oauth2 import service_account
from googleapiclient.discovery import build
from psycopg.rows import dict_row

DB = dsn()
SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
SID = os.getenv("BUDGET_SPREADSHEET_ID", "1RB-jaab8MJRveapBIOzepOZke0a7Wvhe8QqYemIvRgE")
SCHED_SID = os.getenv("SCHEDULE_SPREADSHEET_ID", "")

def clean(text: str, limit: int = 50) -> str:
    """Убирает служебные пометки импорта вида «[дат в периоде: 5]»."""
    if not text:
        return ""
    return re.sub(r"\s*\[.*?\]", "", str(text))[:limit]


MONTHS = {1: "ЯНВАРЬ", 2: "ФЕВРАЛЬ", 3: "МАРТ", 4: "АПРЕЛЬ", 5: "МАЙ", 6: "ИЮНЬ",
          7: "ИЮЛЬ", 8: "АВГУСТ", 9: "СЕНТЯБРЬ", 10: "ОКТЯБРЬ", 11: "НОЯБРЬ", 12: "ДЕКАБРЬ"}
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

C_DARK = {"red": 0.22, "green": 0.28, "blue": 0.31}
C_GROUP = {"red": 0.85, "green": 0.88, "blue": 0.91}
C_SUB = {"red": 0.94, "green": 0.95, "blue": 0.96}
C_INC = {"red": 0.90, "green": 0.95, "blue": 0.90}
C_EXP = {"red": 0.99, "green": 0.92, "blue": 0.91}
C_TOTAL = {"red": 1.0, "green": 0.97, "blue": 0.88}
WHITE = {"red": 1, "green": 1, "blue": 1}


def svc():
    cred = service_account.Credentials.from_service_account_file(
        SA, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=cred, cache_discovery=False)


def sheet_ids(sheets, sid) -> dict:
    meta = sheets.spreadsheets().get(spreadsheetId=sid,
                                     fields="sheets(properties(sheetId,title))").execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}


def ensure(sheets, sid, title: str) -> int:
    ids = sheet_ids(sheets, sid)
    if title in ids:
        return ids[title]
    new_id = max(ids.values(), default=0) + 1
    sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
        {"addSheet": {"properties": {"title": title, "sheetId": new_id}}}]}).execute()
    return new_id


def money_range(gid, r0, r1, col) -> dict:
    return {"repeatCell": {
        "range": {"sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
                  "startColumnIndex": col, "endColumnIndex": col + 1},
        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER",
                                                        "pattern": "# ##0 ₽"}}},
        "fields": "userEnteredFormat.numberFormat"}}


def fill(gid, r0, r1, c0, c1, color, bold=False, white_text=False) -> dict:
    fmt = {"backgroundColor": color}
    if bold:
        fmt["textFormat"] = {"bold": True}
        if white_text:
            fmt["textFormat"]["foregroundColor"] = WHITE
    fields = "userEnteredFormat.backgroundColor"
    if bold:
        fields += ",userEnteredFormat.textFormat"
    return {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
                                     "startColumnIndex": c0, "endColumnIndex": c1},
                           "cell": {"userEnteredFormat": fmt}, "fields": fields}}


def merge(gid, r0, r1, c0, c1) -> dict:
    return {"mergeCells": {"range": {"sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
                                     "startColumnIndex": c0, "endColumnIndex": c1},
                           "mergeType": "MERGE_ALL"}}


def borders(gid, r0, r1, c0, c1) -> dict:
    line = {"style": "SOLID", "width": 1, "color": {"red": 0.65, "green": 0.68, "blue": 0.72}}
    return {"updateBorders": {"range": {"sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
                                        "startColumnIndex": c0, "endColumnIndex": c1},
                              "top": line, "bottom": line, "left": line, "right": line,
                              "innerHorizontal": line, "innerVertical": line}}


# ---------------------------------------------------------------- бюджет месяца
def build_month(sheets, sid, period: str) -> None:
    y, m = int(period[:4]), int(period[5:7])
    title = f"{m:02d}.{y}"
    gid = ensure(sheets, sid, title)

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        dues = conn.execute("""select pr.display_name, d.amount_due, d.amount_paid
                               from dues_charge d join participant pr on pr.id = d.participant_id
                               where d.period = %s order by pr.display_name""", (period,)).fetchall()
        tx = conn.execute("""select t.block, t.tx_date, t.amount, t.article_raw, t.purpose,
                                    coalesce(c.name,'') category
                             from budget_tx t left join category c on c.id = t.category_id
                             where t.period = %s and t.status = 'confirmed'
                             order by t.tx_date, t.id""", (period,)).fetchall()

    exp = [t for t in tx if t["block"] == "expense"]
    inc = [t for t in tx if t["block"] == "show_income"]
    cost = [t for t in tx if t["block"] == "show_cost"]
    sub = [t for t in tx if t["block"] == "sublease"]

    rows: list[list] = [[] for _ in range(4)]        # 4 строки шапки
    rows[0] = [f"{MONTHS[m]} {y}"] + [""] * 13
    rows[1] = ["Ежемесячная жизнь студии"] + [""] * 6 + ["Показы"] + [""] * 3 + ["Субаренда"] + [""] * 2
    rows[2] = ["Взносы студийцев", "", "", "Расходы студии", "", "", "Доходы", "", "Вложения", "", "",
               "Прибыль", "", ""]
    rows[3] = ["Фамилия Имя", "Дата", "Сумма", "Статья", "Дата", "Факт",
               "Источник", "Сумма", "Статья (студиец)", "Сумма", "Возврат",
               "Арендатель", "Дата", "Сумма"]

    n = max(len(dues), len(exp), len(inc), len(cost), len(sub))
    for i in range(n):
        r = [""] * 14
        if i < len(dues):
            d = dues[i]
            r[0] = d["display_name"]
            r[1] = ""
            r[2] = float(d["amount_due"])
        if i < len(exp):
            e = exp[i]
            r[3] = clean(e["article_raw"] or e["purpose"] or e["category"], 50)
            r[4] = e["tx_date"].strftime("%d.%m")
            r[5] = float(e["amount"])
        if i < len(inc):
            s = inc[i]
            r[6] = clean(s["purpose"] or s["article_raw"], 50)
            r[7] = float(s["amount"])
        if i < len(cost):
            s = cost[i]
            r[8] = clean(s["article_raw"] or s["purpose"] or s["category"], 50)
            r[9] = float(s["amount"])
            r[10] = ""
        if i < len(sub):
            s = sub[i]
            r[11] = clean(s["purpose"] or s["article_raw"], 40)
            r[12] = s["tx_date"].strftime("%d.%m")
            r[13] = float(s["amount"])
        rows.append(r)

    data_end = len(rows)
    sum_dues = sum(float(d["amount_due"]) for d in dues)
    sum_exp = sum(float(t["amount"]) for t in exp)
    sum_inc = sum(float(t["amount"]) for t in inc)
    sum_cost = sum(float(t["amount"]) for t in cost)
    sum_sub = sum(float(t["amount"]) for t in sub)

    rows.append(["Итого", "", sum_dues, "", "", sum_exp, "", sum_inc, "", sum_cost, "", "", "", sum_sub])
    rows.append(["Итог месяца", "", "", "", "",
                 sum_dues + sum_inc + sum_sub - sum_exp - sum_cost, "", "", "", "", "", "", "", ""])

    sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{title}'!A1:Z2000").execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=sid, range=f"'{title}'!A1", valueInputOption="RAW",
        body={"values": rows}).execute()

    reqs = [
        merge(gid, 0, 1, 0, 14), fill(gid, 0, 1, 0, 14, C_DARK, bold=True, white_text=True),
        merge(gid, 1, 2, 0, 6), merge(gid, 1, 2, 6, 11), merge(gid, 1, 2, 11, 14),
        fill(gid, 1, 2, 0, 14, C_GROUP, bold=True),
        merge(gid, 2, 3, 0, 3), merge(gid, 2, 3, 3, 6), merge(gid, 2, 3, 6, 8),
        merge(gid, 2, 3, 8, 11), merge(gid, 2, 3, 11, 14),
        fill(gid, 2, 3, 0, 14, C_SUB, bold=True),
        fill(gid, 3, 4, 0, 14, C_GROUP, bold=True),
        fill(gid, 3, data_end, 0, 3, C_INC),
        fill(gid, 3, data_end, 3, 6, C_EXP),
        fill(gid, 3, data_end, 6, 11, C_INC),
        fill(gid, 4, data_end, 9, 11, C_EXP),
        fill(gid, data_end, data_end + 1, 0, 14, C_TOTAL, bold=True),
        fill(gid, data_end + 1, data_end + 2, 0, 14, C_TOTAL, bold=True),
        money_range(gid, 3, data_end + 2, 2), money_range(gid, 3, data_end + 2, 5),
        money_range(gid, 3, data_end + 2, 7), money_range(gid, 3, data_end + 2, 9),
        money_range(gid, 3, data_end + 2, 13),
        borders(gid, 0, data_end + 2, 0, 14),
        {"updateSheetProperties": {"properties": {"sheetId": gid, "gridProperties":
                                                  {"frozenRowCount": 4}},
                                   "fields": "gridProperties.frozenRowCount"}},
    ]
    widths = [150, 70, 100, 190, 70, 100, 170, 100, 170, 100, 80, 150, 70, 100]
    for i, w in enumerate(widths):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": reqs[:200]}).execute()


# ---------------------------------------------------------------- занятость месяца
def build_sched_month(sheets, sid, period: str) -> None:
    y, m = int(period[:4]), int(period[5:7])
    title = f"Зан. {m:02d}.{y}"
    gid = ensure(sheets, sid, title)

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        rows_db = conn.execute("""
            select a.day, a.hour, sum(case when a.is_free then 1 else 0 end) free_cnt,
                   count(*) known_cnt
            from availability_hour a
            where date_trunc('month', a.day) = date_trunc('month', %s::date)
            group by a.day, a.hour order by a.day, a.hour""", (f"{period}-01",)).fetchall()

    days = sorted({r["day"] for r in rows_db})
    hours = list(range(8, 24))
    table = [["Дата", "День"] + [f"{h:02d}" for h in hours] + ["Свободно", "Занятo"]]
    for d in days:
        cells = [d.strftime("%d.%m.%Y"), WD[d.weekday()]]
        free = busy = 0
        for h in hours:
            rec = next((r for r in rows_db if r["day"] == d and r["hour"] == h), None)
            if rec is None:
                cells.append("")
            elif rec["free_cnt"] and rec["free_cnt"] > 0:
                cells.append(1)
                free += 1
            else:
                cells.append(0)
                busy += 1
        cells += [free, busy]
        table.append(cells)

    sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{title}'!A1:Z2000").execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=sid, range=f"'{title}'!A1", valueInputOption="RAW",
        body={"values": table}).execute()

    ncols = len(hours) + 4
    reqs = [
        fill(gid, 0, 1, 0, ncols, C_DARK, bold=True, white_text=True),
        {"updateSheetProperties": {"properties": {"sheetId": gid, "gridProperties":
                                                  {"frozenRowCount": 1, "frozenColumnCount": 2}},
                                   "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
        borders(gid, 0, len(table), 0, ncols),
        # 1 = свободно (зелёный), 0 = занято (красный)
        {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": gid, "startRowIndex": 1, "endRowIndex": len(table),
                        "startColumnIndex": 2, "endColumnIndex": 2 + len(hours)}],
            "booleanRule": {"condition": {"type": "NUMBER_EQ",
                                          "values": [{"userEnteredValue": "1"}]},
                            "format": {"backgroundColor": C_INC}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": gid, "startRowIndex": 1, "endRowIndex": len(table),
                        "startColumnIndex": 2, "endColumnIndex": 2 + len(hours)}],
            "booleanRule": {"condition": {"type": "NUMBER_EQ",
                                          "values": [{"userEnteredValue": "0"}]},
                            "format": {"backgroundColor": C_EXP}}}, "index": 1}},
    ]
    sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute()


def main() -> None:
    sheets = svc()

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        periods = [r["period"] for r in conn.execute(
            "select distinct period from budget_period order by period").fetchall()]
        log = conn.execute("""select ts, actor_type, action, entity_type, entity_id
                              from audit_event order by id desc limit 80""").fetchall()
        checks = conn.execute("select * from v_invariant_checks").fetchall()

    # старый невзрачный лист убираем
    ids = sheet_ids(sheets, SID)
    if "Бюджет (вид)" in ids:
        sheets.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": [
            {"deleteSheet": {"sheetId": ids["Бюджет (вид)"]}}]}).execute()
        print("лист «Бюджет (вид)» удалён")

    for p in periods:
        build_month(sheets, SID, p)
        print("бюджет", p, "готов")

    if SCHED_SID:
        for p in periods:
            build_sched_month(sheets, SCHED_SID, p)
            print("занятость", p, "готова")

    # журнал и проверки
    ensure(sheets, SID, "Журнал бота")
    ACTOR = {"human": "человек", "bot": "бот (AI)", "system": "система"}
    jrows = [["Время", "Автор", "Действие", "Объект", "ID"]]
    for line in log:
        jrows.append([line["ts"].strftime("%d.%m.%Y %H:%M"),
                      ACTOR.get(line["actor_type"], line["actor_type"]), line["action"],
                      line["entity_type"], str(line["entity_id"] or "")])
    sheets.spreadsheets().values().clear(spreadsheetId=SID, range="Журнал бота!A1:E2000").execute()
    sheets.spreadsheets().values().update(spreadsheetId=SID, range="Журнал бота!A1",
                                          valueInputOption="RAW", body={"values": jrows}).execute()

    ensure(sheets, SID, "Проверки")
    crows = [["Код", "Правило", "Статус", "Детали"]]
    for c in checks:
        crows.append([c["code"], c["name"], "ок" if c["ok"] else "НАРУШЕНО", c["detail"]])
    sheets.spreadsheets().values().clear(spreadsheetId=SID, range="Проверки!A1:D100").execute()
    sheets.spreadsheets().values().update(spreadsheetId=SID, range="Проверки!A1",
                                          valueInputOption="RAW", body={"values": crows}).execute()

    print(f"журнал {len(jrows) - 1}, проверок {len(crows) - 1}")
    print(f"https://docs.google.com/spreadsheets/d/{SID}/edit")


if __name__ == "__main__":
    main()
