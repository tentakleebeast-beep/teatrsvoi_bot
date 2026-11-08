#!/usr/bin/env python3
"""
Сверка «файл → БД» (docs/architecture/17 §4, спринт 2).

Истина — то, что человек внёс руками в файл. Бот не спорит: он читает, сравнивает
и приводит БД к файлу, а всё спорное выносит в отчёт.

Что сверяем:
  1. Бюджет: лист «Транзакции» таблицы «Бюджет» ↔ таблица budget_tx.
  2. Занятость: файл «Расписание СВОИ.xlsx» на Drive ↔ availability_hour (только отчёт).

  python scripts/reconcile.py                 # dry-run: только показать
  python scripts/reconcile.py --apply         # применить к БД
  python scripts/reconcile.py --only budget   # только бюджет
"""

from __future__ import annotations

import io
import json
import os
from collections import defaultdict
from datetime import date, datetime

import db_guard
import psycopg
from _cli import add_allow_dev_write, add_json_flag, add_write_mode, apply_env, dsn, parser, writes
from _people import person_index, report_gaps, unmatched_sheets
from google.oauth2 import service_account
from googleapiclient.discovery import build
from psycopg.rows import dict_row

apply_env()

SA = os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
DB = dsn()
BUDGET_SID = os.getenv("BUDGET_SPREADSHEET_ID", "")
SCHEDULE_FILE_ID = os.getenv("SCHEDULE_XLSX_ID", "1kKH2Vdc5_phHVBQ-sd5ANP4U7aBfUz1J")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive.readonly"]


def services():
    cred = service_account.Credentials.from_service_account_file(SA, scopes=SCOPES)
    return (build("sheets", "v4", credentials=cred, cache_discovery=False),
            build("drive", "v3", credentials=cred, cache_discovery=False))


