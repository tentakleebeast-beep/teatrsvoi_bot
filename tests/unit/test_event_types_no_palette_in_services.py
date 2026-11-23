"""Один источник правды: в `services/` нет словарей типов и палитры (190 §8, I-1).

Обоснование: FR-D3, `docs/development/190` §8 инвариант I-1, AC-4.
До карты тип и цвет были зашиты в три словаря кода (`core.KIND_TITLES_RU`,
`core.EVENT_COLORS`, локальный `kind_titles` в `assign.describe`), а цвета —
ещё и в `config/calendar-palette.yaml`. Теперь словарь один — таблица
`calendar_event_type`, и продуктовые модули YAML не читают.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"


def _sources() -> list[pathlib.Path]:
    return [path for path in SERVICES.rglob("*")
            if path.suffix in (".py", ".js", ".css", ".html") and path.is_file()]


def test_в_коде_нет_словарей_типов_и_цветов() -> None:
    """AC-4: `KIND_TITLES_RU` и `EVENT_COLORS` удалены, а не переименованы."""
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for marker in ("KIND_TITLES_RU", "EVENT_COLORS"):
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}: {marker}")
    assert not offenders, "словари вернулись в код: " + "; ".join(offenders)


def test_продуктовые_модули_не_читают_палитру_yaml() -> None:
    """Палитра — вход скрипта заполнения черновика, не продуктового кода (I-1)."""
    offenders = [str(path.relative_to(ROOT)) for path in _sources()
                 if "calendar-palette" in path.read_text(encoding="utf-8")]
    assert not offenders, "сервис читает палитру-файл: " + ", ".join(offenders)


def test_типы_читаются_только_из_таблицы() -> None:
    """Единственный модуль с засевом названий и цветов — `event_types`, и он не литерал.

    Проверяется форма: модуль читает `calendar_event_type`, а не словарь в коде.
    """
    text = (ROOT / "services" / "tool-api" / "app" / "event_types.py").read_text(encoding="utf-8")
    assert "from calendar_event_type" in text
    assert "CALENDAR_COLORS" in text, "палитра названий цветов пропала из одного места"
    for code in ('"rental": "Аренда"', "'rental': 'Аренда'"):
        assert code not in text, "в модуле появился литеральный словарь типов"
