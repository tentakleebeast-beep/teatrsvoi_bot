#!/usr/bin/env python3
"""Обратный путь «человеческий лист → БД» (FR-G13).

Лист «Финансы 2026 от 07» ведёт человек, бот только дописывает свои строки.
Идентификатор операции бот пишет в служебную колонку R (`bot:<id>`), поэтому
сверка идёт по ID, а не по «дате и сумме» (docs/reports/60 §2: без ID это догадка).

Что делает скрипт:
  1. находит блок месяца по заголовку в колонке A («ОКТЯБРЬ 2026») и его итоги;
  2. разбирает строки блока на операции по разделам:
       A–C взносы, D–F расходы, G–H доходы показов, I–J вложения в показ,
       L–N субаренда, O–Q мелкие вложения; колонка R — ID операции;
     оба раздела вложений (I–J и O–Q) сводятся к блоку `budget_tx.show_cost`:
     человек делит затраты показов на крупные и мелкие, в БД это один блок
     (docs/architecture/11, docs/reports/60 §5);
  3. строки с `bot:<id>` сверяет с `budget_tx` по ID и считает расхождения
     (сумма, дата, раздел, текст);
  4. строки без ID — ручные: в БД они не пишутся, только показываются в отчёте;
  5. строки бота, которых в листе больше нет, — удалены человеком, только отчёт
     (отклоняет их `scripts/reconcile.py`, чтобы не задвоить логику C-3).

Dry-run по умолчанию. Запись — только с `--apply`; меняются сумма и дата операции,
найденной по ID, плюс отметка `sheet_written_at`.

Запуск:
  env -u PYTHONPATH .venv/bin/python scripts/read_finance_sheet.py --period 2026-10
  env -u PYTHONPATH .venv/bin/python scripts/read_finance_sheet.py --period 2026-10 --apply
  env -u PYTHONPATH .venv/bin/python scripts/read_finance_sheet.py --period 2026-10 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime

import psycopg
from _cli import ROOT, apply_env
from psycopg.rows import dict_row

apply_env()

SHEET = "Финансы 2026 от 07"
MONTHS = {1: "ЯНВАРЬ", 2: "ФЕВРАЛЬ", 3: "МАРТ", 4: "АПРЕЛЬ", 5: "МАЙ", 6: "ИЮНЬ",
          7: "ИЮЛЬ", 8: "АВГУСТ", 9: "СЕНТЯБРЬ", 10: "ОКТЯБРЬ", 11: "НОЯБРЬ", 12: "ДЕКАБРЬ"}
ID_COLUMN = 17          # R, нумерация с нуля

# kind -> (колонка текста, колонка дня, колонка суммы); None — колонки нет
SECTIONS = {
    "dues": (0, 1, 2),          # A Фамилия, B Дата, C Факт
    "expense": (3, 4, 5),       # D Статья, E Дата, F Факт
    "show_income": (6, None, 7),  # G Источник, H Сумма (дата внутри текста)
    "invest": (8, None, 9),     # I Статья, J Сумма
    "sublease": (11, 12, 13),   # L Статья, M Дата, N Сумма
    "show_cost": (14, 15, 16),  # O Статья, P Дата, Q Факт
}
# Раздел листа -> блок budget_tx. `invest` (I–J) и `show_cost` (O–Q) — один
# блок: лист делит затраты показов на крупные и мелкие, БД не делит.
BLOCK = {"dues": "dues", "expense": "expense", "show_income": "show_income",
         "invest": "show_cost", "sublease": "sublease", "show_cost": "show_cost"}
KIND_RU = {"dues": "взносы", "expense": "расходы", "show_income": "доходы",
           "invest": "вложения", "sublease": "субаренда", "show_cost": "мелкие вложения"}


def client():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    key = ROOT / os.getenv("GOOGLE_SA_JSON", "secrets/google-sa.json")
    creds = service_account.Credentials.from_service_account_file(
        str(key), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def num(value) -> float | None:
    """'3 000 ₽' → 3000.0, '—' → None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = (str(value).replace("₽", "").replace("\u00a0", "").replace(" ", "")
               .replace(",", ".").replace("−", "-").strip())
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_day(value, period: str) -> date | None:
    """День из листа: '6', '22.10', '28.10.26', '26,10' → date.

    Неоднозначное ('6, 13, 20, 27', 'за месяц', 'октябрь', '7 бутылок') → None:
    угадывать дату нельзя.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("\u00a0", "")
    year_hint, month_hint = (int(part) for part in period.split("-"))
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    for separator in (".", ","):
        parts = text.split(separator)
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            day, month = int(parts[0]), int(parts[1])
            if not 1 <= month <= 12:
                return None
            year = year_hint          # лист ведётся по годам: месяц раньше блока — тот же год
            try:
                return date(year, month, day)
            except ValueError:
                return None
    if text.isdigit():
        try:
            return date(year_hint, month_hint, int(text))
        except ValueError:
            return None
    return None


def month_block(values: list[list[str]], period: str) -> tuple[int, int]:
    """Границы блока месяца в нумерации с нуля: (первая строка данных, строка итогов)."""
    year, month = period.split("-")
    title = f"{MONTHS[int(month)]} {year}"
    start = None
    for index, row in enumerate(values):
        if str(row[0] if row else "").strip().upper().startswith(title):
            start = index
            break
    if start is None:
        raise SystemExit(f"в листе нет блока {title}")
    data_start = start + 4          # две строки разделов, шапка и одна пустая/служебная
    totals = None
    for index in range(data_start, min(data_start + 100, len(values))):
        first = str(values[index][0] if values[index] else "").strip().lower()
        if first.startswith("итог"):
            totals = index
            break
    if totals is None:
        raise SystemExit(f"в блоке {title} нет строки «Итог месяца»")
    return data_start, totals


def row_segments(row: list[str], line: int, period: str) -> list[dict]:
    """Строка листа → операции. В одной строке человек может вести несколько разделов."""
    padded = list(row) + [""] * (18 - len(row))
    tx_id = str(padded[ID_COLUMN]).strip() or None
    segments = []
    for kind, (text_col, day_col, amount_col) in SECTIONS.items():
        text = str(padded[text_col]).strip() if padded[text_col] else ""
        amount = num(padded[amount_col])
        raw_day = padded[day_col] if day_col is not None else ""
        if not text and amount is None:
            continue
        segments.append({
            "row": line, "kind": kind, "section": KIND_RU[kind], "block": BLOCK[kind],
            "text": text, "day_raw": str(raw_day).strip() if raw_day else "",
            "day": parse_day(raw_day, period), "amount": abs(amount) if amount is not None else None,
            "tx_id": tx_id,
        })
    if tx_id and len(segments) > 1:
        for segment in segments:
            segment["ambiguous_id"] = True
    return segments


def sheet_operations(values: list[list[str]], period: str) -> list[dict]:
    data_start, totals = month_block(values, period)
    out = []
    for index in range(data_start, totals):
        row = values[index] if index < len(values) else []
        out.extend(row_segments(row, index + 1, period))
    return out


def id_of(segment: dict) -> int | None:
    value = (segment.get("tx_id") or "").strip()
    if not value.startswith("bot:") or segment.get("ambiguous_id"):
        return None
    tail = value[4:]
    return int(tail) if tail.isdigit() else None


def build_report(sheet: list[dict], db_rows: list[dict], period: str) -> dict:
    """Сравнить операции листа с БД. Порядок важен: ID — единственный ключ сверки."""
    db_by_id = {int(row["id"]): row for row in db_rows}
    updates, differences, mistakes = [], [], []
    manual, sheet_ids, matched = [], set(), set()

    for segment in sheet:
        tx_id = id_of(segment)
        if tx_id is None:
            if segment["amount"] is not None or segment["text"]:
                manual.append({k: segment[k] for k in
                               ("row", "section", "day_raw", "amount", "text", "tx_id")})
            continue
        sheet_ids.add(tx_id)
        db = db_by_id.get(tx_id)
        if db is None:
            mistakes.append({"id": tx_id, "row": segment["row"],
                             "what": "ID в листе, строки нет в БД"})
            continue
        if segment.get("ambiguous_id"):
            differences.append({"id": tx_id, "row": segment["row"],
                                "what": "несколько сумм в строке с одним ID — уточнить вручную"})
            continue
        matched.add(tx_id)
        change = {}
        if segment["amount"] is not None and abs(float(db["amount"]) - segment["amount"]) > 0.01:
            change["amount"] = {"было": float(db["amount"]), "стало": segment["amount"]}
        if segment["day"] and db["tx_date"] != segment["day"]:
            change["tx_date"] = {"было": str(db["tx_date"]), "стало": str(segment["day"])}
        if segment["block"] != db["block"]:
            differences.append({"id": tx_id, "row": segment["row"],
                                "what": "раздел", "было": db["block"], "стало": segment["block"]})
        if segment["text"] and db.get("purpose") and segment["text"] != db["purpose"]:
            differences.append({"id": tx_id, "row": segment["row"],
                                "what": "текст", "было": db["purpose"], "стало": segment["text"]})
        if change:
            updates.append({"id": tx_id, "row": segment["row"], "change": change})

    deleted = [int(row["id"]) for row in db_rows
               if int(row["id"]) not in sheet_ids
               and int(row["id"]) not in matched
               and row.get("sheet_written_at") is not None]
    return {
        "period": period, "в_листе": len(sheet), "в_бд": len(db_rows),
        "совпало": len(matched) - len(updates), "к_обновлению": len(updates),
        "ручных": len(manual), "расхождения_полей": len(differences),
        "ошибок": len(mistakes), "удалено_в_листе": len(deleted),
        "обновления": updates, "ручные": manual, "расхождения": differences,
        "ошибки": mistakes, "ids_удалены_в_листе": deleted,
    }


def db_operations(period: str) -> list[dict]:
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        return [dict(row) for row in conn.execute("""
            select id, tx_date, amount, block, purpose, status, sheet_written_at
            from budget_tx where period = %s order by id""", (period,)).fetchall()]


def apply_updates(updates: list[dict], conn) -> int:
    """Записать правки человека в БД по ID. Ничего кроме суммы/даты/отметки не трогаем."""
    changed = 0
    for item in updates:
        change = item["change"]
        sets, params = ["sheet_written_at = now()", "sheet_error = null"], []
        if "amount" in change:
            sets.append("amount = %s")
            params.append(change["amount"]["стало"])
        if "tx_date" in change:
            new_date = date.fromisoformat(change["tx_date"]["стало"])
            sets.append("tx_date = %s")
            sets.append("period = %s")
            params.extend([new_date, new_date.strftime("%Y-%m")])
        params.append(item["id"])
        conn.execute(f"update budget_tx set {', '.join(sets)} where id = %s", params)
        changed += 1
    return changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Обратный путь: правки листа → budget_tx (FR-G13)")
    parser.add_argument("--period", required=True, help="YYYY-MM")
    parser.add_argument("--apply", action="store_true", help="записать правки в БД")
    parser.add_argument("--dry-run", action="store_true", help="только показать (по умолчанию)")
    parser.add_argument("--json", action="store_true", help="отчёт в JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    values = client().spreadsheets().values().get(
        spreadsheetId=os.environ["BUDGET_SPREADSHEET_ID"],
        range=f"'{SHEET}'!A1:R400").execute().get("values", [])
    report = build_report(sheet_operations(values, args.period),
                          db_operations(args.period), args.period)

    if args.apply:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            report["записано"] = apply_updates(report["обновления"], conn)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"период {report['period']}: в листе {report['в_листе']} операций, "
          f"в БД {report['в_бд']}")
    print(f"по ID совпало {report['совпало']}, к обновлению {report['к_обновлению']}, "
          f"ручных {report['ручных']}, ошибок {report['ошибок']}, "
          f"удалено в листе {report['удалено_в_листе']}")
    for item in report["обновления"]:
        changes = ", ".join(f"{key}: {value['было']} → {value['стало']}"
                            for key, value in item["change"].items())
        print(f"  стр.{item['row']} id {item['id']}: {changes}")
    for item in report["ручные"]:
        print(f"  ручная стр.{item['row']} [{item['section']}] "
              f"{item['day_raw']} {item['amount']} {item['text'][:40]}")
    for item in report["расхождения"] + report["ошибки"]:
        print(f"  внимание стр.{item['row']} id {item['id']}: {item['what']}")
    if report["ids_удалены_в_листе"]:
        print(f"  удалены человеком (отклонит reconcile): {report['ids_удалены_в_листе']}")
    if not args.apply:
        print("DRY-RUN: БД не изменена (для записи --apply)")
    else:
        print(f"записано правок: {report.get('записано', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
