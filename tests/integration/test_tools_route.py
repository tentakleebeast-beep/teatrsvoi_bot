"""`POST /tools/route`: маршрут свободного текста в tool-api (V-026, бывший WF-01).

Обоснование: маршрут заменил публичный вебхук n8n. Тесты держат две вещи, которые
нельзя проверить глазами: контракт ответа для gateway (intent/scenario/category/answer)
и то, что сбой модели — это 200 с `ok:false`, а не «сервис недоступен» человеку.
"""

from __future__ import annotations

from app import llm, router_llm


def _fake_answer(**overrides) -> dict:
    base = {"ok": True, "intent": "budget", "category": "Реквизит и костюмы",
            "answer": "", "confidence": None, "model": "test-model",
            "tokens": 10, "tokens_in": 8, "tokens_out": 2, "provider": "test"}
    base.update(overrides)
    return base


def test_сценар_и_категория_приходят_с_закрытым_списком(client, auth, monkeypatch):
    calls: dict = {}

    async def fake(text, user=None, categories=None, **kwargs):
        calls.update(text=text, user=user, categories=categories)
        return _fake_answer()

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(router_llm, "ask", fake)

    r = client.post("/tools/route",
                    json={"text": "купили ткань 12000",
                          "user": {"name": "Проверка", "roles": ["администратор"]}},
                    headers=auth)
    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is True
    assert body["intent"] == "budget" and body["scenario"] == "budget"
    assert body["category"] == "Реквизит и костюмы"
    assert body["engine"] == "tool-api-llm"
    assert body["threshold"] == 0.7
    # список категорий пришёл из базы, а не от вызывающего
    assert calls["text"] == "купили ткань 12000"
    assert "Реквизит и костюмы" in calls["categories"]


def test_сбой_модели_это_200_с_ok_false(client, auth, monkeypatch):
    """Человек не должен увидеть «сервис недоступен»: gateway уходит в кнопки."""
    async def fake(*args, **kwargs):
        raise router_llm.LlmError("llm_unavailable")

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(router_llm, "ask", fake)

    body = client.post("/tools/route", json={"text": "привет"}, headers=auth).json()
    assert body["ok"] is False
    assert body["error"] == "provider_unavailable"
    assert body["intent"] is None and body["answer"] == ""
    assert body["engine"] == "tool-api-fallback"


def test_без_ключа_модель_не_вызывается(client, auth, monkeypatch):
    async def fake(*args, **kwargs):
        raise AssertionError("модель не должна вызываться без ключа")

    monkeypatch.setattr(llm, "is_configured", lambda: False)
    monkeypatch.setattr(router_llm, "ask", fake)

    body = client.post("/tools/route", json={"text": "привет"}, headers=auth).json()
    assert body["ok"] is False and body["error"] == "llm_unavailable"


def test_пустой_текст_не_тратит_модель(client, auth, monkeypatch):
    async def fake(*args, **kwargs):
        raise AssertionError("модель не должна вызываться на пустом тексте")

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(router_llm, "ask", fake)

    body = client.post("/tools/route", json={"text": "   "}, headers=auth).json()
    assert body["ok"] is False and body["error"] == "empty_text"
