"""Инцидент 28.10.2026: бот зацикливался в форме занятости и не выпускал из неё.

Проверяем два свойства обработчика `av_time_text`:
1. интервал разбирается маршрутом без даты (`parse-interval`), а не общим `parse`;
2. при неудаче состояние очищается, а кнопки меню работают всегда.
Обоснование (Ш-51): файл создан в b35986c fix(gateway,api): форма занятости не зацикливается и всегда выпускает человека (INT-004).
"""

import pathlib
import re

import gateway_modules

GATEWAY = pathlib.Path(__file__).resolve().parents[2] / "services" / "tg-gateway" / "app" / "routers" / "availability.py"


def _av_time_text() -> str:
    source = GATEWAY.read_text(encoding="utf-8")
    match = re.search(r"async def av_time_text.*?(?=\n@router|\Z)", source, re.S)
    assert match, "обработчик av_time_text не найден"
    return match.group(0)


def test_форма_зовёт_разбор_интервала_без_даты():
    body = _av_time_text()
    assert "/tools/availability/parse-interval" in body
    assert '"/tools/availability/parse"' not in body


def test_при_неудаче_состояние_очищается():
    body = _av_time_text()
    failure = body.split("if not res.get(\"ok\"):")[1]
    assert "state.clear()" in failure
    assert "Попробуйте ещё раз" in failure


def test_кнопки_меню_работают_в_форме_времени():
    body = _av_time_text()
    assert "MENU_SURFACE" in body
    assert "menu_action(" in body


def test_все_состояния_формы_имеют_выход_в_меню():
    """Инвариант: каждый обработчик состояния формы пропускает кнопки меню."""
    source = gateway_modules.source()
    handlers = re.findall(
        r"@router\.message\((?:AvailForm|BudgetForm|Reg)\.[\w.]+, F\.text\)\s*\n"
        r"async def (\w+)\(.*?(?=\n@router|\Z)", source, re.S)
    assert handlers, "обработчики состояний не найдены"
    missing = []
    for name in handlers:
        block = re.search(rf"async def {name}\(.*?(?=\n@router|\Z)", source, re.S).group(0)
        if "MENU_SURFACE" not in block and "MENU_ACTIONS" not in block and "/start" not in block:
            missing.append(name)
    assert not missing, f"нет выхода в меню из состояний: {missing}"
