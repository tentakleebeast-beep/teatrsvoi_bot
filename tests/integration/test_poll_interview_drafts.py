"""Миграция 069: четыре черновика анкеты оргсостава вместо одного (V-098, вариант «б»).

Решение владельца 22.11.2026 (`docs/reports/74-questions.md`, V-098): одна рассылка —
один набор адресатов, поэтому анкета 161 версии 8 раскладывается на четыре черновика
12/12/11/11 (`docs/development/162-bot-polls.md` §9): организаторам, Марии, педагогам
и актёрам. Три общих вопроса есть в каждом блоке — иначе ответы ролей не с чем сравнить, —
но человек получает только свой блок, поэтому «четырмя подряд» их больше нет.

Проверяется: четыре `poll` со `status='draft'`, вопросы дословно из 161 §3–§6 и своей
роли (12/12/11/11), адресаты своей роли, ни одного `poll_run`/ответа/приглашения,
повторный прогон ничего не меняет, правка владельца переживает прогон, одиночный
черновик 067 удаляется только если он не тронут, а без владельца файл ничего не создаёт.

Обоснование: FR-G18, V-098, `docs/development/162-bot-polls.md` §9,
`docs/development/167-poll-interview-draft.md` §9, `docs/development/161` версия 8.
"""

from __future__ import annotations

import pathlib
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from tests.conftest import ROOT, _base_dsn, _test_dsn
from tests.integration.polls_helpers import person, token

SQL = ROOT / "db" / "init" / "069_poll_interview_drafts.sql"
SQL_067 = ROOT / "db" / "init" / "067_poll_interview_draft.sql"
MARKER = "069_poll_interview_drafts"
OLD_MARKER = "067_poll_interview_draft"
OWNER_KEY = "мамлютов-виктор"

TITLE_ORGS = "Анкета организаторам"
TITLE_MARIA = "Марии: деньги и соцсети"
TITLE_MASTERS = "Педагогам"
TITLE_ACTORS = "Актёрам"
TITLES = (TITLE_ORGS, TITLE_MARIA, TITLE_MASTERS, TITLE_ACTORS)
OLD_TITLE = "Анкета оргсостава — черновик"

ADMIN_KEY = "админ-запасной"
OTHER_KEYS = ("загаринская-мария", "тишина-даина", "наквасина-света", "трегубов-виктор")

