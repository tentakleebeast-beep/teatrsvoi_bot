"""Подтверждение предложений агента: карточка → токен → запись (A-3, docs/development/108 §8).

Три эндпоинта, и ни один из них не является «новым путём записи»:

    POST /tools/agent/propose   {chat_id, tool, args, human_text}
        → {ok, token, ttl_seconds, proposal:{tool,args,human_text}}
        Аргументы проверяет инструмент предложения (категория из закрытого списка,
        сумма, дата); в базу бюджета ничего не пишется.

    POST /tools/agent/confirm   {token, chat_id}
        → {ok, tx:{id,amount,period}, sheet, deduped, proposal}
        Токен забирается один раз, дальше вызывается обычный `budget_commit` —
        тот же код, что у формы, и то же право `budget.write`.

    POST /tools/agent/cancel    {token, chat_id}
        → {ok, cancelled, note}; операции нет, отказ в `audit_event`.

Почему подтверждение отдельным эндпоинтом, а не полем у `/tools/budget/commit`:
у записи уже есть контракт и права, и второй путь записи означал бы второй набор
проверок. Здесь только разрешение на вызов: не подтвердил — не позвали.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import core
from ..agently import plans, proposals, tasks
from .budget import BudgetCommitIn, budget_commit

router = APIRouter()

#: Право, которым закрыт каждый вид подтверждения. Право проверяется на сервере
#: ровно то же, что у формы: тот, кто не может записать событие формой, не
#: подтвердит и предложение агента.
CONFIRM_CAPABILITIES = {"budget.tx": "budget.write",
                        "event.assign": "schedule.write",
                        "note.create": "notes.write"}


def _has_cap(who: dict, capability: str) -> bool:
    caps = list(who.get("caps") or [])
    return "*" in caps or capability in caps


class ProposeIn(core.ApiIn):
    tool: str = Field(default="budget.tx", max_length=64)
    args: dict = Field(default_factory=dict)
    human_text: str = Field(default="", max_length=1000)
    chat_id: int = 0
    correlation_id: str | None = None


class PlanIn(core.ApiIn):
    steps: list[dict] = Field(default_factory=list)
    human_text: str = Field(default="", max_length=1000)
    chat_id: int = 0
    correlation_id: str | None = None


class DecisionIn(core.ApiIn):
    token: str = Field(default="", max_length=128)
    chat_id: int = 0
    correlation_id: str | None = None


class CancelTaskIn(core.ApiIn):
    chat_id: int = 0


@router.post("/tools/agent/propose")
def agent_propose(body: ProposeIn, who: dict = Depends(core.current)) -> dict:
    """Предложение с одноразовым токеном. Права проверяет инструмент предложения."""
    with core.pool.connection() as conn:
        result = proposals.create(conn, who, body.chat_id, body.tool, body.args,
                                  body.human_text)
    if not result.get("ok"):
        # Нет права записи, категория не из списка, сумма <= 0 — это отказ,
        # а не 500: gateway покажет причину и не нарисует карточку.
        return result
    return {**result, "expires_in": result["ttl_seconds"]}


@router.post("/tools/agent/plan")
def agent_plan(body: PlanIn, who: dict = Depends(core.current)) -> dict:
    """План из нескольких предложений: одна карточка «Выполнить план» (05.11.2026)."""
    with core.pool.connection() as conn:
        result = plans.create(conn, who, body.chat_id, body.steps, body.human_text)
    if not result.get("ok"):
        return result
    return {**result, "expires_in": result["ttl_seconds"]}


@router.post("/tools/agent/plan/confirm")
def agent_plan_confirm(body: DecisionIn,
                       who: dict = Depends(core.current)) -> dict:
    """Выполнить план по шагам. Останавливается на первой ошибке, называя шаг."""
    with core.pool.connection() as conn:
        row = conn.execute("select tool from agent_proposal where token = %s",
                           (str(body.token or ""),)).fetchone()
        if row is None or row["tool"] != plans.PLAN_TOOL:
            proposals.refuse_audit(conn, who, "plan", body.token, "token_unknown",
                                   body.chat_id)
            return proposals.refusal("token_unknown",
                                     "Такого плана нет: попросите собрать заново.")
        result = plans.execute(conn, who, body.token, body.chat_id, body.correlation_id)
    if not result.get("ok") and result.get("error") in ("token_expired", "token_used",
                                                       "token_foreign",
                                                       "token_chat_mismatch"):
        with core.pool.connection() as conn:
            proposals.refuse_audit(conn, who, "plan", body.token,
                                   str(result.get("error")), body.chat_id)
    return result


@router.post("/tools/agent/cancel-task")
def agent_cancel_task(body: CancelTaskIn, who: dict = Depends(core.current)) -> dict:
    """«Обнулить»: следующий шаг идущей задачи не начнётся (agently/tasks.py).

    Флаг снимается началом следующего вопроса, поэтому «Обнулить» не глушит чат
    навсегда. Права здесь не нужны: это отказ от собственной просьбы.
    """
    tasks.request(body.chat_id)
    core.log.info("агент: задача в чате %s отменена человеком %s",
                  body.chat_id, who.get("id"))
    return {"ok": True, "cancelled": True, "chat_id": int(body.chat_id or 0)}


def _run_proposal(who: dict, proposal: dict, token: str, chat_id: int,
                  correlation_id: str | None) -> dict:
    """Подтверждённое предложение → обычный детерминированный путь записи.

    Соединение открывается внутри ветки, а не приходит снаружи: соединение,
    возвращённое в пул, коммитить нельзя — вставка заметки могла остаться
    незакоммиченной (ревью 05.11.2026).
    """
    tool = str(proposal.get("tool") or "")
    args = proposal.get("args") or {}
    capability = CONFIRM_CAPABILITIES.get(tool)
    if capability is None:
        return {"ok": False, "error": "unknown_tool",
                "message": f"действия «{tool}» в подтверждении нет"}
    if not _has_cap(who, capability):
        return {"ok": False, "error": "forbidden",
                "message": f"нет права {capability} для этого действия"}

    if tool == "budget.tx":
        commit = BudgetCommitIn(
            **{key: value for key, value in args.items()
               if key in BudgetCommitIn.model_fields},
            chat_id=int(chat_id or 0),
            idempotency_key="agent:confirm:" + str(token),
            source_text=str(proposal.get("human_text") or "")[:1000] or "agent",
            correlation_id=correlation_id)
        result = budget_commit(commit, who=who)
        return {"ok": True, "tx": result.get("tx"), "sheet": result.get("sheet"),
                "sheet_error": result.get("sheet_error"),
                "deduped": bool(result.get("deduped"))}

    if tool == "event.assign":
        from .events import events_assign
        card = args.get("card")
        if not isinstance(card, dict):
            return {"ok": False, "error": "not_ready", "message": "нет карточки события"}
        result = events_assign({"card": card}, who)
        return {"ok": True, "event_id": result.get("event_id"),
                "in_calendar": bool(result.get("in_calendar")),
                "notified": result.get("notified"), "card": result.get("card")}

    from .notes import create_note
    with core.pool.connection() as conn:
        note = create_note(conn, who, title=args.get("title"), body=args.get("body"),
                           tags=args.get("tags") or [], scope=args.get("scope"))
    return {"ok": True, "note": note}


@router.post("/tools/agent/confirm")
def agent_confirm(body: DecisionIn,
                  who: dict = Depends(core.current)) -> dict:
    """Подтверждение: одна операция, тот же код записи, что у формы."""
    with core.pool.connection() as conn:
        claim = proposals.claim(conn, who, body.token, body.chat_id)
        if not claim.get("ok"):
            proposals.refuse_audit(conn, who, "confirm", body.token,
                                   str(claim.get("error")), body.chat_id)
            return claim
        proposal = claim["proposal"]

    try:
        result = _run_proposal(who, proposal, body.token, int(body.chat_id or 0),
                               body.correlation_id)
    except StarletteHTTPException as exc:      # отказ проверенного кода записи
        with core.pool.connection() as conn:
            proposals.mark_failed(conn, proposal["id"], str(exc.detail))
            proposals.refuse_audit(conn, who, "confirm", body.token,
                                   "commit_failed", body.chat_id)
        return {"ok": False, "error": "commit_failed",
                "message": str(exc.detail)[:300], "proposal": proposal}

    if not result.get("ok"):
        with core.pool.connection() as conn:
            proposals.mark_failed(conn, proposal["id"],
                                  str(result.get("error") or "forbidden"))
        return {**result, "proposal": proposal}

    with core.pool.connection() as conn:
        if result.get("tx"):
            proposals.mark_written(conn, proposal["id"], result["tx"],
                                   bool(result.get("deduped")))
        core.audit(conn, "human", who.get("id"), "agent.confirm", "agent_proposal",
                   str(proposal["id"]),
                   after={"proposal_id": proposal["id"],
                          "args": proposal.get("args"),
                          "deduped": bool(result.get("deduped")),
                          "chat_id": int(body.chat_id or 0)},
                   correlation_id=body.correlation_id)
    return {**result, "proposal": proposal}


@router.post("/tools/agent/cancel")
def agent_cancel(body: DecisionIn,
                 who: dict = Depends(core.current)) -> dict:
    """Отмена: ничего не пишем, отказ человека — в `audit_event`."""
    with core.pool.connection() as conn:
        result = proposals.cancel(conn, who, body.token, body.chat_id)
        if not result.get("ok"):
            proposals.refuse_audit(conn, who, "cancel", body.token,
                                   str(result.get("error")), body.chat_id)
    return result
