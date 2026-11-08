"""Guard от записи в рабочую БД (docs/reports/84, карта канбана t_a422101b).

Инцидент 30.10.2026: тестовый прогон писал в dev-базу `svoi`, оставив 177 900 ₽
тестовых денег в балансе и «дубли операций» в инварианте I2.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import db_guard  # noqa: E402 — путь к scripts/ добавляется выше

TEST_DSN = "postgresql://svoi:pass@127.0.0.1:5433/svoi_test_12345"
WORK_DSN = "postgresql://svoi:pass@127.0.0.1:5433/svoi"


def test_db_name_extracts_database() -> None:
    assert db_guard.db_name(TEST_DSN) == "svoi_test_12345"
    assert db_guard.db_name(WORK_DSN) == "svoi"


def test_test_db_is_writable_without_flag() -> None:
    db_guard.assert_writable(TEST_DSN, script="test")  # не должно бросить


def test_work_db_is_refused_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(db_guard.ALLOW_ENV, raising=False)
    with pytest.raises(SystemExit) as exc:
        db_guard.assert_writable(WORK_DSN, script="import_finance")
    message = str(exc.value)
    assert "svoi" in message and "запрещена" in message
    assert "--allow-dev-write" in message


def test_work_db_allowed_by_explicit_flag() -> None:
    db_guard.assert_writable(WORK_DSN, allow=True, script="test")  # не должно бросить


def test_work_db_allowed_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(db_guard.ALLOW_ENV, "1")
    db_guard.assert_writable(WORK_DSN, script="test")  # не должно бросить


def test_unknown_db_is_not_guessed() -> None:
    # незнакомое имя базы guard пропускает: решает вызывающий код, а не догадка
    db_guard.assert_writable("postgresql://u:p@h:5433/other_db", script="test")
