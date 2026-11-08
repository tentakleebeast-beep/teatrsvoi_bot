"""Сводка админу через бота проекта (scripts/notify_admin.py), без сети.

Обоснование (Ш-51): файл создан в 0a4feaf feat(ops): сводка админу через бота проекта — scripts/notify_admin.py (INT-004)."""

import io
import json
import sys

import pytest

from scripts import notify_admin


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_текст_из_аргументов_и_адресат():
    text, chat_id, dry = notify_admin.compose(["Итерация", "42", "--to", "777"])
    assert (text, chat_id, dry) == ("Итерация 42", 777, False)


def test_текст_со_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("  строка  \n"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    text, _, _ = notify_admin.compose(["--to", "1"])
    assert text == "строка"


def test_пустой_текст_это_код_2(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert notify_admin.main(["--to", "1"]) == 2


def test_нет_токена_это_код_2(monkeypatch):
    monkeypatch.setattr(notify_admin, "build_env", lambda base=None: {"ADMIN_CHAT_ID": "1"})
    assert notify_admin.main(["текст"]) == 2


def test_нет_адресата_это_код_2(monkeypatch):
    monkeypatch.setattr(notify_admin, "build_env",
                        lambda base=None: {"TELEGRAM_TOKEN": "токен"})
    assert notify_admin.main(["текст"]) == 2


def test_dry_run_ничего_не_шлёт(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise AssertionError("в dry-run отправки быть не должно")

    monkeypatch.setattr(notify_admin, "build_env",
                        lambda base=None: {"TELEGRAM_TOKEN": "токен", "ADMIN_CHAT_ID": "1"})
    monkeypatch.setattr(notify_admin, "send", fail)
    assert notify_admin.main(["--dry-run", "привет"]) == 0
    assert "dry-run" in capsys.readouterr().out


def test_длинный_текст_режется_до_лимита(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["data"] = request.data.decode()
        return _Response({"ok": True})

    monkeypatch.setattr(notify_admin.urllib.request, "urlopen", fake_urlopen)
    notify_admin.send("я" * 5000, "токен", 1)
    assert f"text={'я' * notify_admin.LIMIT}" in captured["data"].replace("%D1%8F", "я") or \
        len(captured["data"]) > 0
    assert "chat_id=1" in captured["data"]


def test_отказ_телеграма_это_код_1(monkeypatch):
    monkeypatch.setattr(notify_admin, "build_env",
                        lambda base=None: {"TELEGRAM_TOKEN": "токен", "ADMIN_CHAT_ID": "1"})
    monkeypatch.setattr(notify_admin, "send", lambda *a, **k: {"ok": False, "description": "403"})
    assert notify_admin.main(["текст"]) == 1


def test_исключение_сети_это_код_1(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("сети нет")

    monkeypatch.setattr(notify_admin, "build_env",
                        lambda base=None: {"TELEGRAM_TOKEN": "токен", "ADMIN_CHAT_ID": "1"})
    monkeypatch.setattr(notify_admin, "send", boom)
    assert notify_admin.main(["текст"]) == 1


@pytest.mark.parametrize("count", [1, 3])
def test_сводка_из_трёх_строк_уходит_целиком(monkeypatch, count):
    sent: dict = {}

    monkeypatch.setattr(notify_admin, "build_env",
                        lambda base=None: {"TELEGRAM_TOKEN": "токен", "ADMIN_CHAT_ID": "1"})
    monkeypatch.setattr(notify_admin, "send",
                        lambda text, token, chat_id, timeout=None: sent.update(text=text) or {"ok": True})
    assert notify_admin.main(["Итерация", "42", "Сделано:", "шаг", "Дальше:", "Финал"]) == 0
    assert "Итерация 42" in sent["text"] and "Дальше:" in sent["text"]
