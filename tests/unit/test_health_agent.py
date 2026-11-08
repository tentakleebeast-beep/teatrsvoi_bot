"""Health-агент: сверка живой сборки с HEAD и очередь админу (FR-H12/H13, L-005)."""

from __future__ import annotations

from datetime import UTC, datetime, timezone

import scripts.health_agent as agent

HEAD = "a43a396be7c1"
HEALTH = {"ok": True, "git_sha": HEAD, "started_at": "2026-10-28T10:00:00+00:00",
          "uptime": 125}


def test_normalize_sha():
    assert agent.normalize_sha(HEAD.upper()) == HEAD
    assert agent.normalize_sha(HEAD + "f0d2e3b4") == HEAD
    assert agent.normalize_sha("unknown") == ""
    assert agent.normalize_sha(None) == ""
    assert agent.normalize_sha("zzzzzzz") == ""


def test_здоровая_сборка_без_проблем():
    assert agent.problems(HEALTH, HEAD) == []


def test_сервис_не_отвечает():
    assert agent.problems(None, HEAD) == ["tool-api не отвечает на /health"]


def test_расхождение_с_head_и_старый_ответ():
    stale = dict(HEALTH, git_sha="191199a00000", ok=False,
                 started_at=None, uptime=-1)
    found = agent.problems(stale, HEAD)
    assert any("не совпадает с HEAD" in item for item in found)
    assert "tool-api ответил ok=false" in found
    assert "в /health нет started_at" in found
    assert "в /health нет корректного uptime" in found


def test_неизвестная_сборка_отдельная_проблема():
    found = agent.problems(dict(HEALTH, git_sha="unknown"), HEAD)
    assert found == ["живой процесс не сообщает git_sha (сборка неизвестна)"]


def test_текст_алерта():
    text = agent.alert_text(["tool-api не отвечает на /health"], None)
    assert text.startswith("Проблемы с tool-api SVOI:")
    assert "· tool-api не отвечает" in text


def test_main_код_выхода(monkeypatch, capsys):
    monkeypatch.setattr(agent, "fetch_health", lambda url, timeout: HEALTH)
    monkeypatch.setattr(agent, "head_sha", lambda root: HEAD)
    assert agent.main([]) == 0
    monkeypatch.setattr(agent, "head_sha", lambda root: "191199a00000")
    assert agent.main([]) == 1
    monkeypatch.setattr(agent, "head_sha", lambda root: "")
    monkeypatch.setattr(agent, "fetch_health", lambda url, timeout: None)
    assert agent.main([]) == 1
    assert "не отвечает" in capsys.readouterr().out


def test_queue_admins_ставит_по_одной_записи(monkeypatch):
    calls = []

    class Conn:
        def execute(self, sql, params=None):
            calls.append((sql, params))
            if "from participant" in sql:
                return _Rows([(1,), (2,)])
            return _Rows([(10,)])

        def commit(self):
            calls.append(("commit", None))

    class _Rows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Ctx:
        def __enter__(self):
            return Conn()

        def __exit__(self, *exc):
            return False

    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda db: Ctx())
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    queued = agent.queue_admins(["tool-api не отвечает на /health"], None,
                                datetime(2026, 10, 28, 11, 5, tzinfo=UTC))
    assert queued == 2
    keys = [params[2] for sql, params in calls if params and "insert into notification" in sql]
    from scripts import alerts

    expected = alerts.dedupe_key(["tool-api не отвечает на /health"],
                                 datetime(2026, 10, 28, 11, 5, tzinfo=UTC))
    assert keys == [expected, expected]
    assert expected.startswith("health:2026102811:")
