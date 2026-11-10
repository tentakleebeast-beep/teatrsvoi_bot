"""Плитка файла и спрайт иконок: `iconFor`, разметка, дизайн-система (В2/В3).

Раздел «Мои файлы» был плоским списком: имя в 67 знаков не переносилось и уходило за
рамку (горизонтального скролла нет — `body.ui-page` с `overflow-x: clip`), то есть имя
было не дочитать. Плитка с иконкой вида решает это раскладкой, а не обрезкой.

Что доказано: `iconFor` отвечает по таблице (расширение сильнее `kind`, потому что pdf
приходит как `kind='text'`, D8), каждый символ из разметки есть в спрайте и символов
ровно семь, плитка — ссылка, «Удалить» — сосед ссылки, стрелки в папку нет; классы
сетки и дерева живут в дизайн-системе, а не на странице.

Обоснование: docs/development/155-miniapp-files-grid-and-tree.md §5.1, §6.1, §6.3,
docs/development/91-design-system.md §4.11, §4.13, карта t_9297d656.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import files, files_actions, files_grid, files_tree, layout  # noqa: E402

NODE = shutil.which("node") or next(
    (candidate for candidate in ("/opt/homebrew/bin/node", "/usr/local/bin/node")
     if pathlib.Path(candidate).exists()), None)

PAGES = ROOT / "services" / "tool-api" / "app" / "pages"
CSS = ROOT / "services" / "tool-api" / "app" / "static" / "webapp-ui.css"
DESIGN = ROOT / "docs" / "development" / "91-design-system.md"


def _call(expr: str):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(files_grid.ICON_FOR + "\nconsole.log(JSON.stringify(" + expr + "));")
        path = fh.name
    result = subprocess.run([NODE, path], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="нет node для проверки иконок")
@pytest.mark.parametrize("kind,name,expected", [
    ("audio", "01 Увертюра.mp3", "ui-i-audio"),
    ("image", "Афиша.png", "ui-i-image"),
    ("document", "Программа.doc", "ui-i-document"),
    ("text", "заметки.md", "ui-i-text"),
])
def test_иконка_по_kind(kind, name, expected) -> None:
    """Вид файла даёт свой символ: узнавание без чтения подписи."""
    assert _call("iconFor(" + json.dumps(kind) + ", " + json.dumps(name) + ")") == expected


@pytest.mark.skipif(NODE is None, reason="нет node для проверки иконок")
@pytest.mark.parametrize("kind,name,expected", [
    ("text", "Гаммы.pdf", "ui-i-document"),
    ("text", "Смета.docx", "ui-i-document"),
    ("text", "заметки.md", "ui-i-text"),
    ("text", "список.csv", "ui-i-text"),
])
def test_расширение_сильнее_kind(kind, name, expected) -> None:
    """pdf приходит как `kind='text'`: решает расширение, иначе иконка спорит со словом."""
    assert _call("iconFor(" + json.dumps(kind) + ", " + json.dumps(name) + ")") == expected


@pytest.mark.skipif(NODE is None, reason="нет node для проверки иконок")
def test_неизвестный_вид() -> None:
    """Ни вида, ни расширения — общий символ файла, а не пустое место."""
    assert _call("iconFor(null, 'файл.bin')") == "ui-i-file"
    assert _call("iconFor(undefined, '')") == "ui-i-file"


def _symbols() -> list[str]:
    return re.findall(r'<symbol id="([^"]+)"', layout.SPRITE)


def test_все_символы_есть_в_спрайте() -> None:
    """Третий источник иконок не заводится: каждый символ из файлов есть в спрайте."""
    symbols = _symbols()
    assert len(symbols) == 7, f"символов {len(symbols)}, а в дизайне семь"
    used = set()
    for module in (files_grid, files_tree, files_actions):
        for name in re.findall(r'"(ui-i-[a-z]+)"', module.SCRIPT + getattr(module, "ICON_FOR", "")):
            used.add(name)
    assert used, "ни одной иконки в разметке"
    missing = sorted(used - set(symbols))
    assert not missing, f"символы без рисунка: {missing}"


def test_плитка_ссылка_а_удаление_сосед() -> None:
    """Плитка — `<a>` на файл, «Удалить» — сосед ссылки, стрелки в папку нет.

    Вложенная кнопка невалидна и ломает фокус; второй путь в ту же папку — дубль
    действия, поэтому стрелка из строки файла убрана (155 §3 D3).
    """
    page = files.FILES_PAGE
    assert 'link.href = "/webapp/file?id=" + file.id' in page
    assert "cell.appendChild(link)" in page
    assert "deleteFileButton(file, cell)" in page, "удаление вложено в ссылку или потеряно"
    assert "link.appendChild(remove" not in page, "кнопка удаления внутри ссылки"
    assert "ui-row__chevron" not in page, "стрелка в папку вернулась на плитку"
    assert 'ui-grid__remove' in page
    assert 'setAttribute("aria-label", "Удалить файл: " + file.name)' in page


def test_сетка_и_дерево_в_дизайн_системе() -> None:
    """Классы живут в общем CSS, а не в `<style>` страницы; дизайн-система на них ссылается."""
    css = CSS.read_text(encoding="utf-8")
    for cls in (".ui-grid", ".ui-grid__remove", ".ui-tree", ".ui-tree__toggle", ".ui-icon"):
        assert cls in css, f"{cls} нет в дизайн-системе"

    design = DESIGN.read_text(encoding="utf-8")
    assert "### 4.11." in design and "### 4.12." in design and "### 4.13." in design
    section = design[design.index("### 4.11."):design.index("### 4.13.")]
    assert "155" in section, "разделы сетки и дерева не ссылаются на дизайн 155"
    assert ".ui-tree__node" in section, "класс узла дерева не описан"
    assert "без отступа и без линии" in section, "правило вложенного списка потеряно"
