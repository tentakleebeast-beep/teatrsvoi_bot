"""Сторож доставляет алерт: stdin не должен попадать в argparse (31.10.2026).

Баг: `main(sys.stdin.read())` передавал текст проблем как argv. argparse разбирал
строку по символам и падал с «unrecognized arguments: '·' ' ' 'н' …», поэтому на
проде сторож не отправил ни одного алерта, а служба каждый час падала.

Правила и требования раздела: docs/infrastructure/36-notifications.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import alerts, watchdog_alert  # noqa: E402

STDIN = "Проблемы на сервере SVOI:\n· нет свежего бэкапа\n"


def _capture(monkeypatch: pytest.MonkeyPatch) -> tuple[list[list[str]], list[str | None]]:
    """Заглушка очереди: что передали и с какой строкой подключения."""
    seen: list[list[str]] = []
    dbs: list[str | None] = []

    def fake_queue(problems, title, moment=None, db=None, body=None):
        seen.append(list(problems))
        dbs.append(db)
        return len(list(problems))

    monkeypatch.setattr(alerts, "queue_admins", fake_queue)
    return seen, dbs


def test_stdin_is_read_not_parsed_as_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Текст пришёл из stdin: заголовок отброшен, проблема в очереди."""
    seen, dbs = _capture(monkeypatch)
    code = watchdog_alert.main(argv=[], stdin=STDIN)
    assert code == 0
    assert seen, "алерт не попал в очередь"
    assert "нет свежего бэкапа" in " ".join(seen[0])
    # Строку подключения даёт точка входа (`_cli.dsn`), а не модуль алертов.
    assert dbs and dbs[0], "watchdog_alert не передал строку подключения"


def test_problem_flag_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    seen, _ = _capture(monkeypatch)
    code = watchdog_alert.main(argv=["--problem", "диск занят 91%"], stdin="")
    assert code == 0
    assert seen and "диск занят 91%" in " ".join(seen[0])


def test_entrypoint_path_uses_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же вызов, что в `__main__`: argv без опций, текст в stdin."""
    seen, _ = _capture(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["watchdog_alert.py"])
    code = watchdog_alert.main(argv=sys.argv[1:], stdin=STDIN)
    assert code == 0
    assert code == 0 and seen
