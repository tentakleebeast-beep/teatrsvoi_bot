"""Ш-03a плана 129: соответствие «имя в файле → человек» берётся из данных, а не из кода.

После обезличивания (Ш-03 плана 129) словари `NICK` и `aliases` указывали на вымышленные
имена, а боевая БД хранит настоящие, поэтому импорт расписания и сверка занятости переставали
находить людей. Здесь проверяется обратное: человек находится по `person_alias`, `person_key`
и `display_name` из БД, локальный конфиг закрывает только пробелы и не перебивает БД, а
подсказки категории бюджета приходят из данных, а не из списка фамилий в коде.
"""

from __future__ import annotations

from pathlib import Path

from app.participant_names import Names, build_index, category_hints, load_config, normalize
from app.rules import detect_category

PARTICIPANTS = [
    {"id": 1, "display_name": "Соколов Марк", "person_key": "соколов-марк"},
    {"id": 2, "display_name": "Волкова Людмила", "person_key": "волкова-людмила"},
]
ALIASES = [
    {"participant_id": 2, "alias": "Люда"},
    {"participant_id": 1, "alias": "Марк"},
]


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_никнейм_из_бд_находит_человека_без_словаря_в_коде(tmp_path):
    """Никнейм приходит строкой из `person_alias`; в py-файле его нет."""
    index = build_index(PARTICIPANTS, ALIASES, load_config(tmp_path / "нет-конфига.yaml"))
    assert index.resolve("люда") == 2
    assert index.resolve("МАРК") == 1
    assert index.display_name(2) == "Волкова Людмила"


def test_полное_имя_и_person_key_тоже_находят_человека(tmp_path):
    """`display_name` и `person_key` — такие же данные для поиска, как и псевдоним."""
    index = build_index(PARTICIPANTS, ALIASES, load_config(tmp_path / "нет-конфига.yaml"))
    assert index.resolve("Соколов Марк") == 1
    assert index.resolve("волкова-людмила") == 2
    assert index.resolve("посторонний") is None


def test_регистр_ё_и_лишние_пробелы_не_мешают():
    """«Алёна» в БД и «алена» в файле — один человек; то же с двойным пробелом."""
    assert normalize("  Алёна   Морозова ") == "алена морозова"
    index = build_index([{"id": 3, "display_name": "Морозова Алёна", "person_key": None}],
                        [], Names())
    assert index.resolve("морозова алена") == 3
    assert index.resolve("МОРОЗОВА  АЛЁНА") == 3


def test_конфиг_закрывает_пробел_и_не_перебивает_бд(tmp_path):
    """Файл оператора добавляет никнейм, которого нет в БД, и не уводит существующий."""
    config = load_config(_write(tmp_path / "имена.yaml", """
names:
  "Соколов-преподаватель": "соколов-марк"
  "марк": "волкова-людмила"
  "витя м": "нет-такого-человека"
"""))
    index = build_index(PARTICIPANTS, ALIASES, config)
    assert index.resolve("соколов-преподаватель") == 1
    assert index.resolve("марк") == 1                       # в БД, конфиг не перебил
    assert index.conflicts == {"марк": 2}                   # и сказал об этом
    assert index.unresolved == ["витя м"]                   # person_key не найден в БД


def test_нет_конфига_это_не_ошибка(tmp_path):
    """Путь без файла — штатный случай: работаем на данных БД."""
    assert load_config(tmp_path / "нет.yaml") == Names()
    assert category_hints(Names()) == ()


def test_битый_конфиг_называется_ошибкой_а_не_молчанием(tmp_path):
    """Файл, который правил человек, нельзя читать «как получится»."""
    bad = _write(tmp_path / "битый.yaml", "names:\n  - это список, а не словарь\n")
    try:
        load_config(bad)
    except ValueError as error:
        assert "names" in str(error)
    else:  # pragma: no cover - сообщение важнее ветки
        raise AssertionError("битый конфиг должен приводить к ValueError")


def test_подсказка_категории_по_имени_приходит_из_конфига(tmp_path):
    """«5000 Соколову» — гонорары: имя не в коде, а в данных (раздел category_hints)."""
    config = load_config(_write(tmp_path / "имена.yaml", """
category_hints:
  "Соколов": "Гонорары и доли"
"""))
    hints = category_hints(config)
    assert detect_category("5000 соколову") is None
    assert detect_category("5000 соколову", extra_hints=hints) == "Гонорары и доли"
    # своё правило сильнее подсказки: аренда остаётся арендой
    assert detect_category("аренда 5000 соколову", extra_hints=hints) == "Аренда помещения"
