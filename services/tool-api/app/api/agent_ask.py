"""`POST /tools/agent/ask` — агентный режим свободного текста (A-1, docs/development/108).

Контракт для gateway:

    {"text": "какой сегодня день", "chat_id": 123}

Ответ:

    {"ok": true, "engine": "agent", "answer": "...", "scenario": "assistant",
     "steps": [{"step": 1, "tool": "now", "ok": true, "latency_ms": 12}],
     "step_count": 1, "tokens": 210, "latency_ms": 1400, "model": "...",
     "propose": null}

`propose` — намерение записи: `{tool, args, human_text}`. Агент не пишет в БД,
листы и календарь; запись делает детерминированный эндпоинт после нажатия кнопки
в боте. Поэтому «предложение» и «учтено» — разные события.

Деградация: нет ключа, провайдер недоступен, таймаут или исчерпан бюджет токенов —
ответ по правилам (`engine: "rules"`, `degraded: {reason}`), а не «Думаю…» навсегда
(docs/21, docs/development/108 §5). HTTP-ошибка при этом не отдаётся: человек
должен получить ответ, а gateway — понятную причину.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, status
from pydantic import Field

from .. import core, llm, masking
from ..agently import budget, cache, cost, loop, registry, tasks
from ..errors import ApiError

router = APIRouter()


class AgentAskIn(core.ApiIn):
    text: str = Field(default="", max_length=2000)
    chat_id: int | None = None
    correlation_id: str | None = None
    #: Рамка задачи «Озадачить» (решение 05.11.2026): шагов 6–8, план и итог «что
    #: сделал / что не смог». Меняет строку подсказки и метку сценария — не права и
    #: не набор инструментов.
    task: bool = False


# Итог агентной сессии в intent_log (A-5). Закрытый список, тот же, что в миграции 048.
STATUS_ANSWER = "answer"          # агент ответил сам
STATUS_PROPOSAL = "proposal"      # ответ и карточка подтверждения: нужна кнопка
STATUS_REFUSAL = "refusal"        # агент не ответил: деградация к правилам, таймаут, лимит


def _log_session(conn, who: dict, text: str, chat_id: int | None, *,
                 status: str, source: str, steps: list[dict] | None,
                 step_count: int, answer: str | None, tokens: int,
                 latency_ms: int, degraded_reason: str | None = None) -> None:
    """Строка журнала разбора: вопрос, шаги, инструменты, ответ и цифры (A-5).

    Аргументы инструментов пишутся обезличенными (`masking.mask_steps`): токенов,
    паролей и телефонов в журнале быть не должно, а имена инструментов нужны как есть.
    Вопрос и ответ не маскируются — они предмет разбора (решение по карте A-5).

    `degraded_reason` — причина деградации (AUD-10, миграция 055). До этого карты она
    лежала только в `audit_event.after`, и «сколько отказов из-за провайдера, сколько
    из-за бюджета» по журналу не считалось (`docs/reports/119` §9 п.4).
    """
    conn.execute("""
        insert into intent_log (chat_id, participant_id, text, source, resolved,
                                tokens, latency_ms, steps, step_count, agent_status,
                                answer, degraded_reason)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (chat_id, who.get("id") or None, text.strip()[:2000], source,
         bool(answer), int(tokens or 0), int(latency_ms or 0),
         json.dumps(masking.mask_steps(steps), ensure_ascii=False, default=str),
         int(step_count or 0), status, (answer or None), degraded_reason))


