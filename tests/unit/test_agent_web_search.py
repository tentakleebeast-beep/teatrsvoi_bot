"""Внешний поиск: поле описания из ответа SearXNG (дефект dev-стенда 05.11.2026).

SearXNG в JSON кладёт описание в `content`, а не в `snippet`. Пока маппинг этого
не знал, модель получала заголовок и ссылку без описания — и отвечала «нашёл»
без факта. Здесь сеть подменена ответом, повторяющим формат SearXNG.

Обоснование: docs/development/108 §9.5; решение владельца 05.11.2026 (карта «Озадачить», п.6).
"""

from __future__ import annotations

from app.agently import tools


class _Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, response: _Response):
        self._response = response
        self.urls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url: str, params: dict) -> _Response:
        self.urls.append((url, params))
        return self._response


def _search(monkeypatch, payload: dict, status_code: int = 200) -> tuple[dict, _Client]:
    monkeypatch.setenv("AGENT_WEB_SEARCH_URL", "http://127.0.0.1:8080/search")
    client = _Client(_Response(payload, status_code))
    monkeypatch.setattr(tools.httpx, "Client", lambda timeout=None: client)
    return tools.run_web_search(None, {}, {"query": "театральный сезон"}), client


def test_описание_берётся_из_content_как_отдаёт_searxng(monkeypatch) -> None:
    result, client = _search(monkeypatch, {"results": [
        {"title": "Театральный сезон 2026", "url": "https://afisha.ru/season",
         "content": "Сезон открывается 12 сентября."}]})

    assert result["ok"] is True
    assert result["results"][0]["snippet"] == "Сезон открывается 12 сентября."
    assert result["results"][0]["url"] == "https://afisha.ru/season"
    url, params = client.urls[0]
    assert url == "http://127.0.0.1:8080/search"
    assert params == {"q": "театральный сезон", "format": "json"}


def test_snippet_и_body_остаются_запасными_полями(monkeypatch) -> None:
    result, _ = _search(monkeypatch, {"results": [
        {"title": "A", "url": "https://a", "snippet": "из snippet"},
        {"title": "B", "url": "https://b", "body": "из body"}]})

    assert [item["snippet"] for item in result["results"]] == ["из snippet", "из body"]


def test_без_описания_поле_пустое_а_не_выдуманное(monkeypatch) -> None:
    result, _ = _search(monkeypatch, {"results": [
        {"title": "Заголовок", "url": "https://example.test"}]})

    assert result["results"][0]["snippet"] == ""


def test_ошибка_поиска_это_честный_отказ(monkeypatch) -> None:
    result, _ = _search(monkeypatch, {}, status_code=502)

    assert result["ok"] is False and result["error"] == "web_search_failed"


def test_без_адреса_поиск_недоступен_честно(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_WEB_SEARCH_URL", raising=False)

    result = tools.run_web_search(None, {}, {"query": "что-нибудь"})

    assert result["ok"] is False and result["error"] == "web_search_unavailable"
