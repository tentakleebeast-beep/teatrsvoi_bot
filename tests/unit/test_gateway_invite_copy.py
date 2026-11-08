"""Инвайт-код: копирование в буфер (обращение №9)."""

import importlib.util
import pathlib
import sys

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


def test_код_в_code_и_действует_до(gw):
    text = gw.invite_text("SVOI-ABC-123", "2026-11-04T21:00:00+04:00", "troupe")

    assert "<code>SVOI-ABC-123</code>" in text
    assert "актёр труппы" in text
    assert "2026-11-04" in text
    assert "один раз" in text
    assert "&" not in text.replace("&amp;", "")


def test_код_экранируется(gw):
    text = gw.invite_text("A<B&1", None, "org")

    assert "<code>A&lt;B&amp;1</code>" in text
    assert "Использовать можно один раз" in text


def test_кнопка_копирования_и_её_строки(gw):
    markup = gw.copy_code_kb("SVOI-ABC-123",
                             [[("Ещё код", "panel:invite")], [("Назад", "panel:root")]])

    first = markup.inline_keyboard[0][0]
    assert first.text == "Скопировать код"
    assert first.copy_text is not None and first.copy_text.text == "SVOI-ABC-123"
    assert first.callback_data is None, "copy_text-кнопка не может нести callback"
    assert [b.callback_data for row in markup.inline_keyboard[1:] for b in row] == [
        "panel:invite", "panel:root"]
