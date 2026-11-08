"""Подписи кнопок из исходников бота — для сторожа реестра функций агента.

Зачем отдельный модуль. `config/agent-features.yaml` обещает, что у каждой кнопки,
которую человек видит в чате, есть запись в реестре. Проверять это по объявленным
спискам (`keyboards.CHAT_BUTTONS`, `routers.menu.CHAT_ACTION_BUTTONS`) мало: кнопку
можно собрать мимо списка, прямо в обработчике, и тогда обещание тихо ломается.
Поэтому сторож читает сами файлы `routers/menu.py` и `keyboards.py` и достаёт подписи
оттуда — в каком бы виде кнопка ни была написана.

Что считается подписью:

* пара `(подпись, колбэк)` в строке клавиатуры — `[("Записать", "bud:ok")]`, в том
  числе с вычисляемым колбэком (`("Изменить", "form:" + kind)`) и внутри спискового
  включения;
* `InlineKeyboardButton(text=...)`, `KeyboardButton(text=...)`,
  `MenuButtonWebApp(text=...)`;
* первый аргумент `webapp_keyboard(...)` — кнопка, ведущая на экран Mini App.

Подпись разрешается, если это литерал, константа модуля (`BTN_SAVE = "Записать"`)
или атрибут `texts.X`. Если подпись приходит из данных или параметра (`text`,
`c["display_name"]`), кнопка возвращается с `label=None` — такие места обязаны быть
описаны в разделе `scenarios` реестра с причиной, иначе тест падает: молча
непроверяемых кнопок быть не должно.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass

#: GATEWAY_APP — корень модулей бота; от него ищется `texts.py`.
GATEWAY_APP = pathlib.Path(__file__).resolve().parents[2] / "services" / "tg-gateway" / "app"

BUTTON_CLASSES = frozenset({"InlineKeyboardButton", "KeyboardButton", "MenuButtonWebApp"})
WEBAPP_BUILDERS = frozenset({"webapp_keyboard", "keyboard"})

#: Где искать подпись: список строки клавиатуры или включение по нему.
ROW_PARENTS = (ast.List, ast.Tuple, ast.ListComp, ast.GeneratorExp, ast.SetComp)

#: Выражение-колбэк: строка, склейка, f-строка, поле или ссылка. Имя тоже считается
#: колбэком: `(text, data)` — строка клавиатуры, собранная из объявленного списка.
CALLBACK_NODES = (ast.Constant, ast.JoinedStr, ast.BinOp, ast.Attribute, ast.Name, ast.Subscript)


@dataclass(frozen=True)
class ScannedButton:
    """Кнопка, найденная в исходнике.

    `label` — разрешённая подпись или `None`, если она приходит из данных;
    `function` — функция, где кнопка собрана; `expression` — исходный текст подписи.
    """

    file: str
    line: int
    function: str
    kind: str
    expression: str
    label: str | None


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Константы уровня модуля: `BTN_SAVE = "Записать"`."""
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = node.value.value
    return found


_TEXT_CONSTANTS: dict[str, str] | None = None


def _texts() -> dict[str, str]:
    """Строковые константы `texts.py`: подписи кнопок живут там."""
    global _TEXT_CONSTANTS
    if _TEXT_CONSTANTS is None:
        path = GATEWAY_APP / "texts.py"
        _TEXT_CONSTANTS = _string_constants(ast.parse(path.read_text(encoding="utf-8")))
    return _TEXT_CONSTANTS


def _resolve(node: ast.AST, constants: dict[str, str]) -> str | None:
    """Значение подписи, если оно вычисляется без запуска кода."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id == "texts":
        return _texts().get(node.attr)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve(node.left, constants)
        right = _resolve(node.right, constants)
        return None if left is None or right is None else left + right
    return None


def _is_callback(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    return isinstance(node, CALLBACK_NODES)


def scan_file(path: pathlib.Path, file_name: str | None = None) -> list[ScannedButton]:
    """Все подписи кнопок одного файла — с именами функций и номерами строк."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = _string_constants(tree)
    out: list[ScannedButton] = []
    _walk(tree, None, "<module>", constants, file_name or path.name, out)
    return out


def _walk(node: ast.AST, parent: ast.AST | None, function: str,
          constants: dict[str, str], file_name: str, out: list[ScannedButton]) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        function = node.name
    elif isinstance(node, ast.Lambda):
        function = "<lambda>"

    if isinstance(node, ast.Tuple) and len(node.elts) == 2 and isinstance(parent, ROW_PARENTS):
        text, callback = node.elts
        if _is_callback(callback):
            out.append(ScannedButton(file_name, node.lineno, function, "row",
                                     ast.unparse(text), _resolve(text, constants)))

    if isinstance(node, ast.Call):
        name = ast.unparse(node.func)
        if name in BUTTON_CLASSES:
            for keyword in node.keywords:
                if keyword.arg == "text":
                    out.append(ScannedButton(
                        file_name, node.lineno, function, f"text={name}",
                        ast.unparse(keyword.value), _resolve(keyword.value, constants)))
        elif name in WEBAPP_BUILDERS and node.args:
            first = node.args[0]
            out.append(ScannedButton(file_name, node.lineno, function, "webapp",
                                     ast.unparse(first), _resolve(first, constants)))

    for child in ast.iter_child_nodes(node):
        _walk(child, node, function, constants, file_name, out)


def scan(*paths: pathlib.Path) -> list[ScannedButton]:
    """Подписи кнопок нескольких файлов подряд."""
    found: list[ScannedButton] = []
    for path in paths:
        found.extend(scan_file(path))
    return found


def labels(buttons: list[ScannedButton]) -> set[str]:
    """Разрешённые подписи: всё, что можно сверить с реестром напрямую."""
    return {button.label for button in buttons if button.label is not None}


def unresolved(buttons: list[ScannedButton]) -> set[tuple[str, str, str, str]]:
    """Непроверяемые места: файл, функция, вид кнопки, выражение подписи."""
    return {(button.file, button.function, button.kind, button.expression)
            for button in buttons if button.label is None}
