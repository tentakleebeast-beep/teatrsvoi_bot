"""Ш-37: повтор записи в лист доступен человеку из бота.

Эндпоинт `POST /tools/budget/sheet-retry` был, но вызвать его было нечем.
Теперь есть команда `/sheet-retry`, кнопка «Дописать в лист» на экране «Бюджет»
и текст ответа «дописано N, не удалось M, осталось M».
"""

import importlib.util
import pathlib
import sys

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "services" / "tg-gateway"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


def test_ответ_считает_дописанные_и_оставшиеся(gw):
    res = {"ok": True, "attempted": 3, "written": [1, 2], "already": [],
           "failed": [{"id": 3, "error": "quota exceeded"}], "remaining": 1}
    text = gw.sheet_retry_text(res)
    assert "Дописано: 2" in text
    assert "не удалось: 1" in text
    assert "осталось: 1" in text
    assert "№3: quota exceeded" in text


def test_ответ_упоминает_уже_записанные(gw):
    res = {"ok": True, "attempted": 2, "written": [], "already": [7, 8],
           "failed": [], "remaining": 0}
    text = gw.sheet_retry_text(res)
    assert "подтверждены без повтора: 2" in text
    assert "№7" not in text


def test_пустая_очередь_говорит_об_этом_словами(gw):
    res = {"ok": True, "attempted": 0, "written": [], "already": [],
           "failed": [], "remaining": 0}
    assert "Незаписанных строк нет" in gw.sheet_retry_text(res)


def test_ошибки_листа_передаются_словами(gw):
    assert "не настроен" in gw.sheet_retry_text({"ok": False, "error": "no_spreadsheet"})
    assert "не прошёл: timeout" in gw.sheet_retry_text(
        {"ok": False, "error": "timeout"})


def test_дописать_в_лист_это_кнопка_панели_а_не_команда():
    """Правило заказчика: админские действия — кнопками, команд для ввода нет."""
    source = gateway_modules.source()
    assert 'Command("sheet-retry")' not in source
    assert '"/tools/budget/sheet-retry"' in source
    assert 'F.data == "panel:sheet_retry"' in source, "кнопка без обработчика"
    # подпись одна на все места — константа texts.BTN_SHEET_RETRY
    assert "(texts.BTN_SHEET_RETRY, \"panel:sheet_retry\")" in source
