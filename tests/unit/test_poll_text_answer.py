"""Вопрос `text`/`number` в чате: человеку есть чем ответить (E8, дефект).

Дефект. У вопроса `text` и `number` вариантов нет, а кнопка «Другое — напишу» (`p:f` —
сегодня единственный вход в `PollAnswer.await_text`) собирается по флагу `free_text`,
который у этих типов запрещён схемой (`db/init/065_polls.sql:39`). Итог: сообщение
говорит «Ответьте сообщением», но состояние ожидания текста никем не выставляется —
свободный текст ловит `routers/fallback.py:state_fallback_text`, а не опрос, и ответ
человека теряется.

Почему это не мелочь: анкета `docs/development/161` — все вопросы типа `text`,
ради неё фича и делалась (162 §9). Пока входа в ответ нет, прогон 161 в чате не
проходится ни на одном опросе.

Что проверяет тест (не диктует форму правки): после показа вопроса, который принимает
свободный ввод, у человека должен быть рабочий вход — либо состояние `await_text`
уже стоит при показе вопроса, либо в разметке есть кнопка, которая его ставит.
Дальше тест этим входом пользуется и требует, чтобы ответ ушёл в `/tools/polls/answer`.

Обоснование: FR-G18, AC-6, AC-8, `docs/development/162-bot-polls.md` §5.2, §14.4 (D4),
`docs/development/161` §3–§6.
"""

from __future__ import annotations

import asyncio
import types

import gateway_modules
import pytest

polls = gateway_modules.load("routers.polls")
states = gateway_modules.load("states")

STEP = {
    "run_id": 1_234_567, "position": 2, "total": 11, "title": "Анкета организаторам",
    "kind": "text", "text": "Какие файлы вы открываете утром?", "required": True,
    "free_text": False, "max_choices": None, "options": [],
    "chosen": [], "chosen_text": None, "closed": False, "done": False,
}


class _Message:
    def __init__(self) -> None:
        self.chat = types.SimpleNamespace(id=785001)
        self.text = ""
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))


class _Callback:
    def __init__(self, data: str, message: _Message) -> None:
        self.data = data
        self.message = message

    async def answer(self, text=None, **kwargs):
        return None


class _State:
    def __init__(self) -> None:
        self.state = None
        self.data: dict = {}

    async def get_state(self):
        return self.state

    async def set_state(self, state) -> None:
        self.state = state

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def clear(self) -> None:
        self.state = None
        self.data.clear()


POLL_UI = gateway_modules.load("poll_ui")


def _buttons(markup) -> list:
    rows = getattr(markup, "inline_keyboard", None) or []
    return [button for row in rows for button in row]


def _actions(markup) -> list[str]:
    return [POLL_UI.parse(button.callback_data or "")["action"]
            for button in _buttons(markup) if POLL_UI.parse(button.callback_data or "")]


def _open_text_step(kind: str, monkeypatch) -> tuple[_Message, _State]:
    """Нажать «Продолжить» и получить вопрос, который принимает свободный ввод."""
    step = dict(STEP, kind=kind)

    async def fake_api(path, payload=None, chat_id=None, method="POST", timeout=None):
        if path == "/tools/polls/step":
            return {"ok": True, "step": step}
        raise AssertionError(f"неожиданный вызов {method} {path}")

    monkeypatch.setattr(polls, "api", fake_api)
    message, state = _Message(), _State()
    asyncio.run(polls.poll_step(_Callback(f"p:q:{step['run_id']}", message), state))
    return message, state


@pytest.mark.parametrize("kind", ["text", "number"])
def test_вопрос_свободного_ввода_даёт_вход_в_ответ(kind, monkeypatch) -> None:
    """Либо состояние уже стоит, либо есть кнопка `f`: иначе текст уходит в заглушку."""
    message, state = _open_text_step(kind, monkeypatch)
    _text, markup = message.answers[-1]
    actions = _actions(markup)
    in_state = asyncio.run(state.get_state()) == states.PollAnswer.await_text
    assert in_state or "f" in actions, (
        f"у вопроса {kind} нет входа в ответ сообщением: состояние пустое, "
        f"кнопки шага — {actions or 'пусто'}; сообщение просит ответить сообщением, "
        "но диалог опроса такое сообщение не ловит")


@pytest.mark.parametrize("kind", ["text", "number"])
def test_ответ_сообщением_попадает_в_poll_answer(kind, monkeypatch) -> None:
    """Тот же вход в деле: текст человека уходит в `/tools/polls/answer`, а не в LLM."""
    message, state = _open_text_step(kind, monkeypatch)
    _text, markup = message.answers[-1]
    if asyncio.run(state.get_state()) != states.PollAnswer.await_text:
        entry = next((button for row in (markup.inline_keyboard if markup else [])
                      for button in row
                      if POLL_UI.parse(button.callback_data or "").get("action") == "f"), None)
        assert entry is not None, "входа в ответ сообщением нет — см. соседний тест"
        asyncio.run(polls.poll_step(_Callback(entry.callback_data, message), state))

    posted: list[dict] = []

    async def fake_api(path, payload=None, chat_id=None, method="POST", timeout=None):
        if path == "/tools/polls/answer":
            posted.append(dict(payload or {}))
            return {"ok": True, "step": dict(STEP, position=3, done=True)}
        raise AssertionError(f"неожиданный вызов {method} {path}")

    monkeypatch.setattr(polls, "api", fake_api)
    message.text = "Утром открываю админку и календарь"
    asyncio.run(polls.poll_free_text(message, state))

    assert posted, "ответ сообщением не дошёл до /tools/polls/answer"
    assert posted[-1]["action"] == "text" and posted[-1]["text"] == message.text, posted[-1]
