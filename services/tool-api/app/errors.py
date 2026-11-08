"""Единый конверт ошибок HTTP API (RFC 9457 + машинный код).

Зачем: до этого сервис отдавал ошибки четырьмя разными формами — `{"detail": "код"}`,
`{"detail": [список]}`, `{"ok": false, "detail": ...}` и `200 {"ok": false, "error": ...}`.
Клиент (гейтвей, Mini App, n8n) не мог обработать их одним правилом, а строку `detail`
показывал человеку как есть.

Правила модуля:
1. Статус HTTP — по смыслу ошибки, тело — всегда один конверт.
2. `code` — машинный и стабильный; `title`/`detail` — человеческие и неконтрактные.
3. `detail` остаётся строкой (для валидации — списком) на переходный период: текущие
   клиенты читают именно его.
4. Каждый ответ несёт `request_id` и заголовок `X-Request-ID`; 429/503 — `Retry-After`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(StarletteHTTPException):
    """HTTPException с явным машинным кодом.

    Нужен там, где `detail` — не код, а текст исключения (`str(exc)`): иначе код
    пришлось бы угадывать по сообщению. Остальные 80+ вызовов `HTTPException`
    продолжают работать: код выводится из `detail` функцией `code_of`.
    """

    def __init__(self, status_code: int, detail: str, code: str,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code

# Коды, которые нельзя вывести из текста: английские фразы и служебные ответы Starlette.
CODE_ALIASES = {
    "bad service token": "bad_service_token",
    "unknown participant": "unknown_participant",
    "participant is not active": "participant_inactive",
    "not an admin chat": "not_an_admin_chat",
    "X-Actor-Chat-Id required": "actor_chat_id_required",
    "amount must be positive": "amount_not_positive",
    "capability required": "capability_required",
    "admin не выдаётся кодом: только через ADMIN_CHAT_ID": "admin_role_not_issuable",
    "Not Found": "not_found",
    "Method Not Allowed": "method_not_allowed",
    "Internal Server Error": "internal_error",
}

STATUS_TITLES = {
    400: "Некорректный запрос",
    401: "Требуется авторизация",
    403: "Доступ запрещён",
    404: "Не найдено",
    405: "Метод не поддерживается",
    409: "Конфликт состояния",
    413: "Слишком большой запрос",
    422: "Данные не прошли проверку",
    429: "Слишком много запросов",
    500: "Внутренняя ошибка",
    502: "Внешний сервис недоступен",
    503: "Сервис временно недоступен",
    504: "Внешний сервис не ответил",
}

# Повтор запроса без изменений имеет смысл только для этих статусов.
RETRYABLE_STATUSES = {408, 425, 429, 502, 503, 504}

_CODE_RE = re.compile(r"^([a-z][a-z0-9_]{2,})(?::\s.*)?$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_request_id() -> str:
    """Идентификатор запроса: 32 hex-символа, без дефисов — удобно копировать из лога."""
    return uuid.uuid4().hex


def valid_request_id(value: str | None) -> str | None:
    """Чужой `X-Request-ID` принимаем только в безопасной форме (без переводов строк)."""
    return value if value and _REQUEST_ID_RE.fullmatch(value) else None


def request_id_of(request: Any) -> str:
    """Идентификатор запроса: из состояния (поставило middleware) или новый."""
    state = getattr(request, "state", None)
    rid = getattr(state, "request_id", None) if state is not None else None
    if rid:
        return rid
    headers = getattr(request, "headers", None)
    return valid_request_id(headers.get("X-Request-ID") if headers else None) or new_request_id()


def code_of(detail: str) -> str:
    """Машинный код из существующего текста ошибки: `bad_day`, `slot_taken`, ...

    Пока каталог не введён, код выводится из первой части строки до `:` — так все уже
    написанные `raise HTTPException(..., "bad_month")` получают код без правки вызовов.
    """
    text = (detail or "").strip()
    if text in CODE_ALIASES:
        return CODE_ALIASES[text]
    for prefix, code in CODE_ALIASES.items():
        if text.startswith(prefix + ":") or text.startswith(prefix + " "):
            return code
    match = _CODE_RE.match(text)
    return match.group(1) if match else "error"


def problem_response(
    request: Any,
    status_code: int,
    detail: Any,
    *,
    code: str | None = None,
    title: str | None = None,
    errors: list[Any] | None = None,
    headers: Mapping[str, str] | None = None,
    retryable: bool | None = None,
) -> JSONResponse:
    """Один конверт ошибки для всех путей: HTTPException, валидация, 5xx."""
    rid = request_id_of(request)
    error_code = code or code_of(detail if isinstance(detail, str) else "")
    retry = status_code in RETRYABLE_STATUSES if retryable is None else retryable

    body: dict[str, Any] = {
        "type": f"https://svoi.local/errors/{error_code}",
        "title": title or STATUS_TITLES.get(status_code, "Ошибка"),
        "status": status_code,
        "detail": detail,
        "instance": getattr(getattr(request, "url", None), "path", ""),
        "code": error_code,
        "retryable": retry,
        "request_id": rid,
        "ok": False,
    }
    if errors is not None:
        body["errors"] = errors

    out_headers = dict(headers or {})
    out_headers["X-Request-ID"] = rid
    if retry and "Retry-After" not in out_headers and status_code in (429, 503):
        out_headers["Retry-After"] = "5"

    return JSONResponse(status_code=status_code, content=body,
                        headers=out_headers, media_type="application/problem+json")
