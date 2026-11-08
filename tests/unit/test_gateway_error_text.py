"""Гейтвей: код ошибки и текст для человека из ответа tool-api (docs/reports/84, E-04/E-07).

До конверта гейтвей читал `detail` и `error` вперемешку и показывал человеку `slot_taken`.
Теперь логика ветвится по `error_code`, а в сообщение попадает `error_text`.
"""

from __future__ import annotations

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


def test_код_из_нового_конверта(gw):
    assert gw.error_code({"code": "slot_taken", "detail": "Окно занято"}) == "slot_taken"


def test_код_из_старых_полей(gw):
    assert gw.error_code({"error": "quota_exceeded"}) == "quota_exceeded"
    assert gw.error_code({"detail": "participant_not_found"}) == "participant_not_found"
    assert gw.error_code({}) == ""


def test_текст_для_человека_из_конверта(gw):
    body = {"code": "slot_taken", "detail": "Окно 12.11, 18:00 занято",
            "title": "Конфликт состояния"}
    assert gw.error_text(body) == "Окно 12.11, 18:00 занято"


def test_голый_код_не_показывается(gw):
    body = {"code": "participant_not_found", "detail": "participant_not_found",
            "title": "Не найдено"}
    assert gw.error_text(body) == "Не найдено"


def test_текст_из_message(gw):
    body = {"error": "llm_unavailable", "message": "Модель недоступна"}
    assert gw.error_text(body) == "Модель недоступна"
