"""Фабрика приложения tool-api: приложение, зависимости, ошибки, health.

Маршруты живут в `app/api/<домен>.py` — каждый модуль отдаёт свой `APIRouter`.
Общее (настройки, БД, модели, помощники) — в `app/core.py`; настройки читаются
только оттуда, поэтому `configure()` виден всем модулям сразу.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import core, schema_version
from .api import (
    admin,
    agent_ask,
    agent_confirm,
    budget,
    events,
    feedback,
    files,
    finance,
    notes,
    people,
    quota,
    route,
    schedule,
    webapp_api,
)
from .errors import new_request_id, problem_response, request_id_of, valid_request_id
from .pages.routes import build_router as build_webapp_routes
from .version import uptime_seconds
from .webapp import check_secrets

app = FastAPI(title="SVOI tool-api", version="0.2.0-postgres")



@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Один идентификатор запроса на весь путь: заголовок ответа, тело ошибки, лог.

    Клиент присылает свой `X-Request-ID` (например, гейтвей) — принимаем, если он
    безопасной формы; иначе выдаём новый. Ошибка без идентификатора не расследуется.
    """
    rid = valid_request_id(request.headers.get("X-Request-ID")) or new_request_id()
    request.state.request_id = rid
    response = await call_next(request)
    response.headers.setdefault("X-Request-ID", rid)
    # Страницы Mini App не кэшируются. 02.11.2026 клиент показывал старую страницу
    # из кэша WebView: чинили оболочку, а на телефоне оставался прошлый HTML и висел
    # «бесконечный» лоадер — в журнале сервера не было ни одного запроса с устройства.
    # Ассеты кэшируются отдельно (у них свои заголовки), HTML — никогда.
    if request.url.path.startswith("/webapp") and "text/html" in response.headers.get("content-type", ""):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response



@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request, exc: StarletteHTTPException):
    """Все `raise HTTPException` — в единый конверт (app/errors.py).

    `detail` остаётся строкой: гейтвей и Mini App читают именно его. Рядом появляются
    `code`, `status`, `retryable`, `request_id` — по ним можно ветвиться, не разбирая текст.
    """
    detail = exc.detail
    return problem_response(request, exc.status_code, detail,
                            code=getattr(exc, "code", None)
                            or (None if isinstance(detail, str) else "http_error"),
                            headers=exc.headers)



@app.exception_handler(ValueError)
async def value_error_handler(request, exc: ValueError):
    """Кривая дата/месяц из запроса — это ошибка ввода (422), а не падение сервиса.

    Найдено smoke-тестом 27.10: `?month=abc` и `date='abc'` давали 500 с трейсбеком
    `ValueError: time data 'abc' does not match format`.
    """
    return problem_response(request, 422, f"Некорректное значение: {exc}",
                            code="invalid_value")



@app.exception_handler(RequestValidationError)
async def request_validation_handler(request, exc: RequestValidationError):
    """422 без эха входа: стандартный ответ возвращает `input`, и NaN/Infinity в нём
    роняли сериализацию в 500 (smoke docs/reports/62, S-7)."""
    errors = [{"loc": [str(part) for part in err.get("loc", ())],
               "msg": str(err.get("msg", "")), "type": str(err.get("type", ""))}
              for err in exc.errors()]
    return problem_response(request, 422, errors, code="validation_failed", errors=errors)



@app.exception_handler(Exception)
async def unhandled_error_handler(request, exc: Exception):
    """Любое необработанное исключение: клиенту — код и request_id, текст — только в лог.

    Без этого обработчика Starlette отдавал `500 {"detail":"Internal Server Error"}`:
    ни кода, ни следа, ни связи с записью в логе.
    """
    rid = request_id_of(request)
    core.log.exception("необработанная ошибка: request_id=%s path=%s", rid, request.url.path)
    return problem_response(request, 500, "Внутренняя ошибка сервиса", code="internal_error")



# Ошибки в OpenAPI: одна схема Problem на все эндпоинты. Расписывать `responses=` в 138
# декораторах вручную — шум; здесь достаточно объявить общий контракт и показать его
# в каждом методе. Примеры конкретных кодов — в docs/architecture/85.
OPENAPI_ERRORS = {
    "400": "Некорректный запрос",
    "401": "Требуется авторизация",
    "403": "Доступ запрещён",
    "404": "Не найдено",
    "409": "Конфликт состояния",
    "422": "Данные не прошли проверку",
    "429": "Слишком много запросов",
    "500": "Внутренняя ошибка",
    "503": "Сервис временно недоступен",
}


