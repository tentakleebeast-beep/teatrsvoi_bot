"""Стенд Mini App для браузерных тестов: настоящие страницы + детерминированные ответы API.

Зачем отдельный сервер, а не прод: тесты должны быть быстрыми, без базы, без сети и
без секретов, но при этом отдавать те же HTML и те же /webapp/tg.js, /webapp/shell.js,
что и боевой tool-api. Поэтому страницы берутся из `app.webapp_page`, а данные —
из фикстур ниже.

Запуск вручную (посмотреть глазами):

    .venv/bin/python -m uvicorn tests.webapp.server:app --port 8765

Токен: любой запрос к данным требует `Authorization: Bearer test-token`, иначе 401 —
именно так проверяется тихое обновление токена в shell.js.
"""

from __future__ import annotations

import json
import pathlib
import sys

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app import webapp_page as pages  # noqa: E402
from app.pages import files as files_pages  # noqa: E402
from app.pages import home as home_pages  # noqa: E402
from app.pages import services as services_page  # noqa: E402

STATIC = ROOT / "services" / "tool-api" / "app" / "static"
VALID_TOKEN = "test-token"

# Страницы отдаются как константы — ровно как в бою после 05b7d2b: HTML публичный,
# права приходят данными. Подпись «Управление» ставит webapp-shell.js по /webapp/nav.
PAGE_ROUTES = {
    "/webapp": pages.PAGE,
    "/webapp/tx": pages.TX_PAGE,
    "/webapp/week": pages.WEEK_PAGE,
    "/webapp/week/summary": pages.SUMMARY_PAGE,
    "/webapp/week/month": pages.MONTH_PAGE,
    "/webapp/money": pages.MONEY_PAGE,
    "/webapp/person": pages.PERSON_PAGE,
    "/webapp/feedback": pages.FEEDBACK_PAGE,
    "/webapp/slots": pages.SLOTS_PAGE,
    "/webapp/admin/t/people": pages.PEOPLE_PAGE,
    "/webapp/admin/t/dues": pages.DUES_ADMIN_PAGE,
    "/webapp/admin/t/pending": pages.PENDING_PAGE,
    "/webapp/admin/t/rights": pages.RIGHTS_PAGE,
    "/webapp/admin/t/archive": pages.ARCHIVE_PAGE,
    "/webapp/admin/t/audit": pages.AUDIT_PAGE,
    "/webapp/admin/t/agent": pages.AGENT_PAGE,
    "/webapp/admin/t/notifications": pages.NOTIFICATION_DEFAULTS_PAGE,
    "/webapp/admin/t/invites": pages.INVITES_PAGE,
    "/webapp/admin/t/feedback": pages.FEEDBACK_QUEUE_PAGE,
    "/webapp/home": home_pages.HOME_PAGE,
    "/webapp/notifications": pages.NOTIFICATIONS_PAGE,
    "/webapp/dues": pages.DUES_PAGE,
    "/webapp/broadcast": pages.BROADCAST_PAGE,
    "/webapp/ui": pages.UI_KIT_PAGE,
    "/webapp/reconcile": pages.RECONCILE_PAGE,
    "/webapp/services": services_page.SERVICES_PAGE,
    "/webapp/files": files_pages.FILES_PAGE,
    "/webapp/folder": files_pages.FOLDER_PAGE,
    "/webapp/notes": pages.NOTES_PAGE,
    "/webapp/more": home_pages.MORE_PAGE,
}

DAYS = [
    {"day": "2026-10-26", "marked": True, "status": "busy", "from": "12:00", "to": "15:00",
     "note": "", "text": "занят с 12:00 до 15:00", "exact": True},
    {"day": "2026-10-27", "marked": True, "status": "busy", "from": "00:00", "to": "24:00",
     "note": "", "text": "занят весь день", "exact": True},
    {"day": "2026-10-28", "marked": False, "status": None, "from": None, "to": None,
     "note": "", "text": None, "exact": True},
    {"day": "2026-10-29", "marked": True, "status": "free", "from": "18:00", "to": "24:00",
     "note": "", "text": "свободен с 18:00", "exact": True},
    {"day": "2026-10-30", "marked": True, "status": "busy", "from": "10:00", "to": "14:00",
     "note": "репетиция", "text": "занят с 10:00 до 14:00", "exact": True},
    {"day": "2026-10-31", "marked": False, "status": None, "from": None, "to": None,
     "note": "", "text": None, "exact": True},
    {"day": "2026-11-01", "marked": False, "status": None, "from": None, "to": None,
     "note": "", "text": None, "exact": True},
]

