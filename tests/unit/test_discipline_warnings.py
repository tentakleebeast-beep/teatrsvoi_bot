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


def test_отказ_по_правам_людей_на_сервере_один():
    """Текст Б3 — константа сервера: клиент не придумывает слова сам."""
    assert core.MANAGE_FORBIDDEN_CODE == "participants_manage_forbidden"
    assert core.MANAGE_FORBIDDEN_TEXT == "Роли и дисциплины педагога меняет администратор."
