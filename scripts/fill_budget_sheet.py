#!/usr/bin/env python3
"""
Выгрузка бюджета из Postgres в Google-таблицу «Бюджет» (модель v2).

Листы:
  Транзакции  — плоский журнал, с блоком (взносы/расходы/показы-доход/показы-затраты/субаренда)
  Взносы      — начисления по людям
  Свод        — ДВА раздела: итоги внутри месяца и накопительно между месяцами
  Справочники — категории с суммами

  python scripts/fill_budget_sheet.py --dry-run
  python scripts/fill_budget_sheet.py
"""

from __future__ import annotations

import argparse
import os

import psycopg
from _cli import dsn
from google.oauth2 import service_account
from googleapiclient.discovery import build
from psycopg.rows import dict_row

DB = dsn()
SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
SPREADSHEET = os.getenv("BUDGET_SPREADSHEET_ID") or "1RB-jaab8MJRveapBIOzepOZke0a7Wvhe8QqYemIvRgE"
PERIODS = ("2026-07", "2026-08", "2026-09", "2026-10")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

TX_HEADERS = ["tx_id", "Дата", "Тип", "Блок", "Сумма", "Категория", "Проект", "Контрагент",
              "Назначение", "Способ", "Кто внёс", "Основание", "Ревизия", "Статус"]
DUES_HEADERS = ["Участник", "Период", "Начислено", "Оплачено", "Долг", "Дата", "Статус"]
MONTH_HEADERS = ["Период", "Качество", "Взносы", "Билеты (показы)", "Курсы и возвраты",
                 "Субаренда", "Доходы, итого", "Расходы студии", "Затраты показов",
                 "Расходы, итого", "Результат месяца"]
CUM_HEADERS = ["Период", "Накопленный доход", "Накопленный расход",
               "Накопленный результат", "Покрытие расходов, %"]
REF_HEADERS = ["Категория", "Направление", "Операций", "Сумма"]

BLOCK_TITLE = {"dues": "Взносы студийцев", "expense": "Расходы студии",
               "show_income": "Показы · доход", "show_cost": "Показы · затраты",
               "sublease": "Субаренда"}
BLOCK_TYPE = {"dues": "доход", "expense": "расход", "show_income": "доход",
              "show_cost": "расход", "sublease": "доход"}


def ensure_sheet(sheets, sid: str, title: str, headers: list[str]) -> None:
    meta = sheets.spreadsheets().get(spreadsheetId=sid, fields="sheets(properties(title))").execute()
    if title not in [s["properties"]["title"] for s in meta.get("sheets", [])]:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sid, body={"requests": [{"addSheet": {"properties": {"title": title}}}]}).execute()
        print(f"  + лист «{title}»")
    sheets.spreadsheets().values().update(
        spreadsheetId=sid, range=f"{title}!A1", valueInputOption="RAW",
        body={"values": [headers]}).execute()


