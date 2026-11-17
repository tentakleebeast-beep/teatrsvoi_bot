"""Миграция 067: черновик опроса с анкетой оргсостава — 46 вопросов дословно (167 §6).

Проверяются критерии AC-1…AC-11 `docs/development/167-poll-interview-draft.md` §6:
на свежей базе без участников файл ничего не создаёт и не падает; с владельцем —
ровно один `poll` со `status='draft'` и 46 вопросов, тексты которых равны таблице §4
(ожидаемый список держится здесь, скопированный из документа; SQL-файл тест не читает);
шесть таблиц опросов и очередь приглашений пусты; повторный прогон не меняет ни строки,
а правка вопроса и переименование черновика его переживают; чужой опрос не тронут;
черновик виден в списке Mini App и открывается в конструкторе с 46 вопросами.

Обоснование: FR-G18, AC-1…AC-11, `docs/development/167-poll-interview-draft.md` §3, §4, §6.
"""

from __future__ import annotations

import pathlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from tests.conftest import ROOT, _base_dsn, _test_dsn
from tests.integration.polls_helpers import person, token

SQL = ROOT / "db" / "init" / "067_poll_interview_draft.sql"
TITLE = "Анкета оргсостава — черновик"
RENAMED = "Анкета оргсостава — моя"
ALIEN = "Чужой опрос"
TITLES = (TITLE, RENAMED, ALIEN)
INTRO = ("Как сейчас устроена работа в театре: 46 открытых вопросов, ответы своими словами. "
         "Отвечать можно частями — прогресс сохраняется.")
OWNER_KEY = "мамлютов-виктор"
MARKER = "067_poll_interview_draft"

