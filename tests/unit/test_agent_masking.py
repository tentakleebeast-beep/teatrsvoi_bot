"""Обезличивание аргументов инструментов для журнала разбора (INT-010).

Обоснование: журнал нужен целиком — без аргументов «где модель ушла в сторону» не видно,
но токены, пароли, телефоны и почта в нём оставаться не должны (критерий приёмки карты
A-5: «given запись, when чтение, then в аргументах нет токенов и паролей»). Правило
маскирования и его место в записи журнала описаны в docs/development/108 §6.

Номера и почта в фикстурах вымышленные (+7 917 123 24 18, a.b@mail.ru): проверяется
маска, а не чьи-то контакты.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app import masking  # noqa: E402


def test_секретные_ключи_маскируются_целиком():
    """Ключ похож на секрет — значение не попадает в журнал вообще, любого типа."""
    args = masking.mask_args({"token": "sk-live-123", "password": "qwerty",
                              "api_key": "abc", "Authorization": "Bearer x",
                              "retries": 1})

    assert args["token"] == masking.MASK
    assert args["password"] == masking.MASK
    assert args["api_key"] == masking.MASK
    assert args["Authorization"] == masking.MASK
    assert args["retries"] == 1, "обычные аргументы не трогаем: по ним видно вызов"


def test_персональные_ключи_маскируются_но_видны():
    """Идентификатор и телефон скрыты, но факт вызова с ними остаётся в журнале."""
    args = masking.mask_args({"participant_id": 42, "chat_id": 123456789,
                              "phone": "+7 917 123 24 18", "email": "a@b.ru"})

    assert args["participant_id"] == masking.MASK
    assert args["chat_id"] == masking.MASK
    assert "917" in args["phone"] and "24 18" in args["phone"], args["phone"]
    assert "123" not in args["phone"], "середина номера не должна читаться"
    assert args["email"] == masking.MASK


def test_телефон_и_почта_в_свободном_тексте_маскируются():
    """Человек пишет «оплатил за Сашу, 8917…» — модель передаёт это в цель операции."""
    masked = masking.mask_text("оплатил за Сашу, 8 917 123-24-18, чек на a.b@mail.ru")

    assert "24 18" in masked, "хвост номера остаётся для сверки"
    assert "123" not in masked
    assert "a.b@mail.ru" not in masked
    assert "Сашу" in masked, "имена не маскируются: они и есть предмет разбора"


def test_шаги_сохраняют_имена_инструментов_и_теряют_секреты():
    steps = [{"step": 1, "tool": "read_budget", "ok": True, "latency_ms": 12,
              "args": {"period": "2026-11", "token": "sk-1"}},
             {"step": 2, "tool": "propose_budget_tx", "ok": True, "latency_ms": 30,
              "args": {"amount": 1200, "purpose": "ткань, звонить 89171232418"}}]

    masked = masking.mask_steps(steps)

    assert [step["tool"] for step in masked] == ["read_budget", "propose_budget_tx"]
    assert masked[0]["args"]["period"] == "2026-11"
    assert masked[0]["args"]["token"] == masking.MASK
    assert "89171232418" not in str(masked[1]["args"])
    assert masked[0]["latency_ms"] == 12


def test_вложенность_и_мусор_не_роняют_маску():
    """Аргументы приходят от модели: форма может быть любой."""
    args = masking.mask_args({"filter": {"chat_id": 5, "tags": ["a", 89171232418]},
                              "nested": {"deep": {"secret": "x"}}}, depth=0)

    assert args["filter"]["chat_id"] == masking.MASK
    assert "89171232418" not in str(args["filter"]["tags"])
    assert args["nested"]["deep"]["secret"] == masking.MASK
    assert masking.mask_args(None) is None
    assert masking.mask_args("") == ""
