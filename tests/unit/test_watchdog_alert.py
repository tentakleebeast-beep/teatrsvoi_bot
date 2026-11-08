"""Сторож и health-агент ставят алерты в одну очередь с дедупликацией (Ш-30)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import scripts.alerts as alerts
import scripts.watchdog_alert as watchdog

PROJECT = Path(__file__).resolve().parents[2]
MOMENT = datetime(2026, 10, 28, 11, 5, tzinfo=UTC)


def test_нормализация_проблем():
    assert alerts.normalize_problems(["· диск занят 91%", " ", "диск занят 91%", "нет бэкапа"]) == \
        ["диск занят 91%", "нет бэкапа"]


def test_ключ_дедупликации_проблема_плюс_час():
    first = alerts.dedupe_key(["tool-api не отвечает", "нет бэкапа"], MOMENT)
    order = alerts.dedupe_key(["нет бэкапа", "tool-api не отвечает"], MOMENT)
    later = alerts.dedupe_key(["tool-api не отвечает", "нет бэкапа"],
                              datetime(2026, 10, 28, 12, 5, tzinfo=UTC))
    other = alerts.dedupe_key(["tool-api не отвечает"], MOMENT)
    assert first == order
    assert first.startswith("health:2026102811:")
    assert first != later and later.startswith("health:2026102812:")
    assert first != other


def test_чтение_проблем_из_stdin_без_заголовка():
    text = "Проблемы на сервере SVOI:\n· tool-api не отвечает\n· диск занят 91%\n\n"
    assert watchdog.read_problems(None, text) == ["tool-api не отвечает", "диск занят 91%"]
    assert watchdog.read_problems(["· диск занят 91%"], text) == ["диск занят 91%"]


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Conn:
    """Фейковая база: повторный insert с тем же ключом не возвращает id."""

    def __init__(self):
        self.keys: list[tuple[str, int]] = []
        self.payloads: list[dict] = []

    def execute(self, sql, params=None):
        if "from participant" in sql:
            return _Rows([(1,), (2,)])
        assert "insert into notification" in sql
        import json

        key = params[2]
        pair = (key, params[0])
        self.payloads.append(json.loads(params[1]))
        if pair in self.keys:
            return _Rows([])
        self.keys.append(pair)
        return _Rows([(10,)])

    def commit(self):
        pass


class _Ctx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def _patch_db(monkeypatch, conn):
    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda db: _Ctx(conn))


def test_повторный_алерт_в_тот_же_час_не_дублируется(monkeypatch):
    conn = _Conn()
    _patch_db(monkeypatch, conn)
    problems = ["tool-api не отвечает", "нет бэкапа"]
    assert alerts.queue_admins(problems, "Проблемы на сервере SVOI", MOMENT, db="x") == 2
    assert alerts.queue_admins(problems, "Проблемы на сервере SVOI", MOMENT, db="x") == 0
    key = alerts.dedupe_key(problems, MOMENT)
    assert conn.keys == [(key, 1), (key, 2)]
    assert conn.payloads[0]["title"] == "Проблемы на сервере SVOI"
    assert "· tool-api не отвечает" in conn.payloads[0]["body"]


def test_health_агент_и_сторож_дают_один_ключ():
    import scripts.health_agent as agent

    found = ["tool-api не отвечает на /health"]
    expected = alerts.dedupe_key(found, MOMENT)
    assert expected == alerts.dedupe_key(["· tool-api не отвечает на /health"], MOMENT)
    assert agent.alert_text(found, None).splitlines()[0] == "Проблемы с tool-api SVOI:"
    assert expected.startswith("health:")


def test_резерв_при_недоступной_базе(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(alerts, "queue_admins", boom)
    sent: list[str] = []
    monkeypatch.setattr(alerts, "send_telegram", lambda text: sent.append(text) or True)
    code = watchdog.main(["--problem", "tool-api не отвечает"], "")
    assert code == 2
    assert sent and "tool-api не отвечает" in sent[0]
    assert "резерв: отправлено" in capsys.readouterr().err


def test_резерв_тоже_не_удался(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(alerts, "queue_admins", boom)
    monkeypatch.setattr(alerts, "send_telegram", lambda text: False)
    assert watchdog.main(["--problem", "диск занят 91%"], "") == 1


def test_сторож_не_шлёт_в_telegram_напрямую():
    script = (PROJECT / "scripts/watchdog.sh").read_text()
    assert "api.telegram.org" not in script
    assert "watchdog_alert.py" in script
