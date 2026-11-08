"""Дневной бюджет агента на чат: шаги и токены в сутки (A-4, docs/development/108 §10).

Зачем. Агентный цикл — это несколько вызовов модели на одно сообщение. Предел на
запрос (`AGENT_TOKEN_BUDGET`, 6000 токенов) останавливает один цикл, но не мешает
человеку отправить подряд пятьдесят вопросов. Дневной предел на чат отвечает на
вопрос «сколько стоит один разговорщик», а не «сколько стоит один вопрос».

Откуда цифры. Источник — журнал разбора `intent_log` (A-5): у каждой агентной сессии
там `agent_status`, `step_count` и `tokens`. Отдельной таблицы не нужно: расход уже
записан, и второй счётчик неизбежно разошёлся бы с первым.

Что считается:
  * `tokens` — сумма токенов всех агентных сессий чата за сутки;
  * `steps`  — сумма шагов (`step_count`) за сутки: это и есть вызовы модели;
  * ответы из кэша не тратят бюджет: у них `step_count = 0`, а токены не тратились.

Граница суток — по Самаре (`core.now()`), а не по UTC: человек живёт в местном дне,
и «лимит обнулился в 4 утра» он не поймёт.

Область счёта: чат (`chat_id` из запроса), а если gateway его не передал — человек
(`participant_id`). Чужие расходы в бюджет не попадают ни в одном из вариантов.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import core

#: Строка, которую человек видит при исчерпанном бюджете (одна, в конце ответа).
EXHAUSTED_HINT = "Дальше — по кнопкам."

#: Причины исчерпания — закрытый список для журнала и экрана разбора.
REASON_TOKENS = "chat_tokens"
REASON_STEPS = "chat_steps"


def day_start(moment: datetime | None = None) -> datetime:
    """Начало текущих суток по Самаре (с учётом зоны из настроек сервиса)."""
    now = moment or core.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def day_end(moment: datetime | None = None) -> datetime:
    return day_start(moment) + timedelta(days=1)


def scope_clause(chat_id: int | None, participant_id: int | None) -> tuple[str, tuple]:
    """SQL-условие области счёта. Пустая область — «считать нечего»."""
    if chat_id:
        return "chat_id = %s", (int(chat_id),)
    if participant_id:
        return "chat_id is null and participant_id = %s", (int(participant_id),)
    return "", ()


def usage(conn, *, chat_id: int | None = None, participant_id: int | None = None,
          moment: datetime | None = None) -> dict:
    """Израсходовано чатом (или человеком) с начала суток по Самаре."""
    clause, params = scope_clause(chat_id, participant_id)
    if not clause:
        return {"tokens": 0, "steps": 0, "sessions": 0}
    row = conn.execute(
        "select coalesce(sum(tokens), 0) as tokens,"
        "       coalesce(sum(step_count), 0) as steps,"
        "       count(*) as sessions"
        "  from intent_log"
        " where agent_status is not null and ts >= %s and ts < %s and " + clause,
        (day_start(moment), day_end(moment), *params)).fetchone()
    if row is None:
        return {"tokens": 0, "steps": 0, "sessions": 0}
    data = dict(row)
    return {"tokens": int(data.get("tokens") or 0),
            "steps": int(data.get("steps") or 0),
            "sessions": int(data.get("sessions") or 0)}


def check(conn, *, chat_id: int | None = None, participant_id: int | None = None,
          moment: datetime | None = None) -> dict:
    """Можно ли ещё звать модель в этом чате.

    Возвращает цифры, а не только «да/нет»: по ним экран разбора и отчёт видят,
    на каком пределе человек упёрся. Без области счёта бюджет не применяется —
    иначе один запрос без `chat_id` блокировал бы всех.
    """
    from . import models

    limits = models.chat_limits()
    spent = usage(conn, chat_id=chat_id, participant_id=participant_id, moment=moment)
    reason = None
    if spent["tokens"] >= limits["daily_tokens"]:
        reason = REASON_TOKENS
    elif spent["steps"] >= limits["daily_steps"]:
        reason = REASON_STEPS
    scoped = bool(chat_id or participant_id)
    return {"allowed": reason is None,
            "reason": reason,
            "scope": "chat" if chat_id else ("participant" if participant_id else "none"),
            "tokens": spent["tokens"], "steps": spent["steps"],
            "sessions": spent["sessions"],
            "daily_tokens": limits["daily_tokens"],
            "daily_steps": limits["daily_steps"],
            "enforced": scoped}
