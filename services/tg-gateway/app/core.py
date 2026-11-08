"""Общие зависимости gateway: вызовы tool-api, права, контекст человека."""

from __future__ import annotations

import time
from datetime import date as _date
from datetime import datetime as _dt

import httpx
import texts

from config import TOOL_API_TOKEN, TOOL_API_URL, log


# ------------------------------------------------------------------ API-хелперы
async def api(path: str, payload: dict | None, chat_id: int, method: str = "POST",
              timeout: float = 25) -> dict:
    headers = {"Authorization": f"Bearer {TOOL_API_TOKEN}",
               "X-Actor-Chat-Id": str(chat_id)}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(
                method, f"{TOOL_API_URL}{path}",
                json=payload if method != "GET" else None,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        # tool-api перезапускается или сеть недоступна: бот обязан ответить
        # человеку, а не упасть трейсбеком в лог (найдено ревью 27.10).
        log.error("tool-api недоступен: %s %s -> %s", method, path, exc)
        return {"ok": False, "error": "tool_api_unavailable",
                "detail": texts.SERVICE_UNAVAILABLE}
    if r.status_code >= 400:
        log.warning("tool-api %s %s -> %s %s", method, path, r.status_code, r.text[:200])
    try:
        return r.json() if r.content else {}
    except ValueError:
        # не JSON в ответе: отдаём текст как есть, это не поломка кода
        return {"detail": r.text[:150]}


_user_cache: dict[int, dict] = {}


_now_cache: dict = {"ts": 0.0, "day": None}


async def today_local(chat_id: int):
    """Дата по часовому поясу проекта (Самара), а не по системному."""
    if time.time() - _now_cache["ts"] < 60 and _now_cache["day"]:
        return _now_cache["day"]
    h = await api("/health", None, chat_id, method="GET")
    day = _date.today()
    if h.get("ok") and h.get("time"):
        try:
            day = _dt.fromisoformat(h["time"]).date()
        except ValueError:
            pass
    _now_cache.update({"ts": time.time(), "day": day})
    return day


async def user_ctx(chat_id: int) -> dict:
    """Имя и роли — чтобы ассистент знал, с кем говорит."""
    if chat_id in _user_cache:
        return _user_cache[chat_id]
    me = await api("/tools/participants/me", None, chat_id, method="GET")
    if me.get("ok"):
        p = me["data"]
        raw = p.get("roles") or [p["role"]]
        ctx = {"name": p["display_name"],
               "roles": [texts.ROLE_TITLES.get(r, r) for r in raw],
               "roles_raw": raw,
               "capabilities": len(p.get("capabilities") or [])}
        _user_cache[chat_id] = ctx
        return ctx
    return {"name": "", "roles": []}


def clean_answer(text: str) -> str:
    """Если модель всё же вернула JSON — достаём только текст ответа."""
    t = (text or "").strip()
    if t.startswith("{"):
        try:
            import json as _json
            obj = _json.loads(t)
            t = str(obj.get("answer") or obj.get("text") or "").strip() or t
        except (ValueError, TypeError):
            pass
    return t


_cats_cache: dict[int, tuple[float, list[str]]] = {}


async def router_ask(chat_id: int, text: str, user: dict | None = None,
                     categories: list[str] | None = None) -> dict:
    """Маршрутизатор tool-api: сценарий, категория или свободный ответ моделью.

    Раньше это был вебхук n8n; с V-026 (01.11.2026) n8n выведен из контура и
    маршрут живёт внутри tool-api (`POST /tools/route`). Контракт ответа тот же:
    `intent`, `category`, `answer`. Пустой словарь — «роутер не ответил», дальше
    работает ассистент.
    """
    res = await api("/tools/route",
                    {"text": text, "user": user or {},
                     "categories": categories or [], "chat_id": chat_id},
                    chat_id, timeout=50)
    if res.get("ok"):
        return res
    if res.get("error"):
        # модель недоступна или квота: это не поломка бота, человек идёт в кнопки
        log.warning("router -> %s", res.get("error"))
    return {}


async def agent_ask(chat_id: int, text: str, timeout: float = 20,
                    task: bool = False) -> dict:
    """Агент tool-api: сам берёт факты инструментами и отвечает (docs/development/108).

    Пустой словарь — «агент не ответил»: вызывающий уходит на маршрутизатор и кнопки,
    человек не остаётся без ответа. Предложение записи приходит полем `propose`,
    план из нескольких шагов — полем `plan`.

    `task` включает рамку задачи «Озадачить» (решение 05.11.2026): шагов 6–8 вместо
    4, план, итог «что сделал / что не смог» и сценарий `task` в ответе и в
    `llm_usage`. Права агента при этом не меняются.
    """
    res = await api("/tools/agent/ask",
                    {"text": text, "chat_id": chat_id, "task": bool(task)},
                    chat_id, timeout=timeout)
    if res.get("ok"):
        return res
    if res.get("error"):
        # деградация к правилам приходит с ok:true и engine=rules, сюда попадают
        # только честные отказы: пустой текст, лимит шагов, квота
        log.warning("agent -> %s", res.get("error"))
    return res if res.get("error") == "cancelled" else {}


async def agent_plan(chat_id: int, plan: dict, timeout: float = 10) -> dict:
    """Оформить план одним токеном подтверждения: шаги уже проверены tool-api."""
    return await api("/tools/agent/plan", {
        "chat_id": chat_id,
        "steps": plan.get("steps") or [],
        "human_text": plan.get("human_text") or "",
    }, chat_id, timeout=timeout)


async def agent_plan_confirm(chat_id: int, token: str) -> dict:
    """Выполнить план: код идёт по шагам и останавливается на первой ошибке."""
    return await api("/tools/agent/plan/confirm",
                     {"chat_id": chat_id, "token": token}, chat_id, timeout=40)


async def agent_cancel_task(chat_id: int) -> dict:
    """«Обнулить»: идущая задача прекратится между шагами (agently/tasks.py)."""
    return await api("/tools/agent/cancel-task", {"chat_id": chat_id}, chat_id)


async def agent_propose(chat_id: int, proposal: dict, timeout: float = 10) -> dict:
    """Оформить предложение агента одноразовым токеном подтверждения (A-3).

    Аргументы проверяет tool-api тем же инструментом предложения: бот не решает,
    что попадёт в запись. Ответ без `token` — карточку показывать нельзя.
    """
    return await api("/tools/agent/propose", {
        "chat_id": chat_id,
        "tool": proposal.get("tool"),
        "args": proposal.get("args") or {},
        "human_text": proposal.get("human_text") or "",
    }, chat_id, timeout=timeout)


async def agent_confirm(chat_id: int, token: str) -> dict:
    """Подтвердить предложение: запись делает детерминированный эндпоинт tool-api."""
    return await api("/tools/agent/confirm", {"chat_id": chat_id, "token": token}, chat_id)


async def agent_cancel(chat_id: int, token: str) -> dict:
    """Отказаться от предложения: в базу бюджета не идёт ничего."""
    return await api("/tools/agent/cancel", {"chat_id": chat_id, "token": token}, chat_id)


async def parse_budget(text: str, chat_id: int) -> dict:
    """
    Разбор траты. Единственный источник правил — tool-api: он работает на тех же
    названиях категорий, что и БД.

    Раньше здесь был запасной вызов n8n (разбор траты во втором воркфлоу), но тот воркфлоу
    выключен и дублировал логику — то есть ветка была мёртвой. Убрана осознанно
    (docs/infrastructure/29 §1): второй источник истины дороже, чем кажется.
    """
    return await api("/tools/budget/parse", {"text": text}, chat_id)


async def caps_of(chat_id: int) -> list[str]:
    res = await api("/tools/me/capabilities", None, chat_id, method="GET")
    return res.get("capabilities", [])


def has(caps: list[str], *codes: str) -> bool:
    """Есть ли хотя бы одна возможность (или полный доступ «*»)."""
    return "*" in caps or any(code in caps for code in codes)


_UI_TEXTS: dict = {"at": 0.0, "texts": {}}


UI_TEXTS_TTL = 60.0


async def ui_texts_cached(chat_id: int) -> dict:
    """Тексты из `config/ui-texts.yaml` (через tool-api), кэш на минуту.

    Минута — компромисс: правка файла видна почти сразу, а каждый показ справки
    не ходит в сеть.
    """
    now = time.monotonic()
    if now - _UI_TEXTS["at"] < UI_TEXTS_TTL and _UI_TEXTS["texts"]:
        return _UI_TEXTS["texts"]
    res = await api("/tools/ui-texts", None, chat_id, method="GET")
    if res.get("ok") and isinstance(res.get("texts"), dict):
        _UI_TEXTS["at"], _UI_TEXTS["texts"] = now, res["texts"]
    return _UI_TEXTS["texts"]
