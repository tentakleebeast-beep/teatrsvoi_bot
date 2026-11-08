#!/usr/bin/env python3
"""
Импорт финансов из «СВОИ 2025.xlsx» / «Финансы 2026 от 07» — версия 2.

Что исправлено против v1 (после разбора с заказчиком):
  * блок «Вложения» в «Показах» — это ЗАТРАТЫ на показ (гонорары, доли, закупки,
    полиграфия, свет, съёмка), а не доход. «21% Крылову» = расход театра.
  * появления («Выплата ОВ», «оплата Семёнову») распознаются как гонорары.
  * дата извлекается из ТЕКСТА, если в колонке даты её нет: «Бунин 26,09» → 26.09.2026,
    «Пурга 19,09», «ЛитРейв 29.10».
  * опечатки в датах: «1.07», «18.10.26», «26,09», «за месяц», «октябрь».
  * проект (Пурга / Бунин / ЛитРейв) определяется и у доходов, и у затрат.

  python scripts/import_finance.py --dry-run
  python scripts/import_finance.py --apply
"""

from __future__ import annotations

import argparse
import re
from datetime import date, datetime, timedelta

import db_guard
import openpyxl
import psycopg
from _cli import dsn
from _people import hint_rules
from psycopg.rows import dict_row

SOURCE = "/tmp/svoi_src/Инфа СВОИ/Админ/СВОИ 2025.xlsx"
SHEET = "Финансы 2026 от 07"
DB = dsn()
MONTHS = {"ЯНВАРЬ": 1, "ФЕВРАЛЬ": 2, "МАРТ": 3, "АПРЕЛЬ": 4, "МАЙ": 5, "ИЮНЬ": 6,
          "ИЮЛЬ": 7, "АВГУСТ": 8, "СЕНТЯБРЬ": 9, "ОКТЯБРЬ": 10, "НОЯБРЬ": 11, "ДЕКАБРЬ": 12}
TARGET = {(2026, 7), (2026, 8), (2026, 9), (2026, 10)}
PROJECT_WORDS = {"пурга": "Пурга", "бунин": "Бунин", "литрейв": "ЛитРейв",
                 "насеком": "Жизнь насекомых", "ндн": "Нет дороги назад"}

# --- категории: порядок важен, первое совпадение выигрывает -------------------
# Имён людей здесь нет: «21% Соколову» и «оплата Петрову» понимаются по подсказкам из
# локального конфига (config/participant-names.yaml, раздел category_hints) — после
# обезличивания (Ш-03 плана 129) фамилия в коде указывала в пустоту.
EXPENSE_RULES = [
    (r"тренинг", "Тренинги и занятия"),
    *hint_rules(),
    (r"выплата ов\b|\bов\b|гонорар|доля|%", "Гонорары и доли"),
    (r"аренд", "Аренда помещения"),
    (r"комуналк|коммуналк|электрик|электрич|водоснабж|вода|жкх|отоплен", "Коммунальные платежи"),
    (r"закупк", "Закупки к спектаклям"),
    (r"программк|афиш|макет|репертуарк|реклам|печат|кюар|бар", "Реклама и полиграфия"),
    (r"ремонт|кран|раковин|замок|линолеум|краск|пайк|сантехник|установк|гармон|баян|прожектор|переходник|держат", "Ремонт и содержание"),
    (r"съемк|съёмк|видео|фото|монтаж|оператор", "Фото/видео"),
    (r"кофе|чай|бумаг|картридж|майк|бутыл|расходник|цвет", "Расходники и хозтовары"),
]

INCOME_RULES = [
    (r"возврат|от даины|от луды|от люд|на линолеум", "Возвраты"),
    (r"курс|марина|альберт|лена|ознакомительн", "Курсы"),
    (r"пурга|бунин|литрейв|насеком|спектакл", "Продажа билетов"),
]


