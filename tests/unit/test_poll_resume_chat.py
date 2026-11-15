"""«Продолжить» после перезапуска gateway: подпись из БД, состояние не нужно (AC-8).

Что закрывает: интеграционный `test_polls_resume.py` проверяет tool-api (ответы и
указатель в `poll_run`), но не сам обработчик чата. Тут — ровно то, что ломается при
переходе с `MemoryStorage`: `show_runs` обязан взяться за пустое состояние и собрать
кнопку «Продолжить» из ответа `/tools/polls/mine`, а нажатие `p:q:<run>` — открыть
вопрос с той позиции, которую вернула база.

Обоснование: FR-G18, AC-8, `docs/development/162-bot-polls.md` §14.4 (D4).
"""

from __future__ import annotations

import asyncio
import types

import gateway_modules

polls = gateway_modules.load("routers.polls")


class _Message:
    def __init__(self) -> None:
        self.chat = types.SimpleNamespace(id=784099)
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
    """Состояние после перезапуска бота: пусто, как у свежего `MemoryStorage`."""

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


def test_продолжить_берёт_позицию_из_базы_а_не_из_состояния(monkeypatch) -> None:
    """Пройдено 6 из 11: меню «Опросы» показывает вопрос 7, кнопка — `p:q:<run>`."""
    calls: list[tuple[str, str]] = []

    async def fake_api(path, payload=None, chat_id=None, method="POST", timeout=None):
        calls.append((method, path))
        if path == "/tools/polls/mine":
            return {"ok": True, "runs": [{"run_id": 41, "poll_id": 7,
                                          "title": "Анкета организаторам",
                                          "position": 7, "total": 11,
                                          "status": "in_progress"}]}
        if path == "/tools/polls/step":
            return {"ok": True, "step": {"run_id": 41, "position": 7, "total": 11,
                                         "title": "Анкета организаторам", "kind": "text",
                                         "text": "Что вы делаете руками перед показом?",
                                         "required": True, "free_text": False,
                                         "max_choices": None, "options": [],
                                         "chosen": [], "chosen_text": None,
                                         "closed": False, "done": False}}
        raise AssertionError(f"неожиданный вызов {method} {path}")

    monkeypatch.setattr(polls, "api", fake_api)
    message = _Message()
    state = _State()

    asyncio.run(polls.show_runs(message, state))

    text, markup = message.answers[-1]
    assert "вопрос 7 из 11" in text, text
    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert data == ["p:q:41"], data

    callback = _Callback("p:q:41", message)
    asyncio.run(polls.poll_step(callback, state))

    step_text, step_markup = message.answers[-1]
    assert step_text.splitlines()[0] == "Анкета организаторам · вопрос 7 из 11", step_text
    assert "Что вы делаете руками перед показом?" in step_text
    assert ("GET", "/tools/polls/step") in calls
    assert asyncio.run(state.get_state()) is None, "меню опросов оставило состояние формы"
