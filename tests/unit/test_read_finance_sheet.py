"""Обратный путь «лист → БД»: разбор человеческого листа и сверка по ID (FR-G13)."""

from __future__ import annotations

import pathlib
import sys
from datetime import date
from decimal import Decimal

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mod():
    sys.path.insert(0, str(ROOT / "scripts"))
    import read_finance_sheet as read

    yield read


def row(*, dues=None, expense=None, income=None, invest=None, sublease=None,
        cost=None, tx_id="") -> list[str]:
    """Строка листа: A–C взносы, D–F расходы, G–H доходы, I–J вложения,
    L–N субаренда, O–Q вложения показа, R — ID операции."""
    values = [""] * 18
    for segment, cols in ((dues, (0, 1, 2)), (expense, (3, 4, 5)),
                          (income, (6, None, 7)), (invest, (8, None, 9)),
                          (sublease, (11, 12, 13)), (cost, (14, 15, 16))):
        if segment is None:
            continue
        text, day, amount = segment
        values[cols[0]] = text or ""
        if cols[1] is not None:
            values[cols[1]] = day or ""
        values[cols[2]] = amount if amount is not None else ""
    values[17] = tx_id
    return values


def sheet(data: list[list[str]], totals: list[str] | None = None) -> list[list[str]]:
    return [["ОКТЯБРЬ 2026"], ["Ежемесячная жизнь студии"], ["Взносы студийцев"],
            ["Фамилия Имя", "Дата", "Факт"], *data,
            totals or ["Итог месяца (ноябрь)", "", "92000"]]


# ---------------------------------------------------------------- разбор
def test_границы_блока_месяца(mod):
    values = sheet([row(dues=("Иванов Виктор", "", 10000))])
    data_start, totals = mod.month_block(values, "2026-10")
    assert data_start == 4          # первая строка данных, нумерация с нуля
    assert totals == 5


def test_нет_блока_месяца_останавливает_разбор(mod):
    with pytest.raises(SystemExit):
        mod.month_block([["НОЯБРЬ 2026"]], "2026-10")


@pytest.mark.parametrize("raw,period,expected", [
    ("22.10", "2026-10", date(2026, 10, 22)),
    ("28.10.26", "2026-10", date(2026, 10, 28)),
    ("26,10", "2026-10", date(2026, 10, 26)),
    ("6", "2026-10", date(2026, 10, 6)),
    ("26,1", "2026-10", date(2026, 1, 26)),      # январь того же года
    ("за месяц", "2026-10", None),
    ("6, 13, 20, 27", "2026-10", None),          # несколько дней — не угадываем
    ("7 бутылок", "2026-10", None),
    ("", "2026-10", None),
])
def test_разбор_дня(mod, raw, period, expected):
    assert mod.parse_day(raw, period) == expected


def test_оба_раздела_вложений_сводятся_к_блоку_show_cost(mod):
    """I–J «Вложения» и O–Q «мелкие вложения» — один блок budget_tx.show_cost."""
    segments = mod.row_segments(row(invest=("21% Крылову", None, 9000),
                                    cost=("лампы Эдисона", "22.10", 589)),
                                88, "2026-10")
    by_kind = {segment["kind"]: segment for segment in segments}
    assert set(by_kind) == {"invest", "show_cost"}
    assert {segment["block"] for segment in segments} == {"show_cost"}
    assert by_kind["invest"]["section"] == "вложения"
    assert by_kind["show_cost"]["section"] == "мелкие вложения"


def test_строка_бота_разбирается_по_разделам(mod):
    segments = mod.row_segments(row(expense=("Аренда Прачки", "6", 94000),
                                    tx_id="bot:12"), 88, "2026-10")
    assert len(segments) == 1
    segment = segments[0]
    assert segment["kind"] == "expense"
    assert segment["amount"] == 94000
    assert segment["day"] == date(2026, 10, 6)
    assert segment["block"] == "expense"
    assert mod.id_of(segment) == 12


def test_ручная_строка_с_несколькими_разделами(mod):
    segments = mod.row_segments(row(dues=("Иванов Виктор", "", 10000),
                                    expense=("Тренинги Крылов", "6, 13", 40000),
                                    income=("Бунин 16.10", "", 17848)), 86, "2026-10")
    assert {s["kind"] for s in segments} == {"dues", "expense", "show_income"}
    assert all(s["tx_id"] is None for s in segments)


