"""Параметры подключения к LLM живут в `.env`, а не в коде (docs/infrastructure/75 §7.4).

Обоснование: FR-E2 и INT-005 — адрес провайдера был зашит в двух модулях и в воркфлоу,
поэтому смена LLM-провайдера требовала правки кода в трёх местах.

Проверяется контракт, на который опирается смена провайдера: старые имена работают
как алиасы, хвостовой слэш не ломает адрес, мусор в `.env` не роняет сервис.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from app import intro_llm, llm, router_llm

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"

ENV_NAMES = (
    "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_PROVIDER",
    "LLM_TIMEOUT_SECONDS", "LLM_MAX_TOKENS", "LLM_TEMPERATURE",
    "OPENROUTER_API_KEY", "ASSISTANT_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Окружение разработчика не должно влиять на ожидания теста."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_по_умолчанию_openrouter():
    assert llm.base_url() == "https://openrouter.ai/api/v1"
    assert llm.chat_url() == "https://openrouter.ai/api/v1/chat/completions"
    assert llm.model() == "openai/gpt-4o-mini"
    assert llm.provider() == "openrouter"
    assert llm.timeout() == 45.0
    assert llm.max_tokens() == 700
    assert llm.temperature() == 0.3


def test_ключ_берётся_из_нового_имени(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "new-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "old-key")
    assert llm.api_key() == "new-key"
    assert llm.is_configured() is True


def test_старое_имя_работает_как_алиас(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "old-key")
    assert llm.api_key() == "old-key"


def test_модель_из_старого_имени(monkeypatch):
    monkeypatch.setenv("ASSISTANT_MODEL", "anthropic/claude-3.5")
    assert llm.model() == "anthropic/claude-3.5"


def test_новое_имя_модели_сильнее_старого(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("ASSISTANT_MODEL", "anthropic/claude-3.5")
    assert llm.model() == "gpt-4o-mini"


def test_хвостовой_слэш_не_даёт_двойного(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.proxyapi.ru/openai/v1/")
    assert llm.chat_url() == "https://api.proxyapi.ru/openai/v1/chat/completions"


def test_полный_путь_не_дублируется(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.vsegpt.ru/v1/chat/completions")
    assert llm.chat_url() == "https://api.vsegpt.ru/v1/chat/completions"


def test_метка_провайдера_выводится_из_домена(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.proxyapi.ru/openai/v1")
    assert llm.provider() == "proxyapi"


def test_явная_метка_сильнее_домена(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.proxyapi.ru/openai/v1")
    monkeypatch.setenv("LLM_PROVIDER", "proxyapi-team")
    assert llm.provider() == "proxyapi-team"


def test_неизвестный_провайдер_подписывается_хостом(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.org/v1")
    assert llm.provider() == "llm.example.org"


@pytest.mark.parametrize("raw,expected", [
    ("abc", 45.0),      # мусор — дефолт, а не падение на старте
    ("0", 45.0),        # вне диапазона
    ("9999", 45.0),
    ("30", 30.0),
])
def test_таймаут_терпит_мусор(monkeypatch, raw, expected):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", raw)
    assert llm.timeout() == expected


def test_лимиты_ассистента_из_env(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "1200")
    monkeypatch.setenv("LLM_TEMPERATURE", "1")
    assert llm.max_tokens() == 1200
    assert llm.temperature() == 1.0


def test_снимок_настроек():
    config = llm.config()
    assert config.chat_url.endswith("/chat/completions")
    assert config.provider == "openrouter"
    assert config.configured is False        # ключа нет — ветка живёт на правилах


def test_знакомство_ходит_по_адресу_из_env(monkeypatch):
    """Раньше адрес был зашит в intro_llm — теперь берётся из окружения."""
    monkeypatch.setenv("LLM_BASE_URL", "https://api.proxyapi.ru/openai/v1")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        content = json.dumps({"travel_minutes": 20})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    patch = __import__("asyncio").run(
        intro_llm.ask("travel", "минут двадцать", transport=httpx.MockTransport(handler),
                      api_key="k"))
    assert patch == {"travel_minutes": 20}
    assert seen == ["https://api.proxyapi.ru/openai/v1/chat/completions"]


def test_роутер_берёт_адрес_и_модель_из_env(monkeypatch):
    """`POST /tools/route` (бывший WF-01) не зашивает провайдера: адрес и модель из `app/llm.py`."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        body = json.loads(request.content)
        assert body["model"] == "proxy-model"
        content = "ASSISTANT\nОтвет"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setenv("LLM_BASE_URL", "https://api.proxyapi.ru/openai/v1")
    result = asyncio.run(router_llm.ask("привет", transport=httpx.MockTransport(handler),
                                        api_key="k", model="proxy-model"))
    assert result["intent"] == "assistant" and result["answer"] == "Ответ"
    assert seen == ["https://api.proxyapi.ru/openai/v1/chat/completions"]


def test_в_env_example_есть_все_переменные_llm():
    keys = {
        line.split("=", 1)[0].strip()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert {"LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_PROVIDER",
            "LLM_TIMEOUT_SECONDS", "LLM_MAX_TOKENS", "LLM_TEMPERATURE",
            "OPENROUTER_API_KEY", "ASSISTANT_MODEL"} <= keys
