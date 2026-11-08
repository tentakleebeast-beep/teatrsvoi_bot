"""Подсказки чата называют только канонические латинские команды (FR-F7).

Клиент Telegram подсвечивает и автодополняет команды только латиницей. Совет
«Записать здесь: /заметка …» выглядит командой, которой в меню нет, хотя бот её
принимает через алиас `Command("заметка", "note")` — человек читает одно, а меню
показывает другое (дефект dev-бота 05.11.2026, источник — карта t_273c08e6).

Правило: в текстах для человека — каноническое имя из `commands.py`, русская
форма — словами («заметка: …»). Здесь три проверки:

* в текстах gateway и страниц Mini App нет кириллических команд;
* каждая команда, которой gateway советует пользоваться, есть в списке `COMMANDS`;
* меню команд ставится из того же списка, а его имена Telegram примет.

Что не считается подсказкой: докстринги (текст для разработчика) и таблицы
алиасов (`QUIET_COMMANDS = ("/quiet", "/тихие")`) — там литерал состоит из одной
команды целиком и человеку он не показывается. Страницы Mini App проверяются
только на кириллицу: в их строках живут HTML-теги и JS, где `/g`, `/div` — не
команды чата.
"""

from __future__ import annotations

import ast
import pathlib
import re

import gateway_modules

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "services" / "tg-gateway" / "app"
WEBAPP_PAGES = ROOT / "services" / "tool-api" / "app" / "pages"

#: Кириллическая команда — ровно то, что не подсвечивает клиент Telegram.
#: `(?<!\w)` отсекает «Фото/видео» и «Спектакли/Пурга»: это путь, а не команда.
CYRILLIC_COMMAND = re.compile(r"(?<!\w)/[а-яё]")

#: Команда в прозе. Перед ней — начало строки, пробел или кавычка/скобка, после —
#: пробел, знак или конец строки: так `/webapp/notes` и `<a href=…>` не считаются
#: командой «webapp» и «a».
COMMAND_IN_TEXT = re.compile(
    r"(?:(?<=[\s«„(\[{—])|(?<=^))/([a-z][a-z0-9_-]*)(?=$|[\s,.;:!?)»”\]}—])")

#: Литерал, который целиком состоит из русской команды: таблица алиасов.
BARE_ALIAS = re.compile(r"/[а-яё][а-яё-]*")

#: Литерал-адрес: `/health`, `/webapp`, `/tools/notes` — путь, а не совет.
BARE_PATH = re.compile(r"/[a-z][a-z0-9_/-]*")


def _docstrings(tree: ast.AST) -> set[int]:
    """id() строк-докстрингов: они не текст для человека."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            found.add(id(first.value))
    return found


def _fragments(tree: ast.AST) -> set[int]:
    """id() кусков f-строк: это части адресов (`/content`, `/card`), а не проза."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant):
                    found.add(id(part))
    return found


def _user_texts(*roots: pathlib.Path):
    """(файл, строка) для всех литералов, кроме докстрингов и кусков f-строк."""
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            skip = _docstrings(tree) | _fragments(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in skip):
                    yield path, node.value


def test_в_подсказках_нет_кириллических_команд() -> None:
    """`/[а-яё]` в тексте для человека — это совет командой, которой нет в меню."""
    bad = []
    for path, text in _user_texts(GATEWAY, WEBAPP_PAGES):
        if BARE_ALIAS.fullmatch(text.strip()):
            continue
        if CYRILLIC_COMMAND.search(text):
            bad.append(f"{path.relative_to(ROOT)}: {text.strip()[:70]!r}")
    assert not bad, "кириллическая команда в подсказке: " + "; ".join(bad)


def test_каждая_команда_из_подсказок_есть_в_списке() -> None:
    """Подсказка не может звать команду, которой нет в `commands.COMMANDS`."""
    known = frozenset(item.name for item in gateway_modules.load("commands").COMMANDS)
    mentioned: dict[str, set[str]] = {}
    for path, text in _user_texts(GATEWAY):
        if BARE_PATH.fullmatch(text.strip()):
            continue
        for name in COMMAND_IN_TEXT.findall(text):
            mentioned.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    unknown = sorted(f"/{name} ({', '.join(sorted(files))})"
                     for name, files in mentioned.items() if name not in known)
    assert not unknown, "команда в подсказке, которой нет в списке команд: " + "; ".join(unknown)
    assert len(mentioned) >= 10, f"подсказки почти не упоминают команд: {sorted(mentioned)}"


def test_подсказка_по_заметкам_зовёт_note_и_фразу_словами() -> None:
    """Скриншот 05.11.2026: бот советовал `/заметка`, человек печатал `/note`."""
    texts = gateway_modules.load("texts")

    assert "/note" in texts.NOTE_HINT, texts.NOTE_HINT
    assert "заметка:" in texts.NOTE_HINT, texts.NOTE_HINT
    assert "/заметка" not in texts.NOTE_HINT, texts.NOTE_HINT
    assert "/note" in texts.NOTES_HINT, texts.NOTES_HINT


def test_меню_команд_ставится_из_того_же_списка() -> None:
    """Один список на меню и на подсказки: иначе они снова разойдутся."""
    commands = gateway_modules.load("commands")
    menu = commands.menu_commands()

    assert menu, "меню пустое"
    for item in menu:
        assert commands.MENU_NAME_RE.fullmatch(item.name), item.name
        assert item.description and len(item.description) <= 256, item.name
    # Команда с дефисом (`tag-ok`) рабочим остаётся, но в меню Telegram её нет:
    # один такой элемент отклонил бы весь setMyCommands.
    assert "tag-ok" not in {item.name for item in menu}
    assert "tag-ok" in {item.name for item in commands.COMMANDS}

    source = (GATEWAY / "main.py").read_text(encoding="utf-8")
    assert "set_my_commands(" in source, "меню команд никто не ставит при старте"
    assert "commands.menu_commands()" in source, "установка меню читает не ту константу"
