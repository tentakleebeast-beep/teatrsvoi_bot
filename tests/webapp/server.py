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

import base64
import json
import pathlib
import struct
import sys
from urllib.parse import quote

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
    "/webapp/file": files_pages.FILE_PAGE,
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

# Право `notify.send` есть только у администратора (`db/init/057`, FR-G2), поэтому
# заголовок с любой другой ролью стенд считает «без права на рассылку»
# (карта t_41a4ef6d).
NO_NOTIFY_SEND_ROLES = {"participant", "org", "master", "troupe"}


def _is_participant(request: Request) -> bool:
    return (request.headers.get(ROLE_HEADER) or "").strip().lower() == "participant"


# «Файлы студии» по решению V-039 (в) видят admin, org и master; у роли без права
# (`troupe`, `participant`) стенд отвечает 403, как tool-api, — иначе экран в тесте
# показывал бы список там, где на проде человек читает «раздел доступен…» (Б3).
NO_FILES_ACCESS_ROLES = {"participant", "troupe"}


def _files_guard(request: Request):
    """Ответ стенда для маршрутов файлов: 401 без токена, 403 без `files.access`."""
    denied = _guard(request)
    if denied is not None:
        return denied
    role = (request.headers.get(ROLE_HEADER) or "").strip().lower()
    if role in NO_FILES_ACCESS_ROLES:
        return JSONResponse({"detail": "capability required: files.access"}, status_code=403)
    return None


def _files_data(payload: dict):
    """Ответ стенда для чтения файлов: тот же `_data`, но с проверкой `files.access`."""
    async def handler(request: Request):
        denied = _files_guard(request)
        return denied if denied is not None else JSONResponse(payload)
    return handler


def _lacks_notify_send(request: Request) -> bool:
    return (request.headers.get(ROLE_HEADER) or "").strip().lower() in NO_NOTIFY_SEND_ROLES


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


