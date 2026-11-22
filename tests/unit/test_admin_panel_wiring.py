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


def test_админ_панель_без_выпуска_кодов_и_с_обратной_связью():
    """V-118: кнопок кодов и заявок в панели чата нет; обратная связь осталась."""
    buttons = _root_buttons(_keyboards())
    assert "panel:invite" not in buttons, "чатовый выпуск кода вернулся"
    assert "panel:codes" not in buttons
    assert "panel:pending" not in buttons
    assert "panel:feedback" in buttons, "в админ-панели нет кнопки обратной связи"


def test_чатового_шага_выбора_роли_больше_нет():
    """V-118: обработчиков `inv:role:*` и запроса `/admin/invite` из бота нет."""
    source = _source()
    assert 'F.data.startswith("inv:role:")' not in source
    assert '"/admin/invite"' not in source
