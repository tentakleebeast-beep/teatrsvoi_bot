"""Обращение №34: «Весь день» в форме занятости не записывался.

Причина: `int(start or 8)` считал полночь «не заданной» и подставлял 08:00,
поэтому интервал 00–24 выглядел рабочим, а API отвечал 400 outside_work_day.
Тесты держат класс ошибки: 0 — законное значение, а не отсутствие.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"
sys.path.insert(0, str(GATEWAY_APP))

import gateway_modules
from availability_hours import outside_work_day  # noqa: E402


def test_34_midnight_is_a_real_hour():
    assert outside_work_day(0, 24) is True
    assert outside_work_day(0, 8) is True
    assert outside_work_day(8, 24) is False


def test_34_default_work_day_inside():
    assert outside_work_day(8, 23) is False
    assert outside_work_day(23, 24) is False
    assert outside_work_day(18, 24) is False
    assert outside_work_day(5, 9) is True


def test_34_form_does_not_use_falsy_fallback():
    source = gateway_modules.source("routers.availability")
    assert "int(start or 8)" not in source
    assert "int(end or 24)" not in source
    assert "outside_work_day(int(start), int(end))" in source
