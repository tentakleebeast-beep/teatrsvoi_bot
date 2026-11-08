"""Перенос операций бота в человеческий лист «Финансы 2026 от 07».

Лист ведётся руками: месячные блоки, шапка в три строки, итоги формулами.
Задача — дописывать операции бота в нужный блок, не ломая формулы и вид.

Раскладка блока (проверено по источнику):
  A..C  взносы студийцев: A фамилия, B дата, C сумма
  D..F  расходы студии:   D статья, E дата, F факт
  G..H  доходы показов:   G источник, H сумма
  I..K  вложения в показ: I статья, J сумма, K дата возврата
  L..N  субаренда:        L арендатель, M дата, N сумма
  O..Q  мелкие вложения:  O статья, P дата, Q факт (бот сюда не пишет)

Затраты показов человек ведёт в I–J: в сентябре и октябре 2026 там доли
(«21% Крылову», «Выплата ОВ»), закупки, программки, съёмка. Второй раздел
«Вложения» O–Q — мелкие закупки к показу («клеммы», «лампы Эдисона»); он
ручной, у него нет блока в `budget_tx`. Поэтому `show_cost` пишется в I–J,
а обратное чтение (`read_finance_sheet`) сводит оба раздела к блоку
`show_cost` (docs/reports/60 §5).

Логика:
  1. находит блок месяца по заголовку в колонке A («ОКТЯБРЬ 2026»);
  2. находит строку «Итог месяца» внутри блока — граница вставки;
  3. собирает существующие строки листа и операции из БД;
  4. сравнивает по ключу (дата, сумма, текст) — дубли не вставляет;
  5. вставляет строки и пишет значения (расходы → D/E/F, доходы → G/H).

Запуск:
  env -u PYTHONPATH .venv/bin/python scripts/sync_finance_sheet.py --period 2026-10 --dry-run
  env -u PYTHONPATH .venv/bin/python scripts/sync_finance_sheet.py --period 2026-10 --apply
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg
from _cli import ROOT, apply_env
from psycopg.rows import dict_row

apply_env()

SHEET = "Финансы 2026 от 07"
MONTHS = {1: "ЯНВАРЬ", 2: "ФЕВРАЛЬ", 3: "МАРТ", 4: "АПРЕЛЬ", 5: "МАЙ", 6: "ИЮНЬ",
          7: "ИЮЛЬ", 8: "АВГУСТ", 9: "СЕНТЯБРЬ", 10: "ОКТЯБРЬ", 11: "НОЯБРЬ", 12: "ДЕКАБРЬ"}
EXPENSE_BLOCKS = {"expense"}
COST_BLOCKS = {"show_cost"}          # затраты показов → I..J (см. шапку модуля)
INCOME_BLOCKS = {"show_income"}
SUBLEASE_BLOCKS = {"sublease"}       # субаренда → L..N
DUES_BLOCK = "dues"


def client():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    key = ROOT / os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
    creds = service_account.Credentials.from_service_account_file(
        str(key), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def block_bounds(values: list[list[str]], period: str) -> tuple[int, int, int]:
    """Границы блока месяца: (первая строка данных, строка итогов, номер листа)."""
    year, month = period.split("-")
    title = f"{MONTHS[int(month)]} {year}"
    start = None
    for index, row in enumerate(values):
        first = (row[0] if row else "").strip().upper()
        if first.startswith(title):
            start = index
            break
    if start is None:
        raise SystemExit(f"в листе нет блока {title}")

    header_row = start + 4                       # шапка занимает четыре строки
    totals_row = None
    for index in range(header_row, min(header_row + 80, len(values))):
        first = (values[index][0] if values[index] else "").strip().lower()
        if first.startswith("итог"):
            totals_row = index
            break
        if first and first[:4] in [m.lower() for m in MONTHS.values()]:
            break
    if totals_row is None:
        raise SystemExit(f"в блоке {title} нет строки «Итог месяца»")
    return header_row, totals_row, start


def amount_key(value) -> str:
    text = str(value or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return str(round(float(text), 2))
    except ValueError:
        return ""


def day_key(value) -> str:
    text = str(value or "").strip().replace(" ", "").split(",")[0]
    text = text.split(".")[0]
    if text.isdigit():
        return f"{int(text):02d}"
    return text


ID_COLUMN = 17          # R (нумерация с нуля: A=0)
# Читаем до R включительно: без колонки ID `sheet_ids()` всегда пуст и `bot:<id>`
# не отсекает повтор (Ш-40).
VALUES_RANGE = f"'{SHEET}'!A1:R400"


def sheet_ids(values: list[list[str]], header_row: int, totals_row: int) -> set[str]:
    """Идентификаторы операций, уже записанных ботом (колонка R)."""
    found = set()
    for row in values[header_row:totals_row]:
        padded = list(row) + [""] * (18 - len(row))
        if len(padded) > ID_COLUMN and str(padded[ID_COLUMN]).strip():
            found.add(str(padded[ID_COLUMN]).strip())
    return found


def sheet_operations(values: list[list[str]], header_row: int, totals_row: int) -> set[str]:
    """Ключи уже внесённых операций — по тем же разделам и правилам, что `to_rows()`.

    Разделы: A–C взносы, D–F расходы, G–H доходы показов, I–J вложения в показ,
    O–Q мелкие вложения. У вложений в листе даты нет (I статья и J сумма, K — дата
    возврата; O статья, P дата, Q факт), поэтому ключ вложений — сумма и текст,
    как и в `to_rows()`.
    """
    keys = set()
    for row in values[header_row:totals_row]:
        padded = list(row) + [""] * (18 - len(row))

        def by_day(kind: str, date, amount) -> str:
            return f"{kind}|{day_key(date)}|{amount_key(amount)}"

        def by_text(kind: str, text, amount) -> str:
            return f"{kind}|{amount_key(amount)}|{str(text).strip()[:20].lower()}"

        if str(padded[2]).strip() and amount_key(padded[2]):
            keys.add(by_day("dues", padded[1], padded[2]))
        if str(padded[3]).strip() or str(padded[5]).strip():
            keys.add(by_day("expense", padded[4], padded[5]))
        if str(padded[6]).strip() or str(padded[7]).strip():
            keys.add(by_text("income", padded[6], padded[7]))
        if str(padded[8]).strip() and amount_key(padded[9]):
            keys.add(by_text("cost", padded[8], padded[9]))
        if str(padded[14]).strip() and amount_key(padded[16]):
            keys.add(by_text("cost", padded[14], padded[16]))
    return keys


def granularity() -> dict[str, str]:
    """Режим записи по разделам: per_operation | per_source | per_month."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        rows = conn.execute("select section, mode from finance_granularity").fetchall()
    return {row["section"]: row["mode"] for row in rows}


