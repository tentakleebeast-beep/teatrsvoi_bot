"""Кэш повторяющихся ответов агента (карта A-4, docs/development/108 §10).

Что держит тест:
  * один и тот же вопрос дважды в пределах TTL — второй ответ из кэша;
  * «Кто сегодня занят?» и «кто сегодня занят» — один вопрос (нормализация);
  * ответ с карточкой подтверждения не кэшируется: черновик действия не повторяется;
  * TTL берётся по группам конфига, а вопросы вне групп не кэшируются;
  * истёкшая запись не отдаётся;
  * чужой чат не получает ответ из чужого кэша.

Сети и базы нет: время подменяется, конфиг — настоящий `config/agent-models.yaml`.
"""

from __future__ import annotations

import pathlib

import pytest
from app.agently import cache, models

CONFIG_FILE = pathlib.Path(__file__).resolve().parents[2] / "config" / "agent-models.yaml"
ANSWER = {"answer": "Сегодня 02.11.2026, понедельник.", "scenario": "assistant",
          "model": "openai/gpt-4o-mini", "engine": "agent"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODELS_PATH", str(CONFIG_FILE))
    cache.clear()
    yield
    cache.clear()


class Clock:
    """Подменяемое монотонное время: TTL проверяется без sleep."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(cache.time, "monotonic", clock)
    return clock


def test_нормализация_склеивает_один_вопрос() -> None:
    assert cache.normalize("  Кто   СЕГОДНЯ занят?! ") == "кто сегодня занят"
    assert cache.normalize("Какое сегодня число?") == cache.normalize("какое сегодня число")


def test_область_кэша_чат_старше_человека() -> None:
    assert cache.scope_of(555, 7) == "chat:555"
    assert cache.scope_of(None, 7) == "person:7"
    assert cache.scope_of(None, None) == ""


def test_ttl_по_группам_конфига() -> None:
    assert cache.ttl_for("какое сегодня число") == 30
    assert cache.ttl_for("Кто сегодня занят?") == 60
    assert cache.ttl_for("сколько расходов в октябре") == 0, (
        "вопросы про деньги вне групп не кэшируются")


def test_повтор_в_пределах_ttl_идёт_из_кэша(clock) -> None:
    scope = cache.scope_of(100, None)
    assert cache.put(scope, "какое сегодня число", ANSWER) == 30

    clock.now += 5
    entry = cache.get(scope, "какое сегодня число")

    assert entry is not None
    assert entry["answer"] == ANSWER["answer"]
    assert entry["cached"] is True
    assert entry["age_seconds"] == 5.0
    assert cache.stats()["hits"] == 1


def test_истёкшая_запись_не_отдаётся(clock) -> None:
    scope = cache.scope_of(100, None)
    cache.put(scope, "какое сегодня число", ANSWER)

    clock.now += 31
    assert cache.get(scope, "какое сегодня число") is None
    assert cache.stats()["hits"] == 0
    assert cache.stats()["entries"] == 0, "истёкшая запись должна удаляться сразу"


def test_вопрос_вне_групп_не_кэшируется(clock) -> None:
    scope = cache.scope_of(100, None)
    assert cache.put(scope, "сколько расходов в октябре", ANSWER) == 0
    assert cache.get(scope, "сколько расходов в октябре") is None


def test_карточка_подтверждения_не_кэшируется(clock) -> None:
    scope = cache.scope_of(100, None)
    propose = dict(ANSWER, propose={"tool": "budget.tx", "args": {}})

    assert cache.put(scope, "какое сегодня число", propose) == 0
    assert cache.get(scope, "какое сегодня число") is None


def test_пустой_ответ_не_кэшируется(clock) -> None:
    scope = cache.scope_of(100, None)
    assert cache.put(scope, "какое сегодня число", {"answer": "  "}) == 0


def test_чужой_чат_не_видит_ответ(clock) -> None:
    cache.put(cache.scope_of(100, None), "какое сегодня число", ANSWER)
    assert cache.get(cache.scope_of(200, None), "какое сегодня число") is None


def test_ответ_кэшируется_с_теми_же_полями(clock) -> None:
    scope = cache.scope_of(100, None)
    cache.put(scope, "кто сегодня занят", dict(ANSWER, answer="Свободны: Аня."))
    entry = cache.get(scope, "кто сегодня занят")
    assert entry["scenario"] == "assistant"
    assert entry["model"] == "openai/gpt-4o-mini"
    assert entry["answer"] == "Свободны: Аня."


def test_clear_сбрасывает_и_счётчики(clock) -> None:
    scope = cache.scope_of(100, None)
    cache.put(scope, "какое сегодня число", ANSWER)
    cache.get(scope, "какое сегодня число")
    assert cache.stats()["hits"] == 1

    cache.clear()

    assert cache.stats() == {"hits": 0, "misses": 0, "entries": 0, "size": 0}


def test_конфиг_без_групп_выключает_кэш(tmp_path, monkeypatch, clock) -> None:
    file = tmp_path / "models.yaml"
    file.write_text("cache:\n  default_ttl_seconds: 0\n  groups: []\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_MODELS_PATH", str(file))

    assert models.cache_policy()["groups"] == []
    assert cache.put(cache.scope_of(100, None), "какое сегодня число", ANSWER) == 0
