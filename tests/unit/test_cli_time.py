"""Одна функция времени Самары в `scripts/_cli.py` (INT-012).

До правки время считалось в четырёх скриптах, и копии разошлись: три отдавали
aware-значение, `feedback_queue` — naive. Первое сравнение naive с aware дало бы
`TypeError`, а смена правила сдвига потребовала бы помнить о четырёх местах.
Тест держит правило: сдвиг один (из `TZ_OFFSET_HOURS`, по умолчанию UTC+4),
naive включается явным аргументом, доли секунды обрезаются в обоих режимах.

Решение о фиксированном UTC+4 без перехода на летнее время — 27.10.2026.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import _cli  # noqa: E402 — путь к scripts/ добавляется выше


def test_время_самары_aware_и_без_долей_секунды(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ_OFFSET_HOURS", "4")
    moment = _cli.now_local()
    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(hours=4)
    assert moment.microsecond == 0


def test_naive_включается_явным_параметром(monkeypatch: pytest.MonkeyPatch) -> None:
    """Файл вопросов `docs/reports/74-questions.md` ждёт дату без зоны."""
    monkeypatch.setenv("TZ_OFFSET_HOURS", "4")
    moment = _cli.now_local(naive=True)
    assert moment.tzinfo is None
    assert moment.utcoffset() is None
    assert moment.microsecond == 0


def test_сдвиг_берётся_из_окружения(monkeypatch: pytest.MonkeyPatch) -> None:
    """Правило сдвига живёт в одном месте: переменная меняет все четыре скрипта."""
    monkeypatch.setenv("TZ_OFFSET_HOURS", "7")
    assert _cli.now_local().utcoffset() == timedelta(hours=7)