def num(value) -> float | None:
    """'3 000 ₽' → 3000.0"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace("₽", "").replace("\u00a0", "").replace(" ", "").replace(",", ".")
    cleaned = cleaned.replace("−", "-").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- бюджет
def read_tx_sheet(sheets) -> list[dict]:
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=BUDGET_SID, range="Транзакции!A2:N5000").execute().get("values", [])
    out = []
    for r in rows:
        if not r or not str(r[0]).strip().isdigit():
            continue
        r += [""] * (14 - len(r))
        out.append({
            "id": int(r[0]), "date": parse_date(r[1]), "type": str(r[2]).strip().lower(),
            "block": r[3], "amount": num(r[4]), "category": str(r[5]).strip(),
            "project": r[6], "counterparty": r[7], "purpose": r[8],
        })
    return out


def triage_missing(db_rows: list[dict], file_ids: set[int]) -> tuple[list[dict], list[dict]]:
    """Разделить «нет в листе» на удалённое человеком и не дошедшее до листа (C-3).

    Строка, запись которой в лист упала (`sheet_error` заполнен, `sheet_written_at` пуст),
    в файле никогда не появлялась — человек её не удалял, отклонять нельзя.
    Строка, которая в лист попадала, а теперь отсутствует, удалена человеком — rejected.
    """
    rejected, unconfirmed = [], []
    for db in db_rows:
        if db["id"] in file_ids or db["origin"] not in ("import", "file", "bot"):
            continue
        if db.get("sheet_written_at") is None and db.get("sheet_error"):
            unconfirmed.append(db)
        else:
            rejected.append(db)
    return rejected, unconfirmed


def reconcile_budget(sheets, apply: bool) -> dict:
    file_rows = read_tx_sheet(sheets)
    file_by_id = {}
    duplicates = []
    for row in file_rows:
        if row["id"] in file_by_id:
            duplicates.append(row["id"])
        file_by_id[row["id"]] = row

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        db_rows = conn.execute("""
            select t.id, t.tx_date, t.amount, t.tx_type, t.status, t.origin,
                   t.category_id, t.sheet_written_at, t.sheet_error,
                   coalesce(c.name,'') as category
            from budget_tx t left join category c on c.id = t.category_id""").fetchall()
        db_by_id = {r["id"]: r for r in db_rows}
        added, rejected, updated, same = [], [], [], 0
        for tx_id, row in file_by_id.items():
            db = db_by_id.get(tx_id)
            if db is None:
                added.append(row)
                continue
            diffs = {}
            if row["amount"] is not None and abs(float(db["amount"]) - abs(row["amount"])) > 0.01:
                diffs["amount"] = (float(db["amount"]), abs(row["amount"]))
            if row["date"] and db["tx_date"] != row["date"]:
                diffs["tx_date"] = (str(db["tx_date"]), str(row["date"]))
            if row["category"] and row["category"] != db["category"]:
                diffs["category"] = (db["category"], row["category"])
            if diffs:
                updated.append((tx_id, diffs))
            else:
                same += 1

        rejected, unconfirmed = triage_missing(db_rows, set(file_by_id))

        report = {
            "в_файле": len(file_rows), "в_бд": len(db_rows), "совпало": same,
            "нет_в_бд": len(added), "нет_в_файле": len(rejected),
            "лист_не_подтвердил": len(unconfirmed), "расхождения": len(updated),
            "ids_нет_в_бд": [r["id"] for r in added],
            "ids_нет_в_файле": [r["id"] for r in rejected],
            "ids_лист_не_подтвердил": [r["id"] for r in unconfirmed],
            "дубли_в_файле": duplicates, "добавлено": [], "отклонено": [], "изменено": [],
        }

        if apply:
            for row in added:
                cat_id = conn.execute(
                    "select id from category where name = %s", (row["category"],)).fetchone()
                if cat_id is None:
                    cat_id = conn.execute(
                        "insert into category (name, direction) values (%s,%s) returning id",
                        (row["category"], "income" if row["type"] == "доход" else "expense")
                    ).fetchone()
                block = "expense" if row["type"] == "расход" else "show_income"
                tx_date = row["date"] or datetime.now().date()
                conn.execute("""
                    insert into budget_tx (id, period, block, tx_date, tx_type, amount, category_id,
                                           purpose, status, origin, confirmed_at, idempotency_key)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,'confirmed','import',now(),%s)
                    on conflict (id) do nothing""",
                    (row["id"], tx_date.strftime("%Y-%m"), block, tx_date, row["type"],
                     abs(row["amount"] or 0), cat_id["id"], row["purpose"] or "",
                     f"file:{row['id']}"))
                report["добавлено"].append(row["id"])

            for tx_id, diffs in updated:
                row = file_by_id[tx_id]
                cat_id = conn.execute(
                    "select id from category where name = %s", (row["category"],)).fetchone()
                conn.execute("""update budget_tx set amount = %s, tx_date = %s, period = %s,
                                       category_id = coalesce(%s, category_id), origin = 'import'
                                 where id = %s""",
                             (abs(row["amount"] or 0), row["date"], row["date"].strftime("%Y-%m"),
                              cat_id["id"] if cat_id else None, tx_id))
                report["изменено"].append({"id": tx_id, "что": diffs})

            # строка есть в листе — подтверждаем факт записи (C-3, db/init/033)
            for tx_id in file_by_id:
                if tx_id in db_by_id:
                    conn.execute("update budget_tx set sheet_written_at = "
                                 "coalesce(sheet_written_at, now()), sheet_error = null "
                                 "where id = %s", (tx_id,))

            for db in rejected:
                conn.execute("update budget_tx set status = 'rejected' where id = %s", (db["id"],))
                report["отклонено"].append(db["id"])

        # итоги: файл vs БД по периодам
        file_totals = defaultdict(float)
        for row in file_rows:
            if row["date"]:
                sign = -1 if row["type"] == "расход" else 1
                file_totals[row["date"].strftime("%Y-%m")] += sign * abs(row["amount"] or 0)
        db_totals = defaultdict(float)
        for r in db_rows:
            if r["status"] == "confirmed":
                sign = -1 if r["tx_type"] == "expense" else 1
                db_totals[r["tx_date"].strftime("%Y-%m")] += sign * float(r["amount"])
        report["итоги"] = {p: (round(file_totals.get(p, 0), 2), round(db_totals.get(p, 0), 2))
                           for p in sorted(set(file_totals) | set(db_totals))}
    return report


# ---------------------------------------------------------------- занятость
def reconcile_availability(drive, apply: bool) -> dict:
    """Сравниваем часовую матрицу из файла с availability_hour. Пока только отчёт."""
    import openpyxl

    raw = drive.files().get_media(fileId=SCHEDULE_FILE_ID).execute()
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        index = person_index(conn)
        db_cells = {(r["participant_id"], r["day"], r["hour"]): r["is_free"]
                    for r in conn.execute(
                        "select participant_id, day, hour, is_free from availability_hour")}

    stats = {"листов_людей": 0, "клеток_в_файле": 0, "совпало": 0, "разошлось": 0,
             "нет_в_бд": 0, "лишних_в_бд": 0, "по_людям": {}}
    compared_keys = set()
    unclaimed = unmatched_sheets(list(wb.sheetnames), index)

    for sheet in wb.sheetnames:
        pid = index.resolve(sheet)
        if not pid:
            continue
        stats["листов_людей"] += 1
        ws = wb[sheet]
        header = list(next(ws.iter_rows(min_row=1, max_row=1, values_only=True)))
        hours = []
        for h in header[2:]:
            if h is None:
                continue
            try:
                hours.append(int(str(h).split(":")[0]))
            except ValueError:
                continue          # «Итог», «Свободно» и прочие служебные колонки
        mism = 0
        cells = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            day_value = row[1]
            if not day_value:
                continue
            day = parse_date(day_value) or parse_date(str(day_value)[:10])
            if not day:
                continue
            for i, value in enumerate(row[2:2 + len(hours)]):
                if value is None or value == "":
                    continue
                hour = hours[i]
                cells += 1
                stats["клеток_в_файле"] += 1
                key = (pid, day, hour)
                compared_keys.add(key)
                is_free_file = bool(int(value))
                if key not in db_cells:
                    stats["нет_в_бд"] += 1
                    mism += 1
                elif db_cells[key] != is_free_file:
                    stats["разошлось"] += 1
                    mism += 1
                else:
                    stats["совпало"] += 1
        stats["по_людям"][sheet] = {"клеток": cells, "расхождений": mism}

    stats["лишних_в_бд"] = len([k for k in db_cells if k not in compared_keys])
    stats["листы_без_человека"] = unclaimed
    stats["замечания"] = report_gaps(index, unclaimed)
    return stats


def write_report(sheets, budget: dict | None, sched: dict | None) -> None:
    """Пишем результат сверки на лист «Сверка» — чтобы расхождения были видны людям."""
    title = "Сверка"
    meta = sheets.spreadsheets().get(spreadsheetId=BUDGET_SID).execute()
    if title not in [s["properties"]["title"] for s in meta["sheets"]]:
        sheets.spreadsheets().batchUpdate(spreadsheetId=BUDGET_SID, body={"requests": [
            {"addSheet": {"properties": {"title": title}}}]}).execute()

    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    rows = [["Когда", "Контур", "Показатель", "Значение", "Статус"]]
    if budget:
        ok_totals = all(abs(f - d) < 1 for f, d in budget["итоги"].values())
        rows += [
            [stamp, "бюджет", "строк в файле", budget["в_файле"], ""],
            [stamp, "бюджет", "строк в БД", budget["в_бд"], ""],
            [stamp, "бюджет", "совпало", budget["совпало"], "ок"],
            [stamp, "бюджет", "нет в БД", budget["нет_в_бд"],
             "ок" if not budget["нет_в_бд"] else "требует решения"],
            [stamp, "бюджет", "нет в файле", budget["нет_в_файле"],
             "ок" if not budget["нет_в_файле"] else "помечено rejected"],
            [stamp, "бюджет", "лист не подтвердил", budget["лист_не_подтвердил"],
             "ок" if not budget["лист_не_подтвердил"] else "не отклонено, нужен повтор"],
            [stamp, "бюджет", "расхождений полей", budget["расхождения"],
             "ок" if not budget["расхождения"] else "принято из файла"],
            [stamp, "бюджет", "итоги по периодам", " / ".join(
                f"{p}: {f:.0f}·{d:.0f}" for p, (f, d) in budget["итоги"].items()),
             "ок" if ok_totals else "РАСХОЖДЕНИЕ"],
        ]
    if sched:
        rows += [
            [stamp, "занятость", "листов людей", sched["листов_людей"], ""],
            [stamp, "занятость", "клеток в файле", sched["клеток_в_файле"], ""],
            [stamp, "занятость", "совпало", sched["совпало"], "ок"],
            [stamp, "занятость", "разошлось", sched["разошлось"],
             "ок" if not sched["разошлось"] else "требует решения"],
            [stamp, "занятость", "нет в БД", sched["нет_в_бд"],
             "архивные участники" if sched["нет_в_бд"] else "ок"],
            [stamp, "занятость", "лишних в БД", sched["лишних_в_бд"], ""],
            [stamp, "занятость", "листов без человека", len(sched["листы_без_человека"]),
             "ок" if not sched["листы_без_человека"] else "требует решения"],
        ]

    sheets.spreadsheets().values().append(
        spreadsheetId=BUDGET_SID, range="Сверка!A1",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": rows}).execute()
    print("\n  отчёт записан на лист «Сверка»")


def run(apply: bool, only: str | None, report: bool = False) -> tuple[dict | None, dict | None]:
    """Один проход сверки: печатает отчёт и возвращает его данные."""
    sheets, drive = services()
    budget_report = None
    sched_report = None

    if only in (None, "budget"):
        rep = reconcile_budget(sheets, apply)
        print("=== СВЕРКА БЮДЖЕТА (файл «Транзакции» → БД) ===")
        print(f"  в файле {rep['в_файле']} · в БД {rep['в_бд']} · совпало {rep['совпало']}")
        print(f"  нет в БД: {rep['нет_в_бд']} · нет в файле: {rep['нет_в_файле']} · "
              f"расхождений: {rep['расхождения']}")
        if rep["дубли_в_файле"]:
            print(f"  ВНИМАНИЕ, дубли id в файле: {rep['дубли_в_файле']}")
        if rep["ids_нет_в_бд"]:
            print("  отсутствуют в БД: " + ", ".join(str(i) for i in rep["ids_нет_в_бд"][:15]))
        if rep["ids_нет_в_файле"]:
            print("  удалены из файла (будут отклонены): " +
                  ", ".join(str(i) for i in rep["ids_нет_в_файле"][:15]))
        if rep["ids_лист_не_подтвердил"]:
            print("  лист не подтвердил запись (НЕ отклоняем, нужен повтор): " +
                  ", ".join(str(i) for i in rep["ids_лист_не_подтвердил"][:15]))
        for item in rep["изменено"][:10]:
            print(f"  #{item['id']}: {item['что']}")
        print("  итоги по периодам (файл / БД):")
        for period, (f_sum, d_sum) in rep["итоги"].items():
            mark = "OK" if abs(f_sum - d_sum) < 1 else "РАСХОЖДЕНИЕ"
            print(f"    {period}: {f_sum:>12,.0f} / {d_sum:>12,.0f}  {mark}")
        if apply:
            print(f"  ПРИМЕНЕНО: добавлено {len(rep['добавлено'])}, "
                  f"изменено {len(rep['изменено'])}, отклонено {len(rep['отклонено'])}")
        else:
            print("  (dry-run: ничего не изменено)")
        budget_report = rep

    if only in (None, "schedule"):
        st = reconcile_availability(drive, apply)
        print("\n=== СВЕРКА ЗАНЯТОСТИ (файл «Расписание СВОИ.xlsx» → БД) ===")
        print(f"  листов людей: {st['листов_людей']} · клеток в файле: {st['клеток_в_файле']}")
        print(f"  совпало {st['совпало']} · разошлось {st['разошлось']} · "
              f"нет в БД {st['нет_в_бд']} · лишних в БД {st['лишних_в_бд']}")
        for sheet, info in list(st["по_людям"].items())[:12]:
            print(f"    {sheet}: клеток {info['клеток']}, расхождений {info['расхождений']}")
        for line in st["замечания"]:
            print(f"  ! {line}")
        print("  (занятость пока только отчёт: решение о записи — за человеком)")
        sched_report = st

    if report or apply:
        write_report(sheets, budget_report, sched_report)
    return budget_report, sched_report


def main() -> None:
    ap = parser(__doc__ or "Сверка «файл → БД»")
    add_write_mode(ap)
    ap.add_argument("--only", choices=("budget", "schedule", "all"),
                    help="что сверять: бюджет, занятость или оба контура")
    ap.add_argument("--report", action="store_true", help="записать отчёт в таблицу")
    add_json_flag(ap)
    add_allow_dev_write(ap)
    args = ap.parse_args()

    apply = writes(args)
    if apply:  # правки БД — только осознанно, не по привычке «так быстрее»
        db_guard.assert_writable(DB, allow=args.allow_dev_write, script="reconcile")
    only = args.only
    if only == "all":              # «оба контура» — то же, что не указывать
        only = None

    if args.json:
        import contextlib
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            budget_report, sched_report = run(apply, only, args.report)
        print(json.dumps({"ok": True, "applied": apply, "scope": only or "all",
                          "budget": budget_report, "schedule": sched_report,
                          "text": text.getvalue()},
                         ensure_ascii=False, default=str))
        return

    run(apply, only, args.report)


if __name__ == "__main__":
    main()
