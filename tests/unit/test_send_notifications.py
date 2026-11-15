"""Отправка уведомлений: `payload.markup` доезжает до Bot API (AC-4).

Первый вопрос опроса — это push с кнопками вариантов (docs/development/162 §14.6):
без `reply_markup` человек получает текст, на который нечем ответить. Раньше
`send()` собирал только `chat_id`, `text` и `disable_web_page_preview`.

Обоснование: FR-G18, AC-4, `docs/development/162-bot-polls.md` §14.6.
"""

from __future__ import annotations

import json
import urllib.parse

import send_notifications

MARKUP = {"inline_keyboard": [[{"text": "Да", "callback_data": "p:y:41:1"},
                               {"text": "Нет", "callback_data": "p:x:41:1"}]]}


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_markup(monkeypatch):
    """`send` передаёт `reply_markup` urlencoded JSON-ом, как ждёт Bot API."""
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = request.data.decode()
        return _Response()

    monkeypatch.setattr(send_notifications, "TOKEN", "1:test")
    monkeypatch.setattr(send_notifications.urllib.request, "urlopen", fake_urlopen)

    ok, error = send_notifications.send(42, "Вопрос 1 из 11", MARKUP)

    assert ok is True and error == ""
    fields = urllib.parse.parse_qs(captured["body"])
    assert json.loads(fields["reply_markup"][0]) == MARKUP
    assert fields["chat_id"] == ["42"]
    assert fields["disable_web_page_preview"] == ["true"]


def test_без_markup_поле_не_отправляется(monkeypatch):
    """Старые записи очереди работают как раньше: поля нет — его и не шлём."""
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = request.data.decode()
        return _Response()

    monkeypatch.setattr(send_notifications, "TOKEN", "1:test")
    monkeypatch.setattr(send_notifications.urllib.request, "urlopen", fake_urlopen)

    ok, _ = send_notifications.send(42, "Напоминание")
    assert ok is True
    assert "reply_markup" not in urllib.parse.parse_qs(captured["body"])
