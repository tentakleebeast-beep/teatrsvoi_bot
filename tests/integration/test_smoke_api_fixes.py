"""Регрессии по итогам API-смоука (docs/reports/62) и второго смоука (docs/reports/59, C-10).

Каждый тест — одна находка; номер находки в имени теста или в комментарии.
"""

import psycopg
import pytest
from app.person_key import make_key

from tests.conftest import ADMIN_CHAT, _test_dsn
from tests.integration.test_webapp_profile import _api, init_data

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(ADMIN_CHAT)}


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


# --- общий контракт входа (S-7, S-11, S-12) ----------------------------------

def test_s7_nan_в_теле_даёт_422_а_не_500(client):
    r = client.post("/tools/schedule/slots", headers={**HEADERS, "Content-Type": "application/json"},
                    content='{"min_hours": NaN}')
    assert r.status_code == 422
    r = client.post("/tools/budget/commit", headers={**HEADERS, "Content-Type": "application/json"},
                    content='{"chat_id": 1, "amount": Infinity, "idempotency_key": "k"}')
    assert r.status_code == 422


def test_s11_предел_длины_текста(client):
    """S-11: слишком длинный текст — 422; размер сообщения Telegram (4096) проходит."""
    r = client.post("/tools/schedule/slots", headers=HEADERS,
                    json={"text": "я" * 100_000, "block_events": False})
    assert r.status_code == 422
    assert "длиннее" in r.text
    ok = client.post("/tools/budget/parse", headers=HEADERS,
                     json={"text": "3000 " + "а" * 4090})
    assert ok.status_code != 422


def test_s12_nul_в_тексте_отклоняется(client):
    r = client.post("/tools/events/preview", headers=HEADERS,
                    json={"kind": "rehearsal", "date": "2026-10-29", "from_hour": 20,
                          "to_hour": 22, "title": "a\u0000b"})
    assert r.status_code == 422
    assert "NUL" in r.text


def test_nul_во_вложенном_поле_тоже_ловится(client):
    r = client.post("/tools/schedule/slots", headers=HEADERS,
                    json={"project": "x\u0000", "block_events": False})
    assert r.status_code == 422
    r = client.post("/tools/events/preview", headers=HEADERS,
                    json={"kind": "rehearsal", "chosen": {"витя": 1}, "title": ["a\u0000"]})
    assert r.status_code == 422


def test_все_входные_модели_наследуют_общий_контракт(api):
    """Инвариант: новая модель без ApiIn вернула бы S-7/S-11/S-12 для своего эндпоинта.

    После этапа B модели разложены по доменам (`app/api/*.py`), общая база — в ядре.
    """
    import importlib

    from app import core
    from pydantic import BaseModel

    names = ("admin", "budget", "events", "files", "finance", "people", "schedule",
             "webapp_api")
    modules = [core] + [importlib.import_module(f"app.api.{name}") for name in names]

    models = {}
    for module in modules:
        for name, obj in vars(module).items():
            if (isinstance(obj, type) and issubclass(obj, BaseModel)
                    and obj is not BaseModel and name.endswith("In")):
                models[obj.__name__] = obj
    assert models, "модели не найдены — тест ничего не проверяет"
    orphans = [name for name, model in models.items() if not issubclass(model, core.ApiIn)]
    assert orphans == []


# --- перечисления и значения по умолчанию (S-9, S-10, S-13, S-14) ------------

@pytest.mark.parametrize("field,value", [("policy", "garbage"), ("mode", "garbage")])
def test_s9_неизвестное_значение_перечисления_отклоняется(client, field, value):
    """S-9: закрытый список — неизвестное значение отвергается, а не подменяется."""
    r = client.post("/tools/schedule/slots", headers=HEADERS,
                    json={field: value, "block_events": False})
    assert r.status_code == 422
    if field == "policy":
        assert "unknown_policy" in r.text


def test_s9_регистр_и_пустая_строка_не_ломают_запрос(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 789101, "Регистр Режима")
    r = client.post("/tools/schedule/slots", headers=HEADERS,
                    json={"mode": "MAX_FREE", "policy": " Show ", "participants": [pid],
                          "block_events": False})
    assert r.status_code == 200, r.text
    r = client.post("/tools/schedule/slots", headers=HEADERS,
                    json={"policy": "", "participants": [pid], "block_events": False})
    assert r.status_code == 200, r.text


def test_s10_нулевая_длительность_не_превращается_в_два_часа(client):
    r = client.post("/tools/events/preview", headers=HEADERS,
                    json={"kind": "rehearsal", "date": "2026-10-29", "from_hour": 20,
                          "duration_hours": 0})
    assert r.status_code == 422


def test_s13_отрицательный_период_отчёта_отклоняется(client):
    assert client.get("/tools/intent/report?days=-1", headers=HEADERS).status_code == 422
    assert client.get("/tools/intent/report?days=7", headers=HEADERS).status_code == 200