CATEGORIES = {
    "income": ["Взносы студийцев", "Возвраты", "Донат", "Курсы", "Подарок",
               "Продажа билетов", "Прочее (доход)", "Субаренда"],
    "expense": ["Аренда помещения", "Гонорары и доли", "Закупки к спектаклям",
                "Коммунальные платежи", "Прочее", "Расходники и хозтовары",
                "Реквизит и костюмы", "Реклама и полиграфия", "Ремонт и содержание",
                "Тренинги и занятия", "Фото/видео"],
}

PERSON = {
    "display_name": "Тестовый Участник",
    "person_key": "тестов",
    "roles": ["troupe"],
    "phone": "+7 900 000-00-00",
    "aliases": [{"alias": "тест", "alias_kind": "short"}],
    "travel_minutes": 60,
    "tariff": {"tariff": 5000},
}

app = FastAPI()


def _authorized(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    return header.lower() == f"bearer {VALID_TOKEN}"


def _guard(request: Request):
    if not _authorized(request):
        return JSONResponse({"detail": "token_expired"}, status_code=401)
    return None


# Роль на стенде задаётся заголовком: `X-SVOI-Role: participant` — участник без
# права `schedule.read.all` (сводки занятости нет), иначе организатор. Один стенд
# проверяет оба пути, когда роль определяется правами (FR-H9, карта t_70a6d8e5).
ROLE_HEADER = "x-svoi-role"


def _is_participant(request: Request) -> bool:
    return (request.headers.get(ROLE_HEADER) or "").strip().lower() == "participant"


for path, html in PAGE_ROUTES.items():
    app.add_api_route(path, (lambda html=html: HTMLResponse(html)), methods=["GET"])


CSS_FILE = pathlib.Path(__file__).resolve().parents[2] / "services" / "tool-api" / "app" / "static" / "webapp-ui.css"


@app.get("/webapp/ui.css", response_class=PlainTextResponse)
def ui_css():
    return PlainTextResponse(CSS_FILE.read_text(encoding="utf-8"), media_type="text/css")


@app.get("/webapp/tg.js")
def tg_sdk() -> Response:
    return Response((STATIC / "telegram-web-app.js").read_text(encoding="utf-8"),
                    media_type="application/javascript")


@app.get("/webapp/shell.js")
def shell_js() -> Response:
    return Response((STATIC / "webapp-shell.js").read_text(encoding="utf-8"),
                    media_type="application/javascript")


@app.post("/webapp/auth")
async def auth(request: Request) -> JSONResponse:
    body = await request.json()
    if not (body or {}).get("init_data"):
        return JSONResponse({"ok": False, "error": "init_data_missing"}, status_code=401)
    return JSONResponse({"ok": True, "token": VALID_TOKEN})


def _data(payload: dict):
    async def handler(request: Request):
        denied = _guard(request)
        return denied if denied is not None else JSONResponse(payload)
    return handler


app.add_api_route("/webapp/profile", _data({
    "ok": True,
    "profile": {"name": "Тестовый Участник", "short_aliases": ["тест"],
                "ai_manifest": "", "travel_minutes": 60, "roles": ["troupe"]},
    "projects": [],
}), methods=["GET"])

app.add_api_route("/webapp/categories", _data({
    "ok": True,
    "categories": sorted(CATEGORIES["income"] + CATEGORIES["expense"]),
    "by_direction": CATEGORIES,
}), methods=["GET"])

app.add_api_route("/webapp/my", _data({
    "ok": True, "month": "2026-10", "name": "Тестовый Участник",
    "days": [d["day"] for d in DAYS], "cells": {}, "dues": 5000, "paid": 5000,
}), methods=["GET"])

# Журнал запросов занятости. Браузерный сценарий автосохранения проверяет по нему
# порядок: запись уходит при закрытии листа, а переключение недели ждёт её
# (docs/reports/124 §3, З1/З8; план 126, Ш-03). Данные недели не меняются —
# иначе стенд поехал бы под другими тестами.
WEEK_EVENTS: list[tuple[str, dict]] = []

TEMPLATE_DAYS = [
    {"weekday": 0, "status": "busy", "from": None, "to": "15:00", "note": "",
     "text": "занят до 15:00"},
    {"weekday": 2, "status": "free", "from": "18:00", "to": None, "note": "",
     "text": "свободен с 18:00"},
]


@app.get("/webapp/availability/week")
def availability_week(request: Request, start: str | None = None) -> JSONResponse:
    denied = _guard(request)
    if denied is not None:
        return denied
    WEEK_EVENTS.append(("GET", {"start": start}))
    return JSONResponse({
        "ok": True, "start": "2026-10-26", "title": "26 октября — 01 ноября",
        "participant_id": 30, "female": False, "days": DAYS, "note": "",
    })


@app.post("/webapp/availability/week")
async def availability_week_save(request: Request) -> JSONResponse:
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    WEEK_EVENTS.append(("POST", body))
    return JSONResponse({"ok": True, "start": body.get("start"),
                         "days": len(body.get("days") or []), "hours": 16})


@app.post("/webapp/availability/template/from-week")
async def availability_template_from_week(request: Request) -> JSONResponse:
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    WEEK_EVENTS.append(("TEMPLATE_FROM_WEEK", body))
    return JSONResponse({"ok": True, "filled": len(TEMPLATE_DAYS), "days": TEMPLATE_DAYS})


@app.post("/webapp/availability/template/apply")
async def availability_template_apply(request: Request) -> JSONResponse:
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    WEEK_EVENTS.append(("TEMPLATE_APPLY", body))
    if body.get("apply"):
        return JSONResponse({"ok": True, "applied": True, "start": body.get("start"),
                             "hours": 16, "days": TEMPLATE_DAYS, "skipped": []})
    return JSONResponse({"ok": True, "applied": False, "start": body.get("start"),
                         "days": [{"day": "2026-10-26", "text": "занят до 15:00"},
                                  {"day": "2026-10-28", "text": "свободен с 18:00"}],
                         "skipped": []})


@app.post("/__reset")
def reset_events() -> dict:
    """Сброс журнала стенда перед браузерным сценарием."""
    WEEK_EVENTS.clear()
    SLOTS_EVENTS.clear()
    return {"ok": True}


# Поиск окон: две области (С6, docs/architecture/40 §4). Стенд отвечает по типу
# из запроса, чтобы браузерный тест видел разницу: репетиция — окна по людям,
# аренда — свободный зал, и никаких людей в карточке.
SLOTS_EVENTS: list[tuple[str, dict]] = []

@app.get("/webapp/slots/people")
def slots_people(request: Request) -> JSONResponse:
    """Кого можно выбрать в критериях.

    Участнику стенд отвечает 403, как продакшен роли без `schedule.slots`
    (`core._webapp_require` в `api/schedule.py`): один стенд проверяет оба пути
    экрана — с правом и без (карта t_f802d135).
    """
    denied = _guard(request)
    if denied is not None:
        return denied
    if _is_participant(request):
        return JSONResponse({"detail": "capability required: schedule.slots"},
                            status_code=403)
    return JSONResponse({
        "ok": True,
        "people": [{"id": 30, "display_name": "Тестовый Участник", "self": True},
                   {"id": 31, "display_name": "Второй Участник", "self": False}],
    })


@app.post("/webapp/slots/find")
async def slots_find(request: Request) -> JSONResponse:
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    SLOTS_EVENTS.append(("FIND", body))
    if (body.get("policy") or "rehearsal") == "rental":
        return JSONResponse({
            "ok": True, "mode": "venue", "policy": "rental",
            "policy_title": "аренда (Студия)",
            "windows": [{"day": "2026-10-28", "from_hour": 8, "to_hour": 14, "hours": 6},
                        {"day": "2026-10-28", "from_hour": 16, "to_hour": 23, "hours": 7}],
            "text": ["ср 28.10 08:00–14:00 (6 ч)", "ср 28.10 16:00–23:00 (7 ч)"],
            "venue": {"venue": "studio", "name": "Студия", "source": "events",
                      "applied": True, "busy": 1},
            "reason": "",
        })
    return JSONResponse({
        "ok": True, "mode": "policy", "policy": "rehearsal", "policy_title": "репетиция",
        "travel_minutes": {"30": 60},
        "windows": [{"day": "2026-10-29", "from_hour": 19, "to_hour": 21, "hours": 2,
                     "present": [30], "joining": [], "absent": [], "unknown": [31]}],
        "text": ["чт 29.10 19:00–21:00 (2 ч)"],
        "venue": {},
    })

app.add_api_route("/webapp/person/info", _data({
    "ok": True, "data": PERSON, "month": {"settled": False, "charged": True},
}), methods=["GET"])


# Назначение из окна (С1): экран зовёт те же preview/assign, что и чат. Стенд
# отвечает карточкой, чтобы браузерный тест проверил поля запроса — в частности,
# что тип события берётся из выбранной области, а не подменяется репетицией.
@app.post("/webapp/preview")
async def webapp_preview(request: Request) -> JSONResponse:
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    SLOTS_EVENTS.append(("PREVIEW", body))
    return JSONResponse({
        "ok": True, "proposal_id": 7, "warnings": [], "errors": [],
        "input_error": False, "can_assign": True,
        "card": {"kind": body.get("kind") or "rehearsal", "kind_title": "Тренинг",
                 "title": "Тестовый Участник", "date": body.get("date") or "2026-10-29",
                 "weekday": "четверг", "from_hour": body.get("from_hour") or 19,
                 "to_hour": body.get("to_hour") or 21, "venue": "stage",
                 "venue_name": "Сцена", "participants": [{"id": 30, "name": "Тестовый Участник"}],
                 "directors": [], "ambiguity": {}},
    })


@app.post("/webapp/assign")
async def webapp_assign(request: Request) -> JSONResponse:
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    SLOTS_EVENTS.append(("ASSIGN", body))
    return JSONResponse({"ok": True, "event_id": 99, "gcal_event_id": None,
                         "in_calendar": False, "notified": 1, "card": {}})


@app.post("/webapp/person/tariff")
async def person_tariff(request: Request):
    """Сохранение тарифа карточки участника (docs/development/81 §4).

    Отказ воспроизводим: сумма от миллиона приходит разбором полей FastAPI —
    ровно тот случай, на котором страница раньше печатала «undefined».
    """
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    tariff = body.get("tariff")
    if tariff is not None and float(tariff) >= 1_000_000:
        return JSONResponse({"detail": [{"loc": ["body", "tariff"],
                                         "msg": "Input should be a valid number",
                                         "type": "float_parsing"}]}, status_code=422)
    return JSONResponse({"ok": True, "participant_id": body.get("participant_id"),
                         "display_name": PERSON["display_name"],
                         "dues_tariff": tariff, "dues_tariff_from": "2026-12-01"})


# Разбор агентных сессий (A-5): одна сессия со шагом — экран рисует вопрос,
# шаги и ответ, не завязываясь на живую базу.
app.add_api_route("/webapp/admin/agent", _data({
    "ok": True, "limit": 50, "total": 1,
    "sessions": [{
        "id": 1, "ts": "2026-11-01T09:15:00+04:00", "participant_id": 30,
        "question": "сколько денег пришло в ноябре",
        "answer": "В ноябре пришло 120 000 руб.",
        "status": "answer", "step_count": 1, "tokens": 250, "latency_ms": 1800,
        "steps": [{"step": 1, "tool": "read_budget", "args": {"period": "2026-11"},
                   "ok": True, "error": None, "latency_ms": 15}],
    }],
}), methods=["GET"])

@app.get("/webapp/nav")
def nav(request: Request) -> JSONResponse:
    """Признаки оболочки по правам: подпись вкладки, строка сводки, кнопка сверки."""
    participant = _is_participant(request)
    return JSONResponse({"ok": True, "staff": not participant,
                         "summary": not participant, "reconcile": not participant})

app.add_api_route("/webapp/home/data", _data({
    "ok": True, "name": "Тестовый Админ", "role": "admin", "role_label": "администратор",
    "staff": True, "can_record": True,
    "attention": [
        {"title": "Заявки", "meta": "ждут решения", "url": "/webapp/admin/pending", "count": 2},
        {"title": "Обращения", "meta": "новое, 29 октября", "url": "/webapp/more", "count": 1},
        {"title": "Крупная сумма", "meta": "Аренда помещения, 150 000 ₽",
         "url": "/webapp/money"},
    ],
    "today": {"label": "сегодня, суббота",
              "event": {"time": "19:00", "title": "Репетиция", "place": "Зал на Обороны"}},
    "week": {"title": "26 октября — 1 ноября", "marked": 4, "days": 7},
    "debt": {"period": "октябрь 2026", "rest": 3000, "due": 5000},
    "more": {"management": [
        {"title": "Заявки", "meta": "ждут решения", "url": "/webapp/admin/pending", "count": 2},
        {"title": "Люди", "meta": "17 активных", "url": "/webapp/admin/people"},
        {"title": "Обращения", "meta": "открытые", "url": "/webapp/admin/feedback", "count": 1},
        {"title": "Рассылка", "meta": "14 из 17 получат", "url": "/webapp/broadcast"},
        {"title": "Сверка листов", "meta": "база против таблицы", "url": "/webapp/reconcile"},
    ], "personal": [
        {"title": "Файлы студии", "meta": "записи, фонограммы, тексты", "url": "/webapp/files"},
        {"title": "Заметки", "meta": "личные и общие, с тегами", "url": "/webapp/notes"},
    ]},
}), methods=["GET"])

@app.post("/webapp/admin/reconcile")
async def reconcile(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    # Ведёт сверку организатор: у участника права `budget.import` нет, как и на живом API.
    if _is_participant(request):
        return JSONResponse({"detail": "capability required: budget.import"}, status_code=403)
    body = await request.json()
    text = ("Расхождений нет." if body.get("apply")
            else "База: 96 операций, лист: 96 строк.\nРасхождений нет.")
    return JSONResponse({"ok": True, "text": text})


app.add_api_route("/webapp/my/dues", _data({
    "ok": True,
    "current": {"period": "2026-10", "due": 5000.0, "paid": 2000.0,
                "settled": False, "charged": True},
    "data": [
        {"period": "2026-10-01", "amount_due": 5000.0, "amount_paid": 2000.0, "status": "partial"},
        {"period": "2026-09-01", "amount_due": 5000.0, "amount_paid": 5000.0, "status": "paid"},
    ],
    "debt": 0.0,
    "tariff": {"tariff": 5000.0, "amount": 5000.0},
    # Д12: экран печатает те же строки, что и чат (`services/shared/dues.py`).
    "summary": {"period": "2026-10", "title": "октябрь 2026", "lines": [
        "Тариф: 5 000 ₽ в месяц",
        "За октябрь 2026 не учтено: внесено 2 000 ₽ из 5 000 ₽, осталось 3 000 ₽.",
    ]},
}), methods=["GET"])


app.add_api_route("/webapp/notifications/settings", _data({
    "ok": True,
    # Стенд отдаёт боевые значения: 20:00–08:00 (FR-G3). Раньше здесь были 21:00/09:00,
    # и браузерные замеры проверяли не то, что видит человек (docs/90 П-7, Ш-11).
    "settings": {"rehearsals": True, "dues": True, "schedule_changes": True,
                 "quiet_from": "20:00", "quiet_to": "08:00", "source": "personal"},
}), methods=["GET"])

# Заметки (FR-F7): своя личная и чужая общая — экран должен показать обе, но кнопку
# «Изменить» только у своей. Содержимое похоже на настоящее, включая переносы строк.
NOTES = [
    {"id": 11, "title": "Взять реквизит", "body": "Взять реквизит из склада.\nСпросить у Иры.",
     "tags": ["реквизит", "зал"], "scope": "personal", "scope_title": "личная",
     "author": "Тестовый Участник", "mine": True, "editable": True,
     "created_at": "2026-11-03T10:00:00+04:00", "updated_at": "2026-11-03T10:00:00+04:00"},
    {"id": 12, "title": "Зал на Обороны", "body": "В зале переставили станки.",
     "tags": ["зал"], "scope": "shared", "scope_title": "общая",
     "author": "Иванов Виктор", "mine": False, "editable": False,
     "created_at": "2026-11-02T18:00:00+04:00", "updated_at": "2026-11-02T18:00:00+04:00"},
]
NOTES_STATE: dict = {"notes": [dict(note) for note in NOTES], "saved": []}

app.add_api_route("/webapp/notes/list", _data({
    "ok": True, "count": len(NOTES), "notes": NOTES,
}), methods=["GET"])


@app.post("/webapp/notes/save")
async def notes_save(request: Request):
    """Сохранение заметки: стенд запоминает, что пришло, и отвечает как боевой API."""
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    NOTES_STATE["saved"].append(body)
    note = {"id": body.get("id") or 99, "title": body.get("title") or body.get("body", "")[:40],
            "body": body.get("body") or "", "tags": body.get("tags") or [],
            "scope": body.get("scope") or "personal",
            "scope_title": "общая" if body.get("scope") == "shared" else "личная",
            "author": "Тестовый Участник", "mine": True, "editable": True,
            "created_at": "2026-11-04T12:00:00+04:00", "updated_at": "2026-11-04T12:00:00+04:00"}
    return JSONResponse({"ok": True, "note": note})


@app.post("/webapp/notifications/settings")
async def notifications_save(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    return JSONResponse({"ok": True, "settings": body})


app.add_api_route("/webapp/admin/notifications/settings", _data({
    "ok": True,
    # Общие значения студии — те же 20:00–08:00 (FR-G3, FR-G17).
    "settings": {"rehearsals": True, "dues": True, "schedule_changes": True,
                 "quiet_from": "20:00", "quiet_to": "08:00"},
}), methods=["GET"])


@app.post("/webapp/admin/notifications/settings")
async def admin_notifications_save(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    return JSONResponse({"ok": True, "settings": body})


@app.post("/webapp/availability/clear")
async def availability_clear(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    # cleared — дни-отметки (Ш-53), hours — строки availability_hour: экран печатает дни
    return JSONResponse({"ok": True, "cleared": 3, "hours": 19, "start": "2026-10-26"})


@app.post("/webapp/admin/broadcast")
async def broadcast_send(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    return JSONResponse({"ok": True, "recipients": 14 if not body.get("dry_run") else 0})


app.add_api_route("/webapp/admin/broadcast/preview", _data({
    "ok": True, "total": 17, "reachable": 14, "no_telegram": 3, "self": 1,
}), methods=["GET"])


app.add_api_route("/webapp/admin/overview", _data({
    "ok": True,
    "actions": {"manage_rights": True, "set_tariff": True, "accrue_dues": True},
    "blocks": {
        "people": {"counts": {"active": 17, "pending": 2}, "data": [
            {"id": 1, "display_name": "Иванов Виктор", "roles": ["admin"], "status": "active"},
            {"id": 2, "display_name": "Валерий", "roles": ["troupe"], "status": "active"},
        ]},
        "archive": {"data": [
            {"id": 9, "display_name": "Ушедший", "roles": ["troupe"], "status": "archived"},
        ]},
        "pending": [
            {"id": 3, "display_name": "Новичок", "role": "troupe", "invite_code": "AB12CD34",
             "phone": "+7 900 000-00-00", "created_at": "2026-10-29T10:00:00"},
        ],
        "budget": {"period_title": "октябрь 2026", "data": []},
        "feedback": {"data": []},
        "audit": {"data": []},
        "invites": {"data": []},
    },
}), methods=["GET"])


app.add_api_route("/webapp/admin/dues", _data({
    "ok": True, "period": "2026-10", "period_title": "октябрь 2026",
    "totals": {"people": 17, "charged": 12, "due": 60000.0, "paid": 42000.0},
    "periods": [
        {"period": "2026-10", "title": "октябрь 2026", "charged": 12,
         "due": 60000.0, "paid": 42000.0},
    ],
    "data": [
        {"id": 1, "display_name": "Иванов Виктор", "status": "active",
         "tariff": 5000.0, "tariff_from": "2026-01-01", "amount_due": 5000.0,
         "amount_paid": 5000.0, "charged": True, "tariff_applies": True},
        {"id": 2, "display_name": "Валерий", "status": "active",
         "tariff": 5000.0, "tariff_from": "2026-01-01", "amount_due": 5000.0,
         "amount_paid": 2000.0, "charged": True, "tariff_applies": True},
        {"id": 4, "display_name": "Без Тарифа", "status": "active",
         "tariff": None, "tariff_from": None, "amount_due": 0.0,
         "amount_paid": 0.0, "charged": False, "tariff_applies": False},
    ],
}), methods=["GET"])


@app.post("/webapp/admin/dues/accrue")
async def admin_dues_accrue(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    return JSONResponse({"ok": True, "period": body.get("period"), "created": 3,
                         "skipped": 9, "amount_total": 15000.0, "rows": []})


@app.get("/webapp/admin/t/{tab}", response_class=HTMLResponse)
def admin_tab(tab: str):
    return HTMLResponse(pages.ADMIN_PAGE.replace('window.ADMIN_TAB = ""',
                                                 'window.ADMIN_TAB = "' + tab + '"'))


@app.get("/webapp/grid")
def grid(request: Request):
    """Сетка из базы: организатору — данные, участнику — 403 (FR-H9). Записи нет (П-3)."""
    denied = _guard(request)
    if denied is not None:
        return denied
    if _is_participant(request):
        return JSONResponse({"detail": "capability required: schedule.read.all"},
                            status_code=403)
    return {
        "ok": True, "month": "2026-11",
        "days": [f"2026-11-{day:02d}" for day in range(1, 31)],
        "weekdays": [], "words": {},
        "people": [{"id": 1, "name": "Студиец", "self": True}],
        "marks": {}, "diffs": [],
    }


def _month_cells() -> dict:
    """Клетки месяца на стенде: слова состояний и одно расхождение с базой.

    Одно расхождение нужно, чтобы браузерный тест видел и подчёркивание клетки,
    и строку списка «Расхождения с базой».
    """
    busy = {"state": "busy", "word": "занят", "text": "занят до 18:30",
            "base": "busy", "base_word": "занят", "diff": False, "source": "sheet"}
    free = {"state": "free", "word": "свободен", "text": "свободен",
            "base": "free", "base_word": "свободен", "diff": False, "source": "sheet"}
    diff = {"state": "busy", "word": "занят", "text": "занят",
            "base": "free", "base_word": "свободен", "diff": True, "source": "sheet",
            "diff_text": "Тестовый Участник: в таблице «занят», в базе «свободен»"}
    empty = {"state": "empty", "word": "не отмечено", "text": "", "base": "empty",
             "base_word": "не отмечено", "diff": False, "source": "base"}
    first = {f"2026-11-{day:02d}": (busy if day == 3 else free if day == 4 else empty)
             for day in range(1, 31)}
    second = {"2026-11-03": diff}
    return {"30": first, "31": second}


@app.get("/webapp/month-grid")
def month_grid(request: Request):
    """Сетка месяца: организатору — данные листа, участнику — 403 (FR-H9, З9)."""
    denied = _guard(request)
    if denied is not None:
        return denied
    if _is_participant(request):
        return JSONResponse({"detail": "capability required: schedule.read.all"},
                            status_code=403)
    return JSONResponse({
        "ok": True, "month": "2026-11",
        "days": [f"2026-11-{day:02d}" for day in range(1, 31)],
        "weekdays": [(day % 7) for day in range(1, 31)],
        "words": {"free": "свободен", "busy": "занят", "uncertain": "не уверен",
                  "empty": "не отмечено", "unparsed": "не разобрано"},
        "people": [
            {"id": 30, "name": "Тестовый Участник", "self": True, "sheet": "",
             "in_sheet": True},
            {"id": 31, "name": "Второй Студиец", "self": False, "sheet": "",
             "in_sheet": True},
        ],
        "cells": _month_cells(),
        "diffs": [{"day": "2026-11-03", "person": "Второй Студиец", "sheet": "занят",
                   "base": "свободен",
                   "text": "Второй Студиец: в таблице «занят», в базе «свободен»"}],
        "sheet": {"available": True, "reason": "", "days_missing": [],
                  "month_in_sheet": True},
        "note": "",
    })


@app.post("/webapp/tx/save")
async def tx_save(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    if body.get("purpose") == "__422__":
        return JSONResponse({"detail": [{"loc": ["body", "amount"],
                                         "msg": "Input should be a valid number",
                                         "type": "float_parsing"}]}, status_code=422)
    return JSONResponse({"ok": True,
                         "tx": {"id": 1, "period": "2026-10",
                                "amount": float(body.get("amount") or 0),
                                "category": body.get("category") or "Прочее"},
                         "sheet": "ok", "sheet_error": None})


app.add_api_route("/webapp/files/list", _data({
    "ok": True,
    "data": [
        {"id": 1, "name": "Репетиция 12 октября.m4a", "kind": "audio", "size_bytes": 18 * 1024 * 1024,
         "duration_sec": 2680, "folder": None, "folder_id": None},
        {"id": 2, "name": "Гаммы для хора.pdf", "kind": "document", "size_bytes": 420 * 1024,
         "duration_sec": None, "folder": None, "folder_id": None},
        {"id": 3, "name": "01 Увертюра.mp3", "kind": "audio", "size_bytes": 6 * 1024 * 1024,
         "duration_sec": 184, "folder": "Спектакль/Музыка", "folder_id": 7},
    ],
    "quota": {"used": 25 * 1024 * 1024, "total": 1024 * 1024 * 1024, "left": 999 * 1024 * 1024},
}))

app.add_api_route("/webapp/folder/{folder_id}/files", _data({
    "ok": True,
    "folder": {"id": 7, "path": "Спектакль/Музыка", "scope": "folder"},
    "count": 3,
    "data": [
        {"id": 31, "name": "01 Увертюра.mp3", "kind": "audio", "duration_sec": 184, "url": "about:blank"},
        {"id": 32, "name": "02 Выход героя.mp3", "kind": "audio", "duration_sec": 96, "url": "about:blank"},
        {"id": 33, "name": "03 Финал.mp3", "kind": "audio", "duration_sec": 241, "url": "about:blank"},
    ],
}))

# Действия экрана «Файлы» (Ш-08): папки, доступ, теги. Стенд отвечает так же, как
# tool-api, иначе страница показывала бы «ошибка» там, где на проде всё работает.
def _data_folder(payload: dict):
    """Ответ стенда для маршрута с номером папки в пути."""

    async def handler(request: Request, folder_id: int):
        denied = _guard(request)
        return denied if denied is not None else JSONResponse(payload)
    return handler


def _data_file(payload: dict):
    """Ответ стенда для маршрута с номером файла в пути."""

    async def handler(request: Request, file_id: int):
        denied = _guard(request)
        return denied if denied is not None else JSONResponse(payload)
    return handler


def _data_tag(payload: dict):
    """Ответ стенда для маршрута с номером тега в пути."""

    async def handler(request: Request, tag_id: int):
        denied = _guard(request)
        return denied if denied is not None else JSONResponse(payload)
    return handler


app.add_api_route("/webapp/folders", _data({
    "ok": True,
    "count": 2,
    "data": [
        {"id": 11, "parent_id": None, "name": "Спектакли", "path": "Спектакли", "depth": 1},
        {"id": 12, "parent_id": 11, "name": "Пурга", "path": "Спектакли/Пурга", "depth": 2},
    ],
}), methods=["GET"])

app.add_api_route("/webapp/folders", _data({
    "ok": True, "id": 13, "parent_id": None, "name": "Реквизит",
    "path": "Реквизит", "depth": 1,
}), methods=["POST"])

app.add_api_route("/webapp/folders/{folder_id}/rename", _data_folder({
    "ok": True, "id": 13, "name": "Реквизит и костюм", "path": "Реквизит и костюм",
    "renamed": 0,
}), methods=["POST"])

app.add_api_route("/webapp/folders/{folder_id}/delete", _data_folder({
    "ok": True, "id": 13, "folders_closed": 1, "files_detached": 0,
}), methods=["POST"])

app.add_api_route("/webapp/files/{file_id}/access", _data_file({
    "ok": True, "grant_id": 1, "owner_id": 2, "ttl_hours": 24,
    "expires_at": "2026-11-05T12:00:00+04:00",
}), methods=["POST"])

app.add_api_route("/webapp/files/{file_id}/access/revoke", _data_file({
    "ok": True, "file_id": 1, "revoked": 1,
}), methods=["POST"])

app.add_api_route("/webapp/files/{file_id}/access", _data_file({
    "ok": True,
    "count": 1,
    "data": [{"admin_id": 1, "admin_name": "Иванов Виктор", "reason": "сверка сметы",
              "expires_at": "2026-11-05T12:00:00+04:00", "created_at": "2026-11-04T12:00:00+04:00",
              "notified_at": "2026-11-04T12:00:00+04:00"}],
}), methods=["GET"])

app.add_api_route("/webapp/tags/pending", _data({
    "ok": True,
    "count": 1,
    "data": [{"id": 5, "slug": "rekvizit", "title": "Реквизит", "kind": "topic",
              "status": "draft", "uses": 2}],
}), methods=["GET"])

app.add_api_route("/webapp/tags/{tag_id}/approve", _data_tag({
    "ok": True, "id": 5, "title": "Реквизит", "status": "approved",
}), methods=["POST"])

app.add_api_route("/webapp/admin/services", _data({
    "ok": True, "overall": "warn",
    "checked_label": "Проверено 31 октября, 09:40 · это ответ этого сервера",
    "note": "",
    "rows": [
        {"key": "api", "title": "Мини-приложение",
         "meta": "ответ за 12 мс · сборка 1efffaa · работает 4д 3ч", "status": "ok"},
        {"key": "db", "title": "База данных",
         "meta": "Postgres · 17 активных, 96 операций за месяц", "status": "ok"},
        {"key": "bot", "title": "Бот", "meta": "последнее сообщение 40 мин назад",
         "status": "ok"},
        {"key": "llm", "title": "Разбор текста", "meta": "последний разбор моделью 3 ч назад",
         "status": "ok"},
        {"key": "calendar", "title": "Календарь",
         "meta": "последняя запись события вчера", "status": "warn"},
        {"key": "sheets", "title": "Таблица бюджета",
         "meta": "1 строк не записано — откройте «Сверку листов»", "status": "warn"},
    ],
}), methods=["GET"])


@app.get("/__health")
def health() -> dict:
    return {"ok": True, "pages": sorted(PAGE_ROUTES)}


def dump_state() -> str:
    """Отладочный снимок стенда — используется в отчётах тестов."""
    return json.dumps({"pages": sorted(PAGE_ROUTES), "token": VALID_TOKEN}, ensure_ascii=False)
