"""Флаг отмены задачи: «Обнулить» прекращает работу между шагами.

Зачем отдельный модуль, а не поле в запросе: `POST /tools/agent/ask` — один
синхронный вызов, а отмена приходит **другим** запросом в тот же процесс, пока
первый ещё идёт. Поэтому флаг живёт в памяти процесса tool-api (uvicorn запущен
одним воркером, см. `services/tool-api/Dockerfile`), а не в теле исходного запроса.

Правила:
  * отмена действует до следующего `reset` — иначе отменённая задача тянула бы
    за собой все следующие вопросы в этом чате;
  * `reset` вызывается в начале `POST /tools/agent/ask`: новый вопрос после
    «Обнулить» — это новый вопрос, а не продолжение отменённого;
  * речи о записи нет: флаг только читается в петле `agently/loop.py`.

Тест: `tests/unit/test_agent_task_cancel.py`, `tests/integration/test_agent_task.py`.
"""

from __future__ import annotations

_cancelled: set[int] = set()


def request(chat_id: int | None) -> None:
    """Пометить чат отменённым: следующий шаг петли не начнётся."""
    _cancelled.add(int(chat_id or 0))


def reset(chat_id: int | None) -> None:
    """Снять флаг: новый вопрос в этом чате идёт обычным порядком."""
    _cancelled.discard(int(chat_id or 0))


def is_cancelled(chat_id: int | None) -> bool:
    return int(chat_id or 0) in _cancelled


def clear_all() -> None:
    """Сброс для тестов: состояние процесса не должно переходить между прогонами."""
    _cancelled.clear()
