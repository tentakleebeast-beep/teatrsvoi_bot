"""Время через точку — то же, что через двоеточие (обращение №45).

Границы обращения: только часы и минуты. «18.30» равно «18:30», «10,30» тоже;
«10.3» — не время: минуты только двузначные; секунды, зоны и даты не разбираются.
Проверяются все места, где время набирает человек, — иначе точка принимается на
одном экране и по-прежнему отказывает на соседнем, и это снова загадка.

    time.time()  — серверная запись тихих часов (`core.clock_text`);
    parse_interval — интервал занятости в чате (`rules.py`);
    parse_quiet_hours — команда `/quiet` (`routers/menu.py`);
    parse_when — перенос события в чате (`routers/events.py`);
    clock() — общий JS-помощник экранов Mini App.

Обоснование: обращение №45; FR-G3 (тихие часы), FR-D3 (карточка события),
`docs/development/190` §3; на «Занятости» точка уже принималась.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import date as _date

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app import core  # noqa: E402
from app.pages import admin_notifications, events, misc_common, misc_notifications  # noqa: E402
from app.rules import parse_availability, parse_interval  # noqa: E402

NODE = shutil.which("node") or next(
    (candidate for candidate in ("/opt/homebrew/bin/node", "/usr/local/bin/node")
     if pathlib.Path(candidate).exists()), None)


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


# --- серверная запись тихих часов -------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("20:00", "20:00"),
    ("20.00", "20:00"),
    ("20,00", "20:00"),
    ("9.05", "09:05"),
    ("9", "09:00"),
    (" 21.30 ", "21:30"),
])
def test_время_принимается_через_точку_и_запятую(value, expected) -> None:
    """`clock_text` приводит ввод человека к канонической паре «ЧЧ:ММ»."""
    assert core.clock_text(value) == expected


@pytest.mark.parametrize("value", [
    "10.3",        # минуты только двузначные: «10.3» — не время
    "24.00",       # час вне суток
    "20.60",       # минут больше 59
    "20:00:00",    # секунды не разбираются — граница обращения
    "скоро",
    "",
    None,
])
def test_что_не_время_то_не_время(value) -> None:
    """Отказ честный: `None`, а не подставленное «на глаз» значение."""
    assert core.clock_text(value) is None


def test_запись_идёт_канонической_парой() -> None:
    """Точка — вход пользователя, а не формат хранения: в базу идёт «ЧЧ:ММ»."""
    for value in ("20.00", "20,00", "20", "20:00"):
        stored = core.clock_text(value)
        assert stored is not None and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", stored)


# --- интервал занятости в чате ----------------------------------------------

@pytest.mark.parametrize("text,start,end", [
    ("с 10.30 до 14.30", 10, 14),
    ("10.30-14.30", 10, 14),
    ("с 10,30 до 14", 10, 14),
    ("свободен с 18.00 до 23.00", 18, 23),
])
def test_интервал_с_точкой_разбирается(text, start, end) -> None:
    """`parse_interval` понимает точку там, где раньше понимал только двоеточие."""
    res = parse_interval(text)
    assert res["ok"] is True, res
    assert (res["start"], res["end"]) == (start, end)


def test_минуты_вне_диапазона_в_интервале_отклоняются() -> None:
    """«10.75» — не время: иначе человек получает молча чужой интервал."""
    assert parse_interval("с 10.75 до 14")["ok"] is False


@pytest.mark.parametrize("text", [
    "занят 28.10 с 10.30 до 14.30",
    "занят 28.10 с 10:30 до 14:30",
    "занят 28.10 с 10,30 до 14,30",
])
def test_свободный_текст_занятости_с_точкой(text) -> None:
    """Чат «занят 28.10 с 10.30 до 14.30» пишет те же часы, что и с двоеточием."""
    res = parse_availability(text, today=_date(2026, 10, 1))
    assert res["ok"] is True, res
    assert (res["start"], res["end"]) == (10, 14)
    assert res["day"] == _date(2026, 10, 28).isoformat()


# --- тихие часы в чате ------------------------------------------------------

@pytest.mark.parametrize("arg,expected", [
    ("21.00-09.00", ("21:00", "09:00")),
    ("21,00-09,00", ("21:00", "09:00")),
    ("20-8", ("20:00", "08:00")),
    ("21:00-09:00", ("21:00", "09:00")),
])
def test_тихие_часы_с_точкой(gw, arg, expected) -> None:
    """Команда `/quiet 21.00-09.00` пишет то же, что `/quiet 21:00-09:00`."""
    assert gw.parse_quiet_hours(arg) == expected


@pytest.mark.parametrize("arg", ["21.5-09", "21:60-09:00", "21.00-24.00", "кривой"])
def test_кривые_тихие_часы_отклоняются(gw, arg) -> None:
    """Минуты двузначные и от 00 до 59, часы от 0 до 23 — иначе отказ."""
    assert gw.parse_quiet_hours(arg) is None


# --- перенос события в чате -------------------------------------------------


def _today():
    return _date(2026, 11, 1)


@pytest.mark.parametrize("text,expected", [
    ("07.11 19.30", ((11, 7), 20)),
    ("07.11 19:30", ((11, 7), 20)),
    ("завтра 18.30", (None, 19)),
    ("завтра 18:30", (None, 19)),
    ("7 ноября в 19.30", ((11, 7), 20)),
    ("7 ноября в 19.00", ((11, 7), 19)),
])
def test_время_с_точкой_не_съедается_датой(gw, text, expected) -> None:
    """Пара «19.30» — часы и минуты, а не «19-е число 30-го месяца»."""
    day_month, hour = expected
    parsed = gw.parse_when(text, _today())
    assert parsed is not None, f"не разобралось: {text}"
    day, got_hour = parsed
    assert got_hour == hour, f"{text} → {got_hour}, ждали {hour}"
    if day_month is not None:
        assert (day.month, day.day) == day_month


def test_невозможная_дата_всё_ещё_отказ(gw) -> None:
    """Ослабление не открыло дыру: «32.01» — не дата, а не время."""
    assert gw.parse_when("32.01 19.00", _today()) is None


# --- экраны Mini App --------------------------------------------------------

JS_PAGES = {
    "карточка события": events.EVENT_CARD_PAGE,
    "уведомления": misc_notifications.NOTIFICATIONS_PAGE,
    "общие уведомления": admin_notifications.NOTIFICATION_DEFAULTS_PAGE,
}


@pytest.mark.parametrize("name", list(JS_PAGES))
def test_экран_несёт_общую_проверку_времени(name) -> None:
    """Копий одной проверки нет: текст `clock()` приходит из `misc_common`."""
    html = JS_PAGES[name]
    assert "function clock(" in html, f"{name}: нет проверки времени"
    assert misc_common.JS_CLOCK.strip() in html, f"{name}: проверка времени не общая"


def test_карточка_события_называет_оба_формата() -> None:
    """Отказ обязан называть и точку: иначе человек не знает, что делать (№45)."""
    assert "ЧЧ:ММ или ЧЧ.ММ" in events.EVENT_CARD_PAGE


@pytest.mark.parametrize("html", [misc_notifications.NOTIFICATIONS_PAGE,
                                  admin_notifications.NOTIFICATION_DEFAULTS_PAGE])
def test_тихие_часы_подсказывают_формат_с_точкой(html) -> None:
    assert "20:00 или 20.00" in html


@pytest.mark.skipif(NODE is None, reason="нет node для проверки clock()")
@pytest.mark.parametrize("page", [events.EVENT_CARD_PAGE, misc_notifications.NOTIFICATIONS_PAGE,
                                  admin_notifications.NOTIFICATION_DEFAULTS_PAGE])
def test_clock_в_экранах_принимает_точку(tmp_path, page) -> None:
    """Прогоняем настоящую функцию страницы в node, а не сверяем регулярку глазами."""
    script = tmp_path / "clock.js"
    script.write_text(misc_common.JS_CLOCK + "\n"
                      + "var values = JSON.parse(process.argv[2]);\n"
                      + "process.stdout.write(JSON.stringify(values.map(clock)));\n",
                      encoding="utf-8")
    cases = ["18.30", "18,30", "18:30", "18", "9.05", "10.3", "", "25.00", "18.75"]
    result = subprocess.run([NODE, str(script), json.dumps(cases)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:400]
    assert json.loads(result.stdout) == ["18:30", "18:30", "18:30", "18:00", "09:05",
                                         None, "", None, None]
