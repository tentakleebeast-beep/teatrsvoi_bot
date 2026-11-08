"""Сопоставление «имя в файле → человек в БД» — из данных, а не из кода (Ш-03a плана 129).

История. Ш-03 обезличила имена в репозитории. Три места хранили не текст, а само
соответствие «имя в файле → человек»: словарь `NICK` (`scripts/import_schedule.py`),
словарь `aliases` (`scripts/reconcile.py`) и фамилии-подсказки категории бюджета
(`app/rules.py`, `app/core.py`, `scripts/import_finance.py`). После обезличивания они
указывали на вымышленные имена, а боевая БД хранит настоящие: импорт расписания и сверка
занятости переставали находить людей.

Решение (карта Ш-03a): основной источник соответствия — **данные**, а не код.

  * человек ищется по `person_alias`, `person_key` и `display_name` из БД;
  * никнейм, которого в БД ещё нет, оператор дописывает в локальный
    `config/participant-names.yaml` (в git только `.example` с плейсхолдерами, сам файл
    в `.gitignore`): правка данных, а не релиз;
  * подсказки категории по имени человека («5000 Соколову» → «Гонорары и доли») берутся
    оттуда же (`category_hints`). В БД нет признака «этому человеку платят гонорар»,
    а выводить подсказку из всех имён подряд нельзя: любая строка «занятие с Машей»
    стала бы гонораром.

Отсутствие или пустота конфига — не ошибка: работаем на одних данных БД. Ошибкой считается
только битый файл, который оператор правил руками, — о нём нужно сказать, а не молчать.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .version import project_root

CONFIG_NAME = "participant-names.yaml"
_SPACES = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    """Сравнение имён без регистра, ё/е и лишних пробелов: «Витя  М» == «витя м»."""
    return _SPACES.sub(" ", str(text or "").strip().lower().replace("ё", "е"))


def config_path() -> Path:
    """Локальный файл оператора: `<корень репозитория>/config/participant-names.yaml`."""
    return project_root() / "config" / CONFIG_NAME


@dataclass(frozen=True)
class Names:
    """Локальный конфиг: никнеймы листов и подсказки категории по имени человека."""

    aliases: dict[str, str] = field(default_factory=dict)   # нормализованный никнейм → person_key
    hints: tuple[tuple[str, str], ...] = ()                  # (нормализованное имя, категория)


def load_config(path: Path | str | None = None) -> Names:
    """Прочитать локальный конфиг; нет файла — пустые значения (это норма)."""
    file = Path(path) if path is not None else config_path()
    if not file.is_file():
        return Names()
    raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{file}: ожидался словарь с разделами names и category_hints")
    aliases = raw.get("names") or {}
    if not isinstance(aliases, dict):
        raise ValueError(f"{file}: names — словарь «никнейм: person_key»")
    raw_hints = raw.get("category_hints") or {}
    if not isinstance(raw_hints, dict):
        raise ValueError(f"{file}: category_hints — словарь «имя человека: категория»")
    return Names(
        aliases={normalize(key): normalize(value) for key, value in aliases.items()
                 if key and value},
        hints=tuple((normalize(key), str(value).strip()) for key, value in raw_hints.items()
                    if key and str(value or "").strip()),
    )


@dataclass
class PersonIndex:
    """Кто есть в БД и как его найти по имени из файла."""

    by_name: dict[str, int] = field(default_factory=dict)   # нормализованное имя → participant.id
    names: dict[int, str] = field(default_factory=dict)     # participant.id → display_name
    unresolved: list[str] = field(default_factory=list)     # никнеймы конфига без своего человека
    conflicts: dict[str, int] = field(default_factory=dict)  # имя: в БД один человек, в конфиге другой

    def resolve(self, name: str | None) -> int | None:
        """Человек по имени из файла; None — не найден (в БД и в конфиге его нет)."""
        return self.by_name.get(normalize(name))

    def display_name(self, participant_id: int) -> str:
        """Имя для записи в таблицу: как в БД, а не как в словаре кода."""
        return self.names.get(participant_id, "")


def build_index(participants: Iterable[Mapping[str, Any]],
                aliases: Iterable[Mapping[str, Any]],
                config: Names | None = None) -> PersonIndex:
    """Собрать поиск людей из строк БД и локального конфига.

    Старшинство: `person_alias` → `display_name`/`person_key` → конфиг. Конфиг закрывает
    только пробелы (никнейм, которого в БД нет): перебивать боевые алиасы локальным файлом
    нельзя, иначе устаревший конфиг молча уводит людей не туда. Такой случай попадает
    в `conflicts` и печатается вызывающим скриптом.
    """
    index = PersonIndex()
    for row in aliases:
        alias = row.get("alias")
        participant_id = row.get("participant_id")
        if alias and participant_id is not None:
            index.by_name.setdefault(normalize(alias), int(participant_id))

    by_key: dict[str, int] = {}
    for row in participants:
        participant_id = int(row["id"])
        index.names[participant_id] = str(row.get("display_name") or "")
        for text in (row.get("display_name"), row.get("person_key")):
            if text:
                index.by_name.setdefault(normalize(str(text)), participant_id)
        key = row.get("person_key")
        if key:
            by_key[normalize(str(key))] = participant_id

    for nick, person_key in (config or Names()).aliases.items():
        participant_id = by_key.get(person_key)
        if participant_id is None:
            index.unresolved.append(nick)
            continue
        known = index.by_name.get(nick)
        if known is not None and known != participant_id:
            index.conflicts[nick] = participant_id
            continue
        index.by_name[nick] = participant_id
    return index


def category_hints(config: Names | None = None) -> tuple[tuple[str, str], ...]:
    """Подсказки категории по имени человека: («фамилия», «Гонорары и доли»).

    Нужны там, где строка бюджета — только имя и сумма: «5000 Соколову». В БД нет признака
    «этому человеку платят гонорар», поэтому список ведёт человек в локальном конфиге.
    """
    return (config or load_config()).hints