#: Тексты 46 вопросов — дословно из `docs/development/167` §4 (таблица соответствия 161 §3–§6).
QUESTIONS = [
    "Расскажи, как выглядит твой обычный день в театре: с чего начинается, что делаешь первым?",
    "Как часто ты обращаешься к общим файлам театра и зачем? Что ищешь в них чаще всего?",
    "Нужно найти время для репетиции. Проведи меня по шагам: что смотришь сначала, что потом, "
    "кому пишешь или звонишь?",
    "Что из этого ты решаешь сам, а что кто-то подтверждает за тебя?",
    "Что чаще всего идёт не так в расписании и что ты делаешь, когда это случилось?",
    "Кто и когда обновляет таблицу занятости? В ней есть колонка «Решение» — она нужна или её "
    "можно убрать?",
    "Вспомни последнюю аренду: от заявки до оплаты — что и где ты записывал?",
    "Если событие в календаре изменил кто-то другой, как ты об этом узнаёшь сейчас и что "
    "делаешь? Кого из организаторов бот может считать хозяином такого события?",
    "Пять типов событий — аренда [А], спектакль [С], репетиция [Р], тренинг [Т], проект [П] — "
    "каким цветом пометить каждый изначально? Назови словами или номером палитры Google "
    "Календаря.",
    "Что бы ты хотел улучшить в организации театра?",
    "Какие есть идеи по развитию театра?",
    "Чем бы тебе могла быть полезна автоматизация с помощью ИИ?",
    "Проведи меня по бюджету месяца: с чего начинаешь и чем заканчиваешь?",
    "Что в листе трогать нельзя, иначе всё поедет, — а что мешает и хочется переделать?",
    "Расскажи на примере последней траты: где и как ты её записала? Статью пишешь словами или "
    "выбираешь из списка? Есть ли у тебя свой список статей?",
    "Как ты понимаешь, сколько осталось на конец месяца?",
    "Кто отмечает, что студиец заплатил взнос, и когда ты об этом узнаёшь? Что делать с тем, "
    "кто не заплатил: напоминать ему, показывать тебе список, ничего?",
    "Как ты считаешь оплату педагогам: из чего складывается сумма и когда ты её считаешь?",
    "Где вы публикуете и где лежат материалы для постов — афиши, тексты, фото?",
    "Как ты понимаешь, что пост готов к публикации?",
    "Что из твоей работы бот мог бы делать за тебя? О каких сроках тебе важно получать "
    "напоминание?",
    "Что бы ты хотел улучшить в организации театра?",
    "Какие есть идеи по развитию театра?",
    "Чем бы тебе могла быть полезна автоматизация с помощью ИИ?",
    "Расскажи, как проходит твой день с тренингом: что делаешь до него и после?",
    "Как ты показываешь, когда занят, а когда свободен?",
    "Когда ты открываешь расписание и что в нём ищешь?",
    "Что нужно подготовить к тренингу из файлов и музыки? Куда ты складываешь новое?",
    "Как ты узнаёшь, сколько тебе заплатят за месяц? Из чего складывается сумма?",
    "Что тебе нужно узнать из бота первым делом, когда ты его открываешь?",
    "О каких изменениях в расписании тебе важно узнавать сразу, а о каких — не надо?",
    "Что в твоей работе бот делать не должен ни при каких условиях?",
    "Что бы ты хотел улучшить в организации театра?",
    "Какие есть идеи по развитию театра?",
    "Чем бы тебе могла быть полезна автоматизация с помощью ИИ?",
    "Как ты узнаёшь, когда у тебя репетиция или тренинг?",
    "Где и как ты отмечаешь, что свободен или занят?",
    "Что тебе нужно узнать или найти перед репетицией или тренингом? Отличается ли подготовка "
    "к одному и к другому?",
    "Что чаще всего идёт не так с репетициями и тренингами и что ты делаешь, когда это "
    "случилось?",
    "Если репетиция или тренинг отменились, как ты об этом узнаёшь и что делаешь дальше?",
    "О чём тебе важно получать сообщение от театра, а о чём — не надо? Чем сообщение о тренинге "
    "отличается от сообщения о репетиции?",
    "Какие файлы театра тебе нужны чаще всего и зачем? К тренингу и к репетиции нужны разные "
    "файлы?",
    "Что тебе важно знать о репетиции или тренинге, кроме даты и времени? Для репетиции — какой "
    "спектакль и что за роль, для тренинга — какая дисциплина и кто ведёт?",
    "Что бы ты хотел улучшить в организации театра?",
    "Какие есть идеи по развитию театра?",
    "Чем бы тебе могла быть полезна автоматизация с помощью ИИ?",
]

POLL_TABLES = ("poll_option", "poll_audience", "poll_person", "poll_run", "poll_answer",
               "poll_viewer")


