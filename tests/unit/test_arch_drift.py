"""Замер дрейфа архитектуры: каждая метрика ловит свой дефект (docs/development/26 §7c).

Тесты работают на фикстуре репозитория в `tmp_path`: дефект подкладывается по одному,
чтобы метрика доказывала, что видит именно его, а не соседний шум. Чистое дерево
фикстуры даёт ноль находок — иначе метрика бесполезна в еженедельном отчёте.
"""

import importlib.util
import json
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]


def _load():
    path = PROJECT / "scripts" / "arch_drift.py"
    spec = importlib.util.spec_from_file_location("arch_drift", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIFT = _load()


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def metric(metrics, metric_id):
    return next(item for item in metrics if item["id"] == metric_id)


@pytest.fixture()
def clean(tmp_path: Path) -> Path:
    """Чистое дерево: маленькие модули, две миграции, защищённый маршрут, тест с assert."""
    write(tmp_path, "services/tool-api/app/api/tools.py", """
from fastapi import APIRouter

router = APIRouter()


@router.post("/tools/echo")
def echo(who: dict = Depends(core.current)) -> dict:
    return {"ok": True}
""".replace("from fastapi import APIRouter", "from fastapi import APIRouter, Depends"))
    write(tmp_path, "services/tg-gateway/app/bot.py", "VALUE = 1\n")
    write(tmp_path, "services/shared/format.py", "def fmt(value):\n    return str(value)\n")
    write(tmp_path, "db/init/001_init.sql", "select 1;\n")
    write(tmp_path, "db/init/002_next.sql", "select 2;\n")
    write(tmp_path, "tests/unit/test_ok.py", "def test_ok():\n    assert 1 + 1 == 2\n")
    return tmp_path


# --- размеры файлов ---------------------------------------------------------

def test_файл_больше_500_строк_найден(tmp_path):
    write(tmp_path, "services/tool-api/app/big.py", "x = 1\n" * 501)
    metrics = DRIFT.measure_file_sizes(tmp_path)
    assert metric(metrics, "files_over_500")["value"] == 1
    assert "big.py" in metric(metrics, "files_over_500")["details"][0]


def test_маленький_файл_не_шумит(clean):
    metrics = DRIFT.measure_file_sizes(clean)
    assert metric(metrics, "files_over_500")["value"] == 0
    assert metric(metrics, "files_over_800")["value"] == 0


def test_файл_больше_800_считается_отдельно(tmp_path):
    write(tmp_path, "services/tool-api/app/huge.py", "x = 1\n" * 801)
    metrics = DRIFT.measure_file_sizes(tmp_path)
    assert metric(metrics, "files_over_800")["value"] == 1


# --- дубли функций ----------------------------------------------------------

DUPLICATE_BODY = """
def helper(value):
    first = str(value or "")
    second = first.strip()
    return second or "—"
"""


def test_дубль_функции_внутри_сервиса_найден(tmp_path):
    write(tmp_path, "services/tool-api/app/a.py", DUPLICATE_BODY)
    write(tmp_path, "services/tool-api/app/b.py", DUPLICATE_BODY)
    result = DRIFT.measure_duplicate_functions(tmp_path)
    assert result["value"] == 1
    assert "helper" in result["details"][0]


def test_одинаковая_функция_в_двух_сервисах_не_дубль(tmp_path):
    write(tmp_path, "services/tool-api/app/a.py", DUPLICATE_BODY)
    write(tmp_path, "services/tg-gateway/app/b.py", DUPLICATE_BODY)
    assert DRIFT.measure_duplicate_functions(tmp_path)["value"] == 0


# --- импорты между сервисами ------------------------------------------------

def test_прямой_импорт_между_сервисами_найден(tmp_path):
    write(tmp_path, "services/tool-api/app/use.py", "import tg_gateway\n")
    write(tmp_path, "services/tg-gateway/app/bot.py", "VALUE = 1\n")
    result = DRIFT.measure_cross_service_imports(tmp_path)
    assert result["value"] == 1
    assert "tg_gateway" in result["details"][0]


def test_общая_библиотека_shared_не_считается_сервисом(tmp_path):
    write(tmp_path, "services/tool-api/app/use.py", "from services.shared import format\n")
    write(tmp_path, "services/shared/format.py", "def fmt(value):\n    return str(value)\n")
    assert DRIFT.measure_cross_service_imports(tmp_path)["value"] == 0


# --- чтения окружения -------------------------------------------------------

def test_getenv_вне_settings_найден(tmp_path):
    write(tmp_path, "services/tool-api/app/cold.py", "import os\nDIR = os.getenv('COLD')\n")
    result = DRIFT.measure_getenv(tmp_path)
    assert result["value"] == 1
    assert "cold.py" in result["details"][0]


def test_getenv_в_settings_разрешён(tmp_path):
    write(tmp_path, "services/tool-api/app/settings.py", "import os\nV = os.getenv('X')\n")
    assert DRIFT.measure_getenv(tmp_path)["value"] == 0


def test_getenv_в_allowlist_не_найден(tmp_path):
    write(tmp_path, "services/tool-api/app/cold.py", "import os\nDIR = os.getenv('COLD')\n")
    write(tmp_path, "config/settings-allowlist.txt",
          "# путь число # причина\nservices/tool-api/app/cold.py 1  # каталог читается адаптером\n")
    assert DRIFT.measure_getenv(tmp_path)["value"] == 0


# --- миграции ---------------------------------------------------------------

def test_дубль_номера_миграции_найден(tmp_path):
    write(tmp_path, "db/init/001_a.sql", "select 1;\n")
    write(tmp_path, "db/init/001_b.sql", "select 2;\n")
    metrics = DRIFT.measure_migrations(tmp_path)
    assert metric(metrics, "migration_duplicates")["value"] == 1


def test_разрыв_номеров_миграций_найден(tmp_path):
    write(tmp_path, "db/init/001_a.sql", "select 1;\n")
    write(tmp_path, "db/init/003_c.sql", "select 3;\n")
    assert metric(DRIFT.measure_migrations(tmp_path), "migration_gaps")["value"] == 1


def test_задокументированный_разрыв_не_найден(tmp_path):
    for number in list(range(1, 18)) + [19]:
        write(tmp_path, f"db/init/{number:03d}_step.sql", "select 1;\n")
    assert metric(DRIFT.measure_migrations(tmp_path), "migration_gaps")["value"] == 0


# --- публичные маршруты -----------------------------------------------------

PUBLIC_ROUTE = """
from fastapi import APIRouter

router = APIRouter()


@router.get("/tools/status")
def status() -> dict:
    return {"ok": True}
"""

PROTECTED_ROUTE = """
from fastapi import APIRouter, Depends

router = APIRouter()


@router.get("/tools/status")
def status(who: dict = Depends(core.current)) -> dict:
    return {"ok": True}
"""


def test_публичный_маршрут_без_пометки_найден(tmp_path):
    write(tmp_path, "services/tool-api/app/api/status.py", PUBLIC_ROUTE)
    result = DRIFT.measure_public_routes(tmp_path)
    assert result["value"] == 1
    assert "/tools/status" in result["details"][0]


def test_защищённый_маршрут_не_шумит(tmp_path):
    write(tmp_path, "services/tool-api/app/api/status.py", PROTECTED_ROUTE)
    assert DRIFT.measure_public_routes(tmp_path)["value"] == 0


def test_маршрут_в_реестре_публичных_не_шумит(tmp_path):
    write(tmp_path, "services/tool-api/app/api/status.py", PUBLIC_ROUTE)
    write(tmp_path, "config/public-routes.txt", "/tools/status # проверка живости сервиса\n")
    assert DRIFT.measure_public_routes(tmp_path)["value"] == 0


def test_публичный_маршрут_в_корневом_модуле_найден(tmp_path):
    """`/health` объявлен в `app/main.py`, а не в `api/`/`pages/`: скан обязан его видеть."""
    write(tmp_path, "services/tool-api/app/main.py", """
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health() -> dict:
    return {"ok": True}
""")
    result = DRIFT.measure_public_routes(tmp_path)
    assert result["value"] == 1
    assert "/health" in result["details"][0]


def test_скан_маршрутов_достаёт_корневой_модуль_текущего_дерева():
    """Страховка от сужения скана: маршрут `/health` из `app/main.py` виден на живом дереве."""
    declared = {route_path for route_path, _name, _line, _protected in DRIFT._declared_routes(PROJECT)}
    assert "/health" in declared


def test_мёртвая_запись_реестра_публичных_найдена(tmp_path):
    write(tmp_path, "config/public-routes.txt", "/tools/gone # когда-то был\n")
    result = DRIFT.measure_public_routes(tmp_path)
    assert result["value"] == 1
    assert "мёртвая" in result["details"][0]


# --- снимок OpenAPI ---------------------------------------------------------

def test_расхождение_снимка_найдено(tmp_path, monkeypatch):
    monkeypatch.setattr(DRIFT, "openapi_differences", lambda root: ["- /tools/x: удалено"])
    assert DRIFT.measure_openapi(tmp_path)["value"] == 1


def test_совпадающий_снимок_не_шумит(tmp_path, monkeypatch):
    monkeypatch.setattr(DRIFT, "openapi_differences", lambda root: [])
    assert DRIFT.measure_openapi(tmp_path)["value"] == 0


# --- тесты без assert -------------------------------------------------------

def test_тест_без_assert_найден(tmp_path):
    write(tmp_path, "tests/unit/test_empty.py", "def test_nothing():\n    pass\n")
    result = DRIFT.measure_tests_without_assert(tmp_path)
    assert result["value"] == 1
    assert "test_nothing" in result["details"][0]


def test_тест_с_assert_и_raises_не_шумит(tmp_path):
    write(tmp_path, "tests/unit/test_ok.py", """
import pytest


def test_assert():
    assert 1 == 1


def test_raises():
    with pytest.raises(ValueError):
        raise ValueError("x")
""")
    assert DRIFT.measure_tests_without_assert(tmp_path)["value"] == 0


def test_тест_с_объяснением_без_assert_принимается(tmp_path):
    write(tmp_path, "tests/unit/test_guard.py", """
def test_guard_passes():
    # guard пропускает незнакомую базу: решает вызывающий код
    guard("other")
""")
    assert DRIFT.measure_tests_without_assert(tmp_path)["value"] == 0


def test_фикстура_conftest_не_считается_тестом(tmp_path):
    write(tmp_path, "tests/conftest.py", "def test_db():\n    return 'dsn'\n")
    assert DRIFT.measure_tests_without_assert(tmp_path)["value"] == 0


# --- сводный замер, CLI, дельта --------------------------------------------

def _measure(clean, monkeypatch, **kwargs):
    monkeypatch.setattr(DRIFT, "openapi_differences", lambda root: [])
    return DRIFT.measure(clean, **kwargs)


def test_чистое_дерево_без_находок(clean, monkeypatch):
    data = _measure(clean, monkeypatch)
    assert data["over_threshold"] == []
    assert all(item["status"] == "ok" for item in data["metrics"])


def test_cli_печатает_таблицу_и_даёт_код_0(clean, monkeypatch, capsys):
    monkeypatch.setattr(DRIFT, "openapi_differences", lambda root: [])
    write(clean, "services/tool-api/app/big.py", "x = 1\n" * 501)
    code = DRIFT.main(["--root", str(clean), "--json"])
    captured = capsys.readouterr()
    assert code == 0
    data = json.loads(captured.out)
    assert "files_over_500" in data["over_threshold"]


def test_понижение_порога_помечает_метрику(clean, monkeypatch):
    data = _measure(clean, monkeypatch, thresholds={"public_routes_unmarked": -1})
    assert "public_routes_unmarked" in data["over_threshold"]


def test_неизвестный_id_порога_отвергается():
    with pytest.raises(ValueError):
        DRIFT.parse_thresholds(["nosuch=1"])


def test_дельта_из_прошлого_отчёта_разбирается():
    text = ("| Метрика | Сейчас | Порог | Статус |\n|---|---|---|---|\n"
            "| Файлы (`files_over_500`) | 7 | 0 | warn |\n"
            "| Дубли (`duplicate_functions`) | 2 | 0 | warn |\n")
    assert DRIFT.parse_report_values(text) == {"files_over_500": 7, "duplicate_functions": 2}


def test_отчёт_содержит_обязательные_разделы(clean, monkeypatch):
    data = _measure(clean, monkeypatch)
    text = DRIFT.render_report(data, previous={"files_over_500": 7}, proposals="1. Разрезать файл")
    for heading in ("## Снимок", "## Числа", "## Что изменилось", "## Предложения"):
        assert heading in text
    assert "1. Разрезать файл" in text


REPORTS = Path("docs") / "reports"
PREVIOUS_REPORT = REPORTS / f"{110}-backups.md"


def test_номер_отчёта_следующий_свободный(tmp_path):
    write(tmp_path, str(PREVIOUS_REPORT), "# Отчёт\n")
    assert DRIFT.next_doc_number(tmp_path) == 111


def test_отчёт_пишется_следующим_номером(tmp_path, monkeypatch):
    write(tmp_path, str(PREVIOUS_REPORT), "# Отчёт\n")
    monkeypatch.setattr(DRIFT, "_rebuild_index", lambda root: None)
    target = DRIFT.write_report(tmp_path, "# Дрейф\n", "2026-11-02")
    assert target.name == "111-arch-drift-2026-11-02.md"
    assert target.read_text(encoding="utf-8") == "# Дрейф\n"
