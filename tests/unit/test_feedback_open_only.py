"""Экран «Обращения» показывает только открытые (обращение №22).

Проверяем статически: обработчики и страницы не поднимаются в unit-тестах.
После переработки (docs/development/92 §2.4) очередь живёт на своём экране
`/webapp/admin/t/feedback`; раньше это был раздел внутри полотна панели.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

import gateway_modules
from app.pages import admin  # noqa: E402


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_кнопка_обратной_связи_просит_только_открытые():
    source = gateway_modules.source()
    start = source.index("async def panel_feedback")
    block = source[start:start + 2000]
    assert "open_only=true" in block


def test_мини_апп_берёт_из_api_только_открытые():
    source = _read("services", "tool-api", "app", "api", "admin.py")
    assert "feedback_list(status=None, open_only=True, limit=50, who=who)" in source
    assert "Открытых обращений нет" in admin.FEEDBACK_QUEUE_PAGE


def test_очередь_разбирается_на_месте():
    """Решение по обращению принимается тем же обработчиком, что и в боте."""
    page = admin.FEEDBACK_QUEUE_PAGE
    assert "/webapp/admin/feedback/decide" in page
    assert "source: \"human\"" in page
    assert "Ответ автору" in page
