"""R-4 (docs/reports/79 этап A): окружение читается только в settings.py.

Проверяем по AST, а не по тексту файла: докстринги и комментарии не считаются.

Запрещено:
  * на уровне модуля в `services/**` (кроме `settings.py`) — `os.getenv(...)`,
    `os.environ.get(...)`, `os.environ[...]`, `getenv(...)` из `from os import getenv`;
  * в `services/*/app/main.py` — те же конструкции в любом месте файла: там уже есть
    объект `settings`, второй путь к тем же значениям не нужен;
  * где угодно в `services/**` вне `settings.py` — если места нет в
    `config/settings-allowlist.txt` (карта t_c8d875f6). Allowlist хранит путь, число
    совпадений и причину; мёртвая запись (файла нет, чтений стало меньше) — тоже ошибка.

Разрешено: передать `os.environ` целиком (например, `subprocess.run(env={**os.environ})`)
и читать окружение в модулях из allowlist (`files_store`, `llm`, `cold`, `agent`,
`api/admin`) — они читают свои ключи в момент вызова, а не на импорте; разбор —
отдельными картами. `version`, `ui_texts`, `single_instance` на settings уже переведены.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import subprocess
import sys
import typing

import pytest

from scripts import check_settings

ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"
GATEWAY_APP = SERVICES / "tg-gateway" / "app"
SETTINGS_FILES = {
    SERVICES / "tool-api" / "app" / "settings.py",
    SERVICES / "tg-gateway" / "app" / "settings.py",
}
MAIN_FILES = sorted(SERVICES.glob("*/app/main.py"))
# gateway после разреза R-2: main.py — только сборка, объект settings живёт в config.py
GATEWAY_MAIN = SERVICES / "tg-gateway" / "app" / "main.py"
SETTINGS_CARRIERS = [p for p in MAIN_FILES if p != GATEWAY_MAIN] + [
    SERVICES / "tg-gateway" / "app" / "config.py"]
SERVICE_FILES = sorted(path for path in SERVICES.rglob("*.py") if SETTINGS_FILES.isdisjoint({path}))


def _is_env_read(node: ast.AST) -> bool:
    """Чтение значения из окружения (а не передача `os.environ` целиком)."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "getenv":
            return True
        if isinstance(func, ast.Attribute):
            if func.attr == "getenv":
                return True
            inner = func.value
            return (func.attr == "get" and isinstance(inner, ast.Attribute)
                    and inner.attr == "environ")
    if isinstance(node, ast.Subscript):
        value = node.value
        return isinstance(value, ast.Attribute) and value.attr == "environ"
    return False


