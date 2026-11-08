"""Админка, обращения, аудит, агентские действия."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import Field

from .. import agent, cold, core, file_access, llm, masking, notification_store
from ..errors import ApiError
from ..external import DB_ERRORS, HTTP_ERRORS
from ..pages import layout as page_layout
from ..rules import CONFIDENCE_THRESHOLD, cascade_decide
from ..version import format_uptime, uptime_seconds
from ..webapp import allowed_admin_actions, allowed_admin_blocks
from . import budget as budget_mod
from . import people

router = APIRouter()

class InviteIn(core.ApiIn):
    role: Literal["admin", "org", "troupe"]
    ttl_days: int = core.INVITE_TTL_DAYS
    max_uses: int = 1
    correlation_id: str | None = None



@router.post("/admin/invite")
def create_invite(body: InviteIn,
                 who: dict = Depends(core.require("registration.code.issue"))) -> dict:
    if body.role == "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "admin не выдаётся кодом: только через ADMIN_CHAT_ID")
    code = "".join(secrets.choice(core.CODE_ALPHABET) for _ in range(8))
    expires = core.now() + timedelta(days=body.ttl_days)
    with core.pool.connection() as conn:
        row = conn.execute(
            """insert into invite_code (code, role, created_by, expires_at, max_uses)
               values (%s,%s,%s,%s,%s) returning id""",
            (code, body.role, who.get("id") or 0, expires, body.max_uses),
        ).fetchone()
        core.audit(conn, "human", who.get("id"), "invite.create", "invite_code", str(row["id"]),
              after={"role": body.role, "expires_at": expires.isoformat()},
              correlation_id=body.correlation_id)
    return {"ok": True, "code": code, "role": body.role,
            "expires_at": expires.isoformat(timespec="seconds")}



@router.get("/admin/invites")
def list_invites(who: dict = Depends(core.require("registration.code.issue"))) -> dict:
    """Открытые коды: ещё не истекли и не исчерпаны. Секретов здесь нет — только коды."""
    with core.pool.connection() as conn:
        rows = conn.execute(
            """select code, role, created_at, expires_at, max_uses, uses
                 from invite_code
                where expires_at > now() and uses < max_uses
                order by created_at desc limit 50""").fetchall()
    return {"ok": True, "data": [dict(row) for row in rows]}



class InviteRevokeIn(core.ApiIn):
    code: str
    correlation_id: str | None = None



@router.post("/admin/invite/revoke")
def revoke_invite(body: InviteRevokeIn,
                  who: dict = Depends(core.require("registration.code.issue"))) -> dict:
    """Отзыв кода: проставляем срок «сейчас», запись остаётся в истории."""
    with core.pool.connection() as conn:
        row = conn.execute(
            "update invite_code set expires_at = now() where code = %s "
            "and expires_at > now() returning id, role",
            (body.code.strip().upper(),)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "invite_not_found_or_used")
        core.audit(conn, "human", who.get("id"), "invite.revoke", "invite_code", str(row["id"]),
              after={"role": row["role"]}, correlation_id=body.correlation_id)
    return {"ok": True, "code": body.code.strip().upper()}



@router.post("/admin/bootstrap")
def bootstrap_admin(
    body: dict,
    authorization: str | None = Header(default=None),
) -> dict:
    """Первый /start от ADMIN_CHAT_ID: создаём запись администратора (docs/development/13 §A)."""
    if not hmac.compare_digest(authorization or "", f"Bearer {core.TOOL_API_TOKEN}"):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "Неверный сервисный токен",
                       code="bad_service_token")
    chat_id = int(body.get("chat_id") or 0)
    if chat_id != core.ADMIN_CHAT_ID:
        raise ApiError(status.HTTP_403_FORBIDDEN, "Этот чат не админский",
                       code="not_an_admin_chat")
    with core.pool.connection() as conn:
        row = conn.execute("select * from participant where tg_chat_id = %s",
                           (chat_id,)).fetchone()
        if row:
            return {"ok": True, "status": "exists", "participant": core._participant_out(row)}
        created = conn.execute(
            """insert into participant (tg_chat_id, display_name, person_key, role, status,
                                        is_active, activated_at)
               values (%s, %s, %s, 'admin', 'active', true, now()) returning *""",
            (chat_id, body.get("display_name") or "Администратор", "admin"),
        ).fetchone()
        core.ensure_role_row(conn, created["id"], "admin")
        core.audit(conn, "human", created["id"], "participant.bootstrap_admin", "participant",
              str(created["id"]), after={"chat_id": chat_id, "role": "admin"})
    return {"ok": True, "status": "created", "participant": core._participant_out(created)}



@router.get("/tools/audit/recent")
def audit_recent(limit: int = Query(default=20, ge=1, le=200),
                 who: dict = Depends(core.require("audit.view"))) -> dict:
    with core.pool.connection() as conn:
        rows = conn.execute(
            """select a.*, p.display_name as actor_name
               from audit_event a
               left join participant p on p.id = a.actor_id
               order by a.id desc limit %s""", (limit,)
        ).fetchall()
    return {"ok": True, "data": rows}



class FeedbackIn(core.ApiIn):
    kind: Literal["bug", "wish", "wish-fl", "question", "praise"] = "bug"
    text: str = Field(min_length=3, max_length=4000)
    context: dict | None = None



@router.post("/tools/feedback")
def feedback_create(body: FeedbackIn, who: dict = Depends(core.current)) -> dict:
    """Сообщение о проблеме или пожелание. Доступно всем ролям."""
    text = " ".join(body.text.split())
    if len(text) < 3:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "feedback_text_empty")
    with core.pool.connection() as conn:
        row = conn.execute("""
            insert into feedback (participant_id, kind, text, context)
            values (%s, %s, %s, %s) returning id, created_at""",
            (who["id"], body.kind, text,
             json.dumps(body.context or {}, ensure_ascii=False))).fetchone()
        core.audit(conn, "human", who["id"], "feedback.create", "feedback", str(row["id"]),
              after={"kind": body.kind, "text": text[:200]})
        notified = 0  # дайджест в 11:00 ставит scripts/feedback_digest.py (V-005)
    return {"ok": True, "id": row["id"], "status": "new",
            "kind_title": core.FEEDBACK_KINDS[body.kind], "notified": notified}



@router.get("/tools/feedback/mine")
def feedback_mine(limit: int = Query(default=10, ge=1, le=50),
                  who: dict = Depends(core.current)) -> dict:
    """Свои обращения и их статусы."""
    with core.pool.connection() as conn:
        rows = conn.execute("""
            select id, kind, text, status, resolution, created_at
            from feedback where participant_id = %s
            order by created_at desc limit %s""", (who["id"], limit)).fetchall()
    return {"ok": True, "data": [dict(row) for row in rows]}



@router.get("/tools/feedback")
def feedback_list(status: str | None = None,
                  open_only: bool = False,
                  limit: int = Query(default=20, ge=1, le=100),
                  who: dict = Depends(core.require("feedback.triage"))) -> dict:
    """Очередь обращений; open_only — только открытые (new, seen, planned)."""
    with core.pool.connection() as conn:
        rows = conn.execute("""
            select f.id, f.kind, f.text, f.status, f.resolution,
                   f.created_at, f.resolved_at, p.display_name as author
            from feedback f left join participant p on p.id = f.participant_id
            where (%s::text is null or f.status = %s::text)
              and (not %s::boolean or f.status in ('new', 'seen', 'planned'))
            order by f.created_at desc limit %s""", (status, status, open_only, limit)).fetchall()
        counts = {r["status"]: r["n"] for r in conn.execute(
            "select status, count(*) n from feedback group by status").fetchall()}
    return {"ok": True, "data": [dict(row) for row in rows], "counts": counts}



class FeedbackDecideIn(core.ApiIn):
    feedback_id: int
    status: Literal["seen", "planned", "done", "rejected"]
    resolution: str | None = Field(default=None, max_length=1000)
    # Журнал разборов (db/init/047): агент разбора помечает свои ответы source=agent,
    # ссылается на вопрос V-NNN и на коммит с доработкой. notify=False — разбор
    # зафиксирован, но автору ничего не отправляем (например, вопрос ещё открыт).
    # Человек и агент разбора — как раньше; `server` ставит серверный таймер
    # (карта t_17f137b8): по этому источнику мост выгрузки отличает доставленный
    # баг от уже разобранного.
    source: Literal["agent", "human", "server"] = "human"
    question_id: str | None = Field(default=None, max_length=16)
    commit_sha: str | None = Field(default=None, max_length=40)
    notify: bool = True



@router.post("/tools/feedback/decide")
def feedback_decide(body: FeedbackDecideIn,
                    who: dict = Depends(core.require("feedback.triage"))) -> dict:
    """Разбор обращения: статус и ответ автору."""
    with core.pool.connection() as conn:
        row = conn.execute("""
            update feedback set status = %s, resolution = %s,
                   resolved_at = case when %s in ('done', 'rejected') then now() else null end
            where id = %s
            returning id, participant_id, kind, text, status""",
            (body.status, body.resolution, body.status, body.feedback_id)).fetchone()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "feedback_not_found")
        answer = conn.execute("""
            insert into feedback_answer (feedback_id, source, status, text, question_id, commit_sha)
            values (%s, %s, %s, %s, %s, %s)
            returning id""",
            (row["id"], body.source, body.status, body.resolution or "",
             body.question_id, body.commit_sha)).fetchone()
        core.audit(conn, "human" if body.source == "human" else "bot", who["id"],
              "feedback.decide", "feedback", str(row["id"]),
              after={"status": body.status, "question_id": body.question_id})
        notified = 0
        if body.notify and row["participant_id"]:
            inserted = conn.execute("""
                insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at)
                values (%s, 'feedback_status', %s, %s, now())
                on conflict (dedupe_key, recipient_id) do nothing returning id""",
                (row["participant_id"],
                 json.dumps({"feedback_id": row["id"], "status": body.status,
                             "title": core.FEEDBACK_STATUS_TITLES.get(body.status, "Обращение"),
                             "body": ((body.resolution or "") + " «" + row["text"][:150] + "»").strip(),
                             "resolution": body.resolution,
                             "text": row["text"][:200]}, ensure_ascii=False),
                 f"feedback:{row['id']}:{body.status}")).fetchone()
            notified = 1 if inserted else 0
    return {"ok": True, "id": row["id"], "status": row["status"],
            "notified": notified, "answer_id": answer["id"]}



class ReconcileIn(core.ApiIn):
    apply: bool = False
    scope: str = "budget"          # budget | schedule | all



@router.post("/webapp/admin/reconcile")
def webapp_admin_reconcile(body: ReconcileIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """То же, что /admin/reconcile, но по токену Mini App."""
    core._webapp_require(who, "budget.import")
    return admin_reconcile(body, who)



class ApproveIn(core.ApiIn):
    participant_id: int
    approve: bool = True
    correlation_id: str | None = None



@router.get("/admin/pending")
def pending(who: dict = Depends(core.require("registration.approve"))) -> dict:
    with core.pool.connection() as conn:
        rows = conn.execute(
            """select p.id, p.display_name, p.role, p.created_at, p.phone,
                      i.code as invite_code
                 from participant p
                 left join invite_code i on i.id = p.invite_code_id
                where p.status = 'pending' order by p.created_at""").fetchall()
    return {"ok": True, "data": rows}



@router.post("/admin/approve")
def approve(body: ApproveIn, who: dict = Depends(core.require("registration.approve"))) -> dict:
    with core.pool.connection() as conn:
        person = conn.execute("select * from participant where id = %s",
                              (body.participant_id,)).fetchone()
        if person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "participant_not_found")
        new_status = "active" if body.approve else "archived"
        conn.execute("update participant set status = %s, activated_at = coalesce(activated_at, now())"
                     " where id = %s", (new_status, body.participant_id))
        core.audit(conn, "human", who.get("id"), "participant.approve" if body.approve
              else "participant.reject", "participant", str(body.participant_id),
              before={"status": person["status"]}, after={"status": new_status},
              correlation_id=body.correlation_id)
    return {"ok": True, "status": new_status}



class GrantIn(core.ApiIn):
    participant_id: int
    # `master` — роль педагога (db/init/061, docs/architecture/140): права пакетом,
    # дисциплины — отдельным полем ниже.
    role: Literal["admin", "org", "troupe", "master"] | None = None   # добавить роль
    enabled: bool = True                                    # False — отозвать роль
    capability: str | None = None      # выдать/отобрать возможность
    allowed: bool | None = None
    reason: str | None = None
    # Коды дисциплин, которые педагог ведёт (`discipline.code`, как в `AssignIn.discipline`
    # и `GET /tools/disciplines`). Обязательны при выдаче `master`: роль без дисциплины
    # не может выполнить свою работу (140 §2.2, V-037 вариант а).
    disciplines: list[str] | None = None



@router.get("/admin/capabilities")
def capability_catalog(who: dict = Depends(core.require("participants.view"))) -> dict:
    with core.pool.connection() as conn:
        rows = conn.execute("select code, title, grp from rbac_capability order by grp, code").fetchall()
        matrix = conn.execute(
            "select role, array_agg(capability order by capability) caps "
            "from rbac_role_capability group by role").fetchall()
    return {"ok": True, "capabilities": rows, "matrix": matrix}



def _disciplines_to_teach(conn, body: GrantIn) -> list[str]:
    """Дисциплины педагога из тела запроса: непустой список существующих кодов.

    Отказ до записи: роль без дисциплины не попадает в базу, связь не создаётся
    (140 §2.2, V-037 вариант а — «запрещать выдачу»).
    """
    codes = [code.strip() for code in (body.disciplines or []) if code and code.strip()]
    if not codes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "master_discipline_required")
    rows = conn.execute("select code from discipline where code = any(%s)",
                        (codes,)).fetchall()
    known = {row["code"] for row in rows}
    unknown = [code for code in codes if code not in known]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "unknown_discipline: " + ", ".join(unknown))
    return list(dict.fromkeys(codes))


def _teach_disciplines(conn, participant_id: int, codes: list[str]) -> None:
    """Записать «человек ведёт дисциплину»: строки `participant_discipline.relation='teacher'`.

    Повторная выдача — та же строка с `relation='teacher'`, а не вторая: ключ
    `(participant_id, discipline_id)` один (db/init/029).
    """
    conn.execute("""insert into participant_discipline (participant_id, discipline_id, relation)
                    select %s, id, 'teacher' from discipline where code = any(%s)
                    on conflict (participant_id, discipline_id) do update
                      set relation = 'teacher'""", (participant_id, list(codes)))


@router.post("/admin/grant")
def grant(body: GrantIn, who: dict = Depends(core.require("participants.manage"))) -> dict:
    with core.pool.connection() as conn:
        person = conn.execute("select * from participant where id = %s",
                              (body.participant_id,)).fetchone()
        if person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "participant_not_found")

        # Выдача педагога: дисциплина обязательна и проверяется до записи.
        teaching = (_disciplines_to_teach(conn, body)
                    if body.role == "master" and body.enabled else [])

        if body.role and body.enabled:
            # Повторная выдача возвращает прежнюю строку, а не заводит вторую:
            # ключ роли (participant_id, role) один, состояние живёт в той же строке
            # (db/init/060, docs/architecture/11 §5 п.1).
            conn.execute("""insert into participant_role (participant_id, role, granted_by)
                            values (%s,%s,%s) on conflict (participant_id, role) do update
                              set revoked_at = null, revoked_by = null,
                                  revoke_reason = null, granted_by = excluded.granted_by""",
                         (body.participant_id, body.role, who.get("id")))
            if teaching:
                # Мастер обязан попадать в состав тренинга: `_staff_by_rules` читает
                # и `participant_discipline`, и старый признак `is_teacher` (140 §6.3).
                _teach_disciplines(conn, body.participant_id, teaching)
                conn.execute("update participant set is_teacher = true "
                             "where id = %s and not is_teacher", (body.participant_id,))
            core.audit(conn, "human", who.get("id"), "rbac.role.grant", "participant",
                  str(body.participant_id),
                  after={"role": body.role, **({"disciplines": teaching} if teaching else {})})

        if body.role and not body.enabled:
            # нельзя оставить человека без ролей: он потеряет доступ целиком
            left = conn.execute("select count(*) as cnt from participant_role "
                                "where participant_id = %s and role <> %s "
                                "and revoked_at is null",
                                (body.participant_id, body.role)).fetchone()["cnt"]
            if left == 0:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "cannot_revoke_last_role")
            # Отзыв admin у себя и у последнего администратора — путь в потерю доступа
            # ко всей панели (нашёл UX-прогон 30.10.2026).
            if body.role == "admin":
                if body.participant_id == who.get("id"):
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "self_revoke_admin")
                admins = conn.execute("select count(*) as cnt from participant_role "
                                      "where role = 'admin' and revoked_at is null").fetchone()["cnt"]
                if admins <= 1:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "last_admin")
            # Отзыв — пометка строки, а не удаление: кто и когда снял право, остаётся
            # в той же таблице (docs/architecture/11 §5 п.1, Ш-07).
            revoked = conn.execute("update participant_role "
                                   "set revoked_at = now(), revoked_by = %s, revoke_reason = %s "
                                   "where participant_id = %s and role = %s "
                                   "and revoked_at is null",
                                   (who.get("id"), body.reason, body.participant_id,
                                    body.role))
            if revoked.rowcount == 0:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "role_not_granted")
            core.audit(conn, "human", who.get("id"), "rbac.role.revoke", "participant",
                  str(body.participant_id), before={"role": body.role},
                  after={"reason": body.reason})

        if body.capability and body.allowed is not None:
            conn.execute("""insert into participant_capability_override
                              (participant_id, capability, allowed, reason, granted_by)
                            values (%s,%s,%s,%s,%s)
                            on conflict (participant_id, capability) do update
                              set allowed = excluded.allowed, reason = excluded.reason""",
                         (body.participant_id, body.capability, body.allowed,
                          body.reason, who.get("id")))
            core.audit(conn, "human", who.get("id"),
                  "rbac.capability.grant" if body.allowed else "rbac.capability.revoke",
                  "participant", str(body.participant_id),
                  after={"capability": body.capability, "reason": body.reason})

        roles = core.resolve_roles(conn, body.participant_id)
        caps = core.resolve_caps(conn, body.participant_id)
    return {"ok": True, "roles": roles, "capabilities": caps}



class DisciplineIn(core.ApiIn):
    participant_id: int
    discipline: str                       # код, как в `AssignIn.discipline` и `/tools/disciplines`
    enabled: bool = True                  # False — снять связь «ведёт эту дисциплину»


@router.post("/admin/participant/discipline")
def participant_discipline(body: DisciplineIn,
                           who: dict = Depends(core.require("participants.manage"))) -> dict:
    """Дисциплины человека: выдать или снять связь `participant_discipline.relation='teacher'`.

    Право — `participants.manage`, как у `/admin/grant`: это правка связей человека, а не
    расписания. Связь предметная: роль `master` при снятии дисциплины **не** отзывается
    (140 §1) — доступ снимается отдельным отзывом роли. Последнюю дисциплину у мастера
    снять нельзя: роль без дисциплины не может выбрать тренинг (V-037 вариант а).
    """
    with core.pool.connection() as conn:
        person = conn.execute("select id, display_name from participant where id = %s",
                              (body.participant_id,)).fetchone()
        if person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "participant_not_found")
        discipline = conn.execute("select id from discipline where code = %s",
                                  (body.discipline,)).fetchone()
        if discipline is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown_discipline")

        if body.enabled:
            _teach_disciplines(conn, body.participant_id, [body.discipline])
            core.audit(conn, "human", who.get("id"), "rbac.discipline.grant", "participant",
                       str(body.participant_id), after={"discipline": body.discipline})
        else:
            if "master" in core.resolve_roles(conn, body.participant_id):
                left = conn.execute(
                    "select count(*) as n from participant_discipline "
                    "where participant_id = %s and relation = 'teacher' "
                    "and discipline_id <> %s",
                    (body.participant_id, discipline["id"])).fetchone()["n"]
                if left == 0:
                    raise HTTPException(status.HTTP_409_CONFLICT, "master_without_discipline")
            conn.execute("delete from participant_discipline "
                         "where participant_id = %s and discipline_id = %s "
                         "and relation = 'teacher'",
                         (body.participant_id, discipline["id"]))
            core.audit(conn, "human", who.get("id"), "rbac.discipline.revoke", "participant",
                       str(body.participant_id), after={"discipline": body.discipline})

        disciplines = core.disciplines_out(conn, body.participant_id)
    return {"ok": True, "participant_id": body.participant_id, "disciplines": disciplines}



def compose_prompt(role: str, person_key: str | None) -> str:
    """
    Собирает промпт из четырёх слоёв: SOUL, STYLE, роль, личные настройки.

    Личные настройки берутся из двух мест: файл `bot/manifests/participants/<key>.md`
    (ведёт администратор) и поле `participant.ai_manifest` (правит сам человек
    в анкете). Поле добавлено позже, и без него форма профиля была бы «мёртвым» вводом.
    """
    manifest = ""
    if person_key:
        with core.pool.connection() as conn:
            row = conn.execute("select ai_manifest from participant where person_key = %s",
                               (person_key,)).fetchone()
            manifest = (row["ai_manifest"] or "") if row else ""

    key = (role, person_key, manifest)
    if key in core._prompt_cache:
        return core._prompt_cache[key]
    soul = core._read(core.MANIFEST_DIR / "SOUL.md")
    style = core._read(core.MANIFEST_DIR / "STYLE.md")
    role_md = core._read(core.MANIFEST_DIR / "roles" / f"{role}.md")
    personal = core._read(core.MANIFEST_DIR / "participants" / f"{person_key}.md") if person_key else ""
    parts = [soul, style, role_md]
    if personal:
        parts.append("Личные настройки участника:\n" + personal)
    if manifest:
        parts.append("Пожелания участника к общению:\n" + manifest)
    prompt = "\n\n---\n\n".join(p for p in parts if p)
    core._prompt_cache[key] = prompt
    return prompt



class AskIn(core.ApiIn):
    chat_id: int
    text: str
    correlation_id: str | None = None



@router.post("/tools/assistant/ask")
async def assistant_ask(body: AskIn, who: dict = Depends(core.current)) -> dict:
    """Ответ LLM. Проверяет квоту человека и пишет расход токенов."""
    pid = who.get("id")
    with core.pool.connection() as conn:
        bal = conn.execute("select tokens_left, requests_left, hard_stop "
                           "from v_llm_balance where participant_id = %s", (pid,)).fetchone()

    if bal and bal["tokens_left"] is not None and bal["tokens_left"] <= 0:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS,
                       "Лимит AI на этот месяц исчерпан. Доступны формы и кнопки.",
                       code="quota_exceeded")
    if bal and bal["requests_left"] is not None and bal["requests_left"] <= 0:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS,
                       "Дневной лимит запросов исчерпан. Попробуйте завтра.",
                       code="quota_exceeded")
    if not core.LLM_API_KEY:
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE,
                       "AI-помощник не подключён: обратитесь к администратору.",
                       code="llm_unavailable")

    role = (who.get("roles") or [who.get("role")])[0]
    system = compose_prompt(role, who.get("person_key"))
    payload = {
        "model": core.LLM_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": body.text}],
        "max_tokens": llm.max_tokens(),
        "temperature": llm.temperature(),
    }
    try:
        async with httpx.AsyncClient(timeout=llm.timeout()) as client:
            r = await client.post(
                llm.chat_url(),
                headers={"Authorization": f"Bearer {core.LLM_API_KEY}",
                         "Content-Type": "application/json"},
                json=payload,
            )
        data = r.json()
        if r.status_code >= 400:
            core.log.warning("LLM вернул %s: %s", r.status_code,
                        str(data.get("error", {}).get("message", ""))[:300])
            raise ApiError(status.HTTP_502_BAD_GATEWAY,
                           "Модель вернула ошибку — попробуйте позже.",
                           code="llm_error")
        answer = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
    except ApiError:
        raise
    except (*HTTP_ERRORS, ValueError) as e:
        # адаптер LLM: отказ сети и нечитаемый JSON провайдера — это 503, а не «нет модели»
        core.log.warning("LLM недоступен: %s", e)
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE,
                       "Модель временно недоступна — попробуйте позже",
                       code="llm_unavailable") from e

    with core.pool.connection() as conn:
        conn.execute("""insert into llm_usage (participant_id, model, tokens_in, tokens_out,
                           cost_rub, intent, provider)
                        values (%s,%s,%s,%s,0,'assistant',%s)""",
                     (pid, core.LLM_MODEL, usage.get("prompt_tokens", 0),
                      usage.get("completion_tokens", 0), llm.provider()))
        core.audit(conn, "bot", pid, "assistant.answer", "participant", str(pid),
              after={"model": core.LLM_MODEL, "tokens": usage, "provider": llm.provider()},
              correlation_id=body.correlation_id)
    return {"ok": True, "answer": answer, "model": core.LLM_MODEL,
            "tokens": usage.get("total_tokens", 0)}



class IntentIn(core.ApiIn):
    text: str
    chat_id: int | None = None
    intent_llm: str | None = None
    category_llm: str | None = None
    confidence: float | None = None
    latency_ms: int | None = None
    tokens: int | None = None



@router.get("/tools/intent/categories")
def intent_categories(who: dict = Depends(core.current)) -> dict:
    """Закрытый список категорий — модель обязана выбирать только из него."""
    with core.pool.connection() as conn:
        rows = conn.execute("select name, direction from category order by direction, name").fetchall()
    return {"ok": True, "categories": [r["name"] for r in rows],
            "threshold": CONFIDENCE_THRESHOLD}



@router.post("/tools/intent/resolve")
def intent_resolve(body: IntentIn, who: dict = Depends(core.current)) -> dict:
    """
    Каскад: правила → модель из закрытого списка → кнопки. Пишет intent_log.
    Модель вызывается вызывающим кодом (n8n); сюда приходит её ответ, если он есть.
    """
    started = core.now()
    with core.pool.connection() as conn:
        allowed = [r["name"] for r in conn.execute("select name from category").fetchall()]

        decision = cascade_decide(body.text, allowed, body.category_llm, body.confidence,
                                  today=datetime.now(core.TZ).date())

        source = decision["source"] or ("buttons" if decision["need"] == "buttons" else "failed")
        latency = body.latency_ms or int((core.now() - started).total_seconds() * 1000)
        conn.execute("""
            insert into intent_log (chat_id, participant_id, text, intent_rules, intent_llm,
                                    category_rules, category_llm, category_final, source,
                                    confidence, resolved, tokens, latency_ms)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (body.chat_id, who.get("id") or None, body.text,
             "budget" if decision["ok"] else None, body.intent_llm,
             decision["category_rules"], body.category_llm, decision["category_final"],
             source, body.confidence, bool(decision["category_final"]),
             body.tokens, latency))

        # новая категория, которой нет в справочнике, — только кандидат
        if body.category_llm and body.category_llm not in allowed:
            conn.execute("""insert into category_candidate (name, proposed_text)
                            values (%s,%s)
                            on conflict (name) do update
                              set seen_count = category_candidate.seen_count + 1""",
                         (body.category_llm, body.text))

    return {"ok": True, "decision": decision}



