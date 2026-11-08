"""Модели, бюджет и кэш агента — из конфига, а не из кода на каждый эксперимент (A-4).

Зачем модуль. До карты A-4 модель агентного шага и классификатора задавалась только
переменными окружения, а цены, дневной лимит чата и TTL кэша не были записаны нигде.
Эксперимент «попробуем другую модель» означал правку `.env` на сервере и перезапуск,
а цифры замера A-0 оставались в отчёте и не влияли на выбор.

Роли:
    agent       — модель с надёжным tool-calling (выбор по замеру, не по цене);
    classifier  — дешёвая модель без инструментов: только сценарий.

Приоритет значений: переменная окружения (явное переопределение) → `config/agent-models.yaml`
→ умолчание в коде. Файл читается при обращении с кэшем по времени изменения: правка
конфига действует со следующего запроса, без перезапуска.

Битый, пустой или пропавший файл не роняет tool-api: работают умолчания из кода.
Иначе опечатка в YAML превращалась бы в «модель не задана» и молчаливую деградацию
агента к правилам — то есть отказ, который никто не заметил.

Цены и курс в этом же файле нужны двум потребителям цифр: `scripts/agent_cost.py`
(отчёт о стоимости) и боевой учёт расхода в `llm_usage` (`agently/cost.py`, карта
AUD-10, `t_4a9705ea`). На ответы агента они не влияют, но отчёт и журнал обязаны
считать по одному источнику, иначе их суммы расходятся. Модели, которой в конфиге
нет, цена не выдумывается: в `llm_usage` у неё `cost_rub = NULL` и
`price_status = 'no_price'` («цены нет»), а не ноль — ноль значил бы «посчитано и
вышло даром» и молча занижал бы расход.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

import yaml

DEFAULT_PATH = pathlib.Path(__file__).resolve().parents[4] / "config" / "agent-models.yaml"

ROLE_AGENT = "agent"
ROLE_CLASSIFIER = "classifier"

#: Умолчания повторяют цифры замера A-0 (docs/reports/112) и не зависят от файла.
DEFAULT_ROLE_MODELS = {
    ROLE_AGENT: "openai/gpt-4o-mini",
    ROLE_CLASSIFIER: "openai/gpt-4.1-nano",
}
DEFAULT_MAX_STEPS = 4
DEFAULT_DAILY_TOKENS = 60_000
DEFAULT_DAILY_STEPS = 100
DEFAULT_USD_RUB = 80.5573

_CACHE: dict[str, Any] = {"stamp": None, "data": {}}


def path() -> pathlib.Path:
    """Путь к файлу: `AGENT_MODELS_PATH` (тесты, второй стенд) или `config/agent-models.yaml`."""
    return pathlib.Path(os.getenv("AGENT_MODELS_PATH") or DEFAULT_PATH)


def _stamp(file: pathlib.Path) -> tuple[float, int] | None:
    try:
        stat = file.stat()
    except OSError:
        return None
    return (stat.st_mtime, stat.st_size)


def document() -> dict:
    """Разобранный конфиг; при любой проблеме — пустой словарь (работают умолчания)."""
    file = path()
    stamp = _stamp(file)
    if stamp is None:
        return {}
    if _CACHE["stamp"] == stamp:
        return _CACHE["data"]
    try:
        with file.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError):
        data = None
    if not isinstance(data, dict):
        data = {}
    _CACHE["stamp"] = stamp
    _CACHE["data"] = data
    return data


def _section(name: str) -> dict:
    value = document().get(name)
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive_int(value: Any, default: int) -> int:
    """Целое больше нуля; строка из YAML тоже принимается, мусор — умолчание."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def roles() -> dict[str, dict]:
    """Настройки ролей из файла: `{role: {model, max_steps}}`, только известные роли."""
    raw = _section("roles")
    result: dict[str, dict] = {}
    for name in (ROLE_AGENT, ROLE_CLASSIFIER):
        entry = raw.get(name)
        if not isinstance(entry, dict):
            continue
        model = _text(entry.get("model"))
        if not model:
            continue
        role: dict[str, Any] = {"model": model}
        steps = entry.get("max_steps")
        if steps is not None:
            role["max_steps"] = _positive_int(steps, DEFAULT_MAX_STEPS)
        result[name] = role
    return result