def clear_below(sheets, sid: str, title: str, last_col: str) -> None:
    sheets.spreadsheets().values().clear(
        spreadsheetId=sid, range=f"{title}!A2:{last_col}20000").execute()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        tx = conn.execute("""
            select t.id, t.period, t.tx_date, t.block, t.tx_type, t.amount,
                   coalesce(c.name,'Прочее') category, p.name project, t.counterparty,
                   t.purpose, t.method, t.article_raw, pr.display_name participant
            from budget_tx t
            left join category c on c.id = t.category_id
            left join project p on p.id = t.project_id
            left join participant pr on pr.id = t.participant_id
            where t.period = any(%s)
            order by t.period, t.tx_date, t.block, t.id""", (list(PERIODS),)).fetchall()

        dues = conn.execute("""
            select d.period, pr.display_name, d.amount_due, d.amount_paid, d.paid_on, d.status
            from dues_charge d join participant pr on pr.id = d.participant_id
            where d.period = any(%s) order by d.period, pr.display_name""", (list(PERIODS),)).fetchall()

        quality = {r["period"]: r["quality"] for r in conn.execute(
            "select period, quality from budget_period where period = any(%s)", (list(PERIODS),)).fetchall()}

        agg = conn.execute("""
            select period, block,
                   coalesce(c.name,'Прочее') category, c.direction,
                   count(*) n, sum(amount) s
            from budget_tx t left join category c on c.id = t.category_id
            where t.period = any(%s) group by 1,2,3,4 order by 1,2""", (list(PERIODS),)).fetchall()

    tx_rows = []
    for t in tx:
        purpose = t["purpose"] or ""
        tx_rows.append([
            str(t["id"]),
            t["tx_date"].strftime("%d.%m.%Y"),
            BLOCK_TYPE.get(t["block"], ""),
            BLOCK_TITLE.get(t["block"], t["block"]),
            float(t["amount"]),
            t["category"],
            t["project"] or "",
            t["counterparty"] or t["participant"] or "",
            purpose,
            t["method"] or "",
            "импорт (файл)",
            f"Финансы 2026 от 07 · {t['period']} · «{t['article_raw'] or ''}»",
            1,
            "подтверждено",
        ])

    dues_rows = [[d["display_name"], d["period"], float(d["amount_due"]), float(d["amount_paid"]),
                  float(d["amount_due"]) - float(d["amount_paid"]),
                  d["paid_on"].strftime("%d.%m.%Y") if d["paid_on"] else "", d["status"]]
                 for d in dues]

    # --- раздел 1: итоги внутри месяца -------------------------------------
    # ВАЖНО: agg содержит по строке на (период, блок, категория) — суммируем, а не перезаписываем
    sums: dict[tuple[str, str], float] = {}
    tickets = {}
    other_income = {}
    for r in agg:
        key = (r["period"], r["block"])
        sums[key] = sums.get(key, 0.0) + float(r["s"] or 0)
        if r["block"] == "show_income":
            if r["category"] == "Продажа билетов":
                tickets[r["period"]] = tickets.get(r["period"], 0.0) + float(r["s"] or 0)
            else:
                other_income[r["period"]] = other_income.get(r["period"], 0.0) + float(r["s"] or 0)
    dues_sum = {p: sum(float(d["amount_due"]) for d in dues if d["period"] == p) for p in PERIODS}

    month_rows = []
    for p in PERIODS:
        tk = tickets.get(p, 0.0)
        courses = other_income.get(p, 0.0)
        dues_p = dues_sum.get(p, 0.0)
        sub = sums.get((p, "sublease"), 0.0)
        studio = sums.get((p, "expense"), 0.0)
        shows = sums.get((p, "show_cost"), 0.0)
        income = dues_p + tk + courses + sub
        expense = studio + shows
        month_rows.append([p, quality.get(p, "draft"), dues_p, tk, courses, sub,
                           income, studio, shows, expense, income - expense])

    # --- раздел 2: накопительно -------------------------------------------
    cum_rows, acc_inc, acc_exp = [], 0.0, 0.0
    for row in month_rows:
        acc_inc += row[6]
        acc_exp += row[9]
        coverage = round(acc_inc / acc_exp * 100, 1) if acc_exp else 0.0
        cum_rows.append([row[0], round(acc_inc, 2), round(acc_exp, 2),
                         round(acc_inc - acc_exp, 2), coverage])

    ref_rows = [[r["category"], r["direction"] or "", r["n"], float(r["s"])] for r in agg]

    print(f"транзакции {len(tx_rows)} | взносы {len(dues_rows)} | "
          f"месяцы {len(month_rows)} | накопительно {len(cum_rows)} | категории {len(ref_rows)}")
    for r in month_rows:
        print(f"  {r[0]} [{r[1]}] доход {r[6]:>9.0f} расход {r[9]:>9.0f} → {r[10]:>9.0f}")
    print("  накопительно:", [(c[0], c[3]) for c in cum_rows])

    if args.dry_run:
        print("\nDRY-RUN: в Google ничего не пишем")
        return

    cred = service_account.Credentials.from_service_account_file(SA, scopes=SCOPES)
    sheets = build("sheets", "v4", credentials=cred, cache_discovery=False)

    for title, headers in (("Транзакции", TX_HEADERS), ("Взносы", DUES_HEADERS),
                           ("Свод", MONTH_HEADERS), ("Справочники", REF_HEADERS)):
        ensure_sheet(sheets, SPREADSHEET, title, headers)
        clear_below(sheets, SPREADSHEET, title, "N")

    # Транзакции
    if tx_rows:
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET, range="Транзакции!A2", valueInputOption="USER_ENTERED",
            body={"values": tx_rows}).execute()
    if dues_rows:
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET, range="Взносы!A2", valueInputOption="USER_ENTERED",
            body={"values": dues_rows}).execute()

    # Свод: два блока с заголовками-разделами
    svod = [["ИТОГИ ПО МЕСЯЦАМ (внутри периода)"], MONTH_HEADERS] + month_rows
    svod.append([])
    svod.append(["НАКОПИТЕЛЬНО (с начала периода)"])
    svod.append(CUM_HEADERS)
    svod += cum_rows
    # RAW: иначе «2026-08» превращается в дату «2026-8»
    sheets.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET, range="Свод!A1:Z20000").execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET, range="Свод!A1", valueInputOption="RAW",
        body={"values": svod}).execute()

    if ref_rows:
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET, range="Справочники!A2", valueInputOption="USER_ENTERED",
            body={"values": ref_rows}).execute()

    print(f"\nготово: https://docs.google.com/spreadsheets/d/{SPREADSHEET}/edit")


if __name__ == "__main__":
    main()
