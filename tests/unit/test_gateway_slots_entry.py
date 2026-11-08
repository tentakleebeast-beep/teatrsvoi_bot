"""Вход в дом поиска окон из чата — правило «дом + вход» (С6, docs/architecture/40 §5).

Проверяем три вещи:

  * на «найди окна на неделю» чат передаёт тип («репетиция» по умолчанию,
    docs/architecture/42 §6) — иначе окна считались бы без дороги и без правил типа;
  * ответ несёт кнопку в `/webapp/slots`: дом действия — экран, чат только вход;
  * «где больше всего свободных» остаётся режимом `max_free`, тип туда не подставляется;
  * при неизвестной занятости зала чат говорит об этом прямо и не показывает окно.

Обоснование: FR-D5, FR-D7, карта t_dcef1b7e (Ш-05), docs/architecture/40 §4–§5.
"""

import asyncio
import types

import gateway_modules
import pytest

WEBAPP = "https://app.example"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeMessage:
    def __init__(self, text: str = "", chat_id: int = 1):
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append((text, reply_markup))


def _sent(gw, response, monkeypatch, text, *, base=WEBAPP):
    """Прогнать ответ чата и вернуть (payload, ответ бота)."""
    calls: list[dict] = []

    async def fake_api(path, payload=None, chat_id=None, method="POST"):
        calls.append({"path": path, "payload": payload})
        return response

    monkeypatch.setattr(gw, "api", fake_api)
    monkeypatch.setattr(gw, "WEBAPP_BASE_URL", base)
    message = FakeMessage(text)
    handled = asyncio.run(gw.answer_slots(message, text=text))
    assert handled is True
    return calls[0], message.answers[-1]


def _policy_response(**extra):
    body = {"ok": True, "mode": "policy", "policy": "rehearsal",
            "policy_title": "репетиция", "travel_minutes": {"1": 60},
            "text": ["вт 24.11 19:00–21:00 (2 ч)"], "windows": [{"from_hour": 19}],
            "venue": {}, "people": {"1": "Тест"}}
    body.update(extra)
    return body


def test_окна_без_типа_считаются_как_репетиция(gw, monkeypatch):
    call, _ = _sent(gw, _policy_response(), monkeypatch, "найди окна на неделю")
    assert call["path"] == "/tools/schedule/slots"
    assert call["payload"]["policy"] == "rehearsal"
    assert call["payload"]["text"] == "найди окна на неделю"


def test_тип_из_фразы_важнее_умолчания(gw, monkeypatch):
    call, _ = _sent(gw, _policy_response(policy="rental"), monkeypatch,
                    "найди окно для аренды зала")
    assert call["payload"]["policy"] == "rental"
    assert call["payload"]["min_hours"] == 2


def test_ответ_ведёт_в_дом(gw, monkeypatch):
    _, (text, markup) = _sent(gw, _policy_response(), monkeypatch, "найди окна")
    assert "19:00–21:00" in text
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert len(buttons) == 1
    assert buttons[0].text == "Открыть поиск окон"
    assert buttons[0].web_app.url == f"{WEBAPP}/webapp/slots"


def test_больше_всего_свободных_остаётся_режимом(gw, monkeypatch):
    response = {"ok": True, "mode": "max_free",
                "text": ["вт 24.11 19:00 — свободны 5 из 6"], "resolved": {}}
    call, (text, markup) = _sent(gw, response, monkeypatch, "когда больше всего свободных")
    assert "policy" not in call["payload"], "max_free — не тип события"
    assert "свободны 5 из 6" in text
    assert markup is not None, "вход в дом есть и у режима «больше всех»"


def test_занятость_зала_неизвестна_говорим_прямо(gw, monkeypatch):
    response = {"ok": True, "mode": "venue", "policy": "rental", "reason": "venue_unknown",
                "policy_title": "аренда (Студия)", "windows": [], "text": [],
                "venue": {"applied": False, "reason": "no_events"}, "resolved": {}}
    _, (text, markup) = _sent(gw, response, monkeypatch, "найди окно для аренды")
    assert "Занятость зала неизвестна" in text
    assert "08:00" not in text, "выдуманного окна в ответе нет"
    assert markup is not None


def test_без_адреса_приложения_кнопки_нет(gw, monkeypatch):
    """Пустой `WEBAPP_BASE_URL` — кнопку не показываем, текст остаётся."""
    _, (text, markup) = _sent(gw, _policy_response(), monkeypatch, "найди окна", base="")
    assert markup is None
    assert "19:00–21:00" in text
