"""Доступ к модулям tg-gateway из unit-тестов.

После разреза `main.py` по доменам (R-2) обработчики и тексты живут в разных
модулях, а тесты исторически брали их из одного `main.py`. Здесь два способа:

* `namespace()` — плоский вид всех модулей gateway: `gw.dues_text` находит
  функцию в `texts`, `gw.error_code` — в `errors`. Присваивание атрибута
  (`monkeypatch.setattr(gw, "api", ...)`) уходит в тот модуль, который имя
  объявляет, — то есть подмена попадает туда, откуда её читают роутеры.
* `source()` — исходники всех модулей подряд для тестов, которые проверяют
  код текстом (порядок обработчиков, наличие строк, обработчики кнопок).

Модули импортируются настоящими именами (`texts`, `routers.budget`), а не
файлами: иначе тест получил бы копию модуля, отличную от той, что видят
роутеры, и подмена не работала бы.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
from collections.abc import Mapping

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "services" / "tg-gateway" / "app"

#: Порядок поиска имён в namespace: сначала общие модули, потом роутеры.
MODULES = (
    "states",
    "config",
    "runtime",
    "errors",
    "texts",
    "commands",
    "parsers",
    "core",
    "keyboards",
    "routers.menu",
    "routers.assign",
    "routers.events",
    "routers.availability",
    "routers.budget",
    "routers.confirm",
    "routers.profile",
    "routers.feedback",
    "routers.admin",
    "routers.files",
    "routers.agent",
    "routers.intro",
    "routers.notes",
    "routers.fallback",
    "main",
)


def ensure_path() -> None:
    """Путь к app/ в sys.path: модули gateway импортируют друг друга по имени."""
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))


def load(name: str = "main"):
    """Импортировать модуль gateway по имени: `load("texts")`, `load("routers.menu")`."""
    ensure_path()
    return importlib.import_module(name)


def source(*names: str) -> str:
    """Исходники модулей gateway подряд (по умолчанию — всех)."""
    files = []
    for name in names or MODULES:
        path = APP / pathlib.Path(*name.split(".")).with_suffix(".py")
        files.append(path.read_text(encoding="utf-8"))
    return "\n".join(files)


class _NamespaceView(Mapping):
    """Вид `Namespace.__dict__` для pytest: имя → значение из его модуля.

    `monkeypatch.setattr(gw, "api", ...)` запоминает старое значение через
    `target.__dict__.get(name)`. У Namespace собственного dict нет, поэтому без
    этого вида oldval получался «нет значения», и откат делал `delattr` вместо
    возврата настоящей функции — подмена оставалась в модуле и портила
    следующие тесты. Итерация не нужна: pytest спрашивает только `get`.
    """

    _MISSING = object()

    def __init__(self, namespace: Namespace) -> None:
        object.__setattr__(self, "_namespace", namespace)

    def _lookup(self, key: str):
        module = self._namespace.owner(key)
        return self._MISSING if module is None else getattr(module, key)

    def get(self, key, default=None):
        value = self._lookup(key)
        return default if value is self._MISSING else value

    def __getitem__(self, key):
        value = self._lookup(key)
        if value is self._MISSING:
            raise KeyError(key)
        return value

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0


class Namespace:
    """Плоский вид модулей gateway: поиск атрибута по имени, запись — в его модуль."""

    def __init__(self) -> None:
        object.__setattr__(self, "_mods", [load(name) for name in MODULES])

    def __getattribute__(self, name: str):
        if name == "__dict__":
            return _NamespaceView(self)
        return object.__getattribute__(self, name)

    def owner(self, name: str):
        """Модуль, который объявляет имя (None, если имени нет ни в одном)."""
        for module in self._mods:
            if hasattr(module, name):
                return module
        return None

    def __getattr__(self, name: str):
        module = self.owner(name)
        if module is None:
            raise AttributeError(f"в модулях gateway нет имени {name!r}")
        return getattr(module, name)

    def __setattr__(self, name: str, value) -> None:
        module = self.owner(name)
        if module is None:
            object.__setattr__(self, name, value)
        else:
            setattr(module, name, value)

    def __delattr__(self, name: str) -> None:
        """Откат monkeypatch не падает: имя снимается только с самого Namespace.

        `monkeypatch.undo()` зовёт `delattr(obj, name)`, когда при подмене имени
        не было вовсе. В модулях такое имя не появлялось — удалять там нечего;
        молчание вместо AttributeError и есть правильное поведение.
        """
        if name in self.__dict__:
            object.__delattr__(self, name)


def namespace() -> Namespace:
    """Плоский вид всех модулей gateway для фикстуры `gw`."""
    return Namespace()