def test_s14_несуществующий_участник_это_ошибка_а_не_пустая_труппа(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 789102, "Есть Такой")
    r = client.post("/tools/schedule/slots", headers=HEADERS,
                    json={"participants": [pid, 999999], "block_events": False})
    assert r.status_code == 422
    assert "999999" in r.text and str(pid) not in r.json()["detail"].split(":", 1)[1]


# --- данные (S-6, S-6b) --------------------------------------------------------

def test_s6_список_участников_отдаёт_роли_и_права_как_me(client):
    me = client.get("/tools/participants/me", headers=HEADERS).json()["data"]
    listed = client.get("/tools/participants", headers=HEADERS).json()["data"]
    row = next(p for p in listed if p["id"] == me["id"])
    assert row["capabilities"], "права не должны быть пустыми"
    assert sorted(row["capabilities"]) == sorted(me["capabilities"])
    assert sorted(row["roles"]) == sorted(me["roles"])


def test_s6_несколько_ролей_видны_в_списке(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 789103, "Две Роли", role="org")
        conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')",
                     (pid,))
        conn.commit()
    listed = client.get("/tools/participants", headers=HEADERS).json()["data"]
    row = next(p for p in listed if p["id"] == pid)
    assert sorted(row["roles"]) == ["org", "troupe"]


def test_роли_без_строк_participant_role_не_дают_null(client):
    """v_participant_roles отдаёт {NULL} человеку без participant_role — в API None не нужен."""
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("insert into participant (tg_chat_id, display_name, person_key, role, status) "
                     "values (789105, 'Без Ролей', 'без-ролей', 'troupe', 'active')")
        conn.commit()
    me = client.get("/tools/participants/me",
                    headers={**HEADERS, "X-Actor-Chat-Id": "789105"}).json()["data"]
    assert None not in me["roles"]
    listed = client.get("/tools/participants", headers=HEADERS).json()["data"]
    row = next(p for p in listed if p["display_name"] == "Без Ролей")
    assert row["roles"] == ["troupe"], "фолбэк на participant.role, а не [None]"


def test_s6b_часы_вне_рабочего_дня_не_теряются_молча(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 789104, "Ночной Час")
        for hour in (0, 3, 10):
            conn.execute("""insert into availability_hour
                              (participant_id, day, hour, is_free, kind, source)
                            values (%s, '2026-11-12', %s, false, 'busy', 'sheet_import')""",
                         (pid, hour))
        conn.commit()
    token = client.post("/webapp/auth", json={"init_data": init_data(789104)}).json()["token"]
    body = client.get("/webapp/my?month=2026-11",
                      headers={"Authorization": f"Bearer {token}"}).json()

    shown = {int(hour) for hour in body["cells"]["2026-11-12"]}
    low, high = body["hours"]
    assert shown == {10}
    assert all(low <= hour <= high for hour in shown), "ячейки вне объявленного диапазона"
    assert body["outside_hours"] == 2


# --- бюджет (C-10) -------------------------------------------------------------

def _commit(client, **over):
    payload = {"chat_id": ADMIN_CHAT, "tx_type": "expense", "amount": 1500,
               "category": "Реквизит и костюмы", "purpose": "тест ключа"}
    payload.update(over)
    return client.post("/tools/budget/commit", headers=HEADERS, json=payload)


def test_c10_запись_без_ключа_отклоняется(client):
    r = _commit(client)
    assert r.status_code == 422
    assert "idempotency_key_required" in r.text
    assert _commit(client, idempotency_key="   ").status_code == 422
    recent = client.get("/tools/budget/recent?limit=10", headers=HEADERS).json()["data"]
    assert recent == [], "без ключа в бюджет ничего не попадает"


def test_c10_повтор_с_ключом_не_дублирует(client):
    first = _commit(client, idempotency_key="tg:budget:1:42").json()
    second = _commit(client, idempotency_key="tg:budget:1:42").json()
    assert second.get("deduped") is True
    assert first["tx"]["id"] == second["tx"]["id"]


# --- контракт «мягкого» предпросмотра (S-8) -----------------------------------

@pytest.mark.parametrize("payload,code", [
    ({"text": "привет"}, "no_kind"),
    ({"kind": "rehearsal", "date": "2026-12-19"}, "no_time"),
    ({"kind": "rehearsal", "date": "2026-12-19", "from_hour": 7, "to_hour": 9}, "bad_time"),
])
def test_s8_мягкий_предпросмотр_ошибка_ввода(client, payload, code):
    """S-8 (docs/architecture/51): непонимание фразы — 200 с input_error, а не 4xx."""
    r = client.post("/tools/events/preview", headers=HEADERS, json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["input_error"] is True
    assert body["can_assign"] is False
    assert body["card"] is None and body["proposal_id"] is None
    assert body["errors"][0]["code"] == code
    assert body["errors"][0]["text"], "человеку нужна готовая фраза, а не код"