def test_несколько_сумм_с_одним_id_не_сверяются_молча(mod):
    segments = mod.row_segments(row(dues=("Виктор", "", 10000),
                                    expense=("Аренда", "6", 40000), tx_id="bot:5"), 86,
                                "2026-10")
    assert all(s.get("ambiguous_id") for s in segments)
    assert all(mod.id_of(s) is None for s in segments)


# ---------------------------------------------------------------- сверка
def db(tx_id, amount=40000, tx_date=date(2026, 10, 6), block="expense",
       purpose="Аренда Прачки", written=True) -> dict:
    return {"id": tx_id, "amount": Decimal(str(amount)), "tx_date": tx_date,
            "block": block, "purpose": purpose, "status": "confirmed",
            "sheet_written_at": "2026-10-06T10:00:00" if written else None}


def test_правка_суммы_и_даты_по_id_становится_обновлением(mod):
    sheet_ops = mod.sheet_operations(
        sheet([row(expense=("Аренда Прачки", "7", 50000), tx_id="bot:12")]), "2026-10")
    report = mod.build_report(sheet_ops, [db(12)], "2026-10")
    assert report["к_обновлению"] == 1
    change = report["обновления"][0]["change"]
    assert change["amount"] == {"было": 40000.0, "стало": 50000.0}
    assert change["tx_date"] == {"было": "2026-10-06", "стало": "2026-10-07"}


def test_совпадающая_строка_не_попадает_в_обновления(mod):
    sheet_ops = mod.sheet_operations(
        sheet([row(expense=("Аренда Прачки", "6", 40000), tx_id="bot:12")]), "2026-10")
    report = mod.build_report(sheet_ops, [db(12)], "2026-10")
    assert report["обновления"] == []
    assert report["совпало"] == 1


def test_ручные_строки_только_в_отчёте(mod):
    sheet_ops = mod.sheet_operations(
        sheet([row(dues=("Иванов Виктор", "", 10000))]), "2026-10")
    report = mod.build_report(sheet_ops, [], "2026-10")
    assert report["ручных"] == 1
    assert report["обновления"] == []
    assert report["ручные"][0]["text"] == "Иванов Виктор"


def test_id_в_листе_без_строки_в_бд_это_ошибка(mod):
    sheet_ops = mod.sheet_operations(
        sheet([row(expense=("Аренда", "6", 40000), tx_id="bot:99")]), "2026-10")
    report = mod.build_report(sheet_ops, [db(12)], "2026-10")
    assert report["ошибки"][0]["id"] == 99
    assert report["обновления"] == []


def test_удалённая_человеком_строка_только_отмечается(mod):
    sheet_ops = mod.sheet_operations(sheet([]), "2026-10")
    report = mod.build_report(sheet_ops, [db(12)], "2026-10")
    assert report["ids_удалены_в_листе"] == [12]
    # строка, до листа не дошедшая, удалённой не считается
    report = mod.build_report(sheet_ops, [db(13, written=False)], "2026-10")
    assert report["ids_удалены_в_листе"] == []


def test_смена_раздела_и_текста_только_предупреждение(mod):
    sheet_ops = mod.sheet_operations(
        sheet([row(cost=("Пропись света", "6", 40000), tx_id="bot:12")]), "2026-10")
    report = mod.build_report(sheet_ops, [db(12)], "2026-10")
    kinds = {item["what"] for item in report["расхождения"]}
    assert kinds == {"раздел", "текст"}
    assert report["обновления"] == []


# ---------------------------------------------------------------- запись
def test_dry_run_по_умолчанию(mod):
    args = mod.build_parser().parse_args(["--period", "2026-10"])
    assert args.apply is False


def test_запись_меняет_только_сумму_дату_и_отметку(mod):
    class Conn:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((sql, params))

    conn = Conn()
    updates = [{"id": 12, "row": 88, "change": {
        "amount": {"было": 40000.0, "стало": 50000.0},
        "tx_date": {"было": "2026-10-06", "стало": "2026-10-07"}}}]
    assert mod.apply_updates(updates, conn) == 1
    sql, params = conn.calls[0]
    assert "amount = %s" in sql and "tx_date = %s" in sql and "period = %s" in sql
    assert params == [50000.0, date(2026, 10, 7), "2026-10", 12]
