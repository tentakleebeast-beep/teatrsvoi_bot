"""План задачи: N предложений одной карточкой и исполнение по порядку.

Зачем план, а не одна карточка на действие: «собери окна и поставь репетицию в
четверг» — это несколько записей, и подтверждать их по одной значит заставлять
человека нажимать четыре раза. Здесь одна карточка «Выполнить план», один токен
и один исполняющий проход.

Что держит код, а не просьба в промпте:

  * шаги плана — только известные предложения (`proposals.PROPOSE_TOOLS`); шаг
    без детерминированного эндпоинта записи в план не попадает;
  * исполнение идёт **по порядку** и останавливается на первой ошибке, называя
    номер шага: частичный успех возвращается как есть, а не прячется;
  * на каждый шаг пишется строка аудита `agent.plan.step`;
  * удалений в плане нет: набор действий тот же, что у одиночных карточек.

Новый путь записи здесь не появляется: шаг вызывает тот же эндпоинт, что форма
(`budget_commit`), назначение (`events_assign`) и заметка (`create_note`).
"""

from __future__ import annotations

import json
import secrets

from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import core
from ..errors import ApiError
from . import proposals, registry
from .tools import has_cap

#: Инструмент, под которым план лежит в `agent_proposal`.
PLAN_TOOL = "plan"

#: Право, без которого шаг не выполняется. Проверяется ещё раз перед записью:
#: план мог быть собран до смены ролей.
STEP_CAPS = {"budget.tx": "budget.write",
             "event.assign": "schedule.write",
             "note.create": "notes.write"}

#: Итог шага плана в человеческом виде: чем называть шаг в отчёте.
STEP_TITLES = {"budget.tx": "операция бюджета",
               "event.assign": "событие",
               "note.create": "заметка"}


def _human_list(steps: list[dict]) -> str:
    lines = []
    for index, step in enumerate(steps, 1):
        title = STEP_TITLES.get(str(step.get("tool")), str(step.get("tool")))
        text = str(step.get("human_text") or "").strip() or title
        lines.append(f"{index}. {text}")
    return "\n".join(lines)


def create(conn, who: dict, chat_id: int, steps: list[dict],
           human_text: str = "") -> dict:
    """Проверить каждый шаг инструментом предложения и выдать один токен на план."""
    clean: list[dict] = []
    for index, step in enumerate(steps, 1):
        tool = str(step.get("tool") or "")
        agent_tool = proposals.PROPOSE_TOOLS.get(tool)
        if agent_tool is None:
            return proposals.refusal("unknown_tool",
                                     f"шага «{tool}» в плане быть не может")
        result = registry.execute(conn, who, agent_tool, step.get("args") or {})
        if not result.get("ok"):
            return proposals.refusal(str(result.get("error") or "proposal_failed"),
                                     f"шаг {index}: "
                                     + str(result.get("message") or "не сложился"),
                                     step=index, tool=tool)
        clean.append(result["proposal"])
    if not clean:
        return proposals.refusal("empty_plan", "в плане нет ни одного шага")

    token = secrets.token_urlsafe(24)
    summary = (human_text or "План из " + str(len(clean)) + " шагов").strip()[:1000]
    row = conn.execute(
        """insert into agent_proposal
               (token, chat_id, participant_id, tool, args, human_text, expires_at)
           values (%s,%s,%s,%s,%s::jsonb,%s, now() + %s::interval)
           returning id, token, status, expires_at""",
        (token, int(chat_id or 0), int(who.get("id") or 0), PLAN_TOOL,
         json.dumps({"steps": clean}, ensure_ascii=False), summary,
         f"{int(proposals.TTL.total_seconds())} seconds")).fetchone()
    core.audit(conn, "bot", who.get("id"), "agent.plan.propose", "agent_proposal",
               str(row["id"]),
               after={"steps": [step.get("tool") for step in clean],
                      "chat_id": int(chat_id or 0),
                      "expires_at": row["expires_at"].isoformat(timespec="seconds")})
    return {"ok": True, "token": row["token"],
            "ttl_seconds": int(proposals.TTL.total_seconds()),
            "plan": {"id": row["id"], "steps": clean,
                     "human_text": summary + "\n" + _human_list(clean)}}