#: Тексты блоков — дословно из `docs/development/167` §4 (таблица 161 §3–§6, версия 8).
QUESTIONS_ORGS = [
    "Расскажи, как выглядит твой обычный день в театре: с чего начинается, что делаешь первым?",
    "Как часто ты обращаешься к общим файлам театра и зачем? Что ищешь в них чаще всего?",
    "Нужно найти время для репетиции. Проведи меня по шагам: что смотришь сначала, что потом, кому пишешь "
    "или звонишь?",
    "Что из этого ты решаешь сам, а что кто-то подтверждает за тебя?",
    "Что чаще всего идёт не так в расписании и что ты делаешь, когда это случилось?",
    "Кто и когда обновляет таблицу занятости? В ней есть колонка «Решение» — она нужна или её можно "
    "убрать?",
    "Вспомни последнюю аренду: от заявки до оплаты — что и где ты записывал?",
    "Если событие в календаре изменил кто-то другой, как ты об этом узнаёшь сейчас и что делаешь? Кого из "
    "организаторов бот может считать хозяином такого события?",
    "Пять типов событий — аренда [А], спектакль [С], репетиция [Р], тренинг [Т], проект [П] — каким "
    "цветом пометить каждый изначально? Назови словами или номером палитры Google Календаря.",
    "Что бы ты хотел улучшить в организации театра?",
    "Какие есть идеи по развитию театра?",
    "Чем бы тебе могла быть полезна автоматизация с помощью ИИ?",
]
QUESTIONS_MARIA = [
    "Проведи меня по бюджету месяца: с чего начинаешь и чем заканчиваешь?",
    "Что в листе трогать нельзя, иначе всё поедет, — а что мешает и хочется переделать?",
    "Расскажи на примере последней траты: где и как ты её записала? Статью пишешь словами или выбираешь "
    "из списка? Есть ли у тебя свой список статей?",
    "Как ты понимаешь, сколько осталось на конец месяца?",
    "Кто отмечает, что студиец заплатил взнос, и когда ты об этом узнаёшь? Что делать с тем, кто не "
    "заплатил: напоминать ему, показывать тебе список, ничего?",
    "Как ты считаешь оплату педагогам: из чего складывается сумма и когда ты её считаешь?",
    "Где вы публикуете и где лежат материалы для постов — афиши, тексты, фото?",
    "Как ты понимаешь, что пост готов к публикации?",
    "Что из твоей работы бот мог бы делать за тебя? О каких сроках тебе важно получать напоминание?",
    "Что бы ты хотел улучшить в организации театра?",
    "Какие есть идеи по развитию театра?",
    "Чем бы тебе могла быть полезна автоматизация с помощью ИИ?",
]
QUESTIONS_MASTERS = [
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
]
QUESTIONS_ACTORS = [
    "Как ты узнаёшь, когда у тебя репетиция или тренинг?",
    "Где и как ты отмечаешь, что свободен или занят?",
    "Что тебе нужно узнать или найти перед репетицией или тренингом? Отличается ли подготовка к одному и "
    "к другому?",
    "Что чаще всего идёт не так с репетициями и тренингами и что ты делаешь, когда это случилось?",
    "Если репетиция или тренинг отменились, как ты об этом узнаёшь и что делаешь дальше?",
    "О чём тебе важно получать сообщение от театра, а о чём — не надо? Чем сообщение о тренинге "
    "отличается от сообщения о репетиции?",
    "Какие файлы театра тебе нужны чаще всего и зачем? К тренингу и к репетиции нужны разные файлы?",
    "Что тебе важно знать о репетиции или тренинге, кроме даты и времени? Для репетиции — какой спектакль "
    "и что за роль, для тренинга — какая дисциплина и кто ведёт?",
    "Что бы ты хотел улучшить в организации театра?",
    "Какие есть идеи по развитию театра?",
    "Чем бы тебе могла быть полезна автоматизация с помощью ИИ?",
]

BY_TITLE = {
    TITLE_ORGS: QUESTIONS_ORGS,
    TITLE_MARIA: QUESTIONS_MARIA,
    TITLE_MASTERS: QUESTIONS_MASTERS,
    TITLE_ACTORS: QUESTIONS_ACTORS,
}
#: Три общих вопроса 161 версии 8: есть в каждом блоке, чтобы ответы ролей сравнивались.
COMMON = QUESTIONS_ORGS[-3:]


# ------------------------------------------------------------------ помощники
def _run_sql(dsn: str, path: pathlib.Path = SQL) -> list[str]:
    """Прогнать SQL-файл целиком; вернуть notice-сообщения сервера."""
    text = path.read_text(encoding="utf-8")
    notices: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.add_notice_handler(lambda diag: notices.append(diag.message_primary or ""))
        conn.execute(text)
    return notices


