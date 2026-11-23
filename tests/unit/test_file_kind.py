"""Вид файла: расширение, слово и предикат чипа (docs/development/192 §4).

Одно правило на три выхода: `extension` (что за файл), `kind_word` (как назвать человеку),
`kind_predicate` (SQL чипа). До E4 списков было три, и pdf показывался как «текст» там,
где лежит смета (docs/development/150 §4).

Обоснование: FR-K17, V-123, docs/development/192 §4.1–§4.3, docs/development/189 §D4.
"""

from __future__ import annotations

import pytest
from app import file_kind


def test_расширение_с_точкой_и_без_ошибок() -> None:
    """`extension` отдаёт расширение с точкой и никогда `None` (иначе `not in` даст NULL)."""
    assert file_kind.extension("Смета.PDF") == ".pdf"
    assert file_kind.extension("заметки.md") == ".md"
    assert file_kind.extension(".docx") == ".docx"
    assert file_kind.extension("архив.tar.gz") == ".gz"
    assert file_kind.extension("a.b.c") == ".c"
    assert file_kind.extension("без расширения") == ""
    assert file_kind.extension("") == ""
    assert file_kind.extension(None) == ""


@pytest.mark.parametrize("name,expected", [
    ("Смета.pdf", "text"),
    ("Смета.docx", "text"),
    ("заметки.md", "text"),
    ("Афиша.png", "image"),
    ("трек.mp3", "audio"),
    ("папка без расширения", None),
    ("IMG_1.xyz", None),
])
def test_вид_по_имени_без_отказов(name, expected) -> None:
    """`kind_by_name` — про фильтр, а не про приём: неизвестное имя даёт `None`, не отказ."""
    assert file_kind.kind_by_name(name) == expected


@pytest.mark.parametrize("kind,name,word", [
    ("text", "Смета.pdf", "документ"),
    ("text", "заметки.md", "текст"),
    ("text", "список.csv", "текст"),
    (None, "IMG_1.xyz", "файл"),
    ("image", "Афиша.png", "картинка"),
    ("audio", "трек.mp3", "аудио"),
    ("folder", "Пурга 2025", "папка"),
    ("folder", "Смета.pdf", "папка"),
    ("text", "Без расширения", "текст"),
    (None, "", "файл"),
])
def test_слово_вида(kind, name, word) -> None:
    """Слово решается расширением раньше вида: pdf/docx — «документ», а не «текст»."""
    assert file_kind.kind_word(kind, name) == word


def test_слова_чипов_и_папка_вне_чипов() -> None:
    """«папка» — слово, но не чип (V-123): папка видна только под «Все» и «Ссылки»."""
    assert file_kind.KIND_WORDS == ("документ", "текст", "картинка", "аудио")
    assert file_kind.FOLDER_WORD == "папка"
    for word in file_kind.KIND_WORDS:
        clause, params = file_kind.kind_predicate(word)
        assert "f.kind is distinct from 'folder'" in clause, \
            "папка с именем «Смета.pdf» иначе попала бы под «Документы»"
        assert params, "расширения передаются параметрами, а не вклеиваются в текст"
    with pytest.raises(ValueError):
        file_kind.kind_predicate("папка")


def test_предикат_документов_и_текста_по_одному_списку() -> None:
    """Наборы в предикате — те же, что в `kind_word`: второй список не заводится."""
    clause, params = file_kind.kind_predicate("документ")
    assert params == [sorted(file_kind.DOC_EXT)]
    assert "file_ext(f.name) = any(%s)" in clause

    clause, params = file_kind.kind_predicate("текст")
    assert params == [sorted(file_kind.PLAIN_EXT), sorted(file_kind.DOC_EXT)]
    assert clause.count("= any(%s)") == 2

    for word, kind in (("картинка", "image"), ("аудио", "audio")):
        clause, params = file_kind.kind_predicate(word)
        assert f"f.kind = '{kind}'" in clause
        assert params == [sorted(file_kind.DOC_EXT | file_kind.PLAIN_EXT)]


def test_неизвестное_слово_это_ошибка_программиста() -> None:
    """Вход ручки проверяется до вызова: сюда неизвестное слово попасть не должно."""
    for word in ("видео", "все", "", "Документ"):
        with pytest.raises(ValueError):
            file_kind.kind_predicate(word)
