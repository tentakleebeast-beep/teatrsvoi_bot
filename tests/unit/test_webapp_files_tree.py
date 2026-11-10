"""Дерево папок: сборка иерархии из плоского ответа `/webapp/folders` (В2/В3).

Сервер отдаёт строки `order by path`, поэтому вложенность на экране не видна, а сирота
(ребёнок закрытой папки) приходит с `parent_id` в никуда. Иерархию собирает клиент
чистой функцией `buildFolderTree`, и здесь она проверяется в node без браузера — тот же
приём, что `test_js_парсится`: источник берётся импортом константы
`files_tree.TREE_BUILD`, а не регуляркой из HTML.

Что доказано: вложенность, сирота, цикл (обход завершается, каждый узел один раз),
цепочка в цикл, `parent_id === id`, порядок по названию, уровень по фактической
вложенности, повтор `id`, строка без `id`, пустой вход. Плюс статические проверки
собранной страницы: `aria-expanded` на кнопке, `aria-controls` на свой список, подпись
переключателя, `sessionStorage` и раскрытое по умолчанию дерево.

Обоснование: docs/development/155-miniapp-files-grid-and-tree.md §4, §5.2, §7.2,
docs/development/91-design-system.md §4.12, карта t_9297d656.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import files, files_tree  # noqa: E402

NODE = shutil.which("node") or next(
    (candidate for candidate in ("/opt/homebrew/bin/node", "/usr/local/bin/node")
     if pathlib.Path(candidate).exists()), None)


def _call(expr: str):
    """Выполнить выражение в node на `TREE_BUILD` и вернуть разобранный JSON."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(files_tree.TREE_BUILD + "\nconsole.log(JSON.stringify(" + expr + "));")
        path = fh.name
    result = subprocess.run([NODE, path], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def _flat(nodes) -> list[dict]:
    out = []
    for node in nodes:
        out.append(node)
        out.extend(_flat(node["children"]))
    return out


def _rows_for_tree() -> list[dict]:
    return [
        {"id": 1, "parent_id": None, "name": "Спектакли", "path": "Спектакли", "depth": 1},
        {"id": 2, "parent_id": 1, "name": "Бунин", "path": "Спектакли/Бунин", "depth": 2},
        {"id": 3, "parent_id": 2, "name": "заметки", "path": "Спектакли/Бунин/заметки", "depth": 3},
        {"id": 4, "parent_id": 2, "name": "музыка", "path": "Спектакли/Бунин/музыка", "depth": 3},
        {"id": 5, "parent_id": None, "name": "Архив 2024", "path": "Архив 2024", "depth": 1},
        {"id": 6, "parent_id": 999, "name": "старое", "path": "Архив 2024/старое", "depth": 2},
    ]


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_плоский_список_становится_вложенным() -> None:
    """Порядок `order by path` превращается в иерархию: родитель, дети, внуки."""
    tree = _call("buildFolderTree(" + json.dumps(_rows_for_tree()) + ")")

    assert [node["name"] for node in tree] == ["Архив 2024", "Спектакли", "старое"]
    спектакли = tree[1]
    assert [child["name"] for child in спектакли["children"]] == ["Бунин"]
    assert [child["name"] for child in спектакли["children"][0]["children"]] == ["заметки", "музыка"]


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_сирота_выходит_наверх_с_полным_путём() -> None:
    """`parent_id` в никуда (закрытая папка) — корень с полным путём, а не отступ в пустоту."""
    tree = _call("buildFolderTree(" + json.dumps(_rows_for_tree()) + ")")
    сирота = [node for node in tree if node["id"] == 6][0]

    assert сирота["orphan"] is True
    assert сирота["path"] == "Архив 2024/старое"
    assert сирота["level"] == 1


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_цикл_не_завешивает_и_виден_один_раз() -> None:
    """`a → b → a`: обход завершается, каждый узел в выходе ровно один раз."""
    rows = [
        {"id": 10, "parent_id": 20, "name": "a", "path": "a", "depth": 2},
        {"id": 20, "parent_id": 10, "name": "b", "path": "a/b", "depth": 2},
    ]
    tree = _call("buildFolderTree(" + json.dumps(rows) + ")")

    assert len(tree) == 1, "цикл дал больше одного корня"
    assert tree[0]["id"] == 10, "наверх должен выйти узел с меньшим id"
    assert [child["id"] for child in tree[0]["children"]] == [20]
    assert len(_flat(tree)) == 2, "узел цикла показан дважды"


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_цепочка_в_цикл_не_теряется() -> None:
    """`x → a → b → a`: наверх выходит `a` (меньший id цикла), `x` остаётся её ребёнком.

    Выход из цикла выбирается по наименьшему id недостижимого узла: здесь это `a`
    (id 3 против 4 у `b`), а `x` с большим id идёт к нему ребёнком.
    """
    rows = [
        {"id": 3, "parent_id": 4, "name": "a", "path": "a", "depth": 2},
        {"id": 4, "parent_id": 3, "name": "b", "path": "a/b", "depth": 3},
        {"id": 5, "parent_id": 3, "name": "x", "path": "x", "depth": 1},
    ]
    tree = _call("buildFolderTree(" + json.dumps(rows) + ")")
    узлы = {node["id"]: node for node in _flat(tree)}

    assert set(узлы) == {3, 4, 5}
    assert len(tree) == 1 and tree[0]["id"] == 3, "наверх вышел не узел цикла"
    assert [child["id"] for child in tree[0]["children"]] == [4, 5]
    assert узлы[5]["level"] == 2, "цепочка, ведущая в цикл, потеряла родителя"


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_родитель_сам_себе_корень() -> None:
    """`parent_id === id` — не родитель: узел на верхнем уровне, `orphan: true`."""
    rows = [{"id": 7, "parent_id": 7, "name": "петля", "path": "петля", "depth": 1}]
    tree = _call("buildFolderTree(" + json.dumps(rows) + ")")

    assert len(tree) == 1 and tree[0]["orphan"] is True


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_порядок_по_названию_а_не_по_пути() -> None:
    """`numeric` ставит «01 Увертюра» перед «10 Финал», регистр на порядок не влияет."""
    rows = [
        {"id": 1, "parent_id": None, "name": "10 Финал", "path": "10 Финал", "depth": 1},
        {"id": 2, "parent_id": None, "name": "01 Увертюра", "path": "01 Увертюра", "depth": 1},
        {"id": 3, "parent_id": None, "name": "02 Выход", "path": "02 Выход", "depth": 1},
    ]
    tree = _call("buildFolderTree(" + json.dumps(rows) + ")")

    assert [node["name"] for node in tree] == ["01 Увертюра", "02 Выход", "10 Финал"]


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_уровень_считается_по_вложенности() -> None:
    """`depth` из ответа сервера не используется: уровень — по фактической вложенности."""
    rows = [
        {"id": 1, "parent_id": None, "name": "Спектакли", "path": "Спектакли", "depth": 3},
        {"id": 2, "parent_id": 1, "name": "Бунин", "path": "Спектакли/Бунин", "depth": 1},
    ]
    tree = _call("buildFolderTree(" + json.dumps(rows) + ")")

    assert tree[0]["level"] == 1
    assert tree[0]["children"][0]["level"] == 2


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_повтор_id_не_дублирует_строку() -> None:
    """Повтор `id` — одна строка; выигрывает первая, как её отдал сервер."""
    rows = [
        {"id": 7, "parent_id": None, "name": "первая", "path": "первая", "depth": 1},
        {"id": 7, "parent_id": None, "name": "вторая", "path": "вторая", "depth": 1},
    ]
    tree = _call("buildFolderTree(" + json.dumps(rows) + ")")

    assert len(tree) == 1 and tree[0]["name"] == "первая"


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_строка_без_id_отбрасывается() -> None:
    """У строки без числового `id` нет адреса — вести её некуда."""
    rows = [
        {"id": None, "parent_id": None, "name": "без номера", "path": "без номера", "depth": 1},
        {"id": "нет", "parent_id": None, "name": "строка", "path": "строка", "depth": 1},
        {"id": 2, "parent_id": None, "name": "настоящая", "path": "настоящая", "depth": 1},
    ]
    tree = _call("buildFolderTree(" + json.dumps(rows) + ")")

    assert [node["id"] for node in tree] == [2]


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_пустой_вход_даёт_пустой_выход() -> None:
    """Пустой ответ и ответ, где все строки отброшены, — одно и то же: пустое состояние."""
    assert _call("buildFolderTree([])") == []
    assert _call("buildFolderTree(null)") == []


def test_aria_expanded_на_кнопке_переключателя() -> None:
    """Раскрытие — disclosure: `aria-expanded` на кнопке, а не на строке или `<li>`."""
    page = files.FILES_PAGE
    assert "ui-tree__toggle" in page
    assert 'toggle.setAttribute("aria-expanded"' in page, "состояние не сообщается скринридеру"
    assert 'role="tree"' not in page, "половина модели дерева без клавиатурных тестов"


def test_aria_controls_ведёт_на_свой_список() -> None:
    """`aria-controls` ссылается на `id` вложенного `<ul>`, который действительно есть."""
    page = files.FILES_PAGE
    assert 'toggle.setAttribute("aria-controls", kidsId)' in page
    assert 'kids.id = kidsId' in page
    assert '"folder-kids-" + node.id' in page


def test_подпись_переключателя_меняется() -> None:
    """Подпись кнопки говорит, что произойдёт: «Раскрыть» ↔ «Свернуть»."""
    page = files.FILES_PAGE
    assert '"Раскрыть папку "' in page and '"Свернуть папку "' in page


def test_состояние_раскрытия_в_sessionstorage() -> None:
    """Свёрнутое помнится на сеанс; битое хранилище не ломает дерево (try/catch)."""
    page = files.FILES_PAGE
    assert 'svoi.files.collapsed.v1' in page
    assert "window.sessionStorage.setItem(COLLAPSE_KEY" in page
    assert "window.sessionStorage.getItem(COLLAPSE_KEY" in page
    assert "catch (error)" in page


def test_по_умолчанию_дерево_раскрыто() -> None:
    """Ключа нет — свёрнутых нет, дети видны: свёрнутое по умолчанию прятало бы имена."""
    page = files.FILES_PAGE
    assert "collapsedFolders = [];" in page, "у состояния нет пустого значения"
    assert "var collapsed = isCollapsed(node.id);" in page
    assert "kids.hidden = collapsed;" in page


@pytest.mark.skipif(NODE is None, reason="нет node для проверки сборки дерева")
def test_скрипт_страницы_парсится() -> None:
    """Собранный скрипт экрана — валидный JS: новые блоки конкатенируются в замыкание."""
    page = files.FILES_PAGE
    script = page[page.rindex("<script>") + len("<script>"):page.rindex("</script>")]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    result = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:400]


def test_действия_папки_в_листе_а_не_кнопками_строки() -> None:
    """Действия папки — в листе по «…»; «Открыть»/«Имя»/«Закрыть» кнопками строки исчезли."""
    page = files.FILES_PAGE
    assert "function folderMenu(folder)" in page
    assert "keepOpen: true" in page and "danger: true" in page
    assert '"Переименовать"' in page and '"Закрыть папку"' in page
    assert '"Открыть"' not in page, "в строке папки снова кнопка «Открыть»"
    assert '"Имя"' not in page, "в строке папки снова кнопка «Имя»"
    assert "ui-row__chevron" not in page, "шелчок в папку остался в списке файлов"
