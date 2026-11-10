"""Предупреждение «мастер без дисциплины»: одна функция, два текста (V-037).

До этой карты правило было запретом: выдача роли без дисциплины — `400`, снятие
последней — `409`. Владелец решил (08.11.2026): разрешать и показывать
предупреждение. Текст живёт в `core.discipline_warnings` — единственное место,
откуда его берут карточка человека, анкета и «Поиск окон»; копий в JS нет.

Обоснование: FR-A6 (выдача прав), docs/architecture/140-master-role.md,
docs/development/141 §4.2 (Б3), карта t_6e49e144.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app import core  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")

MUSIC = [{"code": "music", "name": "Музыка"}]


def test_не_мастер_не_получает_предупреждения():
    assert core.discipline_warnings(["troupe"], []) == []
    assert core.discipline_warnings([], []) == []
    assert core.discipline_warnings(["admin", "org"], []) == []


def test_мастер_с_дисциплиной_без_предупреждения():
    assert core.discipline_warnings(["master"], MUSIC) == []
    assert core.discipline_warnings(["troupe", "master"], MUSIC) == []


def test_мастер_без_дисциплины_получает_один_элемент():
    warnings = core.discipline_warnings(["master"], [], audience="admin")
    assert len(warnings) == 1
    assert warnings[0]["code"] == "master_without_discipline"
    assert warnings[0]["text"]


def test_тексты_аудиторий_различаются():
    admin = core.discipline_warnings(["master"], [], audience="admin")[0]["text"]
    own = core.discipline_warnings(["master"], [], audience="self")[0]["text"]
    assert admin != own
    # админские экраны — про педагога, анкета — от первого лица
    assert "педагог" in admin.lower()
    assert "дисциплина не выбрана" in own.lower()


def test_предупреждение_без_эмодзи():
    for audience in ("admin", "self"):
        text = core.discipline_warnings(["master"], [], audience=audience)[0]["text"]
        assert not EMOJI.search(text), f"{audience}: эмодзи в тексте"


def test_V061_писать_дисциплину_умеет_один_модуль():
    """Решение V-061 (а): связь «ведёт дисциплину» пишет только `api/admin.py`.

    Владелец выбрал (10.11.2026): дисциплину ставит администратор в карточке человека,
    самообслуживания нет — значит нет ни второго пути записи, ни права `disciplines.self`.
    Отказ мастеру по правам закреплён тестом
    `tests/integration/test_master_role.py::test_Г20_нет_права_на_дисциплину_словами`.
    """
    services = ROOT / "services"
    writers = sorted(path.relative_to(ROOT).as_posix()
                     for path in services.rglob("*.py")
                     if "insert into participant_discipline" in path.read_text(encoding="utf-8"))
    assert writers == ["services/tool-api/app/api/admin.py"], writers
    joined = "".join(path.read_text(encoding="utf-8") for path in services.rglob("*.py"))
    assert "disciplines.self" not in joined, "появилось право самообслуживания (V-061 б)"


def test_отказ_по_правам_людей_на_сервере_один():
    """Текст Б3 — константа сервера: клиент не придумывает слова сам."""
    assert core.MANAGE_FORBIDDEN_CODE == "participants_manage_forbidden"
    assert core.MANAGE_FORBIDDEN_TEXT == "Роли и дисциплины педагога меняет администратор."


def test_код_отказа_по_правам_людей_есть_в_каталоге_ошибок():
    """Каталог `docs/architecture/85` знает код и его статус.

    Код поднимается из `core._webapp_require`, а `test_error_catalog` разбирает
    только `raise` в `main.py` — эту связку он не увидит, и код остался бы
    недокументированным для клиентов. Держит проверка здесь (V-037).
    """
    catalog = (ROOT / "docs" / "architecture" / "85-error-catalog.md").read_text(
        encoding="utf-8")
    rows = [line for line in catalog.splitlines()
            if line.startswith("| `") and
            f"`{core.MANAGE_FORBIDDEN_CODE}`" in line.strip("|").split("|")[0]]
    assert rows, f"нет строки `{core.MANAGE_FORBIDDEN_CODE}` в каталоге ошибок"
    assert "403" in rows[0].strip("|").split("|")[1], rows[0]
