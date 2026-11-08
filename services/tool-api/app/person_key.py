"""
Человекочитаемый ключ участника (`person_key`).

Ключ — это идентификатор, который человек узнаёт в лицо: «иванов-виктор»,
а не `id=30`. Он же имя файла личных настроек (`bot/manifests/participants/<key>.md`),
поэтому:
  * только строчные буквы (кириллица или латиница), цифры и дефис — никаких пробелов и скобок;
  * `ё` заменяется на `е`: в разных импортах пишут и так, и так;
  * порядок слов — как у человека записано в `display_name` (не угадываем,
    где имя, а где фамилия: «Олег Викторович» — валидная запись без фамилии).

Правило одно на код и на миграцию: `db/init/041_person_key_full.sql` делает то же
самое средствами Postgres, и тест сверяет результат обоих.
"""

from __future__ import annotations

import re

SEPARATOR = "-"
ALLOWED = re.compile(r"[^a-zа-я0-9]+")
PARENS = re.compile(r"\([^)]*\)")


def make_key(display_name: str) -> str:
    """«Иванов Виктор (дубль)» → «иванов-виктор»."""
    text = str(display_name or "").strip().lower().replace("ё", "е")
    text = PARENS.sub(" ", text)                       # «(дубль)» — не часть имени
    text = ALLOWED.sub(SEPARATOR, text).strip(SEPARATOR)
    return re.sub(rf"{SEPARATOR}+", SEPARATOR, text)


def is_valid(key: str) -> bool:
    """Ключ пригоден и как идентификатор, и как имя файла."""
    return bool(key) and make_key(key) == key
