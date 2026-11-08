"""
Ш-21 (обращение №16): аудит дня моноширинной таблицей.

Было: строка «время действие» — не видно, кто и что сделал.
Проверяем колонки, инициатора, объект, результат, экранирование и обрезку.
"""

import importlib.util
import pathlib
import sys

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "services" / "tg-gateway"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


ROWS = [
    {"ts": "2026-10-28T14:32:11+04:00", "actor_type": "human", "actor_id": 7,
     "actor_name": "Виктор", "action": "budget.tx.create", "entity_type": "budget_tx",
     "entity_id": "41", "after": {"amount": 5000, "type": "expense"}},
    {"ts": "2026-10-28T14:35:00+04:00", "actor_type": "bot", "actor_id": None,
     "actor_name": None, "action": "access.denied", "entity_type": "capability",
     "entity_id": "audit.view", "after": None},
    {"ts": "2026-10-28T14:36:00+04:00", "actor_type": "system", "actor_id": None,
     "actor_name": None, "action": "invite.expire", "entity_type": "invite_code",
     "entity_id": None, "after": None},
]


def lines_of(gw, rows, limit=3500):
    text = gw.audit_table(rows, limit=limit)
    assert text.startswith("<pre>") and text.endswith("</pre>")
    return text[len("<pre>"):-len("</pre>")].split("\n")


def test_шапка_содержит_пять_колонок(gw):
    header = lines_of(gw, ROWS)[0]
    for column in ("ВРЕМЯ", "КТО", "ДЕЙСТВИЕ", "ОБЪЕКТ", "РЕЗУЛЬТАТ"):
        assert column in header
    assert header.index("ВРЕМЯ") < header.index("КТО") < header.index("ДЕЙСТВИЕ")


def test_строка_несёт_инициатора_объект_и_итог(gw):
    row = lines_of(gw, ROWS)[2]
    assert "28.10 14:32" in row
    assert "Виктор" in row
    assert "budget.tx.create" in row
    assert "budget_tx#41" in row
    assert "amount=5000" in row


def test_бот_и_система_подписаны_словами(gw):
    rows = lines_of(gw, ROWS)
    assert "бот" in rows[3]
    assert "система" in rows[4]


def test_отказ_и_отсутствие_итога(gw):
    assert gw.audit_result(ROWS[1]) == "отказ"
    assert gw.audit_result(ROWS[2]) == "выполнено"


def test_объект_без_id_без_решётки(gw):
    assert "invite_code" in lines_of(gw, ROWS)[4]
    assert "invite_code#" not in lines_of(gw, ROWS)[4]


def test_html_экранируется(gw):
    rows = [{"ts": "2026-10-28T10:00:00", "actor_type": "human",
             "actor_name": "<Виктор & Ко>", "action": "profile.updated",
             "entity_type": "participant", "entity_id": "7", "after": None}]
    text = gw.audit_table(rows)
    assert "&lt;Виктор &amp; Ко&gt;" in text
    assert "<Виктор" not in text


def test_лишние_строки_сворачиваются(gw):
    rows = ROWS * 40
    rows = lines_of(gw, rows)
    assert rows[-1].startswith("… ещё ") or len(rows) < 50


def test_обрезка_по_лимиту(gw):
    rows = lines_of(gw, ROWS, limit=200)
    assert len(rows) < len(ROWS) + 3
    assert "… ещё" in rows[-1]
