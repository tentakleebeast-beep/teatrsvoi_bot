"""Перезапуск сервисов без шелла (Ш-29): прямой запуск, launchd и проверка /health."""

from __future__ import annotations

import json

from scripts import install_launchd, run_service, start_services


class _FakePopen:
    """Заглушка процесса: тесту нужен только PID, а не живой сервис."""

    def __init__(self, pid: int):
        self.pid = pid


def test_прямой_запуск_отвязан_от_сессии(tmp_path, monkeypatch):
    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return _FakePopen(4242)

    monkeypatch.setattr(start_services.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(run_service, "service_env",
                        lambda service, environment="dev", *a, **k: {"PATH": "/usr/bin"})
    pid = start_services.spawn_direct("gateway", logs=tmp_path)

    assert pid == 4242
    argv, kwargs = calls[0]
    # шелла нет: argv — ровно та команда, что в systemd-юните
    assert argv == run_service.command("gateway")
    assert kwargs.get("shell") is not True
    assert kwargs["start_new_session"] is True, "процесс должен пережить конец тика"
    assert kwargs["cwd"] == str(start_services.PROJECT)
    assert "PYTHONPATH" not in kwargs["env"]
    assert (tmp_path / "gateway.log").exists()
    assert (tmp_path / "gateway.err.log").exists()


def test_через_launchd_идёт_с_takeover(monkeypatch):
    seen: dict[str, object] = {}

    def fake_install(services=None, **kwargs):
        seen["services"] = services
        seen["kwargs"] = kwargs
        return ["tool-api: bootstrap ok", "gateway: bootstrap ok"]

    monkeypatch.setattr(start_services, "use_launchd", lambda: True)
    monkeypatch.setattr(start_services.install_launchd, "install", fake_install)
    monkeypatch.setattr(start_services, "wait_ready", lambda *a, **k: [])

    actions, problems = start_services.restart(["gateway"])

    assert problems == []
    assert seen["services"] == ["gateway"]
    assert seen["kwargs"]["takeover"] is True, "без --takeover чужой поллер останется"
    assert any("bootstrap ok" in line for line in actions)


def test_ошибка_launchd_это_провал_без_ожидания(monkeypatch):
    monkeypatch.setattr(start_services, "use_launchd", lambda: True)
    monkeypatch.setattr(
        start_services.install_launchd, "install",
        lambda services=None, **kwargs: ["gateway: bootstrap ошибка: 5: Input/output error"],
    )
    waited: list[bool] = []
    monkeypatch.setattr(start_services, "wait_ready", lambda *a, **k: waited.append(True) or [])

    actions, problems = start_services.restart(["gateway"], timeout=0)

    assert waited == [], "при ошибке загрузки ждать /health бессмысленно"
    assert any("ошибка" in line for line in actions)
    assert problems and "ошибка" in problems[0]


def test_таймаут_ожидания_отдаёт_проблемы(monkeypatch):
    monkeypatch.setattr(start_services.check_services, "check_live",
                        lambda *a, **k: ["http://127.0.0.1:8000/health: нет ответа"])
    problems = start_services.wait_ready("http://127.0.0.1:8000", timeout=0)
    assert problems == ["http://127.0.0.1:8000/health: нет ответа"]


def test_main_возвращает_1_и_печатает_проблемы(monkeypatch, capsys):
    monkeypatch.setattr(start_services, "use_launchd", lambda: False)
    monkeypatch.setattr(start_services, "spawn_direct", lambda service, port, logs=None: 777)
    monkeypatch.setattr(start_services, "wait_ready",
                        lambda *a, **k: ["tg-gateway не запущен: апдейты никто не читает"])

    code = start_services.main(["--direct", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["actions"] == ["tool-api: запущен напрямую, PID 777",
                                  "gateway: запущен напрямую, PID 777"]
    assert "не запущен" in payload["problems"][0]


def test_каталог_логов_тот_же_что_у_launchd():
    assert start_services.log_dir() == install_launchd.log_dir()
