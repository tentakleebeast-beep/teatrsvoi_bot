"""
Общие фикстуры тестов (docs/development/16).

Принципы:
  * unit-тесты не ходят в БД и не поднимают FastAPI;
  * integration-тесты работают на отдельной БД `svoi_test`, схема — из db/init/*.sql;
  * каждая таблица очищается после теста, поэтому порядок тестов не важен.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys
from collections.abc import Iterator

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL_API = ROOT / "services" / "tool-api"
SCRIPTS = ROOT / "scripts"

# путь к пакету app (rules.py), к scripts/ (маска календаря, _cli) и к корню
sys.path.insert(0, str(TOOL_API))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

ADMIN_CHAT = 123456789
TOKEN = "test-token"

# Настройки приложения тестам передаются явно: `app.main.configure(Settings(...))`
# в фикстуре `api` (R-4, docs/reports/79 этап A). Подменять `os.environ` до импорта
# модуля больше не нужно — и нельзя: тест, выставивший окружение, влиял на соседей.

TABLES = (
    "agent_proposal",
    "feedback_answer", "feedback", "slot_proposal", "event_plan", "venue",
    "approval_request", "notification", "notification_pref",
    "intent_log", "category_candidate",
    "availability_hour", "dues_charge", "budget_tx", "budget_month_total",
    "row_map", "import_row", "import_batch", "audit_event", "message_event",
    "invite_attempt", "invite_code", "llm_usage", "participant_role",
    "participant_capability_override", "note",
    "access_grant", "agent_action", "file_tag", "file_folder", "file_text",
    "file_audio", "file_object", "tag", "folder",
    "participant", "person_alias",
)


def _base_dsn() -> str:
    return os.getenv("DATABASE_URL", "postgresql://svoi:svoi_dev_password@127.0.0.1:5433/svoi")


TEST_DB_NAME = f"svoi_test_{os.getpid()}"


def _test_dsn() -> str:
    dsn = _base_dsn()
    return dsn.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"


def service_today() -> dt.date:
    """«Сегодня» в зоне сервиса, а не процесса.

    API считает текущий день через `datetime.now(core.TZ)`, а `date.today()` в CI
    (UTC) вечером UTC расходится с зоной сервиса на сутки: тест кладёт ячейки на
    один день, а сводка отдаёт другой. Поэтому тесты, сверяющиеся с данными
    сервиса, берут дату отсюда (docs/development/26 §7a п.1).
    """
    from app import core  # noqa: PLC0415

    return dt.datetime.now(core.TZ).date()


def google_error(status: int = 503, message: str = "backendError") -> Exception:
    """Реальный отказ Google API для тестов.

    Адаптеры ловят `HttpError` (см. `app/external.py`), а не «любое исключение»:
    подменять отказ Google на `RuntimeError` больше нельзя — такая ошибка теперь
    честно доходит до конверта и превращается в 500.
    """
    import httplib2  # noqa: PLC0415
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    return HttpError(httplib2.Response({"status": status}), message.encode())


@pytest.fixture(autouse=True)
def _reset_agent_cache() -> Iterator[None]:
    """Кэш ответов агента живёт в памяти процесса (карта A-4).

    Без сброса один тест влиял бы на другой: «какой сегодня день», заданный дважды
    в разных тестах, во второй раз пришёл бы из кэша и тест проверял бы не то.
    Модуль импортируется только если тест его уже загрузил, — лишних импортов
    и побочных эффектов у unit-тестов не появляется.
    """
    cached_module = sys.modules.get("app.agently.cache")
    if cached_module is not None:
        cached_module.clear()
    yield
    cached_module = sys.modules.get("app.agently.cache")
    if cached_module is not None:
        cached_module.clear()


@pytest.fixture(scope="session")
def test_db() -> Iterator[str]:
    """Отдельная БД на процесс: параллельные прогоны (CI и задание планировщика) не мешают.

    Раньше все прогоны работали на одной `svoi_test`, и второй pytest дропал базу
    из-под первого — отсюда «288 ошибок» и случайные 403 в интеграционных тестах.
    """
    import psycopg

    base = _base_dsn()
    try:
        admin = psycopg.connect(base, autocommit=True)
    except psycopg.OperationalError as exc:
        dbname = base.rsplit("/", 1)[-1].split("?")[0]
        raise RuntimeError(
            f"нет соединения с {dbname!r}: похоже, DATABASE_URL остался переопределённым "
            f"от предыдущей команды (в оболочке он живёт между вызовами). "
            f"Восстановите: unset DATABASE_URL; set -a; . ./.env; set +a") from exc
    with admin as conn:
        conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity "
            "where datname = %s and pid <> pg_backend_pid()", (TEST_DB_NAME,))
        conn.execute(f'drop database if exists "{TEST_DB_NAME}"')
        conn.execute(f'create database "{TEST_DB_NAME}"')

    # Схема накатывается той же цепочкой, что на проде и в compose: Alembic поверх
    # db/init/*.sql (docs/reports/132). Прямой прогон файлов давал тестам схему,
    # которой нет в учёте версий, и расхождение с деплоем замечалось только на проде.
    # Адрес базы передаём через attributes, окружение не подменяем (правило выше).
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["url"] = _test_dsn()
    command.upgrade(cfg, "head")

    yield _test_dsn()

    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity "
            "where datname = %s and pid <> pg_backend_pid()", (TEST_DB_NAME,))
        conn.execute(f'drop database if exists "{TEST_DB_NAME}"')


@pytest.fixture(scope="session")
def api(test_db):
    """Приложение с тестовой БД. Настройки передаём явно, окружение не подменяем."""
    from app.main import Settings, app, configure  # noqa: PLC0415

    configure(Settings(
        database_url=test_db,
        admin_chat_id=ADMIN_CHAT,
        tool_api_token=TOKEN,
        env_name="test",
        budget_spreadsheet_id="",          # внешние сервисы в тестах не трогаем
    ))

    from fastapi.testclient import TestClient  # noqa: PLC0415

    # HTTPS-адрес, а не http://testserver: на проде Mini App живёт только по HTTPS,
    # и абсолютные http-ссылки должны падать уже в тестах (docs/infrastructure/75 §5.3)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture()
def client(api):
    """Клиент с чистыми таблицами перед каждым тестом."""
    import psycopg

    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        existing = {row[0] for row in conn.execute(
            "select tablename from pg_tables where schemaname = 'public'").fetchall()}
        for table in TABLES:
            if table in existing:
                conn.execute(f'delete from "{table}" where true')
        # корни общих папок — это состояние прода, а не мусор теста: возвращаем их после чистки
        for root in ("Хозяйство", "Соцсети", "Документы", "Спектакли"):
            conn.execute("select ensure_shared_folder(null, %s, %s)", (root, root))
        # справочник пространств восстанавливаем: он нужен почти всем сценариям
        conn.execute("""insert into venue (code, name, kind, sort_order) values
                          ('stage','Сцена','stage',10),
                          ('dressing','Гримёрка','room',20),
                          ('backstage','Подсобки','room',30)
                        on conflict (code) do nothing""")
        # действующий участник с ролью org — чтобы были возможности (RBAC)
        row = conn.execute("""insert into participant (tg_chat_id, display_name, person_key, role, status)
                              values (%s, 'Иванов Виктор', 'иванов', 'org', 'active')
                              on conflict (tg_chat_id) do update set status = 'active'
                              returning id""", (ADMIN_CHAT,)).fetchone()
        conn.execute("""insert into participant_role (participant_id, role) values (%s, 'org')
                        on conflict do nothing""", (row[0],))
        # точечная выдача: организатору нужен доступ к аудиту (проверяем и override)
        for capability in ("audit.view", "registration.code.issue", "feedback.triage"):
            conn.execute("""insert into participant_capability_override
                              (participant_id, capability, allowed, reason)
                            values (%s, %s, true, 'тестовая фикстура')
                            on conflict (participant_id, capability) do update set allowed = true""",
                         (row[0], capability))
    return api


@pytest.fixture()
def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(ADMIN_CHAT)}


def pytest_collection_modifyitems(config, items):
    """Маркер по каталогу: tests/unit → unit, tests/integration → integration."""
    for item in items:
        path = str(item.fspath)
        if "/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/integration/" in path:
            item.add_marker(pytest.mark.integration)
