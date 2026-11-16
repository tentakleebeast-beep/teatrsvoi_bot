"""Конструктор опроса: пять типов вопросов, коды вариантов, сохранение без потерь.

AC-2 (`docs/development/162-bot-polls.md` §14.7): опрос с пятью типами вопросов
сохраняется и читается без потерь, `position` и `code` стабильны, варианты не
теряются, `free_text` у `yesno`/`number`/`text` запрещён.

Обоснование: FR-G18, AC-2, `docs/development/162-bot-polls.md` §14.1, §14.2.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app import poll_store


def _question(**overrides):
    item = {"position": 1, "text": "Вопрос", "kind": "single", "required": True,
            "free_text": False, "max_choices": None,
            "options": [{"label": "Раз"}, {"label": "Два"}]}
    item.update(overrides)
    return item


def test_пять_типов_вопросов_сохраняются_и_читаются_без_потерь():
    """Все пять типов проходят проверку; позиции и коды не меняются при повторной проверке."""
    raw = [
        _question(position=1, kind="single"),
        _question(position=2, kind="multi", options=[{"label": "A"}, {"label": "B"}]),
        _question(position=3, kind="yesno", options=[]),
        _question(position=4, kind="number", options=[], free_text=False),
        _question(position=5, kind="text", options=[], required=False),
    ]
    first = poll_store.validate_questions(raw)
    second = poll_store.validate_questions(raw)
    assert [item["kind"] for item in first] == ["single", "multi", "yesno", "number", "text"]
    assert [item["position"] for item in first] == [1, 2, 3, 4, 5]
    assert first == second
    assert [option["code"] for option in first[0]["options"]] == ["a1", "a2"]
    assert [option["label"] for option in first[1]["options"]] == ["A", "B"]
    assert first[2]["options"] == [] and first[3]["options"] == []


def test_свободный_ответ_запрещён_у_типов_без_списка():
    """`check (kind in ('single','multi') or free_text = false)` держит и код, и схема."""
    for kind in ("yesno", "number", "text"):
        with pytest.raises(poll_store.DraftError) as error:
            poll_store.validate_questions([_question(kind=kind, options=[], free_text=True)])
        assert error.value.code == "bad_free_text"


def test_неизвестный_тип_и_пустой_вопрос_дают_код_контракта():
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.validate_questions([_question(kind="slider")])
    assert error.value.code == "bad_kind"
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.validate_questions([_question(text="   ")])
    assert error.value.code == "empty_question"
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.validate_questions([])
    assert error.value.code == "no_questions"


def test_список_без_вариантов_не_сохраняется():
    """«Список без списка» — частая поломка конструкторов; отказ, а не пустой вопрос."""
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.validate_questions([_question(options=[])])
    assert error.value.code == "no_options"


def test_коды_вариантов_двухбайтовые_и_переходят_на_следующую_букву():
    codes = poll_store.next_codes(11)
    assert codes[:9] == [f"a{index}" for index in range(1, 10)]
    assert codes[9:] == ["b1", "b2"]
    assert all(len(code) == 2 for code in codes)


def test_больше_двухсот_тридцати_четырёх_вариантов_отказ():
    with pytest.raises(poll_store.DraftError) as error:
        poll_store.next_codes(poll_store.MAX_OPTIONS + 1)
    assert error.value.code == "too_many_options"


def test_число_принимается_с_запятой_и_точкой():
    """§14.4 п. 4: дробное число приходит и с запятой, и с точкой."""
    assert poll_store.parse_number("4") == 4
    assert float(poll_store.parse_number("4,5")) == 4.5
    assert float(poll_store.parse_number("4.5")) == 4.5
    assert poll_store.parse_number("четыре") is None
    assert poll_store.parse_number("") is None
    assert poll_store.parse_number("NaN") is None


# --- «Мои опросы»: одно чтение и подписи (E8-D4, docs/development/164 §12.2) ----

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _run(**overrides) -> dict:
    item = {"run_id": 12, "poll_id": 1, "title": "Что по опросам?", "status": "invited",
            "position": 1, "total": 3, "poll_status": "running",
            "closes_at": None, "closed_at": None, "finished_at": None}
    item.update(overrides)
    return item


class _FakeConn:
    """Соединение-пустышка: проверяем текст запроса, а не Postgres (он в integration)."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.params = params
        return self

    def fetchall(self):
        return self.rows