def source_map() -> dict[tuple[str, str], dict]:
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        rows = conn.execute("select section, lower(label) as label, category_id, project_id "
                            "from finance_source_map").fetchall()
    return {(row["section"], row["label"]): dict(row) for row in rows}


def db_operations(period: str) -> list[dict]:
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        return conn.execute("""
            select t.id, t.tx_date, t.amount, t.block, t.purpose, t.counterparty,
                   c.name as category, pr.name as project, p.display_name as participant
            from budget_tx t
            left join category c on c.id = t.category_id
            left join project pr on pr.id = t.project_id
            left join participant p on p.id = t.participant_id
            where t.period = %s and t.status = 'confirmed'
            order by t.tx_date, t.id""", (period,)).fetchall()


# Блок БД -> диапазон строки листа. Один источник правды для записи: если
# раскладка меняется, правится здесь и в SECTIONS у read_finance_sheet.
BLOCK_RANGES = {
    "dues": "A{line}:C{line}",
    "expense": "D{line}:F{line}",
    "income": "G{line}:H{line}",
    "cost": "I{line}:J{line}",
    "sublease": "L{line}:N{line}",
}


def cell_updates(row: dict, line: int) -> list[dict]:
    """Строка операции -> правки ячеек листа (без ID в колонке R)."""
    if row["kind"] == "dues":
        values = [row["who"], row["day"], row["amount"]]
    elif row["kind"] == "expense":
        values = [row["text"], row["day"], row["amount"]]
    elif row["kind"] == "cost":
        values = [row["text"], row["amount"]]
    elif row["kind"] == "sublease":
        values = [row["who"], row["day"], row["amount"]]
    else:
        values = [row["text"], row["amount"]]
    return [{"range": f"'{SHEET}'!{BLOCK_RANGES[row['kind']].format(line=line)}",
             "values": [values]}]


ARTIFACTS = ("[дата не указана", "[дат в", "[сумма", "[не указан", "[проверить")


def clean_text(*parts: str | None) -> str:
    """
    Назначение для человеческого листа: статья, проект, короткое пояснение.

    Из разбора приходят служебные пометки вида «[дата не указана, дата в тексте]» —
    в лист они попадать не должны.
    """
    cleaned = []
    for part in parts:
        if not part:
            continue
        value = " ".join(str(part).split())
        for artifact in ARTIFACTS:
            index = value.find(artifact)
            if index >= 0:
                value = value[:index].strip()
        value = value.strip(" ,.;-—")
        if value and value not in cleaned:
            cleaned.append(value)
    return " · ".join(cleaned)[:80]


