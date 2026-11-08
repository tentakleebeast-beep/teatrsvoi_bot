"""Ежедневный отчёт «требует вас»: текст из фикстуры базы, без Telegram.

Фикстура — настоящая sqlite-база доски (те же таблицы `tasks`, `task_events`,
`task_comments`, что у Hermes), собранная в tmp_path. Проверяем не «скрипт
запустился», а строки, которые владелец увидит в чате.

Правила раздела: docs/infrastructure/36-notifications.md §5c.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.cron import kanban_daily

ROOT = Path(__file__).resolve().parents[2]
MOMENT = datetime(2026, 11, 2, 5, 30, tzinfo=UTC)   # 09:30 в Самаре
NOW = 1_800_000_000
HOUR = 3600
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")

SCHEMA = """
create table tasks (id text primary key, title text, body text, assignee text,
                    status text not null, priority integer default 0,
                    created_at integer not null, completed_at integer,
                    started_at integer, block_kind text);
create table task_events (id integer primary key autoincrement, task_id text,
                          run_id integer, kind text not null, payload text,
                          created_at integer not null);
create table task_comments (id integer primary key autoincrement, task_id text,
                            author text not null, body text not null,
                            created_at integer not null);
"""


def _board(tmp_path: Path) -> Path:
    """Доска, на которой есть ровно всё, о чём должен сказать отчёт."""
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)

    def card(cid, title, status, assignee="worker-feature", priority=3,
             created=None, completed=None, block_kind=None):
        conn.execute(
            "insert into tasks (id, title, body, assignee, status, priority, created_at,"
            " completed_at, block_kind) values (?,?,?,?,?,?,?,?,?)",
            (cid, title, "тело", assignee, status, priority,
             NOW - (created or 0), completed, block_kind))

    def blocked_event(cid, age_h, payload):
        conn.execute("insert into task_events (task_id, kind, payload, created_at) "
                     "values (?, 'blocked', ?, ?)", (cid, payload, NOW - age_h * HOUR))

    # Блокировка с вопросом: ждёт 8 часов, вид needs_input.
    card("t_block", "Ожидание ключа LLM для живого замера", "blocked",
         priority=3, created=9 * HOUR, block_kind="needs_input")
    blocked_event("t_block", 8, '{"reason": "Нужен LLM_API_KEY в .env и подтверждение порога",'
                                ' "kind": "needs_input"}')
    # Блокировка без причины: событие есть, текста нет, комментариев нет — «нет причины».
    card("t_noreason", "Лишняя подзадача от авто-декомпозера", "blocked",
         priority=1, created=11 * HOUR, block_kind="capability")
    blocked_event("t_noreason", 10, '{"kind": "capability"}')
    # Молодая блокировка без вопроса: человеку ещё не мешает, в отчёт не идёт.
    card("t_young", "Свежая блокировка без вопроса", "blocked",
         priority=2, created=3 * HOUR, block_kind="capability")
    blocked_event("t_young", 2, '{"kind": "capability", "reason": "ждём стенд"}')
    # Мёртвые карты: без исполнителя и без класса приоритета.
    card("t_noassignee", "Карта без исполнителя", "ready", assignee=None, priority=2)
    card("t_noprio", "Карта без класса приоритета", "ready", priority=0)
    # Очередь: три карты в порядке диспетчера (priority DESC, created_at ASC).
    card("t_q1", "Первая карта очереди", "ready", priority=5, created=20 * HOUR)
    card("t_q2", "Вторая карта очереди", "todo", priority=4, created=18 * HOUR)
    card("t_q3", "Третья карта очереди", "ready", priority=3, created=16 * HOUR)
    # Итог: одна карта закрыта за сутки, одна раньше, одна в работе, одна в ревью.
    card("t_done_today", "Закрыта сегодня", "done", priority=2,
         created=30 * HOUR, completed=NOW - 5 * HOUR)
    card("t_done_old", "Закрыта позавчера", "done", priority=2,
         created=60 * HOUR, completed=NOW - 40 * HOUR)
    card("t_running", "В работе", "running", priority=3, created=4 * HOUR)
    card("t_review", "В ревью", "review", priority=4, created=6 * HOUR)
    conn.commit()
    conn.close()
    return db


def _empty_board(tmp_path: Path) -> Path:
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return db


@pytest.fixture()
def data(tmp_path):
    return kanban_daily.collect(_board(tmp_path), now=NOW)


@pytest.fixture()
def board_home(tmp_path):
    """Чистый HERMES_HOME с доской: тесты не смотрят в домашний ~/.hermes.

    Гейт на CI падал именно из-за этого: `main()` зовёт `find_db()` раньше
    `collect()`, и на раннере домашней доски нет.
    """
    home = tmp_path / "hermes"
    board = home / "kanban" / "boards" / "teatrsvoi"
    board.mkdir(parents=True)
    _board(board)
    return home


@pytest.fixture()
def local_db(monkeypatch, tmp_path):
    """Подмена `find_db`: тест проверяет main(), а не наличие доски на машине."""
    fake = tmp_path / "kanban.db"
    monkeypatch.setattr(kanban_daily, "find_db", lambda *a, **k: fake)
    return fake


def test_текст_из_фикстуры_содержит_все_разделы(data):
    text = kanban_daily.format_report(data, now=MOMENT)

    assert "Ежедневный отчёт: требует вас" in text
    assert "Доска teatrsvoi, 02.11.2026 09:30" in text
    assert "Требует вас: 2" in text
    assert "Мёртвые: 2" in text
    assert "- без исполнителя: 1 — t_noassignee «Карта без исполнителя»" in text
    assert "- без приоритета: 1 — t_noprio «Карта без класса приоритета»" in text
    assert "Очередь: 3 первые" in text
    assert "Итог: сделано за сутки 1, в работе 1, в ревью 1" in text


def test_блокировка_показывает_вид_вопрос_и_сколько_ждёт(data):
    text = kanban_daily.format_report(data, now=MOMENT)
    assert ("- t_block «Ожидание ключа LLM для живого замера» — нужен ответ, ждёт 8 ч"
            in text)
    assert "  Вопрос: Нужен LLM_API_KEY в .env и подтверждение порога" in text


def test_блокировка_без_комментария_помечена_нет_причины(data):
    text = kanban_daily.format_report(data, now=MOMENT)
    assert ("- t_noreason «Лишняя подзадача от авто-декомпозера» — нужна ручная работа,"
            " ждёт 10 ч, нет причины") in text


def test_молодая_блокировка_без_вопроса_не_попадает(data):
    text = kanban_daily.format_report(data, now=MOMENT)
    assert "t_young" not in text
    assert [b["id"] for b in data["blocks"]] == ["t_noreason", "t_block"]


def test_любой_needs_input_попадает_даже_свежий(tmp_path):
    db = tmp_path / "fresh.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute("insert into tasks (id, title, status, priority, created_at, block_kind) "
                 "values ('t_fresh', 'Свежий вопрос', 'blocked', 3, ?, 'needs_input')",
                 (NOW - HOUR,))
    conn.execute("insert into task_events (task_id, kind, payload, created_at) "
                 "values ('t_fresh', 'blocked', ?, ?)",
                 ('{"kind": "needs_input", "reason": "Нужно решение по порядку листа"}',
                  NOW - 600))
    conn.commit()
    conn.close()

    blocks = kanban_daily.collect(db, now=NOW)["blocks"]
    assert [b["id"] for b in blocks] == ["t_fresh"]
    assert "нужен ответ" in kanban_daily.format_report(
        {"blocks": blocks}, now=MOMENT)


def test_свежая_блокировка_без_причины_всё_равно_в_отчёте(tmp_path):
    """«Нет причины» — это дефект карты, а не «ещё не срок»: показываем сразу."""
    db = tmp_path / "young.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute("insert into tasks (id, title, status, priority, created_at, block_kind) "
                 "values ('t_young_bare', 'Заблокирована без объяснений', 'blocked', 2, ?, "
                 "'capability')", (NOW - 20 * 60,))
    conn.execute("insert into task_events (task_id, kind, payload, created_at) "
                 "values ('t_young_bare', 'blocked', '{\"kind\": \"capability\"}', ?)",
                 (NOW - 10 * 60,))
    conn.commit()
    conn.close()

    blocks = kanban_daily.collect(db, now=NOW)["blocks"]
    assert [b["id"] for b in blocks] == ["t_young_bare"]
    assert "нет причины" in kanban_daily.format_report({"blocks": blocks}, now=MOMENT)


def test_комментарий_после_блокировки_считается_причиной(tmp_path):
    db = tmp_path / "comment.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute("insert into tasks (id, title, status, priority, created_at, block_kind) "
                 "values ('t_c', 'Блокировка с комментарием', 'blocked', 3, ?, 'capability')",
                 (NOW - 9 * HOUR,))
    conn.execute("insert into task_events (task_id, kind, payload, created_at) "
                 "values ('t_c', 'blocked', '{}', ?)", (NOW - 8 * HOUR,))
    conn.execute("insert into task_comments (task_id, author, body, created_at) "
                 "values ('t_c', 'worker-feature', 'Нужен доступ к серверу', ?)",
                 (NOW - 7 * HOUR,))
    conn.commit()
    conn.close()

    blocks = kanban_daily.collect(db, now=NOW)["blocks"]
    assert blocks[0]["reason"] == "Нужен доступ к серверу"
    assert "нет причины" not in kanban_daily.format_report({"blocks": blocks}, now=MOMENT)


def test_очередь_в_порядке_диспетчера(data):
    assert [c["id"] for c in data["queue"]] == ["t_q1", "t_q2", "t_q3"]
    text = kanban_daily.format_report(data, now=MOMENT)
    assert "1. t_q1 «Первая карта очереди» — P0, ждёт 20 ч" in text
    assert "2. t_q2 «Вторая карта очереди» — P1, ждёт 18 ч" in text
    assert "3. t_q3 «Третья карта очереди» — P2, ждёт 16 ч" in text


def test_карта_без_класса_стоит_ниже_всех(tmp_path):
    db = tmp_path / "order.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute("insert into tasks (id, title, status, priority, created_at) "
                 "values ('t_old', 'Старая без класса', 'ready', 0, ?)", (NOW - 50 * HOUR,))
    conn.execute("insert into tasks (id, title, status, priority, created_at) "
                 "values ('t_new', 'Новая P4', 'ready', 1, ?)", (NOW - HOUR,))
    conn.commit()
    conn.close()

    assert [c["id"] for c in kanban_daily.collect(db, now=NOW)["queue"]] == ["t_new", "t_old"]


def test_пустая_доска_говорит_что_требующих_нет(tmp_path):
    data = kanban_daily.collect(_empty_board(tmp_path), now=NOW)
    text = kanban_daily.format_report(data, now=MOMENT)

    assert "требующих человека нет" in text.lower()
    assert "Очередь: пусто" in text
    assert "Итог: сделано за сутки 0, в работе 0, в ревью 0" in text


def test_без_эмодзи_и_в_одну_страницу(data):
    text = kanban_daily.format_report(data, now=MOMENT)
    assert not EMOJI.search(text)
    assert len(text) <= kanban_daily.LIMIT
    assert "\r" not in text


def test_текст_без_латиницы_кроме_идентификаторов(data):
    """Каркас отчёта по-русски; латиница допустима в id карт, метке класса и в данных.

    Названия и вопросы — это данные доски («Ожидание ключа LLM», «Нужен LLM_API_KEY»),
    поэтому из проверки они вырезаются: проверяем формулировки самого отчёта.
    """
    text = kanban_daily.format_report(data, now=MOMENT)
    body = re.sub(r"«[^»]*»", "", text)
    body = "\n".join(line for line in body.splitlines()
                     if not line.startswith("  Вопрос:"))
    latin = re.sub(r"t_[a-z0-9_]+|teatrsvoi|P[0-4]", "", body)
    assert not re.search(r"[A-Za-z]", latin)


def test_длинные_названия_режутся_а_не_рвут_экран(tmp_path):
    db = tmp_path / "long.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute("insert into tasks (id, title, status, priority, created_at) "
                 "values ('t_long', ?, 'ready', 3, ?)", ("Очень длинное название " * 10,
                                                         NOW - HOUR))
    conn.commit()
    conn.close()

    text = kanban_daily.format_report(kanban_daily.collect(db, now=NOW), now=MOMENT)
    line = next(line for line in text.splitlines() if "t_long" in line)
    assert "…" in line
    assert len(line) < 120


def test_нет_доски_это_ошибка_сбора(tmp_path):
    with pytest.raises(kanban_daily.CollectionError, match="не найдена"):
        kanban_daily.find_db("teatrsvoi", hermes_home=str(tmp_path))


def test_явный_hermes_home_находит_доску(tmp_path):
    db = tmp_path / "kanban" / "boards" / "teatrsvoi" / "kanban.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"")
    assert kanban_daily.find_db("teatrsvoi", hermes_home=str(tmp_path)) == db


def test_отчёт_только_читает_доску(tmp_path):
    db = _board(tmp_path)
    before = _dump(db)
    kanban_daily.collect(db, now=NOW)
    kanban_daily.collect(db, now=NOW)
    assert _dump(db) == before


def _dump(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    rows = conn.execute("select id, status, priority, assignee, block_kind "
                        "from tasks order by id").fetchall()
    conn.close()
    return rows


def test_ошибка_сбора_уходит_в_чат_и_даёт_код_1(monkeypatch, local_db):
    sent: list[str] = []
    monkeypatch.setattr(kanban_daily, "deliver",
                        lambda text, dry_run=False: sent.append(text) or 0)
    monkeypatch.setattr(kanban_daily, "collect",
                        lambda *a, **k: (_ for _ in ()).throw(
                            kanban_daily.CollectionError("база доски не открылась")))

    assert kanban_daily.main([]) == 1
    assert len(sent) == 1 and "база доски не открылась" in sent[0]
    assert "Проверить: env -u PYTHONPATH .venv/bin/python" in sent[0]


def test_нет_доски_на_чистой_машине_даёт_ошибку_а_не_падает(tmp_path, capsys):
    """Домашней доски нет — сообщение об ошибке и код 1, без исключения."""
    code = kanban_daily.main(["--hermes-home", str(tmp_path), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Ежедневный отчёт не собран" in out
    assert "не найдена" in out


def test_ошибка_сбора_называет_причину_и_команду():
    text = kanban_daily.format_error("доска teatrsvoi не найдена", now=MOMENT)
    assert "Ежедневный отчёт не собран" in text
    assert "Причина: доска teatrsvoi не найдена" in text
    assert "kanban_daily.py --dry-run" in text
    assert not EMOJI.search(text)


def test_успешный_отчёт_уходит_в_чат(monkeypatch, data, local_db):
    sent: list[str] = []
    monkeypatch.setattr(kanban_daily, "deliver",
                        lambda text, dry_run=False: sent.append(text) or 0)
    monkeypatch.setattr(kanban_daily, "collect", lambda *a, **k: data)

    assert kanban_daily.main([]) == 0
    assert "Требует вас: 2" in sent[0]


def test_dry_run_печатает_тот_же_текст_что_уйдёт_в_чат(monkeypatch, data, local_db,
                                                        capsys):
    """Критерий приёмки: текст ручной проверки совпадает с отправляемым побайтно."""
    sent: list[str] = []

    def fake_send(argv=None):
        sent.append(list(argv or [])[-1])
        return 0

    monkeypatch.setattr(kanban_daily.notify_admin, "main", fake_send)
    monkeypatch.setattr(kanban_daily, "collect", lambda *a, **k: data)

    assert kanban_daily.main([]) == 0
    assert kanban_daily.main(["--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert printed == sent[0] + "\n"


def test_dry_run_не_обращается_к_telegram(monkeypatch, data, local_db):
    def boom(argv=None):
        raise AssertionError("в --dry-run Telegram не нужен")

    monkeypatch.setattr(kanban_daily.notify_admin, "main", boom)
    monkeypatch.setattr(kanban_daily, "collect", lambda *a, **k: data)
    assert kanban_daily.main(["--dry-run"]) == 0


def test_сквозной_прогон_на_доске_в_чистом_hermes_home(monkeypatch, board_home,
                                                        capsys):
    """main() находит доску по --hermes-home и печатает разделы: без ~/.hermes."""

    def boom(argv=None):
        raise AssertionError("в --dry-run Telegram не нужен")

    monkeypatch.setattr(kanban_daily.notify_admin, "main", boom)
    code = kanban_daily.main(["--hermes-home", str(board_home), "--dry-run"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Требует вас: 2" in out
    assert "Мёртвые: 2" in out
    assert "Очередь: 3 первые" in out
    assert "Итог: сделано за сутки" in out


def test_отправка_идёт_через_notify_admin(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(kanban_daily.notify_admin, "main",
                        lambda argv=None: calls.append(list(argv or [])) or 0)
    assert kanban_daily.deliver("текст") == 0
    assert calls == [["текст"]]


def test_вывод_стабилен_по_байтам(data):
    first = kanban_daily.format_report(data, now=MOMENT)
    second = kanban_daily.format_report(data, now=MOMENT)
    assert first == second
    assert ROOT.joinpath("scripts", "cron", "kanban_daily.py").is_file()
