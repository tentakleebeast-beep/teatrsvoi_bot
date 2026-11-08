"""
Параметры подключения к LLM: одно место, значения из окружения.

Зачем отдельный модуль. Провайдер LLM меняется по внешним причинам (агрегаторы
закрывают доступ по стране аккаунта и по IP независимо друг от друга), и раньше
адрес `https://openrouter.ai/api/v1/chat/completions` был зашит в двух местах:
`main.py` (ассистент) и `intro_llm.py` (знакомство). Смена провайдера превращалась
в поиск по репозиторию.

Контракт окружения (`docs/infrastructure/75` §7.4):

    LLM_BASE_URL          https://openrouter.ai/api/v1   (без /chat/completions)
    LLM_API_KEY           ключ провайдера
    LLM_MODEL             модель, по умолчанию openai/gpt-4o-mini
    LLM_PROVIDER          метка в llm_usage; если пусто — выводится из домена
    LLM_TIMEOUT_SECONDS   таймаут запроса, по умолчанию 45
    LLM_MAX_TOKENS        предел ответа ассистента, по умолчанию 700
    LLM_TEMPERATURE       температура ассистента, по умолчанию 0.3

Старые имена (`OPENROUTER_API_KEY`, `ASSISTANT_MODEL`) работают как алиасы: они
остались в чужом `.env` и в документации, и ломать их ради переименования нельзя.

Модуль чистый: только `os` и `urllib.parse`, поэтому его проверяют unit-тесты
без FastAPI и без сети.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
DEFAULT_TIMEOUT = 45.0
DEFAULT_INTRO_TIMEOUT = 20.0
DEFAULT_MAX_TOKENS = 700
DEFAULT_TEMPERATURE = 0.3
CHAT_PATH = "/chat/completions"

# Домены известных провайдеров → метка в llm_usage.provider.
KNOWN_PROVIDERS = {
    "openrouter.ai": "openrouter",
    "proxyapi.ru": "proxyapi",
    "api.proxyapi.ru": "proxyapi",
    "vsegpt.ru": "vsegpt",
    "api.vsegpt.ru": "vsegpt",
}


def _env(*names: str) -> str:
    """Первое непустое значение из перечисленных переменных (в порядке приоритета)."""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _number(name: str, default: float, *, low: float, high: float) -> float:
    """Число из окружения с проверкой диапазона; мусор в `.env` не роняет сервис."""
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if low <= value <= high else default


def base_url() -> str:
    """Корень API без хвостового слэша: к нему добавляется `/chat/completions`."""
    return (_env("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def chat_url(base: str | None = None) -> str:
    """Полный адрес запроса. Терпит и корень, и уже полный путь."""
    url = (base or base_url()).rstrip("/")
    return url if url.endswith(CHAT_PATH) else url + CHAT_PATH


def api_key() -> str:
    return _env("LLM_API_KEY", "OPENROUTER_API_KEY")


def model() -> str:
    return _env("LLM_MODEL", "ASSISTANT_MODEL") or DEFAULT_MODEL


def provider(base: str | None = None) -> str:
    """Метка провайдера для `llm_usage.provider`.

    Явная `LLM_PROVIDER` сильнее вывода из домена: у прокси адрес может быть свой,
    а в отчёте хочется видеть, чей это был ключ.
    """
    explicit = _env("LLM_PROVIDER")
    if explicit:
        return explicit
    host = (urlsplit(base or base_url()).hostname or "").lower()
    host = host.removeprefix("www.")
    if host in KNOWN_PROVIDERS:
        return KNOWN_PROVIDERS[host]
    return host or "custom"


def timeout(default: float = DEFAULT_TIMEOUT) -> float:
    return _number("LLM_TIMEOUT_SECONDS", default, low=1.0, high=300.0)


def max_tokens() -> int:
    return int(_number("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS, low=1.0, high=8000.0))


def temperature() -> float:
    return _number("LLM_TEMPERATURE", DEFAULT_TEMPERATURE, low=0.0, high=2.0)


def is_configured() -> bool:
    """Есть ключ — ветка может работать. Нет ключа — бот живёт на правилах."""
    return bool(api_key())


@dataclass(frozen=True)
class LlmConfig:
    """Снимок настроек: удобно логировать и передавать в тесты."""

    base_url: str
    chat_url: str
    model: str
    provider: str
    timeout: float
    max_tokens: int
    temperature: float

    @property
    def configured(self) -> bool:
        return bool(api_key())


def config() -> LlmConfig:
    return LlmConfig(
        base_url=base_url(),
        chat_url=chat_url(),
        model=model(),
        provider=provider(),
        timeout=timeout(),
        max_tokens=max_tokens(),
        temperature=temperature(),
    )
