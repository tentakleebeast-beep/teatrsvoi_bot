"""«Что умею» — одна строка на право, ответ не может разойтись с матрицей прав.

Ответ собирается не из меню, а из таблицы «право → строка» (`texts.CAP_LINES`) с
фильтром по правам человека (docs/development/188 §2–§3, решения V-121 и V-122 от
22.11.2026). Отсюда четыре обещания, каждое проверяется отдельно:

  * строку видит только тот, у кого есть её право — чужих строк в ответе нет;
  * у каждого права роли есть строка (или запись в `CAP_LINES_SKIP` с причиной):
    новое право в матрице не появится и не пропадёт молча;
  * в ответе нет механики — «нажатие», «окно», «панель», «переход» и связки
    «пункт — пояснение», из которой и брался шум;
  * лимиты длины и порядок строк не поехали.

Обоснование: FR-A6 (права решает сервер, оболочка только показывает),
FR-A2 (код приглашения), docs/development/188 §3–§5, карты t_cfebf3da, t_a487695a.
"""

from __future__ import annotations

import pathlib
import re

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INIT_DIR = ROOT / "db" / "init"

#: Права ролей на свежей схеме после миграций 008…071. Списки повторены намеренно:
#: тест должен падать, если состав прав «уточнят» молча.
ADMIN = {
    "admin.panel", "audit.view", "budget.import", "budget.read", "budget.read.self",
    "budget.write", "calendar.write", "feedback.send", "feedback.triage",
    "files.access", "howto.read", "notes.write", "notify.send",
    "participants.contact", "participants.manage", "participants.view",
    "poll.manage", "poll.results.all", "registration.code.issue",
    "schedule.availability.self", "schedule.read.all", "schedule.slots",
    "schedule.write", "system.health",
}
ORG = {
    "budget.import", "budget.read", "budget.read.self", "budget.write", "calendar.write",
    "feedback.send", "files.access", "notes.write", "participants.contact",
    "participants.view", "poll.manage", "schedule.availability.self",
    "schedule.read.all", "schedule.slots", "schedule.write",
}
MASTER = {
    "files.access", "notes.write", "participants.view", "schedule.availability.self",
    "schedule.read.all", "schedule.slots", "schedule.write",
}
TROUPE = {"budget.read.self", "feedback.send", "notes.write",
          "schedule.availability.self"}

ROLE_CAPS = {"admin": ADMIN, "org": ORG, "master": MASTER, "troupe": TROUPE}

#: Право, которого нет ни у одной роли после 070 (`registration.approve` снят с
#: всех ролей, заявок в боте нет — docs/development/182 §4.1). Строка остаётся в
#: таблице как след матрицы, но человеку не показывается ни в одной роли — это и
#: проверяет тест «право без роли».
ORPHAN = "registration.approve"

#: Эталон порядка (docs/development/188 §3): от частого к редкому.
ORDER = [
    "schedule.availability.self",
    "schedule.read.all",
    "budget.read.self",
    "schedule.slots",
    "budget.write",
    "budget.read",
    "schedule.write",
    "calendar.write",
    "notes.write",
    "files.access",
    "participants.view",
    "poll.manage",
    "poll.results.all",
    "budget.import",
    "feedback.send",
    "",
    "participants.manage",
    "registration.code.issue",
    "registration.approve",
    "feedback.triage",
    "audit.view",
    "notify.send",
    "participants.contact",
    "howto.read",
]

ADMIN_TEXT = """Что умею:
· Отметить, когда свободен: дни и часы
· Показать, что идёт на неделе
· Посчитать, сколько внёс и сколько осталось
· Найти час, когда все свободны
· Записать расход или доход
· Показать бюджет студии за месяц
· Поставить репетицию или показ в расписание
· Отменить или перенести событие в календаре
· Записать заметку: себе или всем
· Найти запись, фонограмму или текст
· Показать, кто в студии и с какой ролью
· Собрать опрос и увидеть ответы
· Увидеть ответы всех опросов
· Сверить бюджет с таблицей на Диске
· Оставить замечание или пожелание
· Заполнить анкету: контакты и дорога
· Выдать роль или право, убрать в архив
· Выпустить приглашение для нового
· Закрыть обращение или передать
· Посмотреть, кто что делал
· Разослать сообщение студии
· Увидеть телефон и контакты человека
· Показать инструкцию по роли

Можно и словами: «что у меня завтра»"""