@router.get("/tools/intent/report")
def intent_report(days: int = Query(default=7, ge=1, le=365),
                  who: dict = Depends(core.require("audit.view"))) -> dict:
    """Где правила молчат — очередь на доработку словаря."""
    with core.pool.connection() as conn:
        gaps = conn.execute("select * from v_intent_gaps limit 30").fetchall()
        top = conn.execute("""
            select text, count(*) as times from intent_log
            where source in ('buttons','human_new','failed')
              and ts > now() - (%s || ' days')::interval
            group by text order by times desc limit 20""", (days,)).fetchall()
        candidates = conn.execute("""select name, seen_count, approved from category_candidate
                                     order by approved nulls first, seen_count desc""").fetchall()
    return {"ok": True, "gaps": gaps, "top_unmatched": top, "category_candidates": candidates}



@router.post("/admin/reconcile")
def admin_reconcile(body: ReconcileIn, who: dict = Depends(core.require("budget.import"))) -> dict:
    """
    Запускает scripts/reconcile.py и возвращает его вывод.
    apply=False — показать, что изменится; apply=True — применить к БД.
    """
    import subprocess
    from pathlib import Path

    project = Path(__file__).resolve().parents[3]
    cmd = [sys.executable, "scripts/reconcile.py"]
    if body.apply:
        cmd.append("--apply")
    cmd += ["--only", body.scope, "--json"]

    proc = subprocess.run(cmd, cwd=project, capture_output=True, text=True,
                          timeout=300,
                          env={**os.environ, "PYTHONPATH": ""})
    payload = {}
    text = (proc.stdout or "").strip()
    if proc.returncode == 0:
        try:
            payload = json.loads(text.splitlines()[-1])
            text = str(payload.get("text") or "").strip()
        except (ValueError, IndexError):
            payload = {}
    else:
        text += "\n" + (proc.stderr or "")[-500:]

    with core.pool.connection() as conn:
        core.audit(conn, "human", who.get("id"), "admin.reconcile", "budget_tx", None,
              after={"apply": body.apply, "scope": body.scope},
              source={"scope": body.scope})

    lines = [row for row in text.splitlines() if row.strip()]
    budget = payload.get("budget") or {}
    return {"ok": proc.returncode == 0, "applied": body.apply,
            "text": "\n".join(lines[:40]),
            "report": {"budget": payload.get("budget"), "schedule": payload.get("schedule")},
            "sheet_unconfirmed": {"count": budget.get("лист_не_подтвердил", 0),
                                  "ids": budget.get("ids_лист_не_подтвердил", [])}}