def role_model(role: str, default: str = "") -> str:
    """Модель роли: файл, затем умолчание кода (или явно переданное вызывающим).

    Умолчание в коде повторяет выбор замера: без файла агент обязан остаться на
    модели, которая мерилась, а не уехать на общую `LLM_MODEL` без инструментов.
    """
    entry = roles().get(role)
    if entry:
        return str(entry["model"])
    return _text(default) or DEFAULT_ROLE_MODELS.get(role, "")


def role_max_steps(default: int = DEFAULT_MAX_STEPS) -> int:
    """Лимит шагов исполнителя из файла (замер A-0: 4 достаточно, больше не нужно)."""
    entry = roles().get(ROLE_AGENT) or {}
    return _positive_int(entry.get("max_steps"), default)


def chat_limits() -> dict[str, int]:
    """Дневной бюджет чата: токены и шаги. Обе цифры — пределы одного чата в сутки."""
    section = _section("chat_budget")
    return {
        "daily_tokens": _positive_int(section.get("daily_tokens"), DEFAULT_DAILY_TOKENS),
        "daily_steps": _positive_int(section.get("daily_steps"), DEFAULT_DAILY_STEPS),
    }


def cache_policy() -> dict[str, Any]:
    """Политика кэша: TTL по умолчанию и группы повторяющихся вопросов.

    `default_ttl_seconds: 0` — «вне групп не кэшируем». Это осознанное умолчание:
    бюджет и расписание меняются, а кэш на минуту превращает свежую цифру в старую.
    """
    section = _section("cache")
    groups: list[dict] = []
    raw_groups = section.get("groups")
    if isinstance(raw_groups, list):
        for entry in raw_groups:
            if not isinstance(entry, dict):
                continue
            ttl = entry.get("ttl_seconds")
            patterns = [str(item) for item in (entry.get("patterns") or [])
                        if isinstance(item, str) and item.strip()]
            try:
                seconds = float(ttl)
            except (TypeError, ValueError):
                continue
            if seconds <= 0 or not patterns:
                continue
            groups.append({"name": _text(entry.get("name")) or "group",
                           "ttl_seconds": seconds, "patterns": patterns})
    try:
        default_ttl = float(section.get("default_ttl_seconds") or 0)
    except (TypeError, ValueError):
        default_ttl = 0.0
    return {"default_ttl_seconds": max(0.0, default_ttl), "groups": groups}


def prices() -> dict[str, tuple[float, float]]:
    """Цены провайдера, $ за 1M токенов: модель → (вход, выход)."""
    result: dict[str, tuple[float, float]] = {}
    for model, value in _section("prices_usd_per_million").items():
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            continue
        try:
            price_in, price_out = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            continue
        if price_in < 0 or price_out < 0:
            continue
        result[str(model)] = (price_in, price_out)
    return result


def rates() -> dict[str, Any]:
    """Курс для колонки «рубли» и его источник (дата и описание — для проверки)."""
    section = _section("currency")
    raw = section.get("usd_rub")
    if raw is None or raw == "":
        usd_rub = DEFAULT_USD_RUB
    else:
        try:
            usd_rub = float(raw)
        except (TypeError, ValueError):
            usd_rub = DEFAULT_USD_RUB
    if usd_rub <= 0:
        usd_rub = DEFAULT_USD_RUB
    rate_date = section.get("usd_rub_date")
    if rate_date is None:
        rate_date_text = ""
    elif hasattr(rate_date, "isoformat"):
        rate_date_text = rate_date.isoformat()
    else:
        rate_date_text = _text(rate_date)
    return {"usd_rub": usd_rub, "usd_rub_date": rate_date_text,
            "usd_rub_source": _text(section.get("usd_rub_source"))}
