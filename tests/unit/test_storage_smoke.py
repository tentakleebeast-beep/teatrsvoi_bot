"""Хранилище файлов: настройки берутся из окружения, отсутствие хранилища не притворяется успехом.

Ш-1 (docs/development/86): код работает с любым S3-совместимым хранилищем, поэтому скрипт
проверки обязан отличать «хранилище сказало ок» от «хранилище не настроено» — второе не
должно выглядеть как пройденная проверка в CI, где Garage не поднят.

Стенд (t_05ea699f): настройки Garage тест читает сам — `dotenv_values` по корневому `.env`
в локальный словарь. Раньше они подтекали в процесс из `scripts/agent_cost.py`, который на
импорте зовёт `_cli.apply_env()`: проверка стенда держалась на чужой утечке. Unit-набор это
чтение изолирует, поэтому источник у теста свой. `os.environ` не трогается вовсе —
подменяется `storage_smoke.env`, и значения уходят вместе с тестом.
"""

import importlib.util
import pathlib
import sys

import pytest
from dotenv import dotenv_values

ROOT = pathlib.Path(__file__).resolve().parents[2]
SLUG = ROOT / "scripts" / "storage_smoke.py"
ENV_FILE = ROOT / ".env"


def _load():
    spec = importlib.util.spec_from_file_location("storage_smoke_under_test", SLUG)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def stand_settings() -> dict[str, str]:
    """Настройки стенда из `.env` локальным словарём, без записи в окружение процесса."""
    if not ENV_FILE.is_file():
        return {}
    values = dotenv_values(ENV_FILE, encoding="utf-8", interpolate=False)
    return {
        key: value.strip()
        for key, value in values.items()
        if key.startswith("STORAGE_") and value and value.strip()
    }


@pytest.fixture
def stand(monkeypatch):
    """Модуль проверки с явными настройками стенда.

    Нет `.env` или в нём нет `STORAGE_ENDPOINT` — пропуск с прежней причиной, а не падение.
    """
    values = stand_settings()
    if not values.get("STORAGE_ENDPOINT"):
        pytest.skip("STORAGE_ENDPOINT не задан — Garage не поднят")
    smoke = _load()
    monkeypatch.setattr(
        smoke, "env", lambda name, default="": values.get(name, default).strip()
    )
    return smoke


def test_без_endpoint_проверка_не_врёт_об_успехе(monkeypatch):
    """Нет STORAGE_ENDPOINT — код выхода 0, но в выводе прямо сказано, что проверять нечего."""
    smoke = _load()
    monkeypatch.delenv("STORAGE_ENDPOINT", raising=False)
    monkeypatch.setattr(smoke, "load_project_env", lambda: None)
    assert smoke.main([]) == 0
    assert smoke.make_client() is None


def test_имена_бакетов_из_окружения(monkeypatch):
    smoke = _load()
    monkeypatch.setenv("STORAGE_BUCKET_PRIVATE", "pr")
    monkeypatch.setenv("STORAGE_BUCKET_SHARED", "sh")
    assert smoke.bucket_names() == ["pr", "sh"]
    assert smoke.bucket_names("private") == ["pr"]
    assert smoke.bucket_names("shared") == ["sh"]


@pytest.mark.integration
def test_круговой_прогон_по_живому_хранилищу(stand):
    """Реальный put/get/presigned/list/delete. Пропускается там, где хранилище не поднято."""
    client = stand.make_client()
    assert client is not None, "STORAGE_ENDPOINT прочитан из .env — клиент обязан собраться"
    problems = []
    for bucket in stand.bucket_names():
        problems.extend(stand.check_bucket(client, bucket))
    assert not problems, problems