PROBLEM_SCHEMA = {
    "type": "object",
    "required": ["code", "status", "detail", "request_id"],
    "properties": {
        "type": {"type": "string", "example": "https://svoi.local/errors/slot_taken"},
        "title": {"type": "string", "example": "Конфликт состояния"},
        "status": {"type": "integer", "example": 409},
        "detail": {"description": "человеческий текст; для валидации — список {loc, msg, type}"},
        "instance": {"type": "string", "example": "/tools/events/assign"},
        "code": {"type": "string", "description": "стабильный код, docs/architecture/85"},
        "retryable": {"type": "boolean", "example": False},
        "request_id": {"type": "string", "example": "4fa789adc68d43bb86c857fb7fe2cd87"},
        "ok": {"type": "boolean", "example": False},
        "errors": {"type": "array", "items": {"type": "object"}},
    },
}



# 200 с ошибкой внутри — не транспортная ошибка, а частичный успех или шаг диалога.
# Клиент обязан смотреть `ok` и поля, а не только HTTP-статус (docs/architecture/85 §5).
PARTIAL_SUCCESS = {
    "/tools/route": "модель не ответила или текст пуст — смотреть ok и error; "
                    "gateway уходит в кнопки, а не показывает «сервис недоступен»",
    "/tools/participants/register": "шаг диалога регистрации: need_name, already_registered, code_*",
    "/tools/participants/check_code": "код не подошёл — это ответ диалога, а не сбой",
    "/tools/participants/attach": "привязка не удалась — смотреть error",
    "/tools/events/preview": "проверка карточки не прошла — смотреть errors",
    "/tools/profile/intro-parse": "ввод не разобран — смотреть error",
    "/tools/budget/sheet-retry": "часть строк не дописана — смотреть failed и remaining",
    "/tools/availability/template/from-week": "нет отмеченных дней — смотреть error",
    "/webapp/availability/template/from-week": "нет отмеченных дней — смотреть error",
    "/tools/availability/sheet/mismatches": "в листе есть расхождения — смотреть данные",
    "/tools/availability/reconcile": "правки не применены — смотреть applied и mismatches",
    "/tools/calendar/health": "часть календарей не настроена — смотреть calendars[*].ok",
    "/tools/calendar/events": "календарь недоступен — смотреть error",
}



def custom_openapi():
    """Схема с общим контрактом ошибок: /docs показывает Problem у каждого метода."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas["Problem"] = PROBLEM_SCHEMA
    content = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            responses = operation.setdefault("responses", {})
            for status_code, description in OPENAPI_ERRORS.items():
                responses.setdefault(status_code, {"description": description, "content": content})
            if path in PARTIAL_SUCCESS:
                operation["x-partial-success"] = PARTIAL_SUCCESS[path]
    app.openapi_schema = schema
    return schema



app.openapi = custom_openapi



@app.on_event("startup")
def _startup() -> None:
    check_secrets(core.ENV_NAME, core.WEBAPP_SECRET, core.TOOL_API_TOKEN)
    core.pool.open(wait=True, timeout=30)
    # Схему накатывает Alembic до старта сервиса; здесь только сверка версии.
    # Расхождение — отказ старта с текстом про `alembic upgrade head`.
    schema_version.check_schema_version()



@app.on_event("shutdown")
def _shutdown() -> None:
    core.pool.close()



@app.get("/health")
def health() -> dict:
    with core.pool.connection() as conn:
        conn.execute("select 1")
        n = conn.execute(
            "select count(*) c from participant where status <> 'archived'").fetchone()["c"]
        archived = conn.execute(
            "select count(*) c from participant where status = 'archived'").fetchone()["c"]
    moment = datetime.now(UTC)
    return {"ok": True, "time": core.now().isoformat(timespec="seconds"),
            "tz_offset": core.TZ.utcoffset(None).total_seconds() / 3600,
            "db": "postgres", "participants": n, "archived": archived,
            "env": core.ENV_NAME,
            # FR-H12/L-005: чем ответил живой процесс и с какой сборки он стартовал
            "git_sha": core.GIT_SHA,
            "started_at": core.STARTED_AT.isoformat(timespec="seconds"),
            "uptime": uptime_seconds(core.STARTED_AT, moment)}


# --- маршруты по доменам -----------------------------------------------------
# Страницы Mini App подключаются первыми: их пути не пересекаются с JSON-API
# (`/webapp/files` — страница, `/webapp/files/list` — данные).
app.include_router(build_webapp_routes(actor=core.webapp_actor, caps_for=core._who_caps))
app.include_router(people.router)
app.include_router(events.router)
app.include_router(schedule.router)
app.include_router(budget.router)
app.include_router(finance.router)
app.include_router(admin.router)
app.include_router(feedback.router)
app.include_router(route.router)
app.include_router(agent_ask.router)
app.include_router(agent_confirm.router)
app.include_router(files.router)
app.include_router(notes.router)
app.include_router(quota.router)
app.include_router(webapp_api.router)


# --- прежний вход для тестов и скриптов ---------------------------------------
# `app.main` исторически отдаёт настройки и configure(); сами значения живут
# в app/core.py, здесь только имена.
from .core import Settings, configure  # noqa: E402, F401
