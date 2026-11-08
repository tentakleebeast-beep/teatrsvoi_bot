"""Очередь неразобранных обращений и серверный разбор (карта t_17f137b8).

Правило владельца 02.11.2026: на сервере нельзя разбирать баги и пожелания.
Сервер отвечает только на вопросы и благодарности, а баги и пожелания помечает
«передано в разработку»; карты из них заводит dev-среда из `feedback/inbox/`.

Маршруты:

    GET  /tools/feedback/open    — что ещё не разобрано (нет строки в журнале)
    POST /tools/feedback/triage  — разобрать пачку: ответить или доставить

Оба требуют `feedback.triage`: это та же возможность, что у ручного разбора,
поэтому серверный таймер ходит тем же ключом, что и человек.

Проведение в жизнь — через существующий `feedback_decide` в `api/admin.py`:
статус, журнал `feedback_answer`, уведомление автору и аудит уже собраны там,
дублировать эту логику нельзя. Отличие одно — `source='server'`, чтобы мост
выгрузки (`feedback-watch.yml`) видел доставленные баги до того, как их заберёт
dev-среда.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from .. import core, feedback_triage, llm, router_llm
from . import admin

router = APIRouter()

OPEN_STATUSES = ("new", "seen", "planned")

OPEN_SQL = """
    select f.id, f.kind, f.status, f.text, f.context, f.created_at,
           f.participant_id,
           coalesce(p.display_name, 'участник') as author,
           coalesce((select array_agg(r.role order by r.role)
                       from participant_role r where r.participant_id = f.participant_id
                         and r.revoked_at is null),
                    '{}') as roles,
           (extract(epoch from now() - f.created_at) / 60)::int as age_minutes
      from feedback f left join participant p on p.id = f.participant_id
     where f.status = any(%s)
       and not exists (select 1 from feedback_answer a where a.feedback_id = f.id)
     order by f.created_at, f.id
     limit %s
"""


class TriageIn(core.ApiIn):
    limit: int = Field(default=5, ge=1, le=20)
    dry_run: bool = False


def open_rows(conn, limit: int) -> list[dict]:
    """Неразобранные обращения, старые первыми. Журнал — единственный признак разбора."""
    rows = conn.execute(OPEN_SQL, (list(OPEN_STATUSES), limit)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        if item.get("created_at") is not None:
            item["created_at"] = item["created_at"].isoformat()
        out.append(item)
    return out


@router.get("/tools/feedback/open")
def feedback_open(limit: int = Query(default=20, ge=1, le=100),
                  who: dict = Depends(core.require("feedback.triage"))) -> dict:
    """Очередь неразобранных: по ней видно, что сервер ещё не ответил и не доставил."""
    with core.pool.connection() as conn:
        rows = open_rows(conn, limit)
    return {"ok": True, "count": len(rows), "data": rows}


async def _model_answer(item: dict) -> tuple[str | None, dict | None]:
    """Ответ модели на вопрос. `(текст, расход)`; без ключа или при сбое — `(None, None)`.

    Сбой провайдера не превращается в выдуманный ответ: обращение остаётся
    неразобранным и попадёт в ежедневный отчёт «требует вас».
    """
    if not feedback_triage.needs_model(item["kind"]):
        return None, None
    if not llm.is_configured():
        core.log.warning("разбор обращений: LLM_API_KEY не задан, вопрос №%s ждёт человека",
                         item["id"])
        return None, None
    user = {"name": item.get("author") or "участник", "roles": list(item.get("roles") or [])}
    try:
        result = await router_llm.ask(item["text"], user, None)
    except router_llm.LlmError as exc:
        core.log.warning("разбор обращений: модель не ответила на №%s (%s)",
                         item["id"], exc.code)
        return None, None
    return result["answer"], result


def _record_usage(item: dict, usage: dict | None) -> None:
    """Токены серверного ответа — в тот же журнал `llm_usage`, что и у ассистента."""
    if not usage or not item.get("participant_id"):
        return
    with core.pool.connection() as conn:
        conn.execute("""insert into llm_usage (participant_id, model, tokens_in,
                           tokens_out, cost_rub, intent, provider)
                        values (%s,%s,%s,%s,0,'feedback',%s)""",
                     (item["participant_id"], usage.get("model"), usage.get("tokens_in"),
                      usage.get("tokens_out"), usage.get("provider")))


@router.post("/tools/feedback/triage")
async def feedback_triage_run(body: TriageIn,
                              who: dict = Depends(core.require("feedback.triage"))) -> dict:
    """Разобрать пачку обращений: вопрос и благодарность — ответ, баг и пожелание — доставка.

    Повторный прогон безопасен: разобранное уже имеет строку в `feedback_answer`
    и в очередь не попадает. Уведомление автору дополнительно защищено ключом
    `feedback:<id>:<status>`.
    """
    with core.pool.connection() as conn:
        rows = open_rows(conn, body.limit)

    answered, handed, skipped = [], [], []
    for item in rows:
        if body.dry_run:
            action = feedback_triage.planned_action(item["kind"])
            if action == "skip":
                skipped.append({"id": item["id"], "kind": item["kind"],
                                "reason": "unknown_kind"})
            elif action == "handoff":
                handed.append({"id": item["id"], "kind": item["kind"]})
            else:
                answered.append({"id": item["id"], "kind": item["kind"], "text": ""})
            continue
        answer, usage = await _model_answer(item)
        decision = feedback_triage.decide(item["kind"], answer)
        if decision["action"] == "skip":
            skipped.append({"id": item["id"], "kind": item["kind"],
                            "reason": decision["reason"]})
            continue
        if decision["action"] == "handoff":
            handed.append({"id": item["id"], "kind": item["kind"]})
        else:
            answered.append({"id": item["id"], "kind": item["kind"],
                             "text": decision["text"][:200]})
        if body.dry_run:
            continue
        admin.feedback_decide(admin.FeedbackDecideIn(
            feedback_id=item["id"], status=decision["status"],
            resolution=decision["text"], source="server", notify=True), who)
        _record_usage(item, usage)

    return {"ok": True, "dry_run": body.dry_run, "count": len(rows),
            "answered": answered, "handed": handed, "skipped": skipped}
