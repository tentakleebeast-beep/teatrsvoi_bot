"""Обвязка сценариев слоя Б: стенд `svoi_smoke`, вход, журнал, уборка.

Тесты по умолчанию пропускаются — как браузерные `tests/webapp`:

    SVOI_SMOKE_TESTS=1 .venv/bin/python -m pytest tests/smoke -q --junitxml=smoke-junit.xml

Без переменной шаги показываются `skipped` с причиной «стенд не включён», и обычный
прогон `scripts/check_done.py` остаётся зелёным без поднятого стенда (К8). Это не
поблажка: смоук снаружи CI, он требует копию боевого дампа, второй tool-api и Garage.

Стенд поднимается отдельно (`docs/development/144`): база `svoi_smoke` из дампа прода,
`tool-api` на `:8011`, маркер прогона `SMOKE-YYYYMMDD-NN`. Тесты пишут только объекты
со своим маркером; живые `chat_id` и строка реальных людей — только чтение.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from helpers import Api, Journal, env, sign_init_data

ENABLED = os.getenv("SVOI_SMOKE_TESTS") == "1"
ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env.smoke"


def _load_env() -> None:
    """Подтянуть `.env.smoke`, если он есть: без файла переменные берутся из окружения."""
    if not ENV_FILE.is_file():
        return
    from dotenv import dotenv_values  # noqa: PLC0415 — нужен только при включённом стенде

    for key, value in dotenv_values(ENV_FILE, encoding="utf-8", interpolate=False).items():
        if key and key not in os.environ:
            os.environ[key] = value or ""


if ENABLED:
    _load_env()


def pytest_collection_modifyitems(config, items):
    if ENABLED:
        return
    skip = pytest.mark.skip(reason="стенд не включён: SVOI_SMOKE_TESTS=1")
    for item in items:
        if "tests/smoke" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


# --- стенд ---------------------------------------------------------------------

@pytest.fixture(scope="session")
def stand() -> dict:
    """Настройки стенда и его живость: адрес, токен, база, маркер прогона."""
    base_url = env("TOOL_API_URL", "http://127.0.0.1:8011")
    token = env("TOOL_API_TOKEN", "smoke-token")
    bot_token = env("DEV_BOT_TOKEN")
    dsn = env("SMOKE_DATABASE_URL") or env("DATABASE_URL")
    marker = env("SVOI_SMOKE_MARKER") or f"SMOKE-{datetime.now():%Y%m%d}-01"
    output = Path(env("SVOI_SMOKE_SCENARIOS", str(ROOT / "smoke-scenarios.json")))
    if not (dsn and "svoi_smoke" in dsn):
        pytest.fail("стенд смоука: DATABASE_URL должен указывать на базу svoi_smoke")
    probe = Api(base_url, token).get("/health")
    if probe.code != 200:
        pytest.fail(f"стенд не поднят: {probe.evidence()} — см. docs/development/144")
    settings = {"base_url": base_url, "token": token, "bot_token": bot_token, "dsn": dsn,
                "marker": marker, "output": output, "health": probe.body}
    yield settings


@pytest.fixture(scope="session")
def db(stand):
    """Соединение со стендом: только чтение и только `svoi_smoke`."""
    import psycopg  # noqa: PLC0415

    with psycopg.connect(stand["dsn"], autocommit=True) as conn:
        conn.execute("set session characteristics as transaction read only")
        yield conn


@pytest.fixture(scope="session")
def admin(stand) -> Api:
    """Клиент владельца: админ берётся из копии боевого дампа по роли, а не по имени."""
    import psycopg  # noqa: PLC0415

    with psycopg.connect(stand["dsn"], autocommit=True) as conn:
        row = conn.execute("""select tg_chat_id from participant
                               where role = 'admin' and status = 'active'
                                 and tg_chat_id is not null
                               order by id limit 1""").fetchone()
    if row is None:
        pytest.fail("на стенде нет активного админа с chat_id")
    return Api(stand["base_url"], stand["token"], int(row[0]))


def chat_id(stand: dict, offset: int) -> int:
    """Тестовый chat_id: номер прогона в маркере + смещение роли.

    Живые люди исключены по построению: 900 000 0NN — заведомо не Telegram-id, а разные
    прогоны в один день (`-01`, `-02`) не делят ни одного адресата.
    """
    number = int(stand["marker"].rsplit("-", 1)[-1])
    return 900_000_000 + number * 10 + offset


@pytest.fixture(scope="session")
def people(stand, admin) -> dict:
    """Три смоук-человека: труппа, организатор, педагог — с маркером в имени.

    Создаются тем же путём, что у людей: приглашение админа → регистрация по коду.
    Роль `master` выдаётся отдельно (`POST /admin/grant`) — она не выбирается при
    регистрации, и это же доказывает FR-A6.
    """
    created: dict[str, dict] = {}
    suffix = stand["marker"]

    def register(role: str, name: str, chat_id: int) -> dict:
        invite = admin.post("/admin/invite", body={"role": role, "ttl_days": 1, "max_uses": 1})
        if not invite.ok:
            pytest.fail(f"приглашение {role}: {invite.evidence()}")
        code = invite.field("code")
        registered = Api(stand["base_url"], stand["token"]).post(
            "/tools/participants/register",
            body={"chat_id": chat_id, "code": code, "display_name": f"{name} {suffix}"})
        if not registered.ok:
            pytest.fail(f"регистрация {name}: {registered.evidence()}")
        person = registered.field("participant") or {}
        return {"chat_id": chat_id, "role": role, "name": f"{name} {suffix}",
                "participant_id": person.get("id")}

    base_chat = chat_id(stand, 0)
    created["troupe"] = register("troupe", "Смоук Актёр", base_chat + 1)
    created["org"] = register("org", "Смоук Организатор", base_chat + 2)
    created["master"] = register("troupe", "Смоук Педагог", base_chat + 3)
    grant = admin.post("/admin/grant", body={
        "participant_id": created["master"]["participant_id"], "role": "master",
        "enabled": True, "disciplines": ["acting"],
        "reason": f"смоук: педагог стенда {suffix}"})
    if not grant.ok:
        pytest.fail(f"выдача роли master: {grant.evidence()}")
    yield created
    # Уборку делает scripts/smoke_data.py (runbook §5), а не фикстура: в отчёте должно
    # быть видно код возврата скрипта, а не «тест прибрался сам».
    _ = stand


def _client(stand, person: dict) -> Api:
    return Api(stand["base_url"], stand["token"], person["chat_id"])


@pytest.fixture()
def org(stand, people) -> Api:
    return _client(stand, people["org"])


@pytest.fixture()
def troupe(stand, people) -> Api:
    return _client(stand, people["troupe"])


@pytest.fixture()
def master(stand, people) -> Api:
    return _client(stand, people["master"])


@pytest.fixture(scope="session")
def webapp_login(stand, admin):
    """Вход в Mini App: подписанный initData → токен. Тот же путь, что у людей."""
    def login(chat_id: int) -> Api:
        if not stand["bot_token"]:
            pytest.skip("DEV_BOT_TOKEN не задан: вход в Mini App не проверить")
        init_data = sign_init_data(chat_id, stand["bot_token"])
        response = Api(stand["base_url"], stand["token"]).post(
            "/webapp/auth", body={"init_data": init_data})
        if not response.ok:
            pytest.fail(f"вход в Mini App: {response.evidence()}")
        return Api(stand["base_url"], stand["token"], chat_id,
                   webapp_token=response.field("token"))
    _ = admin
    return login


@pytest.fixture()
def journal(request, stand):
    """Журнал шагов сценария: метаданные берутся из модуля, шаги — из теста."""
    scenario = getattr(request.module, "SCENARIO", None)
    if not scenario:
        pytest.fail("в модуле сценария нет SCENARIO: id, title, roles, domain")
    run = {"marker": stand["marker"], "date": datetime.now().date().isoformat(),
           "commit": env("SVOI_SMOKE_COMMIT"), "env": "стенд svoi_smoke"}
    item = Journal(scenario, stand["output"], run)
    yield item
    item.finish()


# --- помощники сценариев --------------------------------------------------------

def future_date(days: int = 60) -> str:
    """Дата для занятости и событий: дальняя и явная, без «сегодня» (§5 п.1)."""
    return (datetime.now() + timedelta(days=days)).date().isoformat()


def month_period(days: int = 60) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m")


def dump_json(data) -> str:
    return json.dumps(data, ensure_ascii=False)