def _step_write(who: dict, step: dict, plan_id: int, index: int,
                correlation_id: str | None = None) -> dict:
    """Один шаг плана через обычный детерминированный эндпоинт."""
    tool = str(step.get("tool") or "")
    args = step.get("args") or {}
    cap = STEP_CAPS.get(tool)
    if cap is None:
        return {"ok": False, "error": "unknown_tool", "message": f"шага «{tool}» нет"}
    if not has_cap(who, cap):
        return {"ok": False, "error": "forbidden",
                "message": f"нет права {cap} для шага «{STEP_TITLES.get(tool, tool)}»"}

    if tool == "budget.tx":
        from ..api.budget import BudgetCommitIn, budget_commit
        fields = {key: value for key, value in args.items()
                  if key in BudgetCommitIn.model_fields}
        commit = BudgetCommitIn(
            **fields, idempotency_key=f"agent:plan:{plan_id}:{index}",
            source_text=str(step.get("human_text") or "agent plan"),
            correlation_id=correlation_id)
        result = budget_commit(commit, who=who)
        return {"ok": True, "tx": result.get("tx"), "deduped": bool(result.get("deduped"))}

    if tool == "event.assign":
        from ..api.events import events_assign
        card = args.get("card")
        if not isinstance(card, dict):
            return {"ok": False, "error": "not_ready", "message": "нет карточки события"}
        result = events_assign({"card": card}, who)
        return {"ok": True, "event_id": result.get("event_id"),
                "in_calendar": bool(result.get("in_calendar"))}

    from ..api.notes import create_note
    with core.pool.connection() as conn:
        note = create_note(conn, who, title=args.get("title"), body=args.get("body"),
                           tags=args.get("tags") or [], scope=args.get("scope"))
    return {"ok": True, "note_id": note.get("id")}


def execute(conn, who: dict, token: str, chat_id: int,
            correlation_id: str | None = None) -> dict:
    """Забрать токен и выполнить шаги по порядку; остановиться на первой ошибке."""
    claim = proposals.claim(conn, who, token, chat_id)
    if not claim.get("ok"):
        return claim
    proposal = claim["proposal"]
    args = proposal.get("args") or {}
    if isinstance(args, str):
        args = json.loads(args)
    steps = args.get("steps") or []

    results: list[dict] = []
    failed: dict | None = None
    for index, step in enumerate(steps, 1):
        tool = str(step.get("tool") or "")
        try:
            outcome = _step_write(who, step, int(proposal.get("id") or 0), index,
                                  correlation_id)
        except (StarletteHTTPException, ApiError) as exc:      # отказ проверенного кода
            outcome = {"ok": False, "error": "step_failed",
                       "message": str(getattr(exc, "detail", exc))[:300]}
        record = {"step": index, "tool": tool, "title": STEP_TITLES.get(tool, tool),
                  "human_text": step.get("human_text"), "ok": bool(outcome.get("ok")),
                  "error": outcome.get("error"), "message": outcome.get("message"),
                  "result": {key: value for key, value in outcome.items()
                             if key not in ("ok", "error", "message")}}
        results.append(record)
        core.audit(conn, "human", who.get("id"), "agent.plan.step", "agent_proposal",
                   str(proposal.get("id")),
                   after={"step": index, "tool": tool, "ok": record["ok"],
                          "error": record["error"],
                          "chat_id": int(chat_id or 0)},
                   correlation_id=correlation_id)
        if not record["ok"]:
            failed = record
            break

    payload = {"steps": results, "done": sum(1 for item in results if item["ok"]),
               "total": len(steps), "failed_step": (failed or {}).get("step")}
    conn.execute("update agent_proposal set result = %s::jsonb where id = %s",
                 (json.dumps(payload, ensure_ascii=False, default=str),
                  int(proposal.get("id") or 0)))
    if failed is not None:
        conn.execute("update agent_proposal set status = 'failed', error = %s,"
                     " resolved_at = now() where id = %s",
                     (f"шаг {failed['step']}: {failed.get('message') or failed.get('error')}"[:300],
                      int(proposal.get("id") or 0)))
    return {"ok": failed is None, "done": payload["done"], "total": len(steps),
            "results": results, "failed_step": failed,
            "message": (f"Остановился на шаге {failed['step']}: "
                        f"{failed.get('message') or failed.get('error')}"
                        if failed else ""),
            "proposal": proposal}