def pick(rules, text: str, default: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    for pattern, cat in rules:
        if re.search(pattern, t):
            return cat
    return default


def project_of(text: str) -> str | None:
    t = (text or "").lower().replace("ё", "е")
    for word, name in PROJECT_WORDS.items():
        if word in t:
            return name
    return None


def last_day(year: int, month: int) -> date:
    nxt = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return nxt - timedelta(days=1)


def _date_from_string(s: str, year: int, month: int) -> tuple[date | None, str | None]:
    """
    Понимает: '1.07', '26,09', '18.10.26', '01,09,15,22,30', '6, 13, 20, 27',
    '8,9,10,11.12', '19 и 26.12', 'за месяц', '4 бутылки'.
    Возвращает первую дату периода и пометку.
    """
    s = str(s or "").strip()
    if not s:
        return None, None

    # 1) одиночная дата с разделителем: 1.07 / 26,09 / 18.10.26
    m = re.fullmatch(r"(\d{1,2})\s*[.,/]\s*(\d{1,2})(?:\s*[.,/]\s*(\d{2,4}))?", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if mo > 12 and d <= 12:
            d, mo = mo, d
        yy = year
        if y:
            yy = int(y)
            yy = 2000 + yy if yy < 100 else yy
        try:
            return date(yy, mo, d), None
        except ValueError:
            return None, None

    # 2) список дней, возможно с месяцем в конце: '01,09,15,22,30', '8,9,10,11.12'
    nums = re.findall(r"\d{1,2}", s)
    has_sep = bool(re.search(r"[,\s]и[\s]|[,\-/]", s)) or len(nums) > 1
    if nums and has_sep:
        mo = month
        tail = re.search(r"[.,/]\s*(\d{1,2})\s*$", s)
        if tail and 1 <= int(tail.group(1)) <= 12:
            mo = int(tail.group(1))
        d = int(nums[0])
        if 1 <= d <= 31:
            try:
                note = f"дат в периоде: {len(nums)}" if len(nums) > 1 else None
                return date(year, mo, d), note
            except ValueError:
                return None, None

    # 3) явные слова
    low = s.lower()
    if "месяц" in low or any(mn in low for mn in
                             ("январ", "феврал", "март", "апрел", "ма", "июн", "июл",
                              "август", "сентябр", "октябр", "ноябр", "декабр")):
        return last_day(year, month), "оплата за период (месяц)"

    # 4) «4 бутылки», «7 бутылок» — это не дата
    return None, None


def parse_date(cell, text_hint: str | None, year: int, month: int) -> tuple[date, str | None]:
    if isinstance(cell, datetime):
        return cell.date(), None
    if isinstance(cell, date):
        return cell, None

    d, note = _date_from_string(cell, year, month)
    if d:
        return d, note

    if text_hint:
        d, note = _date_from_string(text_hint, year, month)
        if d:
            return d, (note or "дата взята из текста")

    return last_day(year, month), "дата не указана — конец месяца"


def money(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return abs(float(v)) or None
    s = str(v).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    s = re.sub(r"[^\d.]", "", s)
    try:
        return abs(float(s)) or None
    except ValueError:
        return None


def month_blocks(ws):
    starts = []
    for r in range(1, ws.max_row + 1):
        v = ws.cell(row=r, column=1).value
        if not v:
            continue
        m = re.match(r"^([А-ЯЁ]+)\s*(20\d\d)", str(v).strip().upper())
        if m and m.group(1) in MONTHS:
            starts.append((r, MONTHS[m.group(1)], int(m.group(2))))
    for i, (r, month, year) in enumerate(starts):
        end = starts[i + 1][0] - 1 if i + 1 < len(starts) else ws.max_row
        yield r, end, month, year


def parse_block(ws, header_row: int, end_row: int, month: int, year: int):
    out = []
    for r in range(header_row + 4, end_row + 1):
        c = [ws.cell(row=r, column=i).value for i in range(1, 15)]
        if str(c[0] or "").lower().startswith("итог"):
            break
        if not any(c[i] for i in (0, 3, 6, 8, 11)):
            continue

        # взносы
        if c[0] and (money(c[2]) is not None or c[2] == 0):
            d, note = parse_date(c[1], None, year, month)
            out.append(dict(kind="dues", row=r, person=str(c[0]).strip(), date=d,
                            amount=money(c[2]) or 0.0, note=note))

        # расходы студии
        if money(c[5]):
            d, note = parse_date(c[4], c[3], year, month)
            out.append(dict(kind="expense", row=r, date=d, amount=money(c[5]),
                            article=str(c[3] or ""), category=pick(EXPENSE_RULES, str(c[3]), "Прочее"),
                            project=project_of(str(c[3] or "")), note=note))

        # показы: доходы (источник содержит название и дату)
        if money(c[7]):
            src = str(c[6] or "")
            d, note = parse_date(None, src, year, month)
            out.append(dict(kind="show_income", row=r, date=d, amount=money(c[7]),
                            article=src, category=pick(INCOME_RULES, src, "Прочее (доход)"),
                            project=project_of(src), note=note or "дата из источника"))

        # показы: затраты на показ (ранее ошибочно считались доходом/вложениями)
        if money(c[9]):
            item = str(c[8] or "")
            d, note = parse_date(None, item, year, month)
            out.append(dict(kind="show_cost", row=r, date=d, amount=money(c[9]),
                            article=item, category=pick(EXPENSE_RULES, item, "Прочее"),
                            project=project_of(item), note=note or "затраты на показ"))

        # субаренда
        if money(c[13]):
            d, note = parse_date(c[12], c[11], year, month)
            out.append(dict(kind="sublease", row=r, date=d, amount=money(c[13]),
                            article=str(c[11] or ""), category="Субаренда", note=note))
    return out


def ensure_people(conn, names) -> dict[str, int]:
    mapping = {}
    for name in sorted(n for n in names if n):
        norm = name.strip().lower().replace("ё", "е")
        row = conn.execute(
            """select p.id from participant p
               left join person_alias a on a.participant_id = p.id
               where lower(p.display_name) = %s or a.alias = %s limit 1""",
            (name.strip().lower(), norm)).fetchone()
        if row:
            mapping[name] = row["id"]
            continue
        created = conn.execute(
            """insert into participant (tg_chat_id, display_name, person_key, role, status, is_active)
               values (null,%s,%s,'troupe','pending',true) returning id""",
            (name.strip(), norm.split()[0])).fetchone()
        mapping[name] = created["id"]
        conn.execute("""insert into person_alias (participant_id, alias, alias_kind, source)
                        values (%s,%s,'name','import') on conflict (alias) do nothing""",
                     (created["id"], norm))
    return mapping


def ensure_categories(conn) -> dict[str, int]:
    cats = {}
    for name, direction in [("Аренда помещения", "expense"), ("Коммунальные платежи", "expense"),
                            ("Ремонт и содержание", "expense"), ("Тренинг и занятия", "expense"),
                            ("Тренинги и занятия", "expense"), ("Гонорары и доли", "expense"),
                            ("Закупки к спектаклям", "expense"), ("Реклама и полиграфия", "expense"),
                            ("Фото/видео", "expense"), ("Расходники и хозтовары", "expense"),
                            ("Прочее", "expense"), ("Продажа билетов", "income"),
                            ("Курсы", "income"), ("Субаренда", "income"),
                            ("Взносы студийцев", "income"), ("Возвраты", "income"),
                            ("Прочее (доход)", "income")]:
        row = conn.execute("""insert into category (name, direction) values (%s,%s)
                              on conflict (name) do nothing returning id""", (name, direction)).fetchone()
        if not row:
            row = conn.execute("select id from category where name=%s", (name,)).fetchone()
        cats[name] = row["id"]
    return cats


def ensure_project(conn, name: str | None) -> int | None:
    if not name:
        return None
    row = conn.execute("select id from project where name=%s", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("insert into project (name) values (%s) returning id", (name,)).fetchone()["id"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    db_guard.add_argument(ap)
    args = ap.parse_args()
    dry = not args.apply
    if args.apply:
        db_guard.assert_writable(DB, allow=args.allow_dev_write, script="import_finance")

    wb = openpyxl.load_workbook(SOURCE, data_only=True)
    ws = wb[SHEET]
    records = []
    for header_row, end_row, month, year in month_blocks(ws):
        if year == 2025 and month == 8:
            print(f"  ! r{header_row}: «2025» в заголовке — трактую как 2026")
            year = 2026
        if (year, month) not in TARGET:
            continue
        recs = parse_block(ws, header_row, end_row, month, year)
        for rec in recs:
            rec["period"] = f"{year}-{month:02d}"
        records.extend(recs)
        kinds = {}
        for rec in recs:
            kinds[rec["kind"]] = kinds.get(rec["kind"], 0) + 1
        print(f"  {rec['period']}: {len(recs)} записей {kinds}")

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        conn.execute("alter table participant alter column tg_chat_id drop not null")
        cats = ensure_categories(conn)
        pmap = ensure_people(conn, {rec["person"] for rec in records if rec.get("person")})

        periods = sorted({rec["period"] for rec in records})
        for p in periods:
            conn.execute("""insert into budget_period (period, opened_at, quality)
                            values (%s,%s,%s) on conflict (period) do update
                              set quality = excluded.quality""",
                         (p, f"{p}-01", "clean" if p >= "2026-09" else "draft"))

        # чистая перезаливка импортированных строк за период
        conn.execute("""delete from dues_charge where period = any(%s)""", (periods,))
        conn.execute("""delete from budget_tx where origin='import' and period = any(%s)""", (periods,))

        stats = {}
        for rec in records:
            kind = rec["kind"]
            if kind == "dues":
                pid = pmap.get(rec["person"])
                if not pid:
                    continue
                conn.execute("""
                    insert into dues_charge (participant_id, period, amount_due, amount_paid, status)
                    values (%s,%s,%s,%s,%s)
                    on conflict (participant_id, period) do update
                      set amount_due = excluded.amount_due, amount_paid = excluded.amount_paid""",
                    (pid, rec["period"], rec["amount"], rec["amount"],
                     "paid" if rec["amount"] else "due"))
            else:
                tx_type = "income" if kind in ("show_income", "sublease") else "expense"
                pid = pmap.get(rec["person"]) if rec.get("person") else None
                conn.execute("""
                    insert into budget_tx (period, block, tx_date, tx_type, amount, category_id,
                        project_id, counterparty, participant_id, article_raw, purpose, status, origin)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed','import')""",
                    (rec["period"], kind, rec["date"], tx_type, rec["amount"],
                     cats.get(rec.get("category"), cats["Прочее"]),
                     ensure_project(conn, rec.get("project")), None, pid,
                     rec.get("article"),
                     (rec.get("article") or "") + (f" [{rec['note']}]" if rec.get("note") else "")))
            stats[kind] = stats.get(kind, 0) + 1

        print("\nимпортировано:", stats)
        print("\nсверка по месяцам (расход = студия + показы):")
        dues_by = {d["period"]: float(d["s"]) for d in conn.execute(
            "select period, sum(amount_due) s from dues_charge where period = any(%s) group by period",
            (periods,)).fetchall()}
        rows = conn.execute("""
            select period,
              coalesce(sum(amount) filter (where block='expense'),0) studio,
              coalesce(sum(amount) filter (where block='show_cost'),0) shows,
              coalesce(sum(amount) filter (where block='show_income'),0) ticket,
              coalesce(sum(amount) filter (where block='sublease'),0) sub
            from budget_tx where period = any(%s) group by period order by period""",
            (periods,)).fetchall()
        print(f"{'период':8} {'взносы':>9} {'студия':>10} {'показы':>9} {'билеты':>9} {'субар':>8} {'итог':>11}")
        for r in rows:
            dues = dues_by.get(r["period"], 0.0)
            income = dues + float(r["ticket"]) + float(r["sub"])
            expense = float(r["studio"]) + float(r["shows"])
            print(f"{r['period']:8} {dues:>9.0f} {float(r['studio']):>10.0f} "
                  f"{float(r['shows']):>9.0f} {float(r['ticket']):>9.0f} {float(r['sub']):>8.0f} "
                  f"{income-expense:>11.0f}")

        if dry:
            conn.rollback()
            print("\nDRY-RUN: откатано")
        else:
            conn.commit()
            print("\nзаписано в БД")
    wb.close()


if __name__ == "__main__":
    main()
