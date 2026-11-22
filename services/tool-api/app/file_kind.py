"""Вид файла: расширения, слово для человека и предикат для фильтра — одно место.

До E4 расширения жили в трёх местах: наборы в `files_store.py`, второй список в JS-плитке
(`pages/files_grid.py`) и третий — слово вида в `pages/files_actions.py`. Из-за этого pdf
приходил как `kind='text'`, плитка показывала «текст», а человек читал «документ» там,
где лежит смета (docs/development/150 §4, docs/development/192 §4).

Здесь два выхода одного правила:

* `extension` / `kind_by_name` — что это за файл по имени (Python: приём и запись в БД);
* `kind_word` — как назвать его человеку («документ», «текст», …);
* `kind_predicate` — SQL-фрагмент чипа, собранный из **тех же** наборов.

Второго списка расширений нет ни в Python, ни в SQL, ни в JS. SQL-функция `file_ext`
(`db/init/072_files_two_locations.sql`) повторяет `extension` — их равенство проверяет
`tests/integration/test_files_ext_function.py`.

Обоснование: docs/development/192 §4.1–§4.3, решение V-123 (папка — слово, но не чип),
FR-K17.
"""

from __future__ import annotations

import os

# Расширения → вид файла. Вид определяет папку и обработку, а не то, как файл назвали.
TEXT_EXT = {".txt", ".md", ".csv", ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".rtf"}
PLAIN_EXT = {".txt", ".md", ".csv"}
DOC_EXT = TEXT_EXT - PLAIN_EXT          # pdf docx xlsx pptx odt ods rtf
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".opus", ".flac", ".m4a", ".aac", ".wma"}

# Слова чипов, не считая «Все». Порядок — порядок чипов на экране.
KIND_WORDS = ("документ", "текст", "картинка", "аудио")
# «папка» — слово, но не чип: папка Drive видна под «Все» и «Ссылки» и ни под одним из чипов
# (V-123, docs/development/192 §4.2).
FOLDER_WORD = "папка"
NO_WORD = "файл"


def extension(name: str) -> str:
    """Расширение в нижнем регистре. Файл с именем «.docx» тоже считается документом.

    `os.path.splitext('.docx')` возвращает пустое расширение — для бота это неожиданность:
    человек переслал документ, а расширения «нет». Поэтому ведущая точка — тоже расширение.
    Возвращается с точкой (`'.pdf'`) или пустая строка — никогда `None`: пустое значение
    не должно превращаться в `NULL` внутри `not in (...)`.
    """
    name = (name or "").strip()
    root, ext = os.path.splitext(name)
    if not ext and name.startswith(".") and len(name) > 1:
        return name.lower()
    return ext.lower()


def kind_by_name(name: str) -> str | None:
    """Вид по имени: `text | image | audio | None`. Отказов не бросает — это про фильтр.

    Приём файла (`files_store.detect_kind`) поверх этого ответа решает, что делать с
    неизвестным: отказать, проверить сигнатуру или принять по mime.
    """
    ext = extension(name)
    if ext in TEXT_EXT:
        return "text"
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    return None


def kind_word(kind: str | None, name: str) -> str:
    """Слово для человека: документ | текст | картинка | аудио | папка | файл.

    Расширение решает раньше вида: pdf и docx приходят как `kind='text'`, но человеку это
    «документ», а не «текст». Папка Drive — всегда «папка», как бы её ни назвали.
    """
    if kind == "folder":
        return FOLDER_WORD
    ext = extension(name)
    if ext in DOC_EXT:
        return "документ"
    if ext in PLAIN_EXT:
        return "текст"
    if kind == "text":
        return "текст"
    if kind == "image":
        return "картинка"
    if kind == "audio":
        return "аудио"
    return NO_WORD


def kind_predicate(word: str) -> tuple[str, list]:
    """SQL-фрагмент чипа над алиасом `f` и параметры-массивы расширений.

    Расширения передаются как `= any(%s)`, а не вклеиваются в текст: список — данные, а не
    код запроса. `f.kind is distinct from 'folder'` — не украшение: папка с именем
    `Смета.pdf` иначе попала бы под «Документы».

    Неизвестное слово — `ValueError`: это ошибка программиста. Вход ручки проверяется до
    вызова (§6.6), чтобы человек получил 400 словами, а не 500.
    """
    if word == "документ":
        return ("f.kind is distinct from 'folder' and file_ext(f.name) = any(%s)",
                [sorted(DOC_EXT)])
    if word == "текст":
        return ("f.kind is distinct from 'folder' and (file_ext(f.name) = any(%s)"
                " or (f.kind = 'text' and not (file_ext(f.name) = any(%s))))",
                [sorted(PLAIN_EXT), sorted(DOC_EXT)])
    if word == "картинка":
        return ("f.kind is distinct from 'folder' and f.kind = 'image'"
                " and not (file_ext(f.name) = any(%s))",
                [sorted(DOC_EXT | PLAIN_EXT)])
    if word == "аудио":
        return ("f.kind is distinct from 'folder' and f.kind = 'audio'"
                " and not (file_ext(f.name) = any(%s))",
                [sorted(DOC_EXT | PLAIN_EXT)])
    raise ValueError(f"неизвестное слово вида файла: {word!r}")
