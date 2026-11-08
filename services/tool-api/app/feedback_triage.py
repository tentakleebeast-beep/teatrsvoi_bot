"""Что сервер делает с обращением: отвечает или доставляет в dev.

Правило владельца 02.11.2026: на сервере НЕЛЬЗЯ разбирать баги и пожелания —
сервер не коммитит в git, работа идёт в dev-среде. Поэтому решение по виду
обращения одно и то же и не зависит от настроения модели:

* `question` — ответ моделью (тот же ассистент, что у `/tools/route`);
  модель не ответила — обращение остаётся неразобранным и попадает в отчёт
  «требует вас», а не получает выдуманный ответ;
* `praise` — короткая благодарность, без модели: отвечать тут нечего;
* `bug`, `wish` и `wish-fl` (просьба о доработке) — только пометка «передано
  в разработку» и запись в журнал
  с `source='server'`; карту заводит dev-среда из `feedback/inbox/`, сервер
  карт не создаёт и в git не пишет.

Модуль чистый: ни базы, ни сети, ни LLM. Поэтому решение проверяется
unit-тестом без FastAPI (`tests/unit/test_feedback_triage.py`), а проведение
его в жизнь — маршрутом `POST /tools/feedback/triage`.
"""

from __future__ import annotations

#: Текст, который видит автор бага или пожелания. Дословно из карты t_17f137b8.
HANDOFF_TEXT = "Принято, передано в разработку."

#: Ответ на благодарность. Без эмодзи: правило проекта — статус словом.
PRAISE_TEXT = "Спасибо! Передали команде."

#: Длиннее в уведомление и в журнал не пишем: Telegram всё равно обрежет.
MAX_ANSWER = 1000

ANSWER_KINDS = ("question", "praise")
HANDOFF_KINDS = ("bug", "wish", "wish-fl")


def needs_model(kind: str) -> bool:
    """Нужен ли ответ модели. Только вопрос: благодарность отвечается шаблоном."""
    return kind == "question"


def planned_action(kind: str) -> str:
    """Что сервер сделает с обращением, без текста ответа. Для сухого прогона."""
    kind = (kind or "").strip()
    if kind in HANDOFF_KINDS:
        return "handoff"
    if kind in ANSWER_KINDS:
        return "answer"
    return "skip"


def decide(kind: str, answer: str | None = None) -> dict:
    """Решение по одному обращению: `answer`, `handoff` или `skip`.

    `answer` — уже полученный текст модели (для вопроса). Пусто — модель не
    ответила, и обращение не закрываем: лучше показать его человеку в отчёте,
    чем отправить автору пустоту или выдумку.
    """
    kind = (kind or "").strip()
    if kind == "praise":
        return {"action": "answer", "status": "done", "text": PRAISE_TEXT}
    if kind == "question":
        text = " ".join((answer or "").split())[:MAX_ANSWER]
        if not text:
            return {"action": "skip", "reason": "no_answer"}
        return {"action": "answer", "status": "done", "text": text}
    if kind in HANDOFF_KINDS:
        return {"action": "handoff", "status": "seen", "text": HANDOFF_TEXT}
    return {"action": "skip", "reason": "unknown_kind"}
