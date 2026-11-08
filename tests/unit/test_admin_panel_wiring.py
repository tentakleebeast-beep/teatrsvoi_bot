"""Каждая кнопка админ-панели должна иметь обработчик (ревью 27.10.2026).

Проверяем статически, потому что гейтвей не поднимается в unit-тестах:
собираем callback_data из panel_rows(caps) и сверяем с обработчиками.
Обоснование (Ш-51): файл создан в efef464 fix: кнопки, ошибки API и админ-панель; инвайт-коды; сборка контейнеров.
"""

import pathlib
import re

import gateway_modules

GATEWAY = (pathlib.Path(__file__).resolve().parents[2]
           / "services" / "tg-gateway" / "app" / "main.py")


def _source() -> str:
    return gateway_modules.source()


def _keyboards() -> str:
    return gateway_modules.source("keyboards")


def _root_buttons(source: str) -> set[str]:
    start = source.index("def panel_rows(")
    end = source.index("def menu_groups(", start)
    block = source[start:end]
    return set(re.findall(r'"((?:panel|inv|rec):[a-z:]+)"', block))


def test_все_кнопки_корня_имеют_обработчик():
    source = _source()
    buttons = _root_buttons(_keyboards())
    assert buttons, "в panel_rows не найдено кнопок"
    missing = [b for b in buttons
               if f'F.data == "{b}"' not in source
               and f'F.data.startswith("{b}")' not in source]
    assert not missing, "кнопки без обработчика: " + ", ".join(sorted(missing))


def test_админ_панель_содержит_инвайт_и_обратную_связь():
    buttons = _root_buttons(_keyboards())
    assert "panel:invite" in buttons, "в админ-панели нет кнопки выпуска кода"
    assert "panel:feedback" in buttons, "в админ-панели нет кнопки обратной связи"


def test_шаг_выбора_роли_ведёт_на_выпуск_кода():
    source = _source()
    assert 'F.data.startswith("inv:role:")' in source
    assert '"/admin/invite"' in source
    # контракт API: поле ttl_days, а не days (иначе срок молча берётся по умолчанию)
    assert '"ttl_days": 7' in source
    assert '"days": 7' not in source
