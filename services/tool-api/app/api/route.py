"""Маршрут свободного текста: сценарий + ответ моделью (V-026, бывший WF-01).

`POST /tools/route` — единственный вход роутера после вывода n8n из контура
(01.11.2026). Контракт запроса тот же, что был у вебхука WF-01, поэтому gateway
меняет только адрес:

    {"text": "купили ткань 12000",
     "user": {"name": "Иванов Виктор", "roles": ["администратор"]},
     "categories": ["Реквизит и костюмы", "..."]}

`categories` не обязателен: пустой список — берём закрытый справочник из базы,
чтобы модель никогда не выбирала вне его (docs/21 §2).

Ответ — тоже как у WF-01, плюс `scenario` (синоним `intent`, имя поля из карты):
`{"ok": true, "intent": "budget|schedule|dues|assistant", "scenario": <то же>,
"category": ..., "answer": ..., "confidence": ..., "engine": "tool-api-llm"}`.

Сбой модели — HTTP 200 с `ok: false` и `error: provider_unavailable`: gateway уходит
в кнопки, человек не получает «сервис недоступен». Это осознанное решение WF-01,
сохранено намеренно (docs/infrastructure/29 §3).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import Field

from .. import core, llm, router_llm
from ..errors import ApiError
from ..rules import CONFIDENCE_THRESHOLD

router = APIRouter()


class RouteIn(core.ApiIn):
    text: str = Field(default="", max_length=2000)
    user: dict = Field(default_factory=dict)
    categories: list[str] = Field(default_factory=list)
    chat_id: int | None = None
    correlation_id: str | None = None


def _closed_categories() -> list[str]:
    """Закрытый список категорий — тот же, что отдаёт `/tools/intent/categories`."""
    with core.pool.connection() as conn:
        rows = conn.execute("select name from category order by direction, name").fetchall()
    return [row["name"] for row in rows]


def _fallback(code: str, detail: str = "") -> dict:
    """Ответ «модель не смогла» по контракту WF-01: 200, ok:false, человек идёт в кнопки."""
    return {"ok": False, "intent": None, "scenario": None, "category": None,
            "answer": "", "confidence": None, "error": code,
            "detail": detail[:200], "engine": "tool-api-fallback"}


def _check_quota(pid: int | None) -> None:
    """Квота человека: роутер тратит те же токены, что и ассистент."""
    if not pid:
        return
    with core.pool.connection() as conn:
        bal = conn.execute("select tokens_left, requests_left from v_llm_balance "
                           "where participant_id = %s", (pid,)).fetchone()
    if bal and bal["tokens_left"] is not None and bal["tokens_left"] <= 0:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS,
                       "Лимит AI на этот месяц исчерпан. Доступны формы и кнопки.",
                       code="quota_exceeded")
    if bal and bal["requests_left"] is not None and bal["requests_left"] <= 0:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS,
                       "Дневной лимит запросов исчерпан. Попробуйте завтра.",
                       code="quota_exceeded")


@router.post("/tools/route")
async def route_text(body: RouteIn, who: dict = Depends(core.current)) -> dict:
    """Сценарий и ответ на свободный текст. Заменяет вебхук роутера n8n."""
    text = (body.text or "").strip()
    if not text:
        return _fallback("empty_text", "пустой текст не маршрутизируется")

    categories = body.categories or _closed_categories()
    if not llm.is_configured():
        return _fallback("llm_unavailable", "LLM_API_KEY не задан")

    pid = who.get("id")
    _check_quota(pid)

    try:
        result = await router_llm.ask(text, body.user, categories)
    except router_llm.LlmError as exc:
        core.log.warning("роутер: модель не ответила (%s), уходим в кнопки", exc.code)
        return _fallback("provider_unavailable", exc.code)

    if pid:
        with core.pool.connection() as conn:
            conn.execute("""insert into llm_usage (participant_id, model, tokens_in,
                               tokens_out, cost_rub, intent, provider)
                            values (%s,%s,%s,%s,0,'route',%s)""",
                         (pid, result["model"], result["tokens_in"],
                          result["tokens_out"], result["provider"]))
            core.audit(conn, "bot", pid, "route.classify", "participant", str(pid),
                       after={"intent": result["intent"], "category": result["category"],
                              "confidence": result["confidence"],
                              "model": result["model"]},
                       correlation_id=body.correlation_id)

    return {"ok": True, "intent": result["intent"], "scenario": result["intent"],
            "category": result["category"], "answer": result["answer"],
            "confidence": result["confidence"], "threshold": CONFIDENCE_THRESHOLD,
            "engine": "tool-api-llm"}