@app.get("/webapp/logo.png")
def logo_png() -> Response:
    """Знак шапки Главной. Без него в браузерных тестах всегда работал бы фолбэк."""
    return Response((STATIC / "logo-svoi-word.png").read_bytes(),
                    media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


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

#: Дни, «загруженные из листа» в этом прогоне стенда: `GET /week` отдаёт их
#: отмеченными, чтобы браузерный сценарий видел настоящее последствие загрузки
#: (и «Вернуть» шёл путём `clear` + запись снимка, как в `149` §6.3).
SHEET_LOADED: dict[str, str] = {}

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
    days = []
    for item in DAYS:
        if item["day"] in SHEET_LOADED:
            days.append({**item, "marked": True, "status": "busy", "from": "00:00",
                         "to": "24:00", "note": "", "text": SHEET_LOADED[item["day"]]})
        else:
            days.append(item)
    return JSONResponse({
        "ok": True, "start": "2026-10-26", "title": "26 октября — 01 ноября",
        "participant_id": 30, "female": False, "days": days, "note": "",
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


@app.get("/webapp/availability/sheet/preview")
def availability_sheet_preview(request: Request) -> JSONResponse:
    """Своя колонка из общего листа (Э5, docs/development/149 §4.2).

    Фиксированные дни недели стенда 26.10–01.11.2026: два новых, два совпавших,
    один расходится, один пуст в листе, один не разобран — по ним браузерный
    сценарий проверяет все состояния дня и подписи переключателей.

    Форма ответа — та же, что у настоящего сервера: совпавшие дни приходят
    в `days` (их отбрасывает экран, а не сервер), `counts.same` их считает.
    Иначе браузерный тест зеленел бы на payload, которого сервер не отдаёт.
    """
    denied = _guard(request)
    if denied is not None:
        return denied
    WEEK_EVENTS.append(("SHEET_PREVIEW", {"start": request.query_params.get("start")}))
    return JSONResponse({
        "ok": True, "start": "2026-10-26", "title": "26 октября — 01 ноября",
        "source": {"spreadsheet": "Расписание студийцы", "tab": "студийцы"},
        "person": {"id": 30, "name": "Тестовый Участник", "column": "K"},
        "days": [
            {"day": "2026-10-26", "weekday": "Пн", "sheet_text": "свободен",
             "sheet_state": "free", "db_text": "занят с 12:00 до 15:00", "db_state": "busy",
             "state": "differs"},
            {"day": "2026-10-27", "weekday": "Вт", "sheet_text": "занят весь день",
             "sheet_state": "busy", "db_text": "занят весь день", "db_state": "busy",
             "state": "same"},
            {"day": "2026-10-28", "weekday": "Ср", "sheet_text": "занят весь день",
             "sheet_state": "busy", "db_text": None, "db_state": "empty", "state": "new"},
            {"day": "2026-10-29", "weekday": "Чт", "sheet_text": "",
             "sheet_state": "empty", "db_text": "свободен с 18:00", "db_state": "free",
             "state": "sheet_empty"},
            {"day": "2026-10-30", "weekday": "Пт", "sheet_text": "свободен",
             "sheet_state": "free", "db_text": "свободен", "db_state": "free",
             "state": "same"},
            {"day": "2026-10-31", "weekday": "Сб", "sheet_text": "свободен",
             "sheet_state": "free", "db_text": None, "db_state": "empty", "state": "new"},
            {"day": "2026-11-01", "weekday": "Вс", "sheet_text": "?",
             "sheet_state": "uncertain", "db_text": None, "db_state": "empty",
             "state": "unparsed"},
        ],
        "counts": {"same": 2, "new": 2, "differs": 1, "sheet_empty": 1, "unparsed": 1},
    })


@app.post("/webapp/availability/sheet/load")
async def availability_sheet_load(request: Request) -> JSONResponse:
    """Загрузка выбранных дней из листа (Э5, §4.4): пишем только то, что выбрали."""
    denied = _guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    WEEK_EVENTS.append(("SHEET_LOAD", body))
    days = body.get("days") or []
    for day in days:
        SHEET_LOADED[day] = "занят весь день"
    return JSONResponse({
        "ok": True, "applied": True, "start": body.get("start"),
        "days": [{"day": day, "action": "загружен", "text": "занят весь день"}
                 for day in days],
        "skipped": [],
    })


@app.post("/__reset")
def reset_events() -> dict:
    """Сброс журнала стенда перед браузерным сценарием."""
    WEEK_EVENTS.clear()
    SLOTS_EVENTS.clear()
    FEEDBACK_EVENTS.clear()
    SHEET_LOADED.clear()
    FOLDER_ORDER_CALLS.clear()
    LINK_CALLS.clear()
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


# Право `admin.panel` есть только у администратора, поэтому заголовок с другой
# ролью стенд считает «без права»: экран общих значений не должен рисовать форму
# тому, кому сохранять нельзя (142 §3 строка 30, карта t_5b401a86).
NO_ADMIN_PANEL_ROLES = {"participant", "org", "master", "troupe"}


@app.get("/webapp/admin/notifications/settings")
async def admin_notifications_get(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    if (request.headers.get(ROLE_HEADER) or "").strip().lower() in NO_ADMIN_PANEL_ROLES:
        return JSONResponse({"ok": False, "code": "capability_required",
                             "detail": "capability required: admin.panel"},
                            status_code=403)
    # Общие значения студии — те же 20:00–08:00 (FR-G3, FR-G17).
    return JSONResponse({"ok": True,
                         "settings": {"rehearsals": True, "dues": True,
                                      "schedule_changes": True,
                                      "quiet_from": "20:00", "quiet_to": "08:00"}})


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
    body = await request.json()
    WEEK_EVENTS.append(("CLEAR", body))
    SHEET_LOADED.clear()
    # cleared — дни-отметки (Ш-53), hours — строки availability_hour: экран печатает дни
    return JSONResponse({"ok": True, "cleared": 3, "hours": 19, "start": "2026-10-26"})


@app.post("/webapp/admin/broadcast")
async def broadcast_send(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    # Прод закрывает отправку правом `notify.send` (`api/admin.py`): стенд отвечает
    # так же, иначе роль без права «успешно» рассылала бы на тестах (карта t_41a4ef6d).
    if _lacks_notify_send(request):
        return JSONResponse({"detail": "capability required: notify.send"}, status_code=403)
    body = await request.json()
    return JSONResponse({"ok": True, "recipients": 14 if not body.get("dry_run") else 0})


@app.get("/webapp/admin/broadcast/preview")
def broadcast_preview(request: Request) -> JSONResponse:
    """Кого затронет рассылка.

    Не администратору стенд отвечает 403, как продакшен роли без `notify.send`
    (`core._webapp_require` в `api/admin.py`): один стенд проверяет оба пути
    экрана — с правом и без (FR-G2, карта t_41a4ef6d).
    """
    denied = _guard(request)
    if denied is not None:
        return denied
    if _lacks_notify_send(request):
        return JSONResponse({"detail": "capability required: notify.send"}, status_code=403)
    return JSONResponse({
        "ok": True, "total": 17, "reachable": 14, "no_telegram": 3, "self": 1,
    })


# Право `feedback.send` есть у admin/org/troupe, у мастера его нет
# (`db/init/031`, docs/141 §3). Стенд отвечает как прод после карт t_4f0c64d6 и
# t_f00525ab: право приходит полем `can_send`, пределы вложения — полем `limits`
# (страница берёт числа оттуда, а не из своей разметки), POST без права — 403.
FEEDBACK_LIMITS = {"files": 3, "bytes": 8 * 1024 * 1024,
                   "mime": ["image/png", "image/jpeg", "image/webp", "image/gif"]}

#: Очередь «моих сообщений»: одно обращение со скриншотом и одно без — иначе зелёный
#: тест доказывает разметку формы ответа, которой сервер не производит (§4.8).
FEEDBACK_ITEMS = [
    {"id": 37, "kind": "bug", "text": "Чёрный экран на «Неделе»", "status": "new",
     "resolution": None, "created_at": "2026-11-09T10:00:00",
     "attachments": [{"id": 91, "name": "IMG_0421.png", "mime": "image/png",
                      "size_bytes": 1834921}]},
    {"id": 38, "kind": "wish", "text": "Хочу отчёт за месяц", "status": "seen",
     "resolution": None, "created_at": "2026-11-09T11:00:00",
     "attachments": []},
]

#: Журнал вложений (Э6, К24): по нему видно порядок presign → PUT → POST и тело
#: отправки. Сбрасывается фикстурой теста, как и FEEDBACK_EVENTS.
FEEDBACK_CALLS: list[str] = []
FEEDBACK_POSTS: list[dict] = []
#: Подменяемые коды ответов: 500 на отправке и 403 на presign — сценарии К24.
FEEDBACK_SEND_STATUS = 200
FEEDBACK_PRESIGN_STATUS = 200
FEEDBACK_UPLOADED: dict[str, bytes] = {}


@app.get("/webapp/feedback/mine")
async def feedback_mine(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    role = (request.headers.get(ROLE_HEADER) or "").strip().lower()
    return JSONResponse({"ok": True, "data": FEEDBACK_ITEMS,
                         "can_send": role != "master", "limits": FEEDBACK_LIMITS})


@app.post("/webapp/feedback/presign")
async def feedback_presign(request: Request):
    """Ссылка на загрузку скриншота: `upload_url` ведёт на приёмник PUT этого стенда.

    Право — `feedback.send`, как в бою: у мастера его нет. Квота не проверяется
    (V-055 (а)), поэтому ответ не зависит от занятого места.
    """
    denied = _guard(request)
    if denied is not None:
        return denied
    FEEDBACK_CALLS.append("presign")
    role = (request.headers.get(ROLE_HEADER) or "").strip().lower()
    if role == "master":
        return JSONResponse({"detail": "capability required: feedback.send"},
                            status_code=403)
    if FEEDBACK_PRESIGN_STATUS != 200:
        return JSONResponse({"detail": "capability required: feedback.send"},
                            status_code=FEEDBACK_PRESIGN_STATUS)
    body = await request.json()
    name = str(body.get("name") or "shot.png").lower()
    suffix = pathlib.Path(name).suffix or ".png"
    key = f"p30/stand{len(FEEDBACK_CALLS)}{suffix}"
    return JSONResponse({"ok": True, "upload_url": f"/stand/upload/{key}",
                         "object_key": key, "expires_in": 900,
                         "max_files": FEEDBACK_LIMITS["files"],
                         "max_bytes": FEEDBACK_LIMITS["bytes"]})


@app.put("/stand/upload/{object_key:path}")
async def stand_upload(object_key: str, request: Request):
    """Приёмник PUT: хранилище стенда. Запоминает байты и факт загрузки."""
    FEEDBACK_CALLS.append("PUT")
    FEEDBACK_UPLOADED[object_key] = await request.body()
    return JSONResponse({"ok": True})


@app.post("/webapp/feedback")
async def feedback_send(request: Request):
    denied = _guard(request)
    if denied is not None:
        return denied
    if (request.headers.get(ROLE_HEADER) or "").strip().lower() == "master":
        return JSONResponse({"detail": "capability required: feedback.send"},
                            status_code=403)
    FEEDBACK_CALLS.append("feedback")
    body = await request.json()
    FEEDBACK_POSTS.append(body)
    if FEEDBACK_SEND_STATUS != 200:
        return JSONResponse({"detail": "внутренняя ошибка"},
                            status_code=FEEDBACK_SEND_STATUS)
    return JSONResponse({"ok": True, "id": 1, "status": "new",
                         "kind_title": "Вопрос", "notified": 0,
                         "attachments": [
                             {"id": 91, "name": item.get("name"),
                              "mime": item.get("mime"), "size_bytes": 1834921}
                             for item in (body.get("attachments") or [])]})


ADMIN_OVERVIEW = {
    "ok": True,
    "allowed": ["people", "pending", "rights", "budget", "feedback", "audit", "invites"],
    "actions": {"approve_pending": True, "manage_rights": True, "set_tariff": True,
                "accrue_dues": True, "issue_codes": True},
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
        # Матрица роль → возможности: в бою её отдаёт `capability_catalog` только с
        # правом `participants.manage` (`app/webapp.py:ADMIN_BLOCKS`). Без блока
        # стенд не воспроизводил ни дефект экрана 25, ни поведение с правом
        # (карта t_eb9e0661).
        "rights": {"matrix": [
            {"role": "admin", "caps": [
                "admin.panel", "audit.view", "budget.approve", "budget.import",
                "budget.read", "budget.read.self", "budget.write", "calendar.write",
                "feedback.send", "feedback.triage", "files.access", "howto.read",
                "notes.write", "notify.send", "participants.contact",
                "participants.manage", "participants.view", "quota.manage",
                "registration.approve", "registration.code.issue",
                "schedule.availability.self", "schedule.read.all", "schedule.slots",
                "schedule.write", "system.health"]},
            {"role": "org", "caps": [
                "budget.approve", "budget.import", "budget.read", "budget.read.self",
                "budget.write", "calendar.write", "feedback.send", "files.access",
                "notes.write", "participants.contact", "participants.view",
                "registration.approve", "schedule.availability.self",
                "schedule.read.all", "schedule.slots", "schedule.write", "system.health"]},
            {"role": "master", "caps": [
                "files.access", "notes.write", "participants.view",
                "schedule.availability.self", "schedule.read.all", "schedule.slots",
                "schedule.write"]},
            {"role": "troupe", "caps": [
                "budget.read.self", "feedback.send", "files.access", "notes.write",
                "schedule.availability.self", "system.health"]},
        ]},
    },
}


async def _admin_overview(request: Request) -> JSONResponse:
    """Панель по роли: master видит только людей, troupe не видит панели вовсе.

    В бою разделы приходят списком `allowed` (`allowed_admin_blocks`): у master
    есть `participants.view`, а `pending` требует `registration.approve` или
    `participants.manage`. Без этого стенд отдавал всем админа, и дефект экрана 21
    (карта t_8111b38d) на нём не воспроизводился.
    """
    denied = _guard(request)
    if denied is not None:
        return denied
    role = (request.headers.get(ROLE_HEADER) or "").strip().lower()
    if role in {"master", "participant"}:
        return JSONResponse({"ok": True, "allowed": ["people"], "actions": {},
                             "blocks": {"people": ADMIN_OVERVIEW["blocks"]["people"]}})
    if role == "org":
        # У организатора панель есть (`participants.view`, `registration.approve`,
        # `budget.read`), а `registration.code.issue` и `participants.manage` нет
        # (`db/init/008`), поэтому разделов `invites` и `rights` не приходит ни
        # блоком, ни в `allowed`, а `manage_rights` в ответе ложно — как в бою
        # (карта t_343d42fe: у org экран звал к кнопке, которой у него нет; карта
        # t_c6a13266: без `manage_rights` нет и архива; карта t_eb9e0661: без
        # права нет ни матрицы, ни формы выдачи).
        blocks = {name: ADMIN_OVERVIEW["blocks"][name] for name in ("people", "pending", "budget")}
        actions = {name: value for name, value in ADMIN_OVERVIEW["actions"].items()
                   if name != "issue_codes"}
        actions["manage_rights"] = False
        return JSONResponse({"ok": True, "allowed": ["people", "pending", "budget"],
                             "actions": actions, "blocks": blocks})
    if role == "troupe":
        return JSONResponse({"detail": "admin_panel_forbidden"}, status_code=403)
    return JSONResponse(ADMIN_OVERVIEW)


app.add_api_route("/webapp/admin/overview", _admin_overview, methods=["GET"])


# Очередь обращений: право `feedback.triage` есть только у администратора (V-005:
# обращения идут ему дайджестом), поэтому org, master и troupe получают 403 — как
# в `api/admin.py` (`core._webapp_require`). Журнал нужен браузерному тесту, чтобы
# проверить главное: без права очередь не запрашивается вовсе, а не приходит
# пустой (карта t_83a64c1f).
FEEDBACK_EVENTS: list[str] = []

NO_FEEDBACK_TRIAGE_ROLES = {"participant", "org", "master", "troupe"}

#: Образец очереди для теста скриншота (К25): одно обращение со скриншотом, одно без.
#: По умолчанию очередь стенда пуста — пустое состояние проверяет
#: `test_miniapp_feedback_rights.py`, и его нельзя ломать чужой фикстурой (§4.8).
FEEDBACK_QUEUE_SAMPLE: list[dict] = [
    {"id": 37, "kind": "bug", "text": "Чёрный экран на «Неделе»", "status": "new",
     "resolution": None, "created_at": "2026-11-09T10:00:00", "resolved_at": None,
     "author": "Валерий",
     "attachments": [{"id": 91, "name": "IMG_0421.png", "mime": "image/png",
                      "size_bytes": 1834921}]},
    {"id": 38, "kind": "wish", "text": "Хочу отчёт за месяц", "status": "new",
     "resolution": None, "created_at": "2026-11-09T11:00:00", "resolved_at": None,
     "author": "Валерий", "attachments": []},
]
FEEDBACK_QUEUE: list[dict] = []


@app.get("/webapp/admin/feedback")
def admin_feedback_queue(request: Request) -> JSONResponse:
    denied = _guard(request)
    if denied is not None:
        return denied
    role = (request.headers.get(ROLE_HEADER) or "").strip().lower()
    FEEDBACK_EVENTS.append(role or "admin")
    if role in NO_FEEDBACK_TRIAGE_ROLES:
        return JSONResponse({"detail": "capability required: feedback.triage"},
                            status_code=403)
    return JSONResponse({"ok": True, "data": FEEDBACK_QUEUE,
                         "counts": {"new": len(FEEDBACK_QUEUE), "seen": 0, "planned": 0,
                                    "done": 0, "rejected": 0}})


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


app.add_api_route("/webapp/files/list", _files_data({
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

# Папка по номеру: 7 — только звук, 8 — только документы, 9 — только картинки,
# 10 — смешанная, 11 — звук/документ/звук (очередь), 12 — отказ архива (413).
# Нужны, чтобы проверить кнопки и плеер по набору видов (А3 §6). Ссылки файлов — те
# же адреса, что отдаёт `/webapp/files/{id}/view`: иначе строка папки ведёт на экран
# «Файл не найден», и сквозной сценарий «в папке картинка → нажал → вижу картинку»
# на стенде не проходит, хотя тесты зелёные (карта t_4e6780d1).
FOLDER_FILES = {
    7: {"ok": True, "folder": {"id": 7, "path": "Спектакль/Музыка", "scope": "folder"},
        "count": 3, "data": [
            {"id": 31, "name": "01 Увертюра.mp3", "kind": "audio", "duration_sec": 184, "url": "/stand/tone.wav?track=1"},
            {"id": 32, "name": "02 Выход героя.mp3", "kind": "audio", "duration_sec": 96, "url": "/stand/tone.wav?track=2"},
            {"id": 33, "name": "03 Финал.mp3", "kind": "audio", "duration_sec": 241, "url": "/stand/tone.wav?track=3"},
        ]},
    8: {"ok": True, "folder": {"id": 8, "path": "Документы/Ноты", "scope": "folder"},
        "count": 2, "data": [
            {"id": 41, "name": "Гаммы для хора.pdf", "kind": "text", "duration_sec": None, "url": "/stand/sample.pdf"},
            {"id": 42, "name": "Программа концерта.pdf", "kind": "text", "duration_sec": None, "url": "/stand/sample.pdf"},
        ]},
    9: {"ok": True, "folder": {"id": 9, "path": "Спектакль/Афиши", "scope": "folder"},
        "count": 2, "data": [
            {"id": 51, "name": "Афиша.png", "kind": "image", "duration_sec": None, "url": "/stand/image.png"},
            {"id": 52, "name": "Сцена.png", "kind": "image", "duration_sec": None, "url": "/stand/image.png"},
        ]},
    10: {"ok": True, "folder": {"id": 10, "path": "Спектакль/Пурга", "scope": "folder"},
         "count": 3, "data": [
            {"id": 31, "name": "01 Увертюра.mp3", "kind": "audio", "duration_sec": 184, "url": "/stand/tone.wav?track=1"},
            {"id": 41, "name": "Гаммы для хора.pdf", "kind": "text", "duration_sec": None, "url": "/stand/sample.pdf"},
            {"id": 51, "name": "Афиша.png", "kind": "image", "duration_sec": None, "url": "/stand/image.png"},
        ]},
    11: {"ok": True, "folder": {"id": 11, "path": "Спектакль/Прогон", "scope": "folder"},
         "count": 3, "data": [
            {"id": 31, "name": "01 Увертюра.mp3", "kind": "audio", "duration_sec": 184, "url": "/stand/tone.wav?track=1"},
            {"id": 41, "name": "Гаммы для хора.pdf", "kind": "text", "duration_sec": None, "url": "/stand/sample.pdf"},
            {"id": 32, "name": "02 Выход героя.mp3", "kind": "audio", "duration_sec": 96, "url": "/stand/tone.wav?track=2"},
        ]},
    12: {"ok": True, "folder": {"id": 12, "path": "Спектакль/Большая", "scope": "folder"},
         "count": 1, "data": [
            {"id": 41, "name": "Гаммы для хора.pdf", "kind": "text", "duration_sec": None, "url": "/stand/sample.pdf"},
        ]},
}


def _files_folder_files():
    """Ответ стенда «файлы папки»: данные зависят от номера папки."""

    async def handler(request: Request, folder_id: int):
        denied = _files_guard(request)
        if denied is not None:
            return denied
        return JSONResponse(FOLDER_FILES.get(folder_id) or FOLDER_FILES[7])
    return handler


app.add_api_route("/webapp/folder/{folder_id}/files", _files_folder_files(), methods=["GET"])

# Экран файла (А3): один ответ на каждый вид и по отказу на 404/410.
VIEW_FILES = {
    1: {"ok": True, "id": 1, "url": "/stand/tone.wav", "expires_in": 900, "kind": "audio",
        "name": "Репетиция 12 октября.m4a", "mime": "audio/mp4",
        "size_bytes": 18 * 1024 * 1024, "ext": "m4a", "folder": None,
        "as_admin": False, "cold_restored": False},
    2: {"ok": True, "id": 2, "url": "/stand/sample.pdf", "expires_in": 900, "kind": "text",
        "name": "Гаммы для хора.pdf", "mime": "application/pdf",
        "size_bytes": 430 * 1024, "ext": "pdf", "folder": None,
        "as_admin": False, "cold_restored": False},
    3: {"ok": True, "id": 3, "url": "/stand/image.png", "expires_in": 900, "kind": "image",
        "name": "01 Увертюра.png", "mime": "image/png", "size_bytes": 6 * 1024 * 1024,
        "ext": "png", "folder": "Спектакль/Музыка", "as_admin": False,
        "cold_restored": False},
    4: {"ok": True, "id": 4, "url": "/stand/program.txt", "expires_in": 900, "kind": "text",
        "name": "Программа.txt", "mime": "text/plain", "size_bytes": 512,
        "ext": "txt", "folder": None, "as_admin": False, "cold_restored": False},
    5: {"ok": True, "id": 5, "url": "/stand/archive.bin", "expires_in": 900, "kind": "other",
        "name": "Архив.bin", "mime": "application/octet-stream", "size_bytes": 2048,
        "ext": "bin", "folder": None, "as_admin": False, "cold_restored": False},
    # Ссылка «протухла»: картинка отвечает 403, и экран обязан повторить запрос
    # ровно один раз, а потом показать заглушку (А3 §7.6).
    6: {"ok": True, "id": 6, "url": "/stand/expired.png", "expires_in": 900, "kind": "image",
        "name": "Протухшая ссылка.png", "mime": "image/png", "size_bytes": 2048,
        "ext": "png", "folder": None, "as_admin": False, "cold_restored": False},
    # Файлы папки 7: без них «Скачать» в строке получала 404, хотя строка есть.
    31: {"ok": True, "id": 31, "url": "/stand/tone.wav?track=1", "expires_in": 900,
         "kind": "audio", "name": "01 Увертюра.mp3", "mime": "audio/mpeg",
         "size_bytes": 18 * 1024 * 1024, "ext": "mp3", "folder": "Спектакль/Музыка",
         "as_admin": False, "cold_restored": False},
    32: {"ok": True, "id": 32, "url": "/stand/tone.wav?track=2", "expires_in": 900,
         "kind": "audio", "name": "02 Выход героя.mp3", "mime": "audio/mpeg",
         "size_bytes": 9 * 1024 * 1024, "ext": "mp3", "folder": "Спектакль/Музыка",
         "as_admin": False, "cold_restored": False},
    33: {"ok": True, "id": 33, "url": "/stand/tone.wav?track=3", "expires_in": 900,
         "kind": "audio", "name": "03 Финал.mp3", "mime": "audio/mpeg",
         "size_bytes": 24 * 1024 * 1024, "ext": "mp3", "folder": "Спектакль/Музыка",
         "as_admin": False, "cold_restored": False},
    # Длинное имя в две строки: экран обязан уложиться в высоту без прокрутки (К8).
    8: {"ok": True, "id": 8, "url": "/stand/image.png", "expires_in": 900, "kind": "image",
        "name": "Монолог Гамлета «Быть или не быть, вот в чём вопрос» — снимок экрана 09.11.2026.png",
        "mime": "image/png", "size_bytes": 1169163, "ext": "png",
        "folder": "Личное/Фото", "as_admin": False, "cold_restored": False},
    # Файлы папок 8–12: тот же id, что в `FOLDER_FILES`. Без этих записей строка
    # папки открывала «Файл не найден», а браузерный тест этого не замечал — он ждал
    # только адрес (карта t_4e6780d1).
    41: {"ok": True, "id": 41, "url": "/stand/sample.pdf", "expires_in": 900, "kind": "text",
         "name": "Гаммы для хора.pdf", "mime": "application/pdf", "size_bytes": 430 * 1024,
         "ext": "pdf", "folder": "Документы/Ноты", "as_admin": False, "cold_restored": False},
    42: {"ok": True, "id": 42, "url": "/stand/sample.pdf", "expires_in": 900, "kind": "text",
         "name": "Программа концерта.pdf", "mime": "application/pdf", "size_bytes": 210 * 1024,
         "ext": "pdf", "folder": "Документы/Ноты", "as_admin": False, "cold_restored": False},
    51: {"ok": True, "id": 51, "url": "/stand/image.png", "expires_in": 900, "kind": "image",
         "name": "Афиша.png", "mime": "image/png", "size_bytes": 96 * 1024,
         "ext": "png", "folder": "Спектакль/Афиши", "as_admin": False, "cold_restored": False},
    52: {"ok": True, "id": 52, "url": "/stand/image.png", "expires_in": 900, "kind": "image",
         "name": "Сцена.png", "mime": "image/png", "size_bytes": 120 * 1024,
         "ext": "png", "folder": "Спектакль/Афиши", "as_admin": False, "cold_restored": False},
    # Скриншот обращения (Э6): миниатюра в очереди разбора ведёт на экран файла, а
    # картинку берёт из этого ответа — без записи строка осталась бы без миниатюры.
    91: {"ok": True, "id": 91, "url": "/stand/image.png", "expires_in": 900,
         "kind": "image", "name": "IMG_0421.png", "mime": "image/png",
         "size_bytes": 1834921, "ext": "png", "folder": None, "as_admin": True,
         "cold_restored": False},
}

#: 1×1 прозрачный PNG: больше для проверки «картинка нарисовалась» не нужно.
PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _webapp_file_view():
    """Ответ стенда на `/webapp/files/{id}/view`: 200, 404 или 410, как tool-api."""

    async def handler(request: Request, file_id: int):
        denied = _files_guard(request)
        if denied is not None:
            return denied
        VIEW_CALLS[file_id] = VIEW_CALLS.get(file_id, 0) + 1
        if file_id == 7:
            return JSONResponse({"ok": False, "error": "file_cold_missing",
                                 "detail": "Файл не найден ни в хранилище, ни в архиве."},
                                status_code=410)
        payload = VIEW_FILES.get(file_id)
        if payload is None:
            return JSONResponse({"ok": False, "error": "file_not_found",
                                 "detail": "Файл не найден."}, status_code=404)
        return JSONResponse(payload)
    return handler


#: Сколько раз стенд выдавал ссылку на файл: по этому счёту видно, что экран
#: повторил запрос ровно один раз, а не зациклился (А3 §7.6).
VIEW_CALLS: dict[int, int] = {}


@app.get("/stand/view-calls/{file_id}")
def stand_view_calls(file_id: int, reset: int = 0) -> dict:
    count = VIEW_CALLS.get(file_id, 0)
    if reset:
        VIEW_CALLS[file_id] = 0
    return {"file_id": file_id, "count": count}


app.add_api_route("/webapp/files/{file_id}/view", _webapp_file_view(), methods=["GET"])


#: Журналы папки: по ним видно, что экран действительно сходил в `POST /order`,
#: а не только переставил строки на месте (А3 §6, карта t_4e6780d1).
FOLDER_ORDER_CALLS: dict[int, list[dict]] = {}


@app.post("/webapp/folder/{folder_id}/order")
async def webapp_folder_order(folder_id: int, request: Request):
    """Порядок треков: стенд запоминает тело, чтобы тест проверил новый список id."""
    denied = _files_guard(request)
    if denied is not None:
        return denied
    body = await request.json()
    FOLDER_ORDER_CALLS.setdefault(folder_id, []).append(body)
    return JSONResponse({"ok": True, "updated": len(body.get("file_ids") or [])})


@app.get("/stand/folder-order/{folder_id}")
def stand_folder_order(folder_id: int, reset: int = 0) -> dict:
    calls = list(FOLDER_ORDER_CALLS.get(folder_id) or [])
    if reset:
        FOLDER_ORDER_CALLS[folder_id] = []
    return {"folder_id": folder_id, "count": len(calls),
            "last": calls[-1] if calls else None}


#: Журнал выдачи ссылок на скачивание: по нему видно, что экран сходил в
#: `GET /webapp/files/{id}/link`, а не только переставил разметку (Б2 §8).
LINK_CALLS: dict[int, int] = {}


def _webapp_file_link():
    """Ответ стенда на `/webapp/files/{id}/link`: 200 со ссылкой, 404 и 500.

    Адрес ссылки ведёт на страницу стенда с именем файла: по нему тест видит,
    что браузеру открыли именно этот файл, а не что попало. Файл 6 — обрыв
    связи (500), как и у `/view`, чтобы проверить баннер оболочки (Б2 §6).
    """

    async def handler(request: Request, file_id: int):
        denied = _files_guard(request)
        if denied is not None:
            return denied
        LINK_CALLS[file_id] = LINK_CALLS.get(file_id, 0) + 1
        if file_id == 6:
            return JSONResponse({"ok": False, "code": "storage_unavailable",
                                 "detail": "Сервис файлов не ответил."},
                                status_code=500)
        payload = VIEW_FILES.get(file_id)
        if payload is None:
            return JSONResponse({"ok": False, "error": "file_not_found",
                                 "detail": "Файл не найден."}, status_code=404)
        name = payload["name"]
        return JSONResponse({"ok": True, "url": f"/stand/download/{file_id}/{quote(name)}",
                             "expires_in": 900, "name": name,
                             "size_bytes": payload["size_bytes"]})
    return handler


@app.get("/stand/link-calls/{file_id}")
def stand_link_calls(file_id: int, reset: int = 0) -> dict:
    count = LINK_CALLS.get(file_id, 0)
    if reset:
        LINK_CALLS[file_id] = 0
    return {"file_id": file_id, "count": count}


app.add_api_route("/webapp/files/{file_id}/link", _webapp_file_link(), methods=["GET"])


@app.get("/stand/image.png")
def stand_image() -> Response:
    return Response(PIXEL_PNG, media_type="image/png")


#: Минимальный PDF: ссылка «Открыть в браузере» у документа должна вести на живой
#: файл, а не на 404 стенда (карта t_4e6780d1). Содержимое тестами не читается.
SAMPLE_PDF = (b"%PDF-1.4\n"
              b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
              b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
              b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
              b"trailer<</Root 1 0 R>>\n%%EOF\n")


@app.get("/stand/sample.pdf")
def stand_sample_pdf() -> Response:
    return Response(SAMPLE_PDF, media_type="application/pdf")


#: Тишина 0.05 с, 8 кГц, 8 бит: настоящий WAV, чтобы `<audio>` на стенде не получал
#: `MEDIA_ERR_SRC_NOT_SUPPORTED` от `about:blank`. На Linux-WebKit такой отказ
#: срабатывает сразу, экран делает свой единственный повтор и убирает сцену вместе
#: с плеером — тест «плеер на экране» падал по таймауту (CI `webapp-webkit`, 09.11.2026).
TONE_WAV = (b"RIFF" + struct.pack("<I", 36 + 400) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
            + b"data" + struct.pack("<I", 400) + bytes(400))


@app.get("/stand/tone.wav")
def stand_tone() -> Response:
    return Response(TONE_WAV, media_type="audio/wav")


@app.get("/stand/expired.png")
def stand_expired() -> Response:
    """Подпись истекла: хранилище отвечает 403 — картинка не рисуется."""
    return JSONResponse({"detail": "Request has expired"}, status_code=403)


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
