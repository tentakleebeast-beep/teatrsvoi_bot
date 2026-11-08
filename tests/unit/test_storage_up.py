"""Хранилище на проде: имя контейнера и адреса — из `.env`, а не из головы скрипта.

Д-03, 09.11.2026: `scripts/storage_up.py` искал контейнер `svoi-garage`, а compose
называет его `svoi-garage-dev`; на проде нужен третий вариант — без суффикса.
Один ключ `GARAGE_CONTAINER` в `.env` снимает расхождение, поэтому его разрешение
проверяется отдельно, без docker.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import storage_up  # noqa: E402 — путь к scripts/ добавляется выше


def test_имя_контейнера_из_env_файла(monkeypatch):
    monkeypatch.delenv("GARAGE_CONTAINER", raising=False)
    assert storage_up.container_name({"GARAGE_CONTAINER": "svoi-garage"}) == "svoi-garage"
    # без настройки — локальное имя из docker-compose.yml: скрипт работает и на маке
    assert storage_up.container_name({}) == "svoi-garage-dev"


def test_окружение_главнее_env_файла(monkeypatch):
    monkeypatch.setenv("GARAGE_CONTAINER", "svoi-garage-prod")
    assert storage_up.container_name({"GARAGE_CONTAINER": "svoi-garage"}) == "svoi-garage-prod"


def test_адреса_из_окружения_дописываются_в_env(monkeypatch):
    """Публичный адрес и origin Mini App знает деплой, а не скрипт: они в .env попадают."""
    written: dict[str, str] = {}
    monkeypatch.setattr(storage_up, "read_env", lambda: {})
    monkeypatch.setattr(storage_up, "append_env", lambda pairs: written.update(pairs))
    monkeypatch.setenv("GARAGE_CONTAINER", "svoi-garage")
    monkeypatch.setenv("STORAGE_PUBLIC_ENDPOINT", "https://files.example.ru")
    monkeypatch.setenv("STORAGE_CORS_ORIGINS", "https://example.ru")

    env = storage_up.ensure_secrets()

    assert written["GARAGE_CONTAINER"] == "svoi-garage"
    assert written["STORAGE_PUBLIC_ENDPOINT"] == "https://files.example.ru"
    assert written["STORAGE_CORS_ORIGINS"] == "https://example.ru"
    assert env["STORAGE_ENDPOINT"] == "http://127.0.0.1:3900"
    assert written["STORAGE_ENDPOINT"] == "http://127.0.0.1:3900"