ORG_TEXT = """Что умею:
· Отметить, когда свободен: дни и часы
· Показать, что идёт на неделе
· Посчитать, сколько внёс и сколько осталось
· Найти час, когда все свободны
· Записать расход или доход
· Показать бюджет студии за месяц
· Поставить репетицию или показ в расписание
· Отменить или перенести событие в календаре
· Записать заметку: себе или всем
· Найти запись, фонограмму или текст
· Показать, кто в студии и с какой ролью
· Собрать опрос и увидеть ответы
· Сверить бюджет с таблицей на Диске
· Оставить замечание или пожелание
· Заполнить анкету: контакты и дорога
· Увидеть телефон и контакты человека

Можно и словами: «что у меня завтра»"""

MASTER_TEXT = """Что умею:
· Отметить, когда свободен: дни и часы
· Показать, что идёт на неделе
· Найти час, когда все свободны
· Поставить репетицию или показ в расписание
· Записать заметку: себе или всем
· Найти запись, фонограмму или текст
· Показать, кто в студии и с какой ролью
· Заполнить анкету: контакты и дорога

Можно и словами: «что у меня завтра»"""

TROUPE_TEXT = """Что умею:
· Отметить, когда свободен: дни и часы
· Посчитать, сколько внёс и сколько осталось
· Записать заметку: себе или всем
· Оставить замечание или пожелание
· Заполнить анкету: контакты и дорога

Можно и словами: «что у меня завтра»"""

ROLE_TEXTS = {"admin": ADMIN_TEXT, "org": ORG_TEXT, "master": MASTER_TEXT,
              "troupe": TROUPE_TEXT}

#: Слова механики и переходов: их в ответе не бывает (188 §1.2, §5.4 п. 4).
MECHANICS = ("агент", "переход", "инструмент", "нажатие", "окно", "панель")


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


def _catalog_codes() -> set[str]:
    """Коды справочника `rbac_capability` из db/init: блоки `insert into …` до `;`."""
    codes: set[str] = set()
    for path in sorted(INIT_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
                r"insert\s+into\s+rbac_capability\b.*?;", text, re.S | re.I):
            codes.update(re.findall(r"'([a-z][a-z0-9_]*\.[a-z0-9_.]+)'", match.group(0)))
    return codes


def test_каждая_строка_имеет_право(gw) -> None:
    """Право каждой строки есть в справочнике; пустое право — ровно у анкеты."""
    catalog = _catalog_codes()
    codes = [code for code, _ in gw.CAP_LINES]
    unknown = sorted({code for code in codes if code} - catalog)
    assert unknown == [], f"в CAP_LINES права, которых нет в справочнике: {unknown}"
    assert codes.count("") == 1, "пустое право разрешено ровно одной строке — анкете"


def test_все_права_разобраны(gw) -> None:
    """Права четырёх ролей = строки CAP_LINES плюс исключения без потерь.

    `registration.approve` после 070 не выдан ни одной роли (заявок нет), поэтому
    он единственное право таблицы без роли; всё остальное обязано быть в строках.
    """
    line_codes = {code for code, _ in gw.CAP_LINES if code}
    union = set().union(*ROLE_CAPS.values())
    assert union == (line_codes | gw.CAP_LINES_SKIP) - {ORPHAN}, (
        "новое право без строки или строка без права: "
        f"лишние права {sorted(union - (line_codes | gw.CAP_LINES_SKIP))}, "
        f"лишние строки {sorted((line_codes | gw.CAP_LINES_SKIP) - union - {ORPHAN})}")
    assert ORPHAN in line_codes
    assert all(ORPHAN not in caps for caps in ROLE_CAPS.values()), (
        "у роли снова есть registration.approve: заявок в боте нет (070)")