def _rows(query: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def _scalar(query: str, params: tuple = ()):
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(query, params).fetchone()
    return row[0] if row else None


def _wipe() -> None:
    """Убрать следы прогона: черновики теста (067 и 069), метки, участников."""
    keys = [OWNER_KEY, ADMIN_KEY, *OTHER_KEYS]
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("delete from poll where title = any(%s)", (list(TITLES) + [OLD_TITLE],))
        # переименованные в тестах черновики ловятся по автору
        conn.execute("delete from poll where created_by in "
                     "(select id from participant where person_key = any(%s))", (keys,))
        conn.execute("delete from migration_marker where key = any(%s)", ([MARKER, OLD_MARKER],))
        conn.execute("delete from participant where person_key = any(%s)", (keys,))


@pytest.fixture(autouse=True)
def _clean(test_db: str) -> Iterator[None]:
    _wipe()
    yield
    _wipe()


def _person(chat_id: int, name: str, key: str, role: str = "troupe") -> int:
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


def _owner() -> int:
    return _person(787101, "Мамлютов Виктор", OWNER_KEY, "admin")


def _staff() -> None:
    """Штат из 161 §3–§6: организаторы, Мария, педагог, актёры."""
    _person(787102, "Загаринская Мария", "загаринская-мария", "org")
    _person(787103, "Тишина Даина", "тишина-даина", "org")
    _person(787104, "Наквасина Света", "наквасина-света", "master")
    _person(787105, "Трегубов Виктор", "трегубов-виктор", "troupe")


def _drafts() -> dict[str, dict]:
    rows = _rows("select * from poll where title = any(%s) order by title", (list(TITLES),))
    return {row["title"]: row for row in rows}


def _questions(poll_id: int) -> list[dict]:
    return _rows("select position, text, kind, required, free_text, max_choices "
                 "from poll_question where poll_id = %s order by position", (poll_id,))


@contextmanager
def _fresh_database() -> Iterator[str]:
    """Пустая БД, накатанная `alembic upgrade head`: действующих участников нет."""
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    name = f"svoi_test_069_{uuid.uuid4().hex[:12]}"
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


# ------------------------------------------------------------------ содержание
def test_четыре_черновика_12_12_11_11() -> None:
    """4 черновика, вопросы дословно 161 §3–§6 своей роли: 12/12/11/11."""
    _owner()
    _run_sql(_test_dsn())
    drafts = _drafts()
    assert sorted(drafts) == sorted(TITLES), sorted(drafts)
    for title, expected in BY_TITLE.items():
        rows = _questions(drafts[title]["id"])
        assert len(rows) == len(expected), title
        assert [row["position"] for row in rows] == list(range(1, len(expected) + 1)), title
        assert [row["text"] for row in rows] == expected, title
        for row in rows:
            assert row["kind"] == "text", (title, row["position"])
            assert row["required"] is True
            assert row["free_text"] is False
            assert row["max_choices"] is None
    assert [len(_questions(drafts[t]["id"])) for t in TITLES] == [12, 12, 11, 11]
    assert _scalar("select count(*) from poll_option") == 0


def test_вопросы_не_дублируются_между_блоками() -> None:
    """Внутри блока повторов нет; в чужие блоки вопросы не переехали.

    Три общих вопроса — единственное, что стоит в каждом блоке: это решение 161 версии 8
    (сравнивать ответы ролей), и V-098 его не отменял. Всё остальное уникально: 37 разных
    текстов на 46 строк.
    """
    _owner()
    _run_sql(_test_dsn())
    drafts = _drafts()
    for title in TITLES:
        texts = [row["text"] for row in _questions(drafts[title]["id"])]
        assert len(texts) == len(set(texts)), f"повтор внутри {title}"
    unique_texts = {row["text"] for title in TITLES for row in _questions(drafts[title]["id"])}
    assert len(unique_texts) == 37, len(unique_texts)

    own = {
        TITLE_ORGS: set(QUESTIONS_ORGS) - set(COMMON),
        TITLE_MARIA: set(QUESTIONS_MARIA) - set(COMMON),
        TITLE_MASTERS: set(QUESTIONS_MASTERS) - set(COMMON),
        TITLE_ACTORS: set(QUESTIONS_ACTORS) - set(COMMON),
    }
    for title, texts in own.items():
        alien = {other: (texts & (own[other])) for other in own if other != title}
        assert not any(alien.values()), (title, alien)


def test_метаданные_черновиков() -> None:
    """Все четыре — `draft`, не анонимные, без срока, автор — владелец."""
    owner = _owner()
    _run_sql(_test_dsn())
    for title, draft in _drafts().items():
        assert draft["status"] == "draft", title
        assert draft["anonymous"] is False, title
        assert draft["closes_at"] is None, title
        assert draft["created_by"] == owner, title
        assert draft["started_at"] is None and draft["closed_at"] is None, title
        assert draft["intro"] and "прогресс сохраняется" in draft["intro"], title


def test_запасное_правило_автора() -> None:
    """Владельца по `person_key` нет — автор первый действующий администратор."""
    admin = _person(787106, "Админ Запасной", ADMIN_KEY, "admin")
    _run_sql(_test_dsn())
    assert sorted(_drafts()) == sorted(TITLES)
    for draft in _drafts().values():
        assert draft["created_by"] == admin


# ------------------------------------------------------------------ адресаты
def test_адресаты_своей_роли() -> None:
    """Организаторы и Мария — галочками по `person_key`, педагоги и актёры — правилом роли."""
    _owner()
    _staff()
    _run_sql(_test_dsn())
    drafts = _drafts()

    def people(title: str) -> list[str]:
        return [row["person_key"] for row in _rows(
            "select p.person_key from poll_person pp join participant p on p.id = pp.participant_id "
            "where pp.poll_id = %s and pp.mode = 'include' order by p.person_key",
            (drafts[title]["id"],))]

    assert people(TITLE_ORGS) == sorted([OWNER_KEY, "загаринская-мария", "тишина-даина"])
    assert people(TITLE_MARIA) == ["загаринская-мария"]
    assert people(TITLE_MASTERS) == [] and people(TITLE_ACTORS) == []

    rules = {title: [row["role"] for row in _rows(
        "select role from poll_audience where poll_id = %s", (drafts[title]["id"],))]
        for title in (TITLE_MASTERS, TITLE_ACTORS)}
    assert rules == {TITLE_MASTERS: ["master"], TITLE_ACTORS: ["troupe"]}
    # у «Анкеты организаторам» и блока Марии правил нет: набор задан галочками
    assert _scalar("select count(*) from poll_audience where poll_id = any(%s)",
                   ([drafts[TITLE_ORGS]["id"], drafts[TITLE_MARIA]["id"]],)) == 0


def test_никого_не_пригласили() -> None:
    """Ни `poll_run`, ни ответов, ни приглашений: отправка — действие владельца."""
    _owner()
    _staff()
    _run_sql(_test_dsn())
    for table in ("poll_run", "poll_answer"):
        assert _scalar(f"select count(*) from {table}") == 0, table
    assert _scalar("select count(*) from notification where kind in ('poll','poll_reminder')") == 0


# ------------------------------------------------------------------ идемпотентность
def test_повторный_прогон_ничего_не_меняет() -> None:
    """Второй прогон: те же четыре черновика, те же вопросы, адресаты и права."""
    _owner()
    _staff()
    _run_sql(_test_dsn())
    before = (_rows("select * from poll where title = any(%s) order by title", (list(TITLES),)),
              _rows("select q.* from poll_question q join poll p on p.id = q.poll_id "
                    "where p.title = any(%s) order by q.poll_id, q.position", (list(TITLES),)),
              _rows("select pp.* from poll_person pp join poll p on p.id = pp.poll_id "
                    "where p.title = any(%s) order by 1,2", (list(TITLES),)),
              _rows("select a.* from poll_audience a join poll p on p.id = a.poll_id "
                    "where p.title = any(%s) order by 1", (list(TITLES),)),
              _rows("select v.* from poll_viewer v join poll p on p.id = v.poll_id "
                    "where p.title = any(%s) order by 1,2", (list(TITLES),)))
    _run_sql(_test_dsn())
    after = (_rows("select * from poll where title = any(%s) order by title", (list(TITLES),)),
             _rows("select q.* from poll_question q join poll p on p.id = q.poll_id "
                   "where p.title = any(%s) order by q.poll_id, q.position", (list(TITLES),)),
             _rows("select pp.* from poll_person pp join poll p on p.id = pp.poll_id "
                   "where p.title = any(%s) order by 1,2", (list(TITLES),)),
             _rows("select a.* from poll_audience a join poll p on p.id = a.poll_id "
                   "where p.title = any(%s) order by 1", (list(TITLES),)),
             _rows("select v.* from poll_viewer v join poll p on p.id = v.poll_id "
                   "where p.title = any(%s) order by 1,2", (list(TITLES),)))
    assert after == before


def test_правка_владельца_переживает_прогон() -> None:
    """Правка вопроса и переименование черновика не откатываются: `update` в файле нет."""
    _owner()
    _run_sql(_test_dsn())
    draft = _drafts()[TITLE_MASTERS]
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("update poll_question set text = 'Правка владельца' "
                     "where poll_id = %s and position = 3", (draft["id"],))
        conn.execute("update poll set title = 'Педагогам — моя' where id = %s", (draft["id"],))
    _run_sql(_test_dsn())
    rows = _questions(draft["id"])
    assert len(rows) == 11
    assert rows[2]["text"] == "Правка владельца"
    assert _scalar("select title from poll where id = %s", (draft["id"],)) == "Педагогам — моя"
    # остальные три черновика не задвоились
    assert _scalar("select count(*) from poll where title = any(%s)",
                   ([TITLE_ORGS, TITLE_MARIA, TITLE_ACTORS],)) == 3


# ------------------------------------------------------------------ судьба 067
def test_одиночный_черновик_067_удалён() -> None:
    """Нетронутый черновик 067 заменяется четырьмя: остаётся ровно четыре черновика."""
    _owner()
    _run_sql(_test_dsn(), SQL_067)
    assert _scalar("select count(*) from poll where title = %s", (OLD_TITLE,)) == 1
    _run_sql(_test_dsn())
    assert _scalar("select count(*) from poll where title = %s", (OLD_TITLE,)) == 0
    assert sorted(_drafts()) == sorted(TITLES)
    assert _scalar("select count(*) from poll") == 4


def test_черновик_067_с_правкой_не_удалён() -> None:
    """Правку владельца миграция не откатывает: черновик остаётся, в notice сказано почему."""
    _owner()
    _run_sql(_test_dsn(), SQL_067)
    old = _rows("select id from poll where title = %s", (OLD_TITLE,))[0]["id"]
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("update poll_question set text = 'Правка владельца' "
                     "where poll_id = %s and position = 5", (old,))
    notices = _run_sql(_test_dsn())
    assert _scalar("select count(*) from poll where id = %s", (old,)) == 1
    assert _scalar("select text from poll_question where poll_id = %s and position = 5",
                   (old,)) == "Правка владельца"
    assert sorted(_drafts()) == sorted(TITLES)
    assert any("одиночный черновик 067 не удалён" in note for note in notices), notices


def test_без_владельца_ничего_не_создаётся() -> None:
    """Свежая база из `db/init`: файл не падает, опросов нет, метки нет."""
    with _fresh_database() as dsn:
        with psycopg.connect(dsn) as conn:
            assert conn.execute("select count(*) from poll").fetchone()[0] == 0
            assert conn.execute(
                "select count(*) from participant where status = 'active'").fetchone()[0] == 0
        notices = _run_sql(dsn)
        with psycopg.connect(dsn) as conn:
            assert conn.execute("select count(*) from poll").fetchone()[0] == 0
            assert conn.execute("select count(*) from migration_marker where key = %s",
                                (MARKER,)).fetchone()[0] == 0
        assert any("черновики не созданы" in note for note in notices), notices


# ------------------------------------------------------------------ Mini App и данные
def test_четыре_черновика_видны_в_списке(client) -> None:
    """Список Mini App: четыре строки «черновик» с числами вопросов 12/12/11/11."""
    with psycopg.connect(_test_dsn()) as conn:
        person(conn, 787107, "Мамлютов Виктор", "admin")
    _run_sql(_test_dsn())
    headers = token(client, 787107)
    listing = client.get("/webapp/polls/list", headers=headers)
    assert listing.status_code == 200, listing.text
    rows = {row["title"]: row for row in listing.json()["polls"] if row["title"] in TITLES}
    assert sorted(rows) == sorted(TITLES)
    assert [rows[title]["questions"] for title in TITLES] == [12, 12, 11, 11]
    for row in rows.values():
        assert row["status"] == "draft"
        assert row["invited"] == 0 and row["answered"] == 0


def test_в_файле_нет_личных_данных_и_литералов_id() -> None:
    """Ни `tg_chat_id` числом, ни `created_by` литералом: автор ищется по `person_key`."""
    text = SQL.read_text(encoding="utf-8")
    assert not re.search(r"tg_chat_id\s*=\s*\d", text)
    assert not re.search(r"created_by[^,)]*\d", text)
    assert OWNER_KEY in text
