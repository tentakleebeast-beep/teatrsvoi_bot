"""Модели, лимиты и цены агента из конфига (карта A-4, docs/development/108 §10).

Проверяем то, что можно проверить без сети и без базы:
  * в `config/agent-models.yaml` роли заполнены и классификатор не дороже исполнителя;
  * переменная окружения старше файла, файл старше умолчания в коде;
  * битый и пропавший файл не роняет сервис: работают умолчания;
  * цены и курс заданы с источником — иначе отчёт о стоимости нечем считать.
"""

from __future__ import annotations

import pathlib

import pytest
from app.agently import config, models

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "config" / "agent-models.yaml"


@pytest.fixture(autouse=True)
def _real_config(monkeypatch) -> None:
    """Тесты работают с настоящим файлом, если не подменили путь явно."""
    monkeypatch.delenv("AGENT_MODELS_PATH", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.delenv("AGENT_CLASSIFIER_MODEL", raising=False)
    monkeypatch.delenv("LLM_TOOLS_MODEL", raising=False)
    monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
    monkeypatch.setenv("AGENT_MODELS_PATH", str(CONFIG_FILE))


def test_файл_конфига_есть_в_репозитории() -> None:
    assert CONFIG_FILE.is_file(), "config/agent-models.yaml пропал: выбор моделей снова в коде"


def test_роли_заполнены_и_разные() -> None:
    roles = models.roles()
    assert set(roles) == {models.ROLE_AGENT, models.ROLE_CLASSIFIER}
    assert roles[models.ROLE_AGENT]["model"]
    assert roles[models.ROLE_CLASSIFIER]["model"]
    assert roles[models.ROLE_AGENT]["model"] != roles[models.ROLE_CLASSIFIER]["model"], (
        "роли не разделены: классификатор и исполнитель — одна модель")


def test_классификатор_не_дороже_исполнителя() -> None:
    """Роль классификатора — дешёвая: иначе разделение ролей не экономит."""
    prices = models.prices()
    agent = prices[models.role_model(models.ROLE_AGENT)]
    classifier = prices[models.role_model(models.ROLE_CLASSIFIER)]
    assert sum(classifier) < sum(agent)


def test_выбранные_модели_есть_в_таблице_цен() -> None:
    prices = models.prices()
    for role in (models.ROLE_AGENT, models.ROLE_CLASSIFIER):
        model = models.role_model(role)
        assert model in prices, f"для {role} ({model}) нет цены — отчёт о стоимости не посчитать"


def test_переменная_окружения_старше_файла(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("AGENT_CLASSIFIER_MODEL", "openai/gpt-4.1-mini")
    assert config.agent_model() == "openai/gpt-4o"
    assert config.classifier_model() == "openai/gpt-4.1-mini"


def test_файл_старше_умолчания_в_коде() -> None:
    assert config.agent_model() == models.role_model(models.ROLE_AGENT)
    assert config.classifier_model() == models.role_model(models.ROLE_CLASSIFIER)


def test_llm_tools_model_остаётся_вторым_по_старшинству(monkeypatch) -> None:
    monkeypatch.setenv("LLM_TOOLS_MODEL", "openai/gpt-4.1-mini")
    assert config.agent_model() == "openai/gpt-4.1-mini"


def test_битый_файл_не_роняет_конфиг(tmp_path, monkeypatch) -> None:
    broken = tmp_path / "models.yaml"
    broken.write_text("roles: [это не словарь\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_MODELS_PATH", str(broken))
    assert models.roles() == {}
    assert models.chat_limits()["daily_tokens"] > 0
    assert config.agent_model() == models.DEFAULT_ROLE_MODELS[models.ROLE_AGENT]


def test_пропавший_файл_даёт_умолчания(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODELS_PATH", str(tmp_path / "нет-такого.yaml"))
    assert models.roles() == {}
    assert models.prices() == {}
    assert models.role_model(models.ROLE_AGENT) == models.DEFAULT_ROLE_MODELS[models.ROLE_AGENT]
    assert models.cache_policy()["groups"] == []


def test_пустая_модель_в_файле_не_принимается(tmp_path, monkeypatch) -> None:
    """Пустая строка вместо модели — это не модель: роль падает на умолчание кода."""
    file = tmp_path / "models.yaml"
    file.write_text("version: 1\nroles:\n  agent:\n    model: '  '\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_MODELS_PATH", str(file))
    assert models.roles() == {}
    assert models.role_model(models.ROLE_AGENT) == models.DEFAULT_ROLE_MODELS[models.ROLE_AGENT]


def test_дневной_бюджет_чата_задан_и_сбалансирован() -> None:
    """Оба предела должны срабатывать примерно на одном числе сообщений замера.

    Замер A-0: 1085 токенов и 1,9 шага на сообщение. Если один предел втрое строже
    другого, второй никогда не срабатывает и создаёт ложное чувство запаса.
    """
    limits = models.chat_limits()
    by_tokens = limits["daily_tokens"] / 1085
    by_steps = limits["daily_steps"] / 1.9
    assert 0.5 <= by_tokens / by_steps <= 2.0, (by_tokens, by_steps)


def test_кэш_включает_дату_и_занятость_но_не_бюджет() -> None:
    assert models.cache_policy()["default_ttl_seconds"] == 0, (
        "всё подряд кэшировать нельзя: бюджет и расписание меняются")
    groups = {group["name"]: group for group in models.cache_policy()["groups"]}
    assert groups["date"]["ttl_seconds"] <= 60
    assert groups["availability"]["ttl_seconds"] <= 60
    assert any("какое сегодня" in pattern for pattern in groups["date"]["patterns"])
    assert any("кто сегодня занят" in pattern for pattern in groups["availability"]["patterns"])


def test_цены_положительные_и_курс_с_источником() -> None:
    prices = models.prices()
    assert prices, "нет ни одной цены"
    for model, (price_in, price_out) in prices.items():
        assert price_in >= 0 and price_out >= 0, model
    rates = models.rates()
    assert rates["usd_rub"] > 0
    assert rates["usd_rub_source"], "курс без источника — это оценка, а не цифра"
    assert rates["usd_rub_date"], "у курса должна быть дата"


def test_лимит_шагов_берётся_из_файла(tmp_path, monkeypatch) -> None:
    file = tmp_path / "models.yaml"
    file.write_text("roles:\n  agent:\n    model: openai/gpt-4o-mini\n    max_steps: 3\n",
                    encoding="utf-8")
    monkeypatch.setenv("AGENT_MODELS_PATH", str(file))
    assert config.max_steps() == 3
    monkeypatch.setenv("AGENT_MAX_STEPS", "5")
    assert config.max_steps() == 5, "переменная окружения должна перебивать файл"