def _record_usage(conn, pid: int, usage: dict, intent: str = "agent") -> None:
    """Расход агентной сессии в `llm_usage`: раздельные токены и рубли (AUD-10).

    Пишется и на деградации: если шаги успели потратить токены, это расход, даже когда
    человеку ответили правила. Неизвестная модель попадает сюда с `cost_rub = NULL` и
    `price_status = 'no_price'` — «цены нет», а не «вышло бесплатно».

    `intent` — сценарий расхода: `agent` для обычного вопроса, `task` для рамки
    «Озадачить» (05.11.2026). Иначе по журналу не отличить задачу от вопроса.
    """
    tokens = int(usage.get("tokens") or 0)
    if not pid or not tokens:
        return
    entry = cost.row(usage.get("model"), usage.get("tokens_in") or 0,
                     usage.get("tokens_out") or 0)
    conn.execute("""
        insert into llm_usage (participant_id, model, tokens_in, tokens_out, cost_rub,
                               intent, provider, price_status)
        values (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (pid, entry["model"] or "unknown", entry["tokens_in"], entry["tokens_out"],
         entry["cost_rub"], intent, llm.provider(), entry["price_status"]))


def _check_quota(pid: int | None) -> None:
    """Квота человека: агентный шаг тратит те же токены, что и ассистент."""
    if not pid:
        return
    with core.pool.connection() as conn:
        bal = conn.execute("select tokens_left, requests_left from v_llm_balance"
                           " where participant_id = %s", (pid,)).fetchone()
    if bal and bal["tokens_left"] is not None and bal["tokens_left"] <= 0:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS,
                       "Лимит AI на этот месяц исчерпан. Доступны формы и кнопки.",
                       code="quota_exceeded")
    if bal and bal["requests_left"] is not None and bal["requests_left"] <= 0:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS,
                       "Дневной лимит запросов исчерпан. Попробуйте завтра.",
                       code="quota_exceeded")


def _rules_response(who: dict, text: str, reason: str, started: float,
                    chat_id: int | None = None, steps: list[dict] | None = None,
                    usage: dict | None = None, hint: str | None = None,
                    extra: dict | None = None, task: bool = False) -> dict:
    """Ответ по правилам с понятной причиной: модель не участвовала.

    Строка журнала пишется и здесь: разбору нужно видеть не только удачные ответы,
    но и случаи «агент не справился, ответили правила» — с той же причиной в
    `degraded_reason` (AUD-10): без неё отказы считались только по `audit_event`.
    `hint` — одна строка в конце ответа (при исчерпанном бюджете чата: «дальше —
    по кнопкам»); повторно она не добавляется, чтобы не копилась при нескольких
    деградациях подряд. `usage` — расход, если шаги уже успели потратить токены:
    таймаут и бюджет запроса тоже стоят денег.
    """
    pid = int(who.get("id") or 0)
    latency = int((time.monotonic() - started) * 1000)
    tokens = int((usage or {}).get("tokens") or 0)
    scenario = "task" if task else None
    with core.pool.connection() as conn:
        fallback = loop.rules_answer(conn, who, text)
        answer = str(fallback["answer"])
        if hint and hint not in answer:
            answer = answer.rstrip() + "\n" + hint
        core.audit(conn, "bot", pid, "agent.ask.rules", "participant", str(pid),
                   after={"reason": reason, "scenario": fallback.get("scenario")})
        _record_usage(conn, pid, usage or {}, intent="task" if task else "agent")
        _log_session(conn, who, text, chat_id, status=STATUS_REFUSAL, source="rules",
                     steps=steps, step_count=len(steps or []), answer=answer,
                     tokens=tokens, latency_ms=latency, degraded_reason=reason)
    response = {"ok": True, "engine": "rules", "degraded": {"reason": reason},
                "answer": answer, "scenario": scenario or fallback.get("scenario"),
                "propose": fallback.get("propose"), "steps": steps or [],
                "step_count": len(steps or []),
                "tokens": tokens, "tokens_in": int((usage or {}).get("tokens_in") or 0),
                "tokens_out": int((usage or {}).get("tokens_out") or 0),
                "model": None, "latency_ms": latency}
    if extra:
        response["degraded"] = {**response["degraded"], **extra}
    return response


def _cached_response(who: dict, text: str, entry: dict, started: float,
                     chat_id: int | None) -> dict:
    """Ответ из кэша: модели не было, токенов не потрачено, шагов нет.

    В журнале источник `cache` — иначе экран разбора показывал бы «ответила модель»
    там, где модель не вызывалась, и доля кэша в стоимости была бы не видна.
    """
    pid = int(who.get("id") or 0)
    latency = int((time.monotonic() - started) * 1000)
    with core.pool.connection() as conn:
        core.audit(conn, "bot", pid, "agent.ask.cache", "participant", str(pid),
                   after={"age_seconds": entry.get("age_seconds"),
                          "scenario": entry.get("scenario")})
        _log_session(conn, who, text, chat_id, status=STATUS_ANSWER, source="cache",
                     steps=[], step_count=0, answer=entry.get("answer"), tokens=0,
                     latency_ms=latency)
    return {"ok": True, "engine": "cache", "cached": True,
            "answer": entry.get("answer"), "scenario": entry.get("scenario"),
            "propose": None, "steps": [], "step_count": 0, "tokens": 0,
            "model": entry.get("model"),
            "cached_age_seconds": entry.get("age_seconds"),
            "latency_ms": latency}


@router.post("/tools/agent/ask")
async def agent_ask(body: AgentAskIn, who: dict = Depends(core.current)) -> dict:
    """Ответ на свободный текст с инструментами. Не больше 4 шагов и 15 секунд.

    Порядок проверок важен:
      1. кэш (`agently/cache.py`) — попадание не тратит ни модели, ни квоты;
      2. квота человека (месячные токены и дневные запросы);
      3. дневной бюджет чата (`agently/budget.py`) — шаги и токены за сутки;
      4. ключ, цикл, деградация к правилам.
    """
    started = time.monotonic()
    text = (body.text or "").strip()
    pid = int(who.get("id") or 0)
    task = bool(body.task)
    if not text:
        return {"ok": False, "error": "empty_text", "answer": "",
                "detail": "пустой текст не отправляется модели",
                "latency_ms": int((time.monotonic() - started) * 1000)}

    # Новый вопрос снимает прошлую отмену: «Обнулить» относилось к прежней задаче,
    # а не ко всем следующим (agently/tasks.py).
    tasks.reset(body.chat_id)

    # Рамка «Озадачить» кэш не читает и в кэш не пишет: ответ на ту же фразу без рамки
    # другой, а повтор задачи молча вернул бы формулировку вопроса.
    scope = None if task else cache.scope_of(body.chat_id, pid or None)
    if scope:
        entry = cache.get(scope, text)
        if entry is not None:
            return _cached_response(who, text, entry, started, body.chat_id)

    _check_quota(pid)

    with core.pool.connection() as conn:
        permit = budget.check(conn, chat_id=body.chat_id, participant_id=pid or None)
    if not permit["allowed"]:
        # Бюджет чата исчерпан: короткий ответ по правилам и одна строка «по кнопкам».
        # Это не ошибка (HTTP 200) и не «Думаю…»: человек получает ответ сразу.
        core.log.info("агент: бюджет чата исчерпан (%s)", permit["reason"])
        return _rules_response(who, text, str(permit["reason"]), started,
                               chat_id=body.chat_id, hint=budget.EXHAUSTED_HINT,
                               task=task,
                               extra={"budget": {key: permit[key] for key in
                                                 ("scope", "tokens", "steps",
                                                  "daily_tokens", "daily_steps")}})

    if not llm.is_configured():
        return _rules_response(who, text, "llm_unavailable", started, chat_id=body.chat_id,
                               task=task)

    try:
        with core.pool.connection() as conn:
            result = await loop.run(conn, who, text, task=task, chat_id=body.chat_id)
    except loop.ModelUnavailable as exc:
        core.log.warning("агент: модель недоступна (%s), отвечаем по правилам", exc.code)
        return _rules_response(who, text, exc.code, started, chat_id=body.chat_id, task=task)

    latency = int((time.monotonic() - started) * 1000)
    if result.get("error") in ("timeout", "token_budget"):
        # Бюджет и таймаут — это деградация к правилам, а не «Думаю…». Потраченные
        # до остановки токены — расход: пишем и в журнал, и в llm_usage (AUD-10).
        return _rules_response(who, text, str(result["error"]), started,
                               chat_id=body.chat_id, steps=result.get("steps"),
                               usage=result, task=task)
    if not result.get("ok"):
        with core.pool.connection() as conn:
            _log_session(conn, who, text, body.chat_id, status=STATUS_REFUSAL, source="llm",
                         steps=result.get("steps"), step_count=result.get("step_count") or 0,
                         answer=None, tokens=result.get("tokens") or 0, latency_ms=latency,
                         degraded_reason=str(result.get("error") or "") or None)
            _record_usage(conn, pid, result, intent="task" if task else "agent")
        return {**result, "engine": "agent", "propose": result.get("propose"),
                "latency_ms": latency}

    status = STATUS_PROPOSAL if (result.get("propose") or result.get("plan")) else STATUS_ANSWER
    # Ответ из реестра функций (список действий) модель не звал: источник — rules,
    # иначе экран разбора покажет «разобрала модель» там, где её не было.
    source = "rules" if result.get("engine") == "rules" else "llm"

    if pid and result.get("tokens"):
        with core.pool.connection() as conn:
            _record_usage(conn, pid, result, intent="task" if task else "agent")
            core.audit(conn, "bot", pid, "agent.ask", "participant", str(pid),
                       after={"scenario": result.get("scenario"),
                              "steps": result.get("steps"),
                              "step_count": result.get("step_count"),
                              "tokens": result.get("tokens"),
                              "latency_ms": latency,
                              "propose": (result.get("propose") or {}).get("tool"),
                              "model": result.get("model")},
                       correlation_id=body.correlation_id)

    with core.pool.connection() as conn:
        _log_session(conn, who, text, body.chat_id, status=status, source=source,
                     steps=result.get("steps"), step_count=result.get("step_count") or 0,
                     answer=result.get("answer"), tokens=result.get("tokens") or 0,
                     latency_ms=latency)

    # Кэш только для повторяющихся вопросов из config/agent-models.yaml и только для
    # ответов без карточки подтверждения: черновик действия не повторяется молча.
    # Рамка «Озадачить» (`scope is None`) в кэш не попадает: её ответ — про шаги.
    if status == STATUS_ANSWER and scope:
        cache.put(scope, text, {**result, "engine": result.get("engine") or "agent"})

    return {**result, "engine": result.get("engine") or "agent", "latency_ms": latency,
            "tools_available": [tool.name for tool in registry.available_tools(who)]}