class _EnvReads(ast.NodeVisitor):
    def __init__(self) -> None:
        self.module_level: list[tuple[int, str]] = []
        self.anywhere: list[tuple[int, str]] = []
        self._depth = 0

    def _visit_function(self, node: ast.AST) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function
    visit_Lambda = _visit_function

    def _record(self, node: ast.AST) -> None:
        text = ast.unparse(node)
        self.anywhere.append((node.lineno, text))
        if self._depth == 0:
            self.module_level.append((node.lineno, text))

    def visit_Call(self, node: ast.Call) -> None:
        if _is_env_read(node):
            self._record(node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_env_read(node):
            self._record(node)
        self.generic_visit(node)


def _reads(path: pathlib.Path) -> _EnvReads:
    finder = _EnvReads()
    finder.visit(ast.parse(path.read_text(encoding="utf-8")))
    return finder


def _format(path: pathlib.Path, found: list[tuple[int, str]]) -> str:
    return "\n".join(f"{path.relative_to(ROOT)}:{line}: {text}" for line, text in found)


def test_на_уровне_модуля_окружение_не_читается() -> None:
    """Умолчание, прочитанное на импорте, незаметно уезжает в прод (R-4)."""
    offenders = []
    for path in SERVICE_FILES:
        found = _reads(path).module_level
        if found:
            offenders.append(_format(path, found))
    assert not offenders, (
        "чтение окружения на уровне модуля — только в settings.py:\n" + "\n".join(offenders))


def test_в_main_нет_второго_пути_к_настройкам() -> None:
    """В main.py значения берутся только из объекта settings."""
    assert MAIN_FILES, "не найдены main.py сервисов"
    offenders = []
    for path in MAIN_FILES:
        found = _reads(path).anywhere
        if found:
            offenders.append(_format(path, found))
    assert not offenders, (
        "в main.py не должно быть чтения окружения, есть объект settings:\n"
        + "\n".join(offenders))


@pytest.mark.parametrize("path", sorted(SETTINGS_FILES), ids=lambda p: p.parent.parent.name)
def test_settings_собирает_все_переменные(path: pathlib.Path) -> None:
    """Один объект Settings и один вход: from_env()."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "Settings"]
    assert len(classes) == 1, f"{path}: ожидался один класс Settings"
    methods = {node.name for node in classes[0].body if isinstance(node, ast.FunctionDef)}
    assert "from_env" in methods, f"{path}: у Settings нет from_env()"


def _settings_owner(path: pathlib.Path) -> str | None:
    """Модуль сервиса, который берёт `Settings` из settings.py (у tool-api — core.py)."""
    for candidate in sorted(path.parent.glob("*.py")):
        tree = ast.parse(candidate.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom)
                    and (node.module or "").split(".")[-1] == "settings"
                    and any(alias.name == "Settings" for alias in node.names)):
                return candidate.stem
    return None


@pytest.mark.parametrize("path", SETTINGS_CARRIERS, ids=lambda p: p.parent.parent.name + "-" + p.stem)
def test_main_берёт_настройки_из_settings(path: pathlib.Path) -> None:
    """Настройки в сервисе берутся из одного модуля: settings.py и его владелец.

    После этапа B tool-api держит конфигурацию в `app/core.py` (main.py — фабрика),
    поэтому main.py обязан брать значения у владельца, а не читать их сам.
    """
    owner = _settings_owner(path)
    assert owner, f"{path}: в сервисе нет модуля, берущего Settings из settings.py"
    imported = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[-1])
    assert owner == path.stem or owner in imported, (
        f"{path}: main.py не берёт настройки у {owner}.py")


# --- тесты сами не подменяют окружение --------------------------------------

TEST_FILES = sorted((ROOT / "tests").rglob("*.py"))


class _EnvWrites(ast.NodeVisitor):
    """Запись в `os.environ` на уровне модуля теста (подмена «до импорта»)."""

    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []
        self._depth = 0

    def _visit_function(self, node: ast.AST) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function
    visit_Lambda = _visit_function

    def _is_environ(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == "environ"

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._depth == 0:
            for target in node.targets:
                if isinstance(target, ast.Subscript) and self._is_environ(target.value):
                    self.found.append((node.lineno, ast.unparse(node)))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (self._depth == 0 and isinstance(func, ast.Attribute)
                and func.attr in {"setdefault", "update", "putenv", "setenv"}
                and (self._is_environ(func.value)
                     or (isinstance(func.value, ast.Name) and func.value.id == "os"))):
            self.found.append((node.lineno, ast.unparse(node)))
        self.generic_visit(node)


def test_тесты_не_подменяют_окружение_до_импорта() -> None:
    """Настройки передаются явно (`configure(Settings(...))`), а не через `os.environ`."""
    offenders = []
    for path in TEST_FILES:
        finder = _EnvWrites()
        finder.visit(ast.parse(path.read_text(encoding="utf-8")))
        offenders.extend(_format(path, finder.found).splitlines())
    assert not offenders, (
        "тесты не выставляют окружение на уровне модуля — передайте Settings явно:\n"
        + "\n".join(offenders))


# --- семантика самих настроек ------------------------------------------------

def _load_gateway_settings():
    path = GATEWAY_APP / "settings.py"
    name = "gw_settings_under_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclass с `from __future__ import annotations` ищет модуль в sys.modules
    sys.modules[name] = module
    # бутстрап гейтвея импортируется по имени: каталог app должен быть в sys.path
    sys.path.insert(0, str(GATEWAY_APP))
    spec.loader.exec_module(module)
    return module.Settings


def test_пустое_окружение_даёт_локальные_умолчания() -> None:
    from app.settings import Settings  # noqa: PLC0415

    cfg = Settings.from_env({})
    assert cfg.env_name == "dev"
    assert cfg.tz_offset_hours == 4.0
    assert isinstance(cfg.tz_offset_hours, float)
    assert cfg.tz.utcoffset(None).total_seconds() == 4 * 3600
    assert cfg.tool_api_token == "dev-token"
    assert cfg.database_url.startswith("postgresql://")
    assert cfg.sheet_write_enabled is False          # dev: запись в лист выключена
    assert cfg.webapp_secret_effective == "dev-token"


def test_прод_без_сервисного_токена_не_запускается() -> None:
    from app.settings import Settings  # noqa: PLC0415

    with pytest.raises(RuntimeError):
        Settings(env_name="prod")
    with pytest.raises(RuntimeError):
        Settings.from_env({"ENV": "prod"})
    assert Settings.from_env({"ENV": "prod", "TOOL_API_TOKEN": "t"}).is_prod


def test_запись_в_лист_включается_явно() -> None:
    from app.settings import Settings  # noqa: PLC0415

    assert Settings.from_env({"ENV": "dev", "ALLOW_DEV_SHEET_WRITE": "1"}).sheet_write_enabled
    assert Settings.from_env({"ENV": "prod", "TOOL_API_TOKEN": "t"}).sheet_write_enabled


def test_значения_читаются_из_окружения() -> None:
    from app.settings import Settings  # noqa: PLC0415

    cfg = Settings.from_env({
        "ENV": "test", "TOOL_API_TOKEN": "tok", "ADMIN_CHAT_ID": "42",
        "TZ_OFFSET_HOURS": "5", "INVITE_TTL_DAYS": "3", "WEBAPP_SECRET": "sec",
        "CALENDAR_ID_DRAFT": "draft", "SCHEDULE_SHEET_TITLE": "Расписание",
    })
    assert (cfg.env_name, cfg.tool_api_token, cfg.admin_chat_id) == ("test", "tok", 42)
    assert cfg.tz_offset_hours == 5 and cfg.invite_ttl_days == 3
    assert cfg.webapp_secret_effective == "sec"
    assert cfg.calendar_id_draft == "draft" and cfg.schedule_sheet_title == "Расписание"


def test_гейтвей_имеет_свои_умолчания() -> None:
    Settings = _load_gateway_settings()  # noqa: N806
    cfg = Settings.from_env({})
    assert cfg.tool_api_url == "http://127.0.0.1:8000"
    assert cfg.tool_api_token == "dev-token"
    assert cfg.local_tz.utcoffset(None).total_seconds() == 4 * 3600
    assert Settings.from_env({"TZ_OFFSET_HOURS": "3.5"}).local_tz.utcoffset(None).total_seconds() \
        == 3.5 * 3600
    # пустое значение больше не роняет гейтвей: берётся общее умолчание (решение 131)
    empty = Settings.from_env({"TZ_OFFSET_HOURS": ""})
    assert empty.tz_offset_hours == 4.0
    assert empty.local_tz.utcoffset(None).total_seconds() == 4 * 3600


# --- общие настройки: одно объявление на два сервиса (решение 131, план 133) --

SERVICES_UNDER_TEST = ["tool-api", "tg-gateway"]


def _settings_of(service: str) -> tuple[type, str]:
    """`Settings` сервиса и имя свойства часового пояса в нём."""
    if service == "tool-api":
        from app.settings import Settings  # noqa: PLC0415

        return Settings, "tz"
    return _load_gateway_settings(), "local_tz"


@pytest.mark.parametrize("service", SERVICES_UNDER_TEST)
def test_подчасовое_смещение_одинаково_в_двух_сервисах(service: str) -> None:
    """`TZ_OFFSET_HOURS=4.5` роняло tool-api (`int`), теперь оба читают дробное."""
    Settings, tz_attr = _settings_of(service)  # noqa: N806

    cfg = Settings.from_env({"TZ_OFFSET_HOURS": "4.5"})
    assert cfg.tz_offset_hours == 4.5
    assert isinstance(cfg.tz_offset_hours, float)
    assert getattr(cfg, tz_attr).utcoffset(None).total_seconds() == 16200


@pytest.mark.parametrize("service", SERVICES_UNDER_TEST)
def test_пустое_смещение_даёт_умолчание_в_двух_сервисах(service: str) -> None:
    """Пустое `TZ_OFFSET_HOURS=` роняло гейтвей; умолчание теперь общее — 4.0."""
    Settings, _ = _settings_of(service)  # noqa: N806

    cfg = Settings.from_env({"TZ_OFFSET_HOURS": ""})
    assert cfg.tz_offset_hours == 4.0
    assert isinstance(cfg.tz_offset_hours, float)


@pytest.mark.parametrize("service", SERVICES_UNDER_TEST)
@pytest.mark.parametrize("value", ["четыре", "4,5"])
def test_мусор_в_смещении_называет_переменную(service: str, value: str) -> None:
    """Мусор — ошибка запуска с именем переменной, а не тихое умолчание."""
    Settings, _ = _settings_of(service)  # noqa: N806

    with pytest.raises(ValueError, match="TZ_OFFSET_HOURS"):
        Settings.from_env({"TZ_OFFSET_HOURS": value})


def test_разбор_окружения_определён_один_раз() -> None:
    """`_env` жил в двух копиях, а тип смещения расходился (решение 131)."""
    definitions: dict[str, list[str]] = {}
    for path in sorted(SERVICES.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                definitions.setdefault(node.name, []).append(str(path.relative_to(ROOT)))

    shared = "services/shared/settings.py"
    for name in ("env_str", "env_int", "env_float"):
        assert definitions.get(name) == [shared], (
            f"{name} обязан быть определён один раз в {shared}, а не в {definitions.get(name)}")

    for path in sorted(SETTINGS_FILES):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        local = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        assert not local & {"_env", "_int_env", "env_str", "env_int", "env_float"}, (
            f"{path.relative_to(ROOT)}: разбор окружения снова завёлся в самом сервисе")
        casts = [ast.unparse(node) for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id in {"int", "float"}]
        assert not casts, (
            f"{path.relative_to(ROOT)}: тип приводится мимо env_int/env_float: {casts}")


@pytest.mark.parametrize("service", SERVICES_UNDER_TEST)
def test_общая_база_в_mro_обоих_сервисов(service: str) -> None:
    """Общие поля объявлены в BaseSettings, а не скопированы в каждый сервис."""
    from services.shared.settings import BaseSettings  # noqa: PLC0415

    Settings, _ = _settings_of(service)  # noqa: N806

    assert BaseSettings in Settings.__mro__
    assert "tz_offset_hours" in Settings.__dataclass_fields__
    assert typing.get_type_hints(Settings)["tz_offset_hours"] is float


def test_прод_правило_токена_действует_только_в_tool_api() -> None:
    """Асимметрия сервисов зафиксирована как есть: прод-правило есть только у tool-api."""
    from app.settings import Settings  # noqa: PLC0415

    with pytest.raises(RuntimeError):
        Settings(env_name="prod")

    Gateway = _load_gateway_settings()  # noqa: N806
    assert Gateway.from_env({"ENV": "prod"}).tool_api_token == "dev-token"


# --- allowlist: чтение только через settings, исключения — с причиной ---------
#
# Карта t_c8d875f6: правило проверяет scripts/check_settings.py (он же шаг CI),
# а исключения — config/settings-allowlist.txt. Здесь проверяется и сам страж:
# фикстурные деревья показывают, что каждая ошибка списка действительно роняет
# проверку, иначе список тихо сгниёт до «всё разрешено».

BAD_SOURCE = """import os


def endpoint():
    return os.getenv("STORAGE_ENDPOINT", "")
"""


def _tree(tmp_path: pathlib.Path, files: dict[str, str], allowlist: str = "") -> pathlib.Path:
    """Фикстурное дерево: services/** и config/settings-allowlist.txt."""
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    allow = tmp_path / "config" / "settings-allowlist.txt"
    allow.parent.mkdir(parents=True, exist_ok=True)
    allow.write_text(allowlist, encoding="utf-8")
    return allow


def test_чтение_вне_allowlist_называет_файл_и_строку(tmp_path):
    allow = _tree(tmp_path, {"services/demo/app/bad.py": BAD_SOURCE})
    issues = check_settings.problems(tmp_path, allow)
    assert len(issues) == 1
    assert ("читай конфиг через settings, файл services/demo/app/bad.py, строка 5"
            in issues[0])


def test_allowlist_с_причиной_пропускает_старое_чтение(tmp_path):
    allow = _tree(
        tmp_path,
        {"services/demo/app/bad.py": BAD_SOURCE},
        "services/demo/app/bad.py 1  # адаптер хранилища, разбор отдельной картой\n",
    )
    assert check_settings.problems(tmp_path, allow) == []


def test_запись_без_причины_не_принимается(tmp_path):
    allow = _tree(
        tmp_path,
        {"services/demo/app/bad.py": BAD_SOURCE},
        "services/demo/app/bad.py 1\n",
    )
    assert any("нет причины" in issue for issue in check_settings.problems(tmp_path, allow))


def test_удалённый_файл_оставляет_мёртвую_запись(tmp_path):
    allow = _tree(
        tmp_path,
        {"services/demo/app/ok.py": "VALUE = 1\n"},
        "services/demo/app/gone.py 3  # когда-то читал ключи\n",
    )
    issues = check_settings.problems(tmp_path, allow)
    assert len(issues) == 1 and "мёртвая запись" in issues[0]
    assert "services/demo/app/gone.py" in issues[0]


def test_меньше_совпадений_чем_разрешено_роняет_проверку(tmp_path):
    """Модуль перевели на settings, а строку в списке не поправили — список гниёт."""
    allow = _tree(
        tmp_path,
        {"services/demo/app/bad.py": BAD_SOURCE},
        "services/demo/app/bad.py 2  # два ключа\n",
    )
    issues = check_settings.problems(tmp_path, allow)
    assert len(issues) == 1 and "мёртвая запись" in issues[0]
    assert "разрешено 2" in issues[0]


def test_лишнее_чтение_в_разрешённом_файле_видно_по_строке(tmp_path):
    allow = _tree(
        tmp_path,
        {"services/demo/app/bad.py": BAD_SOURCE},
        "services/demo/app/bad.py 1  # одно чтение\n",
    )
    extra = BAD_SOURCE + "\n\ndef other():\n    return os.environ.get(\"X\")\n"
    (tmp_path / "services/demo/app/bad.py").write_text(extra, encoding="utf-8")
    issues = check_settings.problems(tmp_path, allow)
    assert any("читай конфиг через settings, файл services/demo/app/bad.py, строка 9" in issue
               for issue in issues)


def test_settings_py_и_докстринги_не_считаются(tmp_path):
    """settings.py читает окружение по определению, упоминание в докстринге — не чтение."""
    allow = _tree(tmp_path, {
        "services/demo/app/settings.py": "import os\n\nVALUE = os.getenv(\"X\", \"\")\n",
        "services/demo/app/memo.py": '"""Раньше здесь был os.getenv("X"), теперь settings."""\n',
    })
    assert check_settings.problems(tmp_path, allow) == []


def test_окружение_целиком_переданное_в_env_считается_чтением(tmp_path):
    """`env={**os.environ}` — тоже место, откуда конфиг попадает мимо settings."""
    allow = _tree(tmp_path, {
        "services/demo/app/run.py": (
            "import os\nimport subprocess\n\n\n"
            "def run():\n    subprocess.run([\"ls\"], env={**os.environ})\n"),
    })
    issues = check_settings.problems(tmp_path, allow)
    assert len(issues) == 1 and "services/demo/app/run.py, строка 6" in issues[0]


def test_текущее_дерево_чистое():
    """Новое чтение окружения в services/** роняет этот тест и шаг CI."""
    assert check_settings.problems(check_settings.ROOT, check_settings.ALLOWLIST) == []


def test_allowlist_состоит_из_живых_записей_с_причиной():
    entries = check_settings.read_allowlist(check_settings.ALLOWLIST)
    assert entries, "allowlist пуст: перевод на settings не должен стирать историю"
    assert all(entry.has_reason for entry in entries)
    assert {entry.path for entry in entries} == set(check_settings.scan(check_settings.ROOT))


def test_скрипт_даёт_код_0_на_текущем_дереве():
    done = subprocess.run(
        [sys.executable, str(check_settings.ROOT / "scripts" / "check_settings.py")],
        cwd=check_settings.ROOT, capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    assert check_settings.main([]) == 0


def test_проверка_включена_в_ci():
    workflow = (check_settings.ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8")
    lint = workflow.split("  tests:", 1)[0]
    assert "scripts/check_settings.py" in lint, "шаг проверки конфига не попал в job lint"


# --- три модуля читают значения из settings ----------------------------------

def test_версия_берёт_sha_из_settings(monkeypatch, tmp_path):
    """GIT_SHA читается в settings, поведение прежнее: короткий sha сборки."""
    from app.version import read_git_sha  # noqa: PLC0415

    monkeypatch.setenv("GIT_SHA", "191199a0000000000000000000000000")
    assert read_git_sha(tmp_path) == "191199a00000"


def test_тексты_берут_путь_из_settings(monkeypatch, tmp_path):
    """UI_TEXTS_PATH читается в settings, поведение прежнее: путь из переменной."""
    from app import ui_texts  # noqa: PLC0415

    target = tmp_path / "uii.yaml"
    monkeypatch.setenv("UI_TEXTS_PATH", str(target))
    assert ui_texts.path() == target
    monkeypatch.delenv("UI_TEXTS_PATH", raising=False)
    assert ui_texts.path() == ui_texts.DEFAULT_PATH