def to_rows(operations: list[dict]) -> list[dict]:
    """Операции БД → строки листа с ключами для сверки."""
    rows = []
    for op in operations:
        day = op["tx_date"].strftime("%d,%m")
        text = clean_text(op["category"], op["project"], op["purpose"], op.get("counterparty"))
        category = (op.get("category") or "").lower()
        if op["block"] == DUES_BLOCK or category.startswith("взнос"):
            kind = "dues"
        elif op["block"] in EXPENSE_BLOCKS:
            kind = "expense"
        elif op["block"] in COST_BLOCKS:
            kind = "cost"
        elif op["block"] in INCOME_BLOCKS:
            kind = "income"
        elif op["block"] in SUBLEASE_BLOCKS:
            kind = "sublease"
        else:
            continue
        who = op.get("participant") or op.get("counterparty") or "студиец"
        rows.append({"id": op["id"], "kind": kind, "day": day, "who": who,
                     "amount": float(op["amount"]), "text": text,
                     "key": (f"{kind}|{round(float(op['amount']), 2)}|{text[:20].lower()}"
                             if kind in ("income", "cost")
                             else f"{kind}|{day_key(day)}|{round(float(op['amount']), 2)}")})
    return rows


def pending(operations: list[dict], values: list[list[str]],
            header_row: int, totals_row: int) -> list[dict]:
    """Операции БД, которых ещё нет в листе: ни по `bot:<id>` в R, ни по ключу раздела."""
    existing = sheet_operations(values, header_row, totals_row)
    written = sheet_ids(values, header_row, totals_row)
    return [row for row in operations
            if f"bot:{row['id']}" not in written and row["key"] not in existing]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True, help="YYYY-MM")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--i-know", action="store_true",
                        help="подтвердить запись в человеческий лист (см. docs/reports/60)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    service = client()
    spreadsheet_id = os.environ["BUDGET_SPREADSHEET_ID"]
    values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=VALUES_RANGE).execute().get("values", [])

    header_row, totals_row, start = block_bounds(values, args.period)
    modes = granularity()
    mapping = source_map()
    blocked = [section for section, mode in modes.items()
               if mode == "per_source" and not any(
                   key[0] == section for key in mapping)]
    if blocked:
        print("Разделы с гранулярностью «по источникам» без карты соответствий:",
              ", ".join(sorted(blocked)))
        print("Заполните карту: POST /tools/finance/sources {section, label, category_id}")
        print("Иначе перенос даст двойной счёт (docs/reports/60).")
        return 2

    existing = sheet_operations(values, header_row, totals_row)
    operations = pending(to_rows(db_operations(args.period)), values, header_row, totals_row)

    print(f"блок с строки {start + 1}, данные с {header_row + 1}, итоги в {totals_row + 1}")
    print(f"в листе уже есть: {len(existing)} ключей; в БД новых операций: {len(operations)}")
    for row in operations[:12]:
        print(f"  {row['kind']:8} {row['day']:6} {row['amount']:>10,.2f}  {row['text'][:40]}")

    if args.apply and not args.i_know:
        print("Запись в человеческий лист требует --i-know: без маркера операции")
        print("сверка идёт по дате и сумме, а этого мало (docs/reports/60).")
        return 2
    if not args.apply or not operations:
        print("DRY-RUN: лист не изменён" if not args.apply else "нечего переносить")
        return 0

    free_row = header_row
    for index in range(header_row, totals_row):
        padded = list(values[index]) + [""] * (17 - len(values[index]))
        if not any(str(cell).strip() for cell in padded):
            free_row = index
            break

    rows_to_add = len(operations)
    sheet_id = None
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in meta["sheets"]:
        if sheet["properties"]["title"] == SHEET:
            sheet_id = sheet["properties"]["sheetId"]

    needed = max(0, free_row + rows_to_add - totals_row)
    if needed:
        # вставляем перед итогами: формулы SUM растягиваются сами
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [
            {"insertDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
                                           "startIndex": totals_row,
                                           "endIndex": totals_row + needed},
                                 "inheritFromBefore": True}}]}).execute()
        print(f"вставлено строк перед итогами: {needed}")

    updates = []
    for offset, row in enumerate(operations):
        line = free_row + offset + 1              # в A1-нотации строки с единицы
        updates.append({"range": f"'{SHEET}'!R{line}", "values": [[f"bot:{row['id']}"]]})
        updates.extend(cell_updates(row, line))

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "USER_ENTERED", "data": updates}).execute()
    print(f"перенесено строк: {rows_to_add}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