# ------------------------------------------------------------------ помощники
def _run_sql(dsn: str) -> list[str]:
    """Прогнать файл 067 целиком; вернуть notice-сообщения сервера."""
    sql = SQL.read_text(encoding="utf-8")
    notices: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.add_notice_handler(lambda diag: notices.append(diag.message_primary or ""))
        conn.execute(sql)
    return notices


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _scalar(sql: str, params: tuple = ()):
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _wipe() -> None:
    """Убрать следы прогона: опросы теста, метку и участников-авторов."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("delete from poll where title = any(%s)", (list(TITLES),))
        conn.execute("delete from migration_marker where key = %s", (MARKER,))
        conn.execute("delete from participant where person_key = any(%s)",
                     ([OWNER_KEY, "админ-запасной", "чужой-автор"],))


@pytest.fixture(autouse=True)
def _clean(test_db: str) -> Iterator[None]:
    _wipe()
    yield
    _wipe()


def _owner(chat_id: int = 787001, name: str = "Мамлютов Виктор",
           role: str = "admin", key: str = OWNER_KEY) -> int:
    """Действующий участник с устойчивым ключом владельца (или другой роли)."""
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s,%s,%s,%s,'active') "
            "on conflict (person_key) do update set status = 'active', role = excluded.role "
            "returning id", (chat_id, name, key, role)).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s,%s) "
                     "on conflict do nothing", (pid, role))
        conn.commit()
    return pid


def _draft() -> dict:
    rows = _rows("select * from poll where title = %s", (TITLE,))
    assert len(rows) == 1, f"черновиков с заголовком {TITLE!r}: {len(rows)}"
    return rows[0]


def _questions(poll_id: int | None = None) -> list[dict]:
    return _rows("select position, text, kind, required, free_text, max_choices "
                 "from poll_question where poll_id = %s order by position",
                 (poll_id if poll_id is not None else _draft()["id"],))


@contextmanager
def _fresh_database() -> Iterator[str]:
    """Пустая БД, накатанная `alembic upgrade head`: участников в ней нет (AC-1, AC-9)."""
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    name = f"svoi_test_067_{uuid.uuid4().hex[:12]}"
    base = _base_dsn()
    dsn = base.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    try:
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.attributes["url"] = dsn
        command.upgrade(cfg, "head")
        yield dsn
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname = %s and pid <> pg_backend_pid()", (name,))
            conn.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))


# ------------------------------------------------------------------ AC-1, AC-9
def test_без_владельца_ничего_не_создаётся() -> None:
    """AC-1: свежая база из db/init — файл не падает, poll пуст, метки нет."""
    with _fresh_database() as dsn:
        with psycopg.connect(dsn) as conn:
            assert conn.execute("select count(*) from poll").fetchone()[0] == 0
            assert conn.execute("select count(*) from participant where status = 'active'"
                                ).fetchone()[0] == 0
        notices = _run_sql(dsn)
        with psycopg.connect(dsn) as conn:
            assert conn.execute("select count(*) from poll").fetchone()[0] == 0
            assert conn.execute("select count(*) from migration_marker where key = %s",
                                (MARKER,)).fetchone()[0] == 0
        assert any("черновик не создан" in note for note in notices), notices


# ------------------------------------------------------------------ AC-2
def test_метаданные_черновика() -> None:
    """AC-2: один poll, draft, не анонимный, без срока, автор — владелец, title/intro из D3."""
    owner = _owner()
    _run_sql(_test_dsn())
    draft = _draft()
    assert draft["status"] == "draft"
    assert draft["anonymous"] is False
    assert draft["closes_at"] is None
    assert draft["created_by"] == owner
    assert draft["intro"] == INTRO
    assert draft["started_at"] is None and draft["closed_at"] is None


# ------------------------------------------------------------------ AC-3
def test_46_вопросов_дословно() -> None:
    """AC-3: 46 строк, position 1…46, тексты равны таблице 167 §4, все text/required."""
    _owner()
    _run_sql(_test_dsn())
    rows = _questions()
    assert len(rows) == 46
    assert [row["position"] for row in rows] == list(range(1, 47))
    assert [row["text"] for row in rows] == QUESTIONS
    for row in rows:
        assert row["kind"] == "text"
        assert row["required"] is True
        assert row["free_text"] is False
        assert row["max_choices"] is None


# ------------------------------------------------------------------ AC-4, AC-11
def test_никого_не_пригласили() -> None:
    """AC-4, AC-11: шесть таблиц опросов и очередь приглашений пусты."""
    _owner()
    _run_sql(_test_dsn())
    for table in POLL_TABLES:
        assert _scalar(f"select count(*) from {table}") == 0, table
    assert _scalar("select count(*) from notification where kind in ('poll','poll_reminder')") == 0


# ------------------------------------------------------------------ AC-5
def test_повторный_прогон_ничего_не_меняет() -> None:
    """AC-5: второй прогон — 0 новых и 0 изменённых строк, черновик один."""
    _owner()
    _run_sql(_test_dsn())
    before_poll = _draft()
    before = _questions()
    _run_sql(_test_dsn())
    assert _draft() == before_poll
    assert _questions() == before
    assert _scalar("select count(*) from poll where title = %s", (TITLE,)) == 1


# ------------------------------------------------------------------ AC-6
def test_правка_владельца_переживает_прогон() -> None:
    """AC-6: правка вопроса и переименование черновика не откатываются (метка в marker)."""
    _owner()
    _run_sql(_test_dsn())
    draft = _draft()
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("update poll_question set text = 'Правка владельца' "
                     "where poll_id = %s and position = 7", (draft["id"],))
        conn.execute("update poll set title = 'Анкета оргсостава — моя' where id = %s",
                     (draft["id"],))
    _run_sql(_test_dsn())
    rows = _questions(draft["id"])
    assert len(rows) == 46
    assert rows[6]["text"] == "Правка владельца"
    assert _scalar("select count(*) from poll where id = %s", (draft["id"],)) == 1
    assert _scalar("select title from poll where id = %s", (draft["id"],)) == \
        "Анкета оргсостава — моя"


# ------------------------------------------------------------------ AC-7
def test_запасное_правило_автора() -> None:
    """AC-7: владельца по person_key нет — автор первый действующий администратор."""
    admin = _owner(chat_id=787002, name="Админ Запасной", role="admin", key="админ-запасной")
    _run_sql(_test_dsn())
    assert _draft()["created_by"] == admin


# ------------------------------------------------------------------ AC-8
def test_существующие_опросы_не_тронуты() -> None:
    """AC-8: чужой опрос, его вопросы, прохождение и ответ не меняются."""
    _owner()
    with psycopg.connect(_test_dsn()) as conn:
        author = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (787003, 'Чужой Автор', 'чужой-автор', 'org', 'active') returning id"
        ).fetchone()[0]
        alien = conn.execute(
            "insert into poll (title, status, created_by) values (%s, 'running', %s) "
            "returning id", (ALIEN, author)).fetchone()[0]
        question = conn.execute(
            "insert into poll_question (poll_id, position, text, kind) "
            "values (%s, 1, 'Чужой вопрос?', 'text') returning id", (alien,)).fetchone()[0]
        run = conn.execute(
            "insert into poll_run (poll_id, participant_id, status) values (%s,%s,'done') "
            "returning id", (alien, author)).fetchone()[0]
        conn.execute("insert into poll_answer (run_id, question_id, text_value) "
                     "values (%s,%s,'Чужой ответ')", (run, question))
        conn.commit()
    before = (_rows("select * from poll where id = %s", (alien,)),
              _rows("select * from poll_question where poll_id = %s", (alien,)),
              _rows("select * from poll_run where poll_id = %s", (alien,)),
              _rows("select * from poll_answer where run_id = %s", (run,)))
    _run_sql(_test_dsn())
    after = (_rows("select * from poll where id = %s", (alien,)),
             _rows("select * from poll_question where poll_id = %s", (alien,)),
             _rows("select * from poll_run where poll_id = %s", (alien,)),
             _rows("select * from poll_answer where run_id = %s", (run,)))
    assert after == before
    assert _scalar("select count(*) from poll where id = %s", (alien,)) == 1
    assert _scalar("select count(*) from poll where title = %s", (TITLE,)) == 1


# ------------------------------------------------------------------ AC-10
def test_черновик_виден_в_списке_и_открывается(client) -> None:
    """AC-10: список Mini App показывает черновик с questions=46, конструктор отдаёт 46."""
    with psycopg.connect(_test_dsn()) as conn:
        owner = person(conn, 787004, "Мамлютов Виктор", "admin")
    _run_sql(_test_dsn())
    headers = token(client, 787004)
    listing = client.get("/webapp/polls/list", headers=headers)
    assert listing.status_code == 200, listing.text
    rows = [row for row in listing.json()["polls"] if row["title"] == TITLE]
    assert len(rows) == 1
    assert rows[0]["status"] == "draft"
    assert rows[0]["questions"] == 46
    assert rows[0]["invited"] == 0 and rows[0]["answered"] == 0
    data = client.get("/webapp/polls/edit/data", headers=headers,
                      params={"poll_id": rows[0]["id"]})
    assert data.status_code == 200, data.text
    body = data.json()
    assert body["can_edit"] is True
    assert len(body["questions"]) == 46
    assert _draft()["created_by"] == owner