def test_ни_одной_чужой_строки(gw) -> None:
    """Каждая показанная строка — право роли; четыре ответа совпадают дословно."""
    for role, caps in ROLE_CAPS.items():
        text = gw.help_text(sorted(caps))
        assert text == ROLE_TEXTS[role], f"ответ роли {role} разошёлся с эталоном"
        shown = [line[2:] for line in text.splitlines() if line.startswith("· ")]
        by_line = {line: code for code, line in gw.CAP_LINES}
        for line in shown:
            code = by_line[line]
            assert code == "" or code in caps, f"роль {role} видит чужое право {code}"


def test_нет_механики(gw) -> None:
    """В ответе любой роли нет слов о механике и связки «пункт — пояснение»."""
    for caps in (*ROLE_CAPS.values(), {"*"}):
        text = gw.help_text(sorted(caps)).lower()
        for word in MECHANICS:
            assert word not in text, f"в ответе механика: «{word}»"
        assert " — " not in text, "вернулась связка «пункт — пояснение»"


def test_лимиты(gw) -> None:
    """Строка — не длиннее 70 знаков, ответ — не длиннее 1200."""
    long_lines = [(code, line) for code, line in gw.CAP_LINES if len(line) > 70]
    assert long_lines == [], f"строки длиннее 70 знаков: {long_lines}"
    for caps in (*ROLE_CAPS.values(), {"*"}):
        text = gw.help_text(sorted(caps))
        assert len(text) <= 1200, f"ответ {len(text)} знаков при пределе 1200"


def test_порядок_не_поехал(gw) -> None:
    """Последовательность прав равна эталону §3, первая строка — занятость."""
    assert [code for code, _ in gw.CAP_LINES] == ORDER
    assert gw.CAP_LINES[0][0] == "schedule.availability.self"


def test_перехват_вопроса_до_модели(gw) -> None:
    """V-122: «что умеешь» и перефразы — тот же ответ, и фраза не идёт в модель."""
    import re as _re

    phrases = [
        "что ты умеешь",
        "Что ты умеешь?",
        "что умеешь",
        "что ты можешь",
        "что можешь",
        "ты что вообще можешь",
        "что ты вообще умеешь",
        "какие у тебя возможности",
        "на что ты способен",
        # Обратный порядок слов и разговорное «что-нибудь» — те же вопросы V-122.
        # Русский порядок свободный: «что бот умеет» и «что может бот» уходили в
        # модель, пока правило требовало только «что умеет бот» (возврат ревью
        # 22.11.2026, docs/development/69 L-051).
        "что бот умеет",
        "Что бот умеет?",
        "что может бот",
        "что бот вообще может",
        "что умеет бот",
        "что он может",
        "что может ассистент",
        "умеешь что-нибудь?",
        "можешь что-нибудь",
        "умеешь что нибудь",
        "можешь что-нибудь делать?",
    ]
    for phrase in phrases:
        assert _re.search(gw.CAPS_Q, phrase.lower()), f"перефраз не перехвачен: {phrase}"
    # Вопрос не должен попадать и в ветки данных: иначе он станет тратой или задачей.
    for phrase in phrases:
        assert gw.data_input_kind(phrase) is None, f"«{phrase}» разобралось как данные"
    assert not gw.is_task("что ты умеешь")


def test_перехват_не_забирает_чужие_фразы(gw) -> None:
    """Перефразы — часть критерия, но перехват не должен съедать соседние вопросы.

    «Что может быть / случиться» — вопрос о событиях, а не об умениях бота;
    «можешь что-нибудь по бюджету» — просьба о действии, её нельзя подменять
    справкой (docs/development/188 §8, L-051).
    """
    import re as _re

    not_caps = [
        "что может быть",
        "что может случиться",
        "что может быть в студии",
        "можешь что-нибудь по бюджету",
        "что бот сделал вчера",
    ]
    for phrase in not_caps:
        assert not _re.search(gw.CAPS_Q, phrase.lower()), (
            f"«{phrase}» перехвачено как вопрос об умениях")