@router.post("/webapp/feedback")
def webapp_feedback(body: FeedbackIn, who: dict = Depends(core.webapp_actor)) -> dict:
    return feedback_create(body, who)



@router.get("/webapp/feedback/mine")
def webapp_feedback_mine(limit: int = Query(default=10, ge=1, le=50),
                         who: dict = Depends(core.webapp_actor)) -> dict:
    return feedback_mine(limit, who)



# Вкладки панели — явными адресами, а не шаблоном /webapp/admin/{tab}:
# шаблон перехватывал /webapp/admin/overview и ломал JSON (дефект 30.10.2026).
@router.get("/webapp/admin/overview")
def webapp_admin_overview(who: dict = Depends(core.webapp_actor)) -> dict:
    """
    Разделы админ-панели одним ответом. Права проверяются здесь, на сервере:
    страница может скрыть вкладку, но чужие данные не отдаются.
    """
    with core.pool.connection() as conn:
        roles = core.resolve_roles(conn, who["id"]) or ["troupe"]
        caps = core.resolve_caps(conn, who["id"])
    viewer = dict(who, roles=roles, caps=caps, status=who.get("status") or "active")

    allowed = allowed_admin_blocks(caps)
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin_panel_forbidden")

    blocks: dict[str, object] = {}
    if "people" in allowed:
        rows = people.participants(include_archived=False, who=viewer)["data"]
        blocks["people"] = {
            "counts": {"active": sum(1 for p in rows if p["status"] == "active"),
                       "pending": sum(1 for p in rows if p["status"] == "pending")},
            "data": rows,
        }
    if "people" in allowed:
        # V-018: архив — отдельный экран, иначе вернуть человека неоткуда
        everyone = people.participants(include_archived=True, who=viewer)["data"]
        archived = [p for p in everyone if p["status"] == "archived"]
        # Колонка «В архиве с» была всегда пустой: поля archived_at в схеме нет.
        # Берём последнее событие архивации/отказа из журнала.
        if archived:
            ids = [p["id"] for p in archived]
            rows = conn.execute(
                "select entity_id, action, before, ts from audit_event "
                "where action in ('participant.archive','participant.reject') "
                "and entity_id = any(%s) order by id", ([str(i) for i in ids],)).fetchall()
            marks = {}
            for row in rows:
                marks[row["entity_id"]] = row          # последнее событие перекрывает
            for person in archived:
                mark = marks.get(str(person["id"]))
                if mark:
                    person["archived_at"] = str(mark["ts"])[:10]
                    person["archived_action"] = mark["action"]
        blocks["archive"] = {"data": archived}
    if "pending" in allowed:
        blocks["pending"] = pending(who=viewer)["data"]
    if "rights" in allowed:
        catalog = capability_catalog(who=viewer)
        blocks["rights"] = {"matrix": catalog["matrix"]}
    if "budget" in allowed:
        budget = budget_mod.budget_recent(limit=5, period=None, who=viewer)
        blocks["budget"] = {"period_title": budget["period_title"], "data": budget["data"]}
    if "feedback" in allowed:
        feedback = feedback_list(status=None, open_only=True, limit=10, who=viewer)
        blocks["feedback"] = {"data": feedback["data"], "counts": feedback["counts"]}
    if "audit" in allowed:
        blocks["audit"] = audit_recent(limit=12, who=viewer)["data"]
    if "invites" in allowed:
        blocks["invites"] = list_invites(who=viewer)["data"]
    return {"ok": True, "blocks": blocks, "allowed": allowed,
            "actions": allowed_admin_actions(caps)}



