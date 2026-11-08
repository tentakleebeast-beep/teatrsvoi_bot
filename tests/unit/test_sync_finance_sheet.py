"""Раскладка операций в человеческий лист: два раздела «Вложения» (Ш-31)."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mod():
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_finance_sheet as sync

    yield sync


def op(kind: str, **extra) -> dict:
    row = {"id": 7, "kind": kind, "day": "22,10", "amount": 1000.0,
           "text": "21% Крылову", "who": "Иванов Виктор"}
    row.update(extra)
    # ключ строится по правилу to_rows(): вложения — «сумма+текст», остальные — «день+сумма»
    if kind in ("income", "cost"):
        row["key"] = f"{kind}|{round(float(row['amount']), 2)}|{row['text'][:20].lower()}"
    else:
        row["key"] = f"{kind}|{row['day'].split(',')[0]}|{round(float(row['amount']), 2)}"
    return row


def ranges(updates: list[dict]) -> list[str]:
    return [item["range"].split("!")[1] for item in updates]


def test_затраты_показов_идут_во_вложения_I_J(mod):
    """Проверено по листу: в сентябре и октябре доли и закупки стоят в I–J."""
    updates = mod.cell_updates(op("cost"), 96)
    assert ranges(updates) == ["I96:J96"]
    assert updates[0]["values"] == [["21% Крылову", 1000.0]]


@pytest.mark.parametrize("kind,expected", [
    ("dues", "A96:C96"),
    ("expense", "D96:F96"),
    ("income", "G96:H96"),
    ("cost", "I96:J96"),
    ("sublease", "L96:N96"),
])
def test_разделы_листа_не_пересекаются(mod, kind, expected):
    assert ranges(mod.cell_updates(op(kind), 96)) == [expected]


@pytest.mark.parametrize("block", ["dues", "expense", "show_income", "show_cost", "sublease"])
def test_каждый_блок_БД_получает_диапазон_листа(mod, block):
    from datetime import date

    rows = mod.to_rows([{"id": 1, "tx_date": date(2026, 10, 22), "amount": 1000,
                         "block": block, "purpose": "тест", "counterparty": None,
                         "category": None, "project": None, "participant": None}])
    assert len(rows) == 1
    assert rows[0]["kind"] in mod.BLOCK_RANGES


def test_второй_раздел_вложений_O_Q_бот_не_трогает(mod):
    """O–Q — ручной раздел мелких закупок; он не должен попасть в запись."""
    written = {value.split(":")[0] for value in mod.BLOCK_RANGES.values()}
    assert "O" not in written and "Q" not in written


# --- Ш-40: ID-дедупликация по колонке R и сверка вложений I–J / O–Q ---


def sheet_row(cells: dict[int, object]) -> list[str]:
    """Строка листа по индексам колонок (0 = A)."""
    width = max(cells) + 1 if cells else 1
    return [str(cells.get(index, "")) for index in range(width)]


def test_диапазон_чтения_дотягивается_до_колонки_ID(mod):
    """Раньше читали A1:Q400 — колонки R в ответе не было и `sheet_ids()` был пуст."""
    letters = mod.VALUES_RANGE.rsplit("!", 1)[1].split(":")[1]
    column = "".join(ch for ch in letters if ch.isalpha())
    index = 0
    for ch in column:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    assert index - 1 == mod.ID_COLUMN


def test_bot_id_в_колонке_R_отсекает_повтор(mod):
    values = [sheet_row({8: "21% Крылову", 9: "1000", 17: "bot:7"}), sheet_row({})]
    assert mod.sheet_ids(values, 0, 1) == {"bot:7"}
    assert mod.pending([op("cost")], values, 0, 1) == []


def test_строка_вложений_I_J_не_переносится_повторно(mod):
    """В листе уже есть та же статья и сумма в I–J — второй раз не пишем."""
    values = [sheet_row({8: "21% Крылову", 9: "1 000,00"}), sheet_row({})]
    assert mod.pending([op("cost")], values, 0, 1) == []
    other = op("cost", amount=2000.0)
    assert len(mod.pending([other], values, 0, 1)) == 1


def test_мелкие_вложения_O_Q_не_переносятся_повторно(mod):
    """O–Q — ручной раздел мелких закупок, но сверка по «сумма+текст» его видит."""
    values = [sheet_row({14: "Клеммы", 15: "22,10", 16: "1000"}), sheet_row({})]
    assert mod.pending([op("cost", text="Клеммы")], values, 0, 1) == []


def test_расходы_и_доходы_по_прежним_ключам(mod):
    values = [sheet_row({3: "Аренда", 4: "22,10", 5: "1000"}),
              sheet_row({6: "Показ", 7: "1000"}), sheet_row({})]
    keys = mod.sheet_operations(values, 0, 2)
    assert "expense|22|1000.0" in keys
    assert "income|1000.0|показ" in keys
