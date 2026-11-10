"""Очередь неразобранных обращений и серверный разбор (карта t_17f137b8).

Правило владельца 02.11.2026: на сервере нельзя разбирать баги и пожелания.
Сервер отвечает только на вопросы и благодарности, а баги и пожелания помечает
«передано в разработку»; карты из них заводит dev-среда из `feedback/inbox/`.

Маршруты:

    GET  /tools/feedback/open    — что ещё не разобрано (нет строки в журнале)
    POST /tools/feedback/triage  — разобрать пачку: ответить или доставить
    POST /webapp/feedback/presign — ссылка на загрузку скриншота обращения (Э6)
    GET  /tools/feedback/{id}/attachment/{file_id} — байты скриншота для разбора (Э6)

Оба требуют `feedback.triage`: это та же возможность, что у ручного разбора,
поэтому серверный таймер ходит тем же ключом, что и человек.

Проведение в жизнь — через существующий `feedback_decide` в `api/admin.py`:
статус, журнал `feedback_answer`, уведомление автору и аудит уже собраны там,
дублировать эту логику нельзя. Отличие одно — `source='server'`, чтобы мост
выгрузки (`feedback-watch.yml`) видел доставленные баги до того, как их заберёт
dev-среда.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from pydantic import Field

from .. import cold, core, feedback_triage, files_store, llm, router_llm
from ..errors import ApiError
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


class FeedbackPresignIn(core.ApiIn):
    """Запрос ссылки на загрузку скриншота: имя, тип и размер до отправки байтов."""

    name: str = Field(min_length=1, max_length=200)
    mime: str = Field(default="application/octet-stream", max_length=120)
    size: int = Field(default=0, ge=0)


@router.post("/webapp/feedback/presign")
def feedback_presign(body: FeedbackPresignIn,
                     who: dict = Depends(core.webapp_actor)) -> dict:
    """Ссылка на прямую загрузку скриншота в хранилище (Э6 §3.1).

    Право — `feedback.send`, а не `files.access`: у мастера первое есть, второго нет
    (`db/init/031`, docs/141 §3), и скриншот — часть обращения, а не «Файлов студии».

    Квота не проверяется (V-055 (а)): `used_bytes` здесь не вызывается вовсе — иначе
    человек с забитыми 2 ГБ не смог бы сообщить о дефекте.

    Тип проверяем до загрузки мегабайтов: не-картинка узнаётся сразу, а не после того,
    как телефон потратит трафик. `<input accept>` на экране — удобство, не защита.
    """
    viewer = core._webapp_require(who, "feedback.send")
    try:
        kind = files_store.detect_kind(body.name, body.mime, b"")
    except files_store.FileRejected as exc:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                       files_store.FEEDBACK_TYPE_MESSAGE,
                       code="file_type_not_allowed") from exc
    if kind != "image":
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                       files_store.FEEDBACK_TYPE_MESSAGE,
                       code="file_type_not_allowed")
    if body.size and body.size > files_store.FEEDBACK_MAX_BYTES:
        raise ApiError(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                       files_store.FEEDBACK_TOO_LARGE_MESSAGE, code="file_too_large")
    data = files_store.presign_put(int(viewer["id"]), body.name, body.mime)
    return {"ok": True, **data,
            "max_files": files_store.FEEDBACK_MAX_FILES,
            "max_bytes": files_store.FEEDBACK_MAX_BYTES}


@router.get("/tools/feedback/{feedback_id}/attachment/{file_id}")
def feedback_attachment(feedback_id: int, file_id: int,
                        who: dict = Depends(core.require("feedback.triage"))):
    """Байты скриншота обращения — для моста dev и разбора (Э6 §3.5).

    Порядок проверок важен: сначала «этот файл приложен к этому обращению», потом
    «файл виден читателю». Иначе чужой `file_id` подставлялся бы к любому обращению,
    и правило «файл приложен к обращению» перестало бы что-то значить.

    Отдаём потоком, а не presigned-ссылкой: проверка прав и запись в аудит остаются
    в одном месте, а dev-машине не нужно уметь ходить в хранилище прода. Холодный
    файл возвращается в горячий бакет до выдачи; нет холодной копии — 410.
    """
    with core.pool.connection() as conn:
        pair = conn.execute(
            "select 1 from feedback_attachment where feedback_id = %s and file_id = %s",
            (feedback_id, file_id)).fetchone()
        if pair is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "Файл не найден.",
                           code="file_not_found")
        row, as_admin = files_store.file_for_reader(conn, file_id, who)
        if row is None or not row["object_key"]:
            raise ApiError(status.HTTP_404_NOT_FOUND, "Файл не найден.",
                           code="file_not_found")
        restored = False
        if row.get("cold_at"):
            try:
                restored = cold.restore(conn, file_id)
            except FileNotFoundError as exc:
                raise ApiError(status.HTTP_410_GONE,
                               "Файл не найден ни в хранилище, ни в архиве.",
                               code="file_cold_missing") from exc
        conn.execute("update file_object set last_access_at = now() where id = %s",
                     (file_id,))
        core.audit(conn, "human", who["id"], "file.view", "file_object", str(file_id),
                   after={"via": "feedback", "as_admin": as_admin,
                          "feedback_id": feedback_id, "restored": restored})

    body, mime, size = files_store.open_stream(row["object_key"])

    def chunks():
        while True:
            chunk = body.read(64 * 1024)
            if not chunk:
                break
            yield chunk

    return StreamingResponse(
        chunks(), media_type=mime,
        headers={"Content-Length": str(size),
                 "Content-Disposition": files_store.attachment_header(row["name"]),
                 "X-File-Id": str(file_id)})


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