class WebAppTariffIn(core.ApiIn):
    participant_id: int
    tariff: float | None = Field(default=None, ge=0, le=1_000_000,
                                 description="взнос в месяц, ₽; null — снять тариф")



@router.post("/webapp/admin/approve")
def webapp_admin_approve(body: ApproveIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Подтвердить или отклонить заявку из Mini App (право `registration.approve`)."""
    return approve(body, who=core._webapp_require(who, "registration.approve"))



@router.post("/webapp/admin/grant")
def webapp_admin_grant(body: GrantIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Выдать или отозвать роль/возможность из Mini App (`participants.manage`)."""
    return grant(body, who=core._webapp_require(who, "participants.manage"))



@router.post("/webapp/admin/discipline")
def webapp_admin_discipline(body: DisciplineIn,
                            who: dict = Depends(core.webapp_actor)) -> dict:
    """Дисциплины педагога из Mini App: тот же обработчик и то же право, что у чата."""
    return participant_discipline(body, who=core._webapp_require(who, "participants.manage"))



@router.post("/webapp/admin/tariff")
def webapp_admin_tariff(body: WebAppTariffIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Тариф взноса участника из Mini App (`budget.write`, как у кнопки бота)."""
    viewer = core._webapp_require(who, "budget.write")
    return people.dues_tariff_set(body.participant_id, core.DuesTariffIn(tariff=body.tariff), who=viewer)



@router.post("/webapp/admin/sheet-retry")
def webapp_admin_sheet_retry(who: dict = Depends(core.webapp_actor)) -> dict:
    """Дописать в лист строки, до листа не дошедшие (Ш-37).

    Тот же обработчик и то же право, что у `POST /tools/budget/sheet-retry`
    и кнопки бота: второго набора правил нет.
    """
    viewer = core._webapp_require(who, "budget.write")
    return budget_mod.budget_sheet_retry(None, who=viewer)



class WebAppPersonIn(core.ApiIn):
    participant_id: int



@router.post("/webapp/admin/person")
def webapp_admin_person(body: WebAppPersonIn,
                        who: dict = Depends(core.webapp_actor)) -> dict:
    """Карточка человека в Mini App (Ш-50): то же чтение и то же право, что у бота."""
    core._webapp_require(who, "participants.view")
    return core._person_card(body.participant_id, who.get("id"), who.get("caps"))



@router.get("/webapp/admin/invites")
def webapp_admin_invites(who: dict = Depends(core.webapp_actor)) -> dict:
    """Открытые инвайт-коды в панели Mini App."""
    return list_invites(who=core._webapp_require(who, "registration.code.issue"))



class WebAppInviteIn(core.ApiIn):
    role: Literal["org", "troupe"] = "troupe"
    ttl_days: int = 7
    max_uses: int = 1



@router.post("/webapp/admin/invite")
def webapp_admin_invite(body: WebAppInviteIn,
                        who: dict = Depends(core.webapp_actor)) -> dict:
    """Выпустить код кнопкой: админ не выдаётся кодом даже из панели."""
    return create_invite(InviteIn(role=body.role, ttl_days=body.ttl_days,
                                 max_uses=body.max_uses),
                         who=core._webapp_require(who, "registration.code.issue"))



@router.post("/webapp/admin/invite-revoke")
def webapp_admin_invite_revoke(body: InviteRevokeIn,
                               who: dict = Depends(core.webapp_actor)) -> dict:
    return revoke_invite(body, who=core._webapp_require(who, "registration.code.issue"))



@router.get("/webapp/admin/feedback")
def webapp_admin_feedback_queue(who: dict = Depends(core.webapp_actor)) -> dict:
    """Очередь открытых обращений для экрана «Обращения» (docs/development/92 §2.4).

    Данные те же, что в боте: обработчик `feedback_list` один на оба входа, второго
    набора правил нет (принцип docs/development/73).
    """
    core._webapp_require(who, "feedback.triage")
    return feedback_list(status=None, open_only=True, limit=50, who=who)



@router.post("/webapp/admin/feedback/decide")
def webapp_admin_feedback_decide(body: FeedbackDecideIn,
                                 who: dict = Depends(core.webapp_actor)) -> dict:
    """Разбор обращения из Mini App: статус, ответ автору и запись в журнал."""
    core._webapp_require(who, "feedback.triage")
    return feedback_decide(body, who)



def _ago(moment) -> str:
    """Сколько прошло по-русски: «5 мин назад», «2 ч назад», «вчера»."""
    if moment is None:
        return ""
    delta = datetime.now(core.TZ) - moment.astimezone(core.TZ)
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return "только что"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    days = hours // 24
    if days == 1:
        return "вчера"
    if days < 30:
        return f"{days} дн назад"
    return page_layout.human_date(moment.astimezone(core.TZ).date())



def _freshness(moment, *, ok_hours: int, warn_days: int) -> str:
    """Состояние по свежести отметки: свежо — работает, давно — внимание."""
    if moment is None:
        return "unknown"
    age_hours = (datetime.now(core.TZ) - moment.astimezone(core.TZ)).total_seconds() / 3600
    return "ok" if age_hours <= ok_hours else "warn"



def _services_report() -> dict:
    """Состояние приложений для экрана «Сервисы» (docs/development/86 §2.5).

    Проверяем только то, что можно проверить честно: собственный ответ, базу,
    свежесть последнего сообщения в бота, последний разбор моделью, последнюю запись
    в календарь и очередь записи в лист. Никаких процентов и «примерно».
    """
    moment = datetime.now(core.TZ)
    month = moment.strftime("%Y-%m")
    db_ms: int | None = None
    db_error: str | None = None
    active = 0
    month_tx = 0
    unwritten = 0
    last_intent = last_llm = last_calendar = None
    try:
        started = datetime.now(UTC)
        with core.pool.connection() as conn:
            conn.execute("select 1")
            db_ms = max(1, int((datetime.now(UTC) - started).total_seconds() * 1000))
            active = conn.execute(
                "select count(*) as n from participant where status = 'active'").fetchone()["n"]
            month_tx = conn.execute(
                "select count(*) as n from budget_tx where period = %s", (month,)).fetchone()["n"]
            unwritten = conn.execute(
                "select count(*) as n from budget_tx where sheet_written_at is null "
                "and status = 'confirmed'").fetchone()["n"]
            last_intent = conn.execute("select max(ts) as ts from intent_log").fetchone()["ts"]
            last_llm = conn.execute(
                "select max(ts) as ts from intent_log where source = 'llm'").fetchone()["ts"]
            last_calendar = conn.execute(
                "select max(ts) as ts from audit_event "
                "where action = 'calendar.event.create'").fetchone()["ts"]
    except DB_ERRORS as error:
        # база не ответила: экран честно говорит «нет связи», остальное показывает
        db_error = str(error)[:120]

    uptime = format_uptime(uptime_seconds(core.STARTED_AT, moment))
    rows: list[dict] = [
        {"key": "api", "title": "Мини-приложение",
         "meta": f"ответ за {db_ms} мс · сборка {core.GIT_SHA[:7]} · работает {uptime}"
                 if db_ms else "отвечает, но база не ответила",
         "status": "ok"},
        {"key": "db", "title": "База данных",
         "meta": f"Postgres · {active} активных, {month_tx} операций за месяц"
                 if not db_error else "нет связи: проверьте сервер",
         "status": "down" if db_error else "ok"},
        {"key": "bot", "title": "Бот",
         "meta": f"последнее сообщение {_ago(last_intent)}" if last_intent
                 else "сообщений ещё не было",
         "status": _freshness(last_intent, ok_hours=24, warn_days=3)},
        {"key": "llm", "title": "Разбор текста",
         "meta": f"последний разбор моделью {_ago(last_llm)}" if last_llm
                 else "модель ещё не вызывалась — работают правила",
         "status": _freshness(last_llm, ok_hours=72, warn_days=7)
         if last_llm else "unknown"},
        {"key": "calendar", "title": "Календарь",
         "meta": f"последняя запись события {_ago(last_calendar)}" if last_calendar
                 else "события из приложения ещё не писали",
         "status": _freshness(last_calendar, ok_hours=24 * 7, warn_days=30)
         if last_calendar else "unknown"},
        {"key": "sheets", "title": "Таблица бюджета",
         "meta": f"{unwritten} строк не записано — откройте «Сверку листов»" if unwritten
                 else "все операции в листе",
         "status": "warn" if unwritten else "ok"},
    ]
    statuses = {row["status"] for row in rows}
    overall = "down" if "down" in statuses else ("warn" if "warn" in statuses else "ok")
    return {"ok": True, "overall": overall, "rows": rows,
            "checked_label": "Проверено " + page_layout.human_date(moment.date())
                             + f", {moment:%H:%M} · это ответ этого сервера",
            "note": "" if not db_error else "База не ответила: остальные проверки неполные."}



@router.get("/webapp/admin/services")
def webapp_admin_services(who: dict = Depends(core.webapp_actor)) -> dict:
    """Состояние приложений: только администратор (`admin.panel`)."""
    core._webapp_require(who, "admin.panel")
    return _services_report()


# --- экран разбора агентных сессий (A-5, docs/development/108 §6) --------------
#
# Что видно: вопрос, шаги с инструментами, ответ, время и цифры (шаги, токены, мс).
# Право — `admin.panel`: журнал содержит тексты вопросов людей, поэтому он не
# открыт ни организатору, ни роли с `audit.view`.
#
# Аргументы инструментов маскируются дважды: при записи (`masking.mask_steps`)
# и при чтении. Второй раз — потому что в таблице могут быть строки, записанные
# до появления маски, а критерий «в аргументах нет токенов и паролей» относится
# к тому, что отдаёт экран, а не к тому, что когда-то легло в базу.

AGENT_SESSIONS_LIMIT = 50


def agent_sessions(limit: int = AGENT_SESSIONS_LIMIT) -> list[dict]:
    """Последние агентные сессии в виде для экрана разбора, свежие первыми."""
    with core.pool.connection() as conn:
        rows = conn.execute("""
            select id, ts, participant_id, text, answer, agent_status,
                   step_count, tokens, latency_ms, steps
            from intent_log
            where agent_status is not null
            order by ts desc, id desc
            limit %s""", (limit,)).fetchall()
    sessions: list[dict] = []
    for row in rows:
        steps = masking.mask_steps(row["steps"] or [])
        sessions.append({
            "id": row["id"],
            "ts": row["ts"].isoformat() if row["ts"] else None,
            "participant_id": row["participant_id"],
            "question": row["text"],
            "answer": row["answer"],
            "status": row["agent_status"],
            "step_count": int(row["step_count"] or 0),
            "tokens": int(row["tokens"] or 0),
            "latency_ms": int(row["latency_ms"] or 0),
            "steps": steps,
        })
    return sessions


@router.get("/webapp/admin/agent")
def webapp_admin_agent(limit: int = Query(default=AGENT_SESSIONS_LIMIT, ge=1, le=200),
                       who: dict = Depends(core.webapp_actor)) -> dict:
    """Последние сессии агента для экрана разбора (право `admin.panel`)."""
    core._webapp_require(who, "admin.panel")
    sessions = agent_sessions(limit)
    return {"ok": True, "sessions": sessions, "limit": limit,
            "total": len(sessions)}



class ArchiveIn(core.ApiIn):
    participant_id: int
    reason: str = ""



def archive_participant(body: ArchiveIn, admin: dict) -> dict:
    """Архивировать участника: данные остаются, человек исчезает из активных списков.

    Один путь записи для обеих оболочек (ADR-13, docs/architecture/25 §9.2):
    `/webapp/admin/archive` (экран) и `/admin/archive` (чат) зовут эту функцию,
    а право проверяют их собственные зависимости.
    """
    if body.participant_id == admin.get("id"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "self_archive: нельзя архивировать себя")
    with core.pool.connection() as conn:
        row = conn.execute("select display_name, status from participant where id = %s",
                           (body.participant_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "participant_not_found")
        conn.execute("update participant set status = 'archived', is_active = false where id = %s",
                     (body.participant_id,))
        # Через общий audit(): ручной insert не заполнял hash и падал на NOT NULL
        core.audit(conn, "human", admin.get("id"), "participant.archive", "participant",
              str(body.participant_id),
              before={"status": row["status"], "reason": body.reason})
    return {"ok": True, "participant_id": body.participant_id, "name": row["display_name"]}


@router.post("/webapp/admin/archive")
def webapp_admin_archive(body: ArchiveIn, who: dict = Depends(core.webapp_actor)) -> dict:
    return archive_participant(body, core._webapp_require(who, "participants.manage"))


@router.post("/admin/archive")
def chat_admin_archive(body: ArchiveIn,
                       who: dict = Depends(core.require("participants.manage"))) -> dict:
    """Архив из чата: та же функция, что у экрана (AUD-3, Л6, FR-A7)."""
    return archive_participant(body, who)


def unarchive_participant(body: ArchiveIn, admin: dict) -> dict:
    """Вернуть человека из архива: архивирование мягкое, значит путь назад обязателен.

    Статус до архивации лежит в audit_event.before — восстанавливаем его, а не
    ставим 'active' наугад (человек мог быть «ожидающим»).
    """
    with core.pool.connection() as conn:
        row = conn.execute("select display_name, status from participant where id = %s",
                           (body.participant_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "participant_not_found")
        if row["status"] != "archived":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "not_archived")
        before = conn.execute(
            "select before from audit_event where action = 'participant.archive' "
            "and entity_id = %s order by id desc limit 1", (str(body.participant_id),)).fetchone()
        restored = "pending"
        raw = before["before"] if before else None
        if isinstance(raw, str):          # старые записи: jsonb пришёл строкой
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
        if isinstance(raw, dict):
            restored = raw.get("status") or "pending"
        # Отклонённая заявка возвращается в «ожидающие», а не в активные:
        # иначе возврат из архива обходит регистрацию (нашёл UX-прогон 30.10.2026).
        if restored in ("archived", "active") and before is None:
            restored = "pending"
        if restored == "archived":
            restored = "pending"
        conn.execute("update participant set status = %s, is_active = true where id = %s",
                     (restored, body.participant_id))
        core.audit(conn, "human", admin.get("id"), "participant.unarchive", "participant",
              str(body.participant_id),
              before={"status": "archived", "restored": restored})
    return {"ok": True, "participant_id": body.participant_id,
            "name": row["display_name"], "status": restored}


@router.post("/webapp/admin/unarchive")
def webapp_admin_unarchive(body: ArchiveIn, who: dict = Depends(core.webapp_actor)) -> dict:
    return unarchive_participant(body, core._webapp_require(who, "participants.manage"))


@router.post("/admin/unarchive")
def chat_admin_unarchive(body: ArchiveIn,
                         who: dict = Depends(core.require("participants.manage"))) -> dict:
    """Возврат из архива в чате: та же функция, что у экрана (AUD-3, Л6, FR-A7)."""
    return unarchive_participant(body, who)


class BlockIn(core.ApiIn):
    participant_id: int
    blocked: bool = True          # False — вернуть доступ
    reason: str = ""


def block_participant(body: BlockIn, admin: dict) -> dict:
    """Заблокировать человека или вернуть доступ — один путь записи (Ш-07, Л7, FR-A9).

    Блокировка — не архив: человек остаётся в списках и в истории, но `core.current()`
    не пускает статус, отличный от `active`, поэтому доступ закрывается сразу, без
    перезапуска и без ожидания кэша. Статус до блокировки берётся из аудита, а не
    ставится `active` наугад: человек мог быть «ожидающим», и возврат в `active`
    обошёл бы регистрацию (то же правило, что у возврата из архива).
    """
    if body.participant_id == admin.get("id"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "self_block: нельзя заблокировать себя")
    with core.pool.connection() as conn:
        row = conn.execute("select display_name, status from participant where id = %s",
                           (body.participant_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "participant_not_found")
        current = row["status"]

        if body.blocked:
            if current == "archived":
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "archived_participant: архивного блокировать нечего")
            if current == "blocked":
                # повторное нажатие — не ошибка: кнопка могла остаться от старой карточки
                return {"ok": True, "participant_id": body.participant_id,
                        "name": row["display_name"], "status": "blocked"}
            conn.execute("update participant set status = 'blocked', is_active = false "
                         "where id = %s", (body.participant_id,))
            core.audit(conn, "human", admin.get("id"), "participant.block", "participant",
                       str(body.participant_id),
                       before={"status": current}, after={"reason": body.reason})
            return {"ok": True, "participant_id": body.participant_id,
                    "name": row["display_name"], "status": "blocked"}

        if current != "blocked":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "not_blocked")
        before = conn.execute(
            "select before from audit_event where action = 'participant.block' "
            "and entity_id = %s order by id desc limit 1",
            (str(body.participant_id),)).fetchone()
        restored = "active"
        raw = before["before"] if before else None
        if isinstance(raw, str):          # старые записи: jsonb пришёл строкой
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
        if isinstance(raw, dict) and raw.get("status") in ("pending", "active"):
            restored = raw["status"]
        conn.execute("update participant set status = %s, is_active = true where id = %s",
                     (restored, body.participant_id))
        core.audit(conn, "human", admin.get("id"), "participant.unblock", "participant",
                   str(body.participant_id), before={"status": "blocked"},
                   after={"status": restored, "reason": body.reason})
    return {"ok": True, "participant_id": body.participant_id,
            "name": row["display_name"], "status": restored}


@router.post("/webapp/admin/block")
def webapp_admin_block(body: BlockIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Блокировка из Mini App: та же функция и то же право, что у чата."""
    return block_participant(body, core._webapp_require(who, "participants.manage"))


@router.post("/admin/block")
def chat_admin_block(body: BlockIn,
                     who: dict = Depends(core.require("participants.manage"))) -> dict:
    """Блокировка из чата: та же функция, что у экрана (Ш-07, Л7, FR-A9)."""
    return block_participant(body, who)



# --------------------------------------- общие настройки уведомлений (FR-G17, О5)
# П-8: общие значения задаёт администратор в админ-панели, частные остаются в профиле
# (О3) и важнее общих. Право — `admin.panel`, как у остальных разделов «Управления».

class NotificationDefaultsIn(core.ApiIn):
    """Общие значения формы: те же три типа и тихие часы, что у частного экрана."""

    rehearsals: bool = True
    dues: bool = True
    schedule_changes: bool = True
    quiet_from: str = "20:00"   # FR-G3: интервал студии по умолчанию
    quiet_to: str = "08:00"



def _defaults_out(defaults: dict) -> dict:
    return {**notification_store.kinds_to_form(defaults["kinds_muted"]),
            "quiet_from": defaults["quiet_from"], "quiet_to": defaults["quiet_to"]}



@router.get("/webapp/admin/notifications/settings")
def webapp_admin_notifications_get(who: dict = Depends(core.webapp_actor)) -> dict:
    """Общие настройки студии: типы событий и тихие часы по умолчанию."""
    core._webapp_require(who, "admin.panel")
    with core.pool.connection() as conn:
        defaults = notification_store.load_defaults(conn)
    return {"ok": True, "settings": _defaults_out(defaults)}



@router.post("/webapp/admin/notifications/settings")
def webapp_admin_notifications_save(body: NotificationDefaultsIn,
                                    who: dict = Depends(core.webapp_actor)) -> dict:
    """Сохранить общие настройки.

    Частные записи `notification_pref` не трогаются: по правилу «частные важнее общих»
    смена общего значения не переписывает то, что человек настроил себе сам.
    """
    admin = core._webapp_require(who, "admin.panel")
    for value in (body.quiet_from, body.quiet_to):
        if not core.CLOCK_RE.match(value or ""):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_time: формат ЧЧ:ММ")
    muted = notification_store.form_to_kinds(body.rehearsals, body.dues,
                                             body.schedule_changes)
    with core.pool.connection() as conn:
        before = _defaults_out(notification_store.load_defaults(conn))
        notification_store.save_defaults(conn, muted, body.quiet_from, body.quiet_to)
        after = notification_store.load_defaults(conn)
        core.audit(conn, "human", admin.get("id"), "notify.defaults.set", "app_setting",
                   "notification", before=before, after=_defaults_out(after))
    return {"ok": True, "settings": _defaults_out(after)}



class BroadcastIn(core.ApiIn):
    text: str = Field(max_length=3500)     # длиннее Telegram не примет ни у кого
    dry_run: bool = False
    request_id: str | None = None          # один текст = один идентификатор



@router.get("/webapp/admin/broadcast/preview")
def webapp_broadcast_preview(who: dict = Depends(core.webapp_actor)) -> dict:
    """Кого затронет рассылка — до отправки, а не после."""
    admin = core._webapp_require(who, "notify.send")
    with core.pool.connection() as conn:
        total = conn.execute(
            "select count(*) as n from participant where status = 'active'").fetchone()["n"]
        others = conn.execute(
            "select count(*) as n from participant where status = 'active' "
            "and tg_chat_id is not null and id <> %s", (admin.get("id"),)).fetchone()["n"]
        with_bot = conn.execute(
            "select count(*) as n from participant where status = 'active' "
            "and tg_chat_id is not null").fetchone()["n"]
    return {"ok": True, "total": total, "reachable": others,
            "no_telegram": total - with_bot, "self": 1}



@router.post("/webapp/admin/broadcast")
def webapp_admin_broadcast(body: BroadcastIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Рассылка активным: сначала счётчик адресатов, отправка — через очередь уведомлений."""
    admin = core._webapp_require(who, "notify.send")
    text = (body.text or "").strip()
    if len(text) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "short_text: нужно хотя бы пару слов")
    with core.pool.connection() as conn:
        rows = conn.execute(
            "select id from participant where status = 'active' and tg_chat_id is not null "
            "and id <> %s", (admin.get("id"),)).fetchall()
        recipients = [row["id"] for row in rows]
        if body.dry_run:
            return {"ok": True, "dry_run": True, "recipients": len(recipients)}
        for participant_id in recipients:
            conn.execute(
                """insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at)
                   values (%s, 'broadcast', %s, %s, now())
                   on conflict (dedupe_key) do nothing""",
                (participant_id, json.dumps({"text": text}, ensure_ascii=False),
                 f"broadcast:{body.request_id or int(datetime.now(UTC).timestamp())}:{participant_id}"))
        conn.execute(
            "insert into audit_event (actor_type, actor_id, action, entity_type, entity_id, before) "
            "values ('human', %s, 'broadcast.send', 'notification', null, %s)",
            (admin.get("id"), json.dumps({"recipients": len(recipients), "text": text[:200]},
                                         ensure_ascii=False)))
    return {"ok": True, "recipients": len(recipients)}



class FileAccessIn(core.ApiIn):
    reason: str = Field(min_length=3, max_length=500)
    ttl_hours: int = Field(default=24, ge=1, le=720)



@router.post("/admin/files/{file_id}/access")
def file_access_request(file_id: int, body: FileAccessIn,
                        who: dict = Depends(core.current)) -> dict:
    """Открыть админу доступ к чужому личному файлу: причина обязательна, владелец уведомлён."""
    with core.pool.connection() as conn:
        try:
            result = file_access.request(conn, who, file_id, body.reason, body.ttl_hours)
        except file_access.AccessError:
            raise
        core.audit(conn, "human", who.get("id"), "file.access.grant", "file_object", str(file_id),
              after={"reason": body.reason, "ttl_hours": result["ttl_hours"],
                     "owner_id": result["owner_id"]})
    return {"ok": True, **result}



@router.get("/admin/files/storage")
def admin_file_storage(
    authorization: str | None = Header(default=None),
    x_actor_chat_id: int | None = Header(default=None, alias="X-Actor-Chat-Id"),
) -> dict:
    """Сколько лежит горячим, сколько в холоде и сколько места на холодном томе.

    Служебный маршрут: его зовёт таймер/мониторинг, а не человек (ADR-13 §9.2 п.7,
    docs/architecture/25 §9.4 Х3). Поэтому вход — сервисный Bearer-токен, как у
    `POST /admin/bootstrap`, и актор не принимается вовсе: запрос с `X-Actor-Chat-Id`
    — это вызов из-под человека, и он получает отказ. Прежняя проверка ролей в теле
    (`set(roles) | {role} & {...}`) из-за приоритета `&` пропускала любого участника.
    """
    if not hmac.compare_digest(authorization or "", f"Bearer {core.TOOL_API_TOKEN}"):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "Неверный сервисный токен",
                       code="bad_service_token")
    if x_actor_chat_id is not None:
        raise ApiError(status.HTTP_403_FORBIDDEN,
                       "Служебный маршрут: действие от имени человека не принимается",
                       code="actor_not_allowed")
    with core.pool.connection() as conn:
        data = cold.status(conn)
    return {"ok": True, **data, "disk": cold.free_space()}



@router.post("/admin/files/cold-rotate")
def admin_cold_rotate(days: int = Query(default=180, ge=1, le=3650),
                      limit: int = Query(default=200, ge=1, le=1000),
                      authorization: str | None = Header(default=None),
                      x_actor_chat_id: int | None = Header(default=None,
                                                          alias="X-Actor-Chat-Id")) -> dict:
    """Перенести давно не открытые файлы на холодный том. Обычно это делает таймер.

    Служебный маршрут: вход — сервисный Bearer-токен, актор не принимается
    (ADR-13 §9.2 п.7, docs/architecture/25 §9.4 Х3). Запись в аудит — `system`.
    """
    if not hmac.compare_digest(authorization or "", f"Bearer {core.TOOL_API_TOKEN}"):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "Неверный сервисный токен",
                       code="bad_service_token")
    if x_actor_chat_id is not None:
        raise ApiError(status.HTTP_403_FORBIDDEN,
                       "Служебный маршрут: действие от имени человека не принимается",
                       code="actor_not_allowed")
    with core.pool.connection() as conn:
        result = cold.move_to_cold(conn, days, limit)
        core.audit(conn, "system", None, "file.cold", "file_object", None,
              after={**result, "days": days})
    return {"ok": True, **result}



class AgentFilesIn(core.ApiIn):
    text: str = Field(min_length=2, max_length=2000)
    correlation_id: str | None = None



@router.post("/tools/agent/files")
async def agent_files(body: AgentFilesIn, who: dict = Depends(core.current)) -> dict:
    """Вопрос агенту по своим файлам: он сам решает, какие инструменты вызвать.

    Квота проверяется до запроса к модели, расход пишется после — один учёт на все шаги,
    иначе цикл из пяти шагов тихо съедал бы лимит человека.
    """
    participant_id = int(who.get("id") or 0)
    if not llm.is_configured():
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE,
                       "LLM не подключён: нет LLM_API_KEY в .env.", code="llm_unavailable")
    with core.pool.connection() as conn:
        bal = conn.execute("select tokens_left, requests_left from v_llm_balance"
                           " where participant_id = %s", (participant_id,)).fetchone()
    if bal and bal["tokens_left"] is not None and bal["tokens_left"] <= 0:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS,
                       "Лимит AI на этот месяц исчерпан.", code="quota_exceeded")
    if bal and bal["requests_left"] is not None and bal["requests_left"] <= 0:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS,
                       "Дневной лимит запросов исчерпан.", code="quota_exceeded")

    with core.pool.connection() as conn:
        try:
            result = await agent.run(conn, who, body.text)
        except (*HTTP_ERRORS, agent.ModelUnavailable) as exc:
            # адаптер LLM: провайдер недоступен — человеку понятный отказ, не 500
            return {"ok": False, "error": "llm_unavailable", "message": str(exc)[:200]}
        conn.execute(
            "insert into llm_usage (participant_id, model, tokens_in, tokens_out, cost_rub,"
            " intent, provider) values (%s,%s,0,%s,0,'files_agent',%s)",
            (participant_id, agent.tools_model(), int(result.get("tokens") or 0), llm.provider()))
        core.audit(conn, "agent", participant_id, "agent.files", "participant", str(participant_id),
              after={"question": body.text[:200], "steps": result.get("steps"),
                     "tokens": result.get("tokens"), "model": agent.tools_model()},
              correlation_id=body.correlation_id)
    actions = [step for step in (result.get("steps") or []) if step.get("tool") == "propose_move"]
    return {**result, "proposed": bool(actions)}



@router.post("/agent/actions/{action_id}/apply")
def agent_action_apply(action_id: int, who: dict = Depends(core.current)) -> dict:
    """Применить предложение агента. Только человек, только своё (FR-L2)."""
    with core.pool.connection() as conn:
        result = agent.apply_action(conn, who, action_id)
        if result.get("ok"):
            core.audit(conn, "human", who.get("id"), "agent.apply", "agent_action", str(action_id),
                  after=result)
    return result



@router.post("/agent/actions/{action_id}/reject")
def agent_action_reject(action_id: int, who: dict = Depends(core.current)) -> dict:
    with core.pool.connection() as conn:
        result = agent.reject_action(conn, who, action_id)
        if result.get("ok"):
            core.audit(conn, "human", who.get("id"), "agent.reject", "agent_action", str(action_id))
    return result

