"""
Ссылки на Mini App: один адрес, одно правило (M-1, docs/infrastructure/75 §5.2).

Проверяем то, из-за чего кнопка ломается в проде: `http://` вместо `https://`,
двойной слэш в адресе и кнопка, показанная там, где приложение ещё недоступно.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tg-gateway" / "app"))

from webapp_links import base_url, hint, keyboard, page_url  # noqa: E402

HTTPS = "https://teatr-svoi.online"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        (None, ""),
        (HTTPS, HTTPS),
        (HTTPS + "/", HTTPS),          # хвостовой слэш срезаем: иначе в адресе «//»
        ("  " + HTTPS + "  ", HTTPS),  # пробелы из .env не ломают адрес
        ("http://teatr-svoi.online", ""),   # HTTP в проде — ошибка конфигурации
        ("teatr-svoi.online", ""),           # схема обязательна
    ],
)
def test_m1_адрес_принимается_только_по_https(value, expected):
    """M-1: http:// не «пока сойдёт» — Telegram не откроет Mini App, человек увидит пустой экран."""
    assert base_url(value) == expected


def test_m1_страница_собирается_без_двойного_слэша():
    """M-1: путь со слэшем и без даёт один и тот же адрес."""
    assert page_url("/webapp/days", HTTPS) == HTTPS + "/webapp/days"
    assert page_url("webapp/days", HTTPS + "/") == HTTPS + "/webapp/days"
    assert page_url("webapp", "") is None


def test_m1_кнопки_нет_если_адреса_нет():
    """M-1: локально и на обкатке кнопку не показываем — иначе она ведёт в никуда."""
    assert keyboard("Открыть неделю", "/webapp/days", "") is None
    assert keyboard("Открыть неделю", "/webapp/days", "http://localhost:8000") is None


def test_m1_кнопка_ведёт_на_страницу_приложения():
    """M-1: ровно одна кнопка с web_app и адресом нужной страницы."""
    markup = keyboard("Открыть неделю", "/webapp/days", HTTPS)
    assert markup is not None
    assert len(markup.inline_keyboard) == 1 and len(markup.inline_keyboard[0]) == 1
    button = markup.inline_keyboard[0][0]
    assert button.text == "Открыть неделю"
    assert button.web_app is not None and button.web_app.url == HTTPS + "/webapp/days"


def test_m1_адрес_для_текста_когда_кнопку_не_показать():
    """M-1: если кнопку не рисуем, адрес всё равно можно назвать в тексте."""
    assert hint("/webapp", HTTPS) == HTTPS + "/webapp"
    assert hint("/webapp", "") is None
