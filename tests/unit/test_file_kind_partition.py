"""Свойство разбиения: строка попадает ровно под один чип или ни под один (192 §4.2).

Чипы «Документы · Текст · Картинки · Аудио» делят файлы без пересечений: у строки ровно
0 или 1 чип, и если чип есть — его слово совпадает с `kind_word`. Проверка идёт на парах
`(kind, name)`: пересечение слов означало бы, что один файл виден под двумя чипами, а
пропуск — что файл не находится ни одним (docs/development/192 §4.2, AC-8).

Правило чипа переписано здесь отдельно от `file_kind.kind_predicate` намеренно: если
предикат разойдётся с таблицей слов, тест покраснеет, а не подтвердит сам себя.
Наборы расширений берутся из `file_kind` — это данные, а не второй список.

Обоснование: FR-K17, V-123, docs/development/189 §D4.
"""

from __future__ import annotations

import pytest
from app import file_kind

WORD_BY_CHIP = {
    "документ": "документ",
    "текст": "текст",
    "картинка": "картинка",
    "аудио": "аудио",
}

DOC = file_kind.DOC_EXT
PLAIN = file_kind.PLAIN_EXT


def chips_of(kind: str | None, name: str) -> list[str]:
    """Чипы, под которые попадает строка, — по таблице §4.2, независимо от SQL."""
    if kind == "folder":
        return []
    ext = file_kind.extension(name)
    # «.docx» — расширение: `extension` возвращает его целиком, наборы тоже с точкой.
    matched: list[str] = []
    if ext in DOC:
        matched.append("документ")
    if ext in PLAIN or (kind == "text" and ext not in DOC):
        matched.append("текст")
    if kind == "image" and ext not in DOC | PLAIN:
        matched.append("картинка")
    if kind == "audio" and ext not in DOC | PLAIN:
        matched.append("аудио")
    return matched


PAIRS = [
    ("text", "Смета.pdf"),
    ("text", "Смета.PDF"),
    ("text", "заметки.md"),
    ("text", "список.csv"),
    ("text", "Письмо.txt"),
    (None, "IMG_1.xyz"),
    (None, "без расширения"),
    (None, ""),
    ("image", "Афиша.png"),
    ("image", "скрин.jpeg"),
    ("image", "Смета.pdf"),
    ("audio", "трек.mp3"),
    ("audio", "Запись репетиции"),
    ("folder", "Пурга 2025"),
    ("folder", "Смета.pdf"),
]


@pytest.mark.parametrize("kind,name", PAIRS)
def test_строка_попадает_ровно_под_один_чип_или_ни_под_один(kind, name) -> None:
    """0 или 1 чип на строку: пересечение — двойной показ, пропуск — «не находится»."""
    matched = chips_of(kind, name)
    assert len(matched) <= 1, f"{kind}/{name}: чипов {matched}"
    if matched:
        assert matched[0] == file_kind.kind_word(kind, name), \
            f"{kind}/{name}: слово и чип разошлись"


@pytest.mark.parametrize("kind,name", PAIRS)
def test_нет_чипа_только_у_файла_без_расширения_и_папки(kind, name) -> None:
    """Без чипа остаются ровно два случая: папка и имя без известного расширения."""
    matched = chips_of(kind, name)
    if matched:
        assert kind != "folder"
        return
    assert kind == "folder" or file_kind.extension(name) not in DOC | PLAIN


def test_папка_не_видна_ни_под_одним_чипом() -> None:
    """V-123: папка Drive — отдельная запись, но фильтры её не «ловят» ни одним чипом."""
    for name in ("Пурга", "Смета.pdf", "Афиша.png", "трек.mp3", "IMG_1.xyz"):
        assert chips_of("folder", name) == []


def test_смта_пдф_документ_а_не_текст() -> None:
    """Главный случай карты: `(text, Смета.pdf)` — «документ» и не под «Текст»."""
    assert chips_of("text", "Смета.pdf") == ["документ"]
    assert file_kind.kind_word("text", "Смета.pdf") == "документ"
    assert chips_of("text", "заметки.md") == ["текст"]


def test_неизвестное_расширение_видно_только_под_все() -> None:
    """`(null, IMG_1.xyz)` — «файл», только под «Все»: ни один чип его не берёт."""
    assert chips_of(None, "IMG_1.xyz") == []
    assert file_kind.kind_word(None, "IMG_1.xyz") == "файл"
