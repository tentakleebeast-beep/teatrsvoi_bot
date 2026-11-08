"""Единый конверт ошибок API (docs/reports/84): код, request_id, retryable.

Проверяем то, что клиент читает в проде: `detail` остаётся на месте, рядом появляются
`code`/`status`/`request_id`, а 5xx не раскрывает внутренний текст.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.errors import code_of, problem_response
from app.main import (
    http_error_handler,
    request_id_middleware,
    request_validation_handler,
    unhandled_error_handler,
)
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel


def _request(rid: str = "abc12345", path: str = "/x") -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(request_id=rid),
                           url=SimpleNamespace(path=path), headers={})


class Body(BaseModel):
    amount: int


@pytest.mark.parametrize("detail,expected", [
    ("slot_taken", "slot_taken"),
    ("bad_hours: допустимо 0–24", "bad_hours"),
    ("bad service token", "bad_service_token"),
    ("participant is not active", "participant_inactive"),
    ("capability required: budget.read", "capability_required"),
    ("unknown_participants: [999]", "unknown_participants"),
    ("Not Found", "not_found"),
    ("уже занято", "error"),
])
def test_код_выводится_из_текста(detail, expected):
    assert code_of(detail) == expected


def test_конверт_409_не_повторяемый():
    response = problem_response(_request(), 409, "slot_taken")
    body = json.loads(response.body)
    assert response.status_code == 409
    assert body["code"] == "slot_taken"
    assert body["status"] == 409
    assert body["detail"] == "slot_taken"
    assert body["retryable"] is False
    assert body["ok"] is False
    assert body["request_id"] == "abc12345"
    assert response.headers["X-Request-ID"] == "abc12345"


def test_конверт_503_повторяемый_с_retry_after():
    response = problem_response(_request(), 503, "Сервис недоступен")
    assert json.loads(response.body)["retryable"] is True
    assert response.headers["Retry-After"] == "5"


def _tiny_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.post("/ok")
    def ok(body: Body) -> dict:
        return {"ok": True}

    @app.get("/boom")
    def boom() -> dict:
        raise RuntimeError("пароль от базы: secret")

    return app


def test_request_id_ставится_на_успешный_ответ():
    with TestClient(_tiny_app()) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]


def test_чужой_request_id_принимается_если_безопасен():
    with TestClient(_tiny_app()) as client:
        response = client.get("/openapi.json", headers={"X-Request-ID": "gw-12345678"})
    assert response.headers["X-Request-ID"] == "gw-12345678"


def test_валидация_отдаёт_список_и_код():
    with TestClient(_tiny_app()) as client:
        response = client.post("/ok", json={"amount": "не число"})
    body = response.json()
    assert response.status_code == 422
    assert body["code"] == "validation_failed"
    assert isinstance(body["detail"], list) and body["detail"]
    assert body["errors"] == body["detail"]
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_необработанная_ошибка_не_раскрывает_текст():
    with TestClient(_tiny_app(), raise_server_exceptions=False) as client:
        response = client.get("/boom")
    body = response.json()
    assert response.status_code == 500
    assert body["code"] == "internal_error"
    assert "secret" not in response.text
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_404_тоже_в_конверте():
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app = _tiny_app()
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/нет-такого")
    body = response.json()
    assert response.status_code == 404
    assert body["code"] == "not_found"
    assert body["detail"] == "Not Found"


def test_openapi_собирается():
    """Схема не должна падать на forward reference: /docs и /openapi.json — рабочие."""
    from app.main import app

    schema = app.openapi()
    assert schema["paths"], "схема пустая"
    assert not [path for path in schema["paths"] if "approval" in path], \
        "маршруты отменённого подтверждения вернулись"


def test_openapi_описывает_контракт_ошибок():
    """У каждого метода — 500 с общей схемой Problem: клиент видит контракт до прода."""
    from app.main import app

    schema = app.openapi()
    assert "Problem" in schema["components"]["schemas"]
    methods = {"get", "post", "put", "patch", "delete"}
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method not in methods:
                continue
            responses = operation.get("responses", {})
            assert "500" in responses, f"{method.upper()} {path}: нет ответа 500"
            ref = responses["500"]["content"]["application/problem+json"]["schema"]["$ref"]
            assert ref.endswith("/Problem"), f"{method.upper()} {path}: 500 без схемы Problem"


def test_частичный_успех_помечен_в_openapi():
    """200 с ошибкой внутри — не сбой: методы помечены x-partial-success."""
    from app.main import PARTIAL_SUCCESS, app

    schema = app.openapi()
    for path in PARTIAL_SUCCESS:
        assert path in schema["paths"], f"{path}: нет в схеме"
        for method, operation in schema["paths"][path].items():
            if method in {"get", "post"}:
                assert operation.get("x-partial-success"), f"{method.upper()} {path}: нет пометки"


