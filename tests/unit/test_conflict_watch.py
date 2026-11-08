"""Сторож конфликта поллеров: 409 в логе гейтвея → алерт админу (карта t_4aa28fe9).

Инцидент 30.10.2026: два поллера на одном токене, 1457 ошибок `TelegramConflictError`,
бот молчал 4 часа. Проверяем, что сторож замечает маркеры, отправляет один алерт на
эпизод (а не каждые 5 минут) и говорит, с какого хоста сигнал.

Обоснование: docs/infrastructure/08 §1a (сторож конфликта поллеров), docs/infrastructure/35
(таймер `svoi-conflicts.timer`).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from scripts import conflict_watch

NOW = datetime(2026, 11, 1, 12, 0).astimezone()   # локальное настенное время лога
LOG_LINE = ("2026-11-01 11:58:12,101 ERROR aiogram: Telegram server says - "
            "Conflict: terminated by other getUpdates request")
MARKER_LINE = ("2026-11-01 11:58:20,500 ERROR aiogram.dispatcher: "
               "aiogram.exceptions.TelegramConflictError: Telegram server says - Conflict")
OLD_LINE = ("2026-10-29 11:58:12,101 ERROR aiogram: Telegram server says - "
            "Conflict: terminated by other getUpdates request")


def log_dir(tmp_path: Path, *lines: str) -> Path:
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "gateway.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def recent(minutes_ago: float = 2) -> str:
    """Метка времени «сейчас минус N минут» в локальной шкале лога (для CLI-тестов)."""
    moment = datetime.now().astimezone() - timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def recent_conflict(minutes_ago: float = 2) -> str:
    return f"{recent(minutes_ago)},101 ERROR aiogram: Conflict: terminated by other getUpdates request"


def test_маркеры_конфликта_узнаются():
    assert conflict_watch.is_conflict(LOG_LINE)
    assert conflict_watch.is_conflict(MARKER_LINE)
    assert not conflict_watch.is_conflict("2026-11-01 11:00:00,000 INFO gateway started")


def test_метка_времени_из_строки():
    assert conflict_watch.stamp(LOG_LINE) == "2026-11-01 11:58:12"
    assert conflict_watch.stamp("без времени") == ""


def test_конфликта_нет_алерта_нет(tmp_path):
    directory = log_dir(tmp_path, "2026-11-01 11:00:00,000 INFO gateway started")
    result = conflict_watch.check(source="files", logs=directory, state=tmp_path / "s.json",
                                  now=NOW, apply=True, send=lambda text: True)
    assert result["found"] == 0 and result["alerted"] is False


def test_старый_лог_не_даёт_конфликтов(tmp_path):
    """Метки трёхдневной давности — не конфликт «прямо сейчас»: окно решает, не хвост файла."""
    directory = log_dir(tmp_path, OLD_LINE, OLD_LINE.replace("11:58:12", "11:59:00"))
    result = conflict_watch.check(source="files", logs=directory, state=tmp_path / "s.json",
                                  now=NOW, apply=True, send=lambda text: True)
    assert result["found"] == 0 and result["alerted"] is False


def test_старый_лог_не_читается_целиком(tmp_path, monkeypatch):
    """Второй слой: нет меток моложе окна — файл не читаем вовсе (быстрый выход)."""
    directory = log_dir(tmp_path, OLD_LINE)

    def boom(*args, **kwargs):
        raise AssertionError("старый лог прочитан целиком")

    monkeypatch.setattr(Path, "read_text", boom)
    assert conflict_watch.read_files([directory / "gateway.log"], now=NOW) == []


def test_свежий_конфликт_в_окне_даёт_алерт(tmp_path):
    directory = log_dir(tmp_path, OLD_LINE, LOG_LINE)
    sent: list[str] = []
    result = conflict_watch.check(source="files", logs=directory, state=tmp_path / "s.json",
                                  now=NOW, apply=True,
                                  send=lambda text: sent.append(text) or True)
    assert result["found"] == 1 and result["alerted"] is True
    assert "строк с конфликтом: 1" in sent[0]


def test_старые_конфликты_при_свежем_хвосте_молчат(tmp_path):
    """Случай 05.11.2026: лог дописывается, но конфликты в нём с 01.11 — алерта быть не должно."""
    directory = log_dir(tmp_path, OLD_LINE,
                        "2026-11-01 11:10:00,000 ERROR aiogram: Conflict: terminated by other "
                        "getUpdates request",
                        "2026-11-01 11:59:00,000 INFO update id=1 is handled")
    result = conflict_watch.check(source="files", logs=directory, state=tmp_path / "s.json",
                                  now=NOW + timedelta(minutes=5), apply=True,
                                  send=lambda text: True)
    assert result["found"] == 0


def test_строка_без_метки_игнорируется(tmp_path):
    """У строки без времени нет доказательства свежести — даже с маркером внутри."""
    directory = log_dir(tmp_path, "aiogram.exceptions.TelegramConflictError: Telegram says Conflict",
                        "2026-11-01 11:59:00,000 INFO update handled")
    result = conflict_watch.check(source="files", logs=directory, state=tmp_path / "s.json",
                                  now=NOW, apply=True, send=lambda text: True)
    assert result["found"] == 0


def test_конфликт_без_apply_не_шлёт(tmp_path):
    directory = log_dir(tmp_path, LOG_LINE)
    sent: list[str] = []
    result = conflict_watch.check(source="files", logs=directory, state=tmp_path / "s.json",
                                  now=NOW, send=sent.append)
    assert result["found"] == 1
    assert sent == []
    assert "без --apply" in result["skipped"]


def test_алерт_уходит_и_содержит_хост_и_счёт(tmp_path):
    directory = log_dir(tmp_path, LOG_LINE, "мусор", MARKER_LINE)
    sent: list[str] = []
    result = conflict_watch.check(source="files", logs=directory, state=tmp_path / "s.json",
                                  now=NOW, apply=True, send=lambda text: sent.append(text) or True,
                                  host="svoi-vps")
    assert result["alerted"] is True
    assert len(sent) == 1
    text = sent[0]
    assert "svoi-vps" in text
    assert "строк с конфликтом: 2" in text
    assert "11:58:12 — 11:58:20" in text


def test_один_алерт_на_эпизод(tmp_path):
    """Второй прогон сторожа в пределах эпизода молчит: иначе админа завалит."""
    directory = log_dir(tmp_path, LOG_LINE)
    state = tmp_path / "state.json"
    sent: list[str] = []
    send = lambda text: sent.append(text) or True  # noqa: E731

    first = conflict_watch.check(source="files", logs=directory, state=state, now=NOW,
                                 apply=True, send=send)
    second = conflict_watch.check(source="files", logs=directory, state=state,
                                  now=NOW + timedelta(minutes=5), apply=True, send=send)

    assert first["alerted"] is True
    assert second["alerted"] is False
    assert "уже сообщали" in second["skipped"]
    assert len(sent) == 1


def test_новый_эпизод_после_паузы(tmp_path):
    directory = log_dir(tmp_path, LOG_LINE)
    state = tmp_path / "state.json"
    sent: list[str] = []
    send = lambda text: sent.append(text) or True  # noqa: E731

    conflict_watch.check(source="files", logs=directory, state=state, now=NOW,
                         apply=True, send=send)
    later = NOW + timedelta(hours=conflict_watch.EPISODE_HOURS + 1)
    (directory / "gateway.log").write_text(
        later.astimezone().strftime("%Y-%m-%d %H:%M:%S") + ",101 ERROR aiogram: Telegram "
        "server says - Conflict: terminated by other getUpdates request\n", encoding="utf-8")
    result = conflict_watch.check(source="files", logs=directory, state=state, now=later,
                                  apply=True, send=send)
    assert result["alerted"] is True
    assert len(sent) == 2


def test_отказ_телеграма_не_пишет_состояние(tmp_path):
    directory = log_dir(tmp_path, LOG_LINE)
    state = tmp_path / "state.json"
    result = conflict_watch.check(source="files", logs=directory, state=state, now=NOW,
                                  apply=True, send=lambda text: False)
    assert result["alerted"] is False
    assert "Telegram" in result["skipped"]
    assert not state.exists()


def test_битое_состояние_не_мешает_алерту(tmp_path):
    directory = log_dir(tmp_path, LOG_LINE)
    state = tmp_path / "state.json"
    state.write_text("{это не json", encoding="utf-8")
    result = conflict_watch.check(source="files", logs=directory, state=state, now=NOW,
                                  apply=True, send=lambda text: True)
    assert result["alerted"] is True
    assert json.loads(state.read_text(encoding="utf-8"))["host"]


def test_журнал_как_источник_на_сервере(tmp_path):
    """На сервере гейтвей пишет в journald, а не в файлы launchd."""
    lines = ["2026-11-01T11:59:00+0400 svoi-gateway[1]: " + MARKER_LINE]
    result = conflict_watch.check(source="journal", unit="svoi-gateway", state=tmp_path / "s.json",
                                  now=NOW, apply=False,
                                  journal=lambda unit: lines)
    assert result["found"] == 1
    assert "journalctl -u svoi-gateway" in result["source"]


def test_файлы_используются_когда_журнала_нет(tmp_path, monkeypatch):
    directory = log_dir(tmp_path, MARKER_LINE)
    monkeypatch.setattr(conflict_watch, "has_journal", lambda: False)
    lines, where = conflict_watch.collect("auto", logs=directory, now=NOW)
    assert conflict_watch.conflicts(lines)
    assert where == str(directory)


def test_отсутствующие_файлы_логов_не_ломают_сторожа(tmp_path):
    result = conflict_watch.check(source="files", logs=tmp_path / "нет-такой-папки",
                                  state=tmp_path / "s.json", now=NOW)
    assert result["found"] == 0


def test_cli_коды_выхода(tmp_path, capsys, monkeypatch):
    directory = log_dir(tmp_path, recent_conflict())
    monkeypatch.setattr(conflict_watch, "log_dir", lambda: directory)
    monkeypatch.setattr(conflict_watch, "deliver", lambda text: True)
    monkeypatch.setattr(conflict_watch, "state_path", lambda: tmp_path / "state.json")

    assert conflict_watch.main(["--source", "files"]) == 1        # есть конфликт, без --apply
    assert "не отправлен" in capsys.readouterr().out

    assert conflict_watch.main(["--source", "files", "--apply"]) == 0
    assert "отправлен" in capsys.readouterr().out


def test_таймер_и_юнит_сторожа_на_месте():
    """Алерт «не позже 5 минут» держится расписанием таймера, а не обещанием."""
    systemd = Path(conflict_watch.PROJECT) / "deploy" / "systemd"
    timer = (systemd / "svoi-conflicts.timer").read_text(encoding="utf-8")
    service = (systemd / "svoi-conflicts.service").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* *:00/5:00" in timer
    assert "conflict_watch.py" in service and "--apply" in service
    assert "--source journal" in service


def test_dev_среда_сторожа_берёт_dev_бота(tmp_path, monkeypatch, capsys):
    """На маке launchd не умеет EnvironmentFile: --env dev берёт .env.dev и dev-бота."""
    env_file = tmp_path / ".env.dev"
    env_file.write_text(
        "DEV_BOT_TOKEN=123:devtoken\nADMIN_CHAT_ID=42\n"
        "DATABASE_URL=postgresql://svoi:pw@127.0.0.1:5433/svoi\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("SVOI_DEV_ENV_FILE", str(env_file))
    directory = log_dir(tmp_path, recent_conflict())
    monkeypatch.setattr(conflict_watch, "log_dir", lambda: directory)
    monkeypatch.setattr(conflict_watch, "deliver", lambda text: True)
    monkeypatch.setattr(conflict_watch, "state_path", lambda: tmp_path / "state.json")

    assert conflict_watch.main(["--source", "files", "--apply", "--env", "dev"]) == 0
    out = capsys.readouterr().out
    assert ".env.dev" in out and "dev-бот" in out
    import os

    assert os.environ["TELEGRAM_TOKEN"] == "123:devtoken"


def test_нет_dev_токена_сторож_отказывается(tmp_path, monkeypatch, capsys):
    """Тот же guard, что у run_service: без DEV_BOT_TOKEN — отказ, а не прод-токен."""
    empty = tmp_path / ".env.dev"
    empty.write_text("ADMIN_CHAT_ID=42\n", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("SVOI_DEV_ENV_FILE", str(empty))

    code = conflict_watch.main(["--source", "files", "--env", "dev"])
    assert code == 2
    assert "среда разработки не настроена" in capsys.readouterr().err
    import os

    assert "TELEGRAM_TOKEN" not in os.environ