def test_чтение_одно_и_без_фильтра_по_статусу():
    """Один `select` на три потребителя: фильтруют они, а не запрос (D5)."""
    conn = _FakeConn(rows=[_run(), _run(run_id=9, status="done")])
    runs = poll_store.my_runs(conn, 42)
    assert [run["run_id"] for run in runs] == [12, 9]
    assert "participant_id = %s" in conn.sql and conn.params == (42,)
    assert "order by p.created_at desc, r.id" in conn.sql
    # Условий по статусу прохождения и опроса в запросе нет — иначе чат и
    # приложение разошлись бы в том, какой опрос считать открытым.
    assert "r.status in" not in conn.sql and "p.status =" not in conn.sql


def test_группы_раскладывают_прохождения_по_одной_строке():
    runs = [
        _run(run_id=1),
        _run(run_id=2, status="in_progress", position=3, total=11),
        _run(run_id=3, status="done", position=11, total=11,
             finished_at=datetime(2026, 11, 14, 18, 0, tzinfo=UTC)),
        _run(run_id=4, poll_status="closed", closed_at=datetime(2026, 11, 12, 9, 0, tzinfo=UTC)),
        _run(run_id=5, closes_at=datetime(2026, 11, 10, 9, 0, tzinfo=UTC)),
        _run(run_id=6, status="declined"),
        # Пройденный опрос, закрытый потом: для человека он остаётся пройденным.
        _run(run_id=7, status="done", poll_status="closed"),
    ]
    grouped = poll_store.groups(runs, NOW)
    assert [run["run_id"] for run in grouped["open"]] == [1, 2]
    assert [run["run_id"] for run in grouped["done"]] == [3, 7]
    assert [run["run_id"] for run in grouped["closed"]] == [4, 5, 6]


def test_подпись_открытого_различает_не_начат_и_продолжить():
    """`invited` и `in_progress` различаются словами, а не цветом (AC-2)."""
    assert poll_store.run_meta(_run(total=3), NOW) == "3 вопроса · не начат"
    assert poll_store.run_meta(_run(total=1), NOW) == "1 вопрос · не начат"
    assert poll_store.run_meta(_run(total=5), NOW) == "5 вопросов · не начат"
    assert poll_store.run_meta(_run(status="in_progress", position=3, total=11),
                               NOW) == "вопрос 3 из 11 · продолжить"


def test_подпись_пройденного_и_закрытого_с_датой():
    done = _run(status="done", position=11, total=11,
                finished_at=datetime(2026, 11, 14, 18, 0, tzinfo=UTC))
    assert poll_store.run_meta(done, NOW) == "пройден 14 ноября · 11 из 11"
    closed = _run(poll_status="closed", closed_at=datetime(2026, 11, 12, 9, 0, tzinfo=UTC))
    assert poll_store.run_meta(closed, NOW) == "опрос закрыт 12 ноября"
    # Закрыт по сроку: дата берётся из срока, отдельного `closed_at` нет.
    expired = _run(closes_at=datetime(2026, 11, 12, 9, 0, tzinfo=UTC))
    assert poll_store.run_meta(expired, NOW) == "опрос закрыт 12 ноября"
    # Резерв `declined` — без даты: её у такого прохождения нет.
    assert poll_store.run_meta(_run(status="declined"), NOW) == "опрос закрыт"


def test_подпись_входа_на_главной_без_нулевых_частей():
    """«2 ждут ответа · 3 пройдено»; нулевая часть не печатается (AC-1, AC-4)."""
    assert poll_store.entry_meta({"open": 2, "done": 3}) == "2 ждут ответа · 3 пройдено"
    assert poll_store.entry_meta({"open": 1, "done": 0}) == "1 ждёт ответа"
    assert poll_store.entry_meta({"open": 5, "done": 1}) == "5 ждут ответа · 1 пройдено"
    assert poll_store.entry_meta({"open": 0, "done": 1}) == "1 пройдено"
    assert poll_store.entry_meta({"open": 0, "done": 0}) == ""


def test_отмена_приглашения_трогает_только_неотправленное():
    """V-096: `scheduled`/`failed` без `sent_at` — в `cancelled`, остальное не трогаем."""
    conn = _FakeConn()
    poll_store.cancel_invites(conn, 12, 42)
    assert "update notification set status = 'cancelled'" in conn.sql
    assert "sent_at is null" in conn.sql
    assert "status in ('scheduled', 'failed')" in conn.sql
    assert "payload->>'run_id' = %s" in conn.sql
    assert conn.params == (42, "12")
