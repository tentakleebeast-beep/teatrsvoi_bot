"""Недельная сводка конвейера: текст из фикстуры, без запуска Telegram.

Фикстура `tests/fixtures/kanban_metrics_week.json` — это ровно то, что отдаёт
`scripts/kanban_metrics.py --board teatrsvoi --json`.

Правила и требования раздела: docs/infrastructure/36-notifications.md §5b.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.cron import kanban_digest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "kanban_metrics_week.json"
MOMENT = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


@pytest.fixture()
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_пять_блоков_и_цифры_из_фикстуры(payload):
    text = kanban_digest.format_digest(payload["teatrsvoi"], now=MOMENT)

    assert "Выполнено за неделю: 14" in text
    assert "Цикл (медиана): 1.4 ч" in text
    assert "Доводки ревью: 3" in text
    assert "Аварии (таймаут/падение): 4 / 11" in text
    assert "В очереди / блокировано: 15 / 2" in text
    # окно и момент попадают в шапку, чтобы сводку можно было прочитать позже
    assert "окно 7 дн" in text
    assert f"{MOMENT.astimezone(kanban_digest.SAMARA):%d.%m.%Y %H:%M}" in text


def test_без_эмодзи_и_в_одну_страницу(payload):
    text = kanban_digest.format_digest(payload["teatrsvoi"], now=MOMENT)
    assert not EMOJI.search(text)
    assert len(text) <= kanban_digest.LIMIT
    assert len(text.splitlines()) <= 12


def test_пустые_значения_не_ломают_текст():
    text = kanban_digest.format_digest(
        {"done": 1, "done_in_window": 0, "cycle_median_h": None,
         "runs_per_card": None, "rework": 0, "rework_rate": 0.0,
         "timeouts": 0, "crashes": 0, "open": 1, "blocked": 0,
         "oldest_open_h": None},
        now=MOMENT)
    assert "Цикл (медиана): —" in text
    assert "самая старая ждёт —" in text
    assert "Выполнено за неделю: 0" in text


def test_сообщение_об_ошибке_называет_причину():
    text = kanban_digest.format_error("доска teatrsvoi не найдена", now=MOMENT)
    assert "не собрана" in text
    assert "Причина: доска teatrsvoi не найдена" in text
    assert "kanban_metrics.py --board teatrsvoi --json" in text
    assert not EMOJI.search(text)


def test_ошибка_сбора_уходит_в_чат_и_даёт_код_1(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(kanban_digest, "deliver",
                        lambda text, dry_run=False: sent.append(text) or 0)
    monkeypatch.setattr(kanban_digest, "collect",
                        lambda *a, **k: (_ for _ in ()).throw(
                            kanban_digest.CollectionError("на доске нет карт")))

    assert kanban_digest.main([]) == 1
    assert len(sent) == 1 and "на доске нет карт" in sent[0]


def test_успешная_сводка_идёт_в_чат(monkeypatch, payload):
    sent: list[str] = []
    monkeypatch.setattr(kanban_digest, "deliver",
                        lambda text, dry_run=False: sent.append(text) or 0)
    monkeypatch.setattr(kanban_digest, "collect", lambda *a, **k: payload["teatrsvoi"])

    assert kanban_digest.main([]) == 0
    assert "Аварии (таймаут/падение): 4 / 11" in sent[0]


def test_нет_доски_это_ошибка_сбора(payload):
    with pytest.raises(kanban_digest.CollectionError, match="не найдена"):
        kanban_digest.pick_board({}, "teatrsvoi")


def test_ошибка_чтения_базы_это_ошибка_сбора():
    with pytest.raises(kanban_digest.CollectionError, match="недоступна"):
        kanban_digest.pick_board({"teatrsvoi": {"error": "unable to open database file"}})


def test_пустая_доска_это_ошибка_сбора():
    data = {"done": 0, "done_in_window": 0, "open": 0, "blocked": 0}
    with pytest.raises(kanban_digest.CollectionError, match="нет карт"):
        kanban_digest.pick_board({"teatrsvoi": data})


def test_сбор_снимает_HERMES_HOME_профиля(monkeypatch):
    """Иначе в профиле воркера метрика ищет доску не там и молча отдаёт {}."""
    seen: dict = {}

    class _Done:
        returncode = 0
        stdout = json.dumps({"teatrsvoi": {"done": 1, "open": 0}})
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen.update(kwargs.get("env") or {})
        return _Done()

    monkeypatch.setenv("HERMES_HOME", "/Users/x/.hermes/profiles/worker-feature")
    monkeypatch.setattr(kanban_digest.subprocess, "run", fake_run)

    assert kanban_digest.collect()["done"] == 1
    assert "HERMES_HOME" not in seen


def test_явный_hermes_home_передаётся(monkeypatch):
    seen: dict = {}

    class _Done:
        returncode = 0
        stdout = json.dumps({"teatrsvoi": {"done": 1, "open": 0}})
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen.update(kwargs.get("env") or {})
        return _Done()

    monkeypatch.setattr(kanban_digest.subprocess, "run", fake_run)
    kanban_digest.collect(hermes_home="/tmp/other-hermes")
    assert seen["HERMES_HOME"] == "/tmp/other-hermes"


def test_падение_метрики_это_ошибка_сбора(monkeypatch):
    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "Traceback ...\nsqlite3.OperationalError: disk I/O error\n"

    monkeypatch.setattr(kanban_digest.subprocess, "run", lambda *a, **k: _Failed())
    with pytest.raises(kanban_digest.CollectionError, match="disk I/O error"):
        kanban_digest.collect()


def test_не_json_это_ошибка_сбора(monkeypatch):
    class _Garbage:
        returncode = 0
        stdout = "не json"
        stderr = ""

    monkeypatch.setattr(kanban_digest.subprocess, "run", lambda *a, **k: _Garbage())
    with pytest.raises(kanban_digest.CollectionError, match="не JSON"):
        kanban_digest.collect()


def test_текст_не_содержит_латиницы_кроме_команд(payload):
    """Русский язык в блоках; латиница допустима только в имени доски и команде."""
    text = kanban_digest.format_digest(payload["teatrsvoi"], now=MOMENT)
    body = "\n".join(line for line in text.splitlines()
                     if not line.startswith("Проверить:"))
    latin = re.sub(r"teatrsvoi|kanban_metrics|\.py", "", body)
    assert not re.search(r"[A-Za-z]", latin)


def test_deliver_зовёт_notify_admin(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(kanban_digest.notify_admin, "main",
                        lambda argv=None: calls.append(list(argv or [])) or 0)
    assert kanban_digest.deliver("текст") == 0
    assert calls == [["текст"]]
    assert kanban_digest.deliver("текст", dry_run=True) == 0
    assert calls[-1] == ["--dry-run", "текст"]


def test_вывод_стабилен_по_байтам(payload):
    first = kanban_digest.format_digest(payload["teatrsvoi"], now=MOMENT)
    second = kanban_digest.format_digest(payload["teatrsvoi"], now=MOMENT)
    assert first == second
    assert "\r" not in first


def test_sys_path_не_ломает_импорт():
    """Скрипт запускается и как `scripts.cron.kanban_digest`, и напрямую из cron."""
    assert Path(kanban_digest.METRICS).is_file()
    assert str(ROOT / "scripts") in sys.path
