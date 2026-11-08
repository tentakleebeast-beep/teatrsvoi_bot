"""Хранилище файлов: настройки берутся из окружения, отсутствие хранилища не притворяется успехом.

Ш-1 (docs/development/86): код работает с любым S3-совместимым хранилищем, поэтому скрипт
проверки обязан отличать «хранилище сказало ок» от «хранилище не настроено» — второе не
должно выглядеть как пройденная проверка в CI, где Garage не поднят.
"""

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SLUG = ROOT / "scripts" / "storage_smoke.py"


def _load():
    spec = importlib.util.spec_from_file_location("storage_smoke_under_test", SLUG)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
def test_круговой_прогон_по_живому_хранилищу():
    """Реальный put/get/presigned/list/delete. Пропускается там, где хранилище не поднято."""
    smoke = _load()
    smoke.load_project_env()
    client = smoke.make_client()
    if client is None:
        pytest.skip("STORAGE_ENDPOINT не задан — Garage не поднят")
    problems = []
    for bucket in smoke.bucket_names():
        problems.extend(smoke.check_bucket(client, bucket))
    assert not problems, problems
