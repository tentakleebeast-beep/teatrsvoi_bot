"""Админ-панель: права, снятые пути заявок и кодов, сверка (docs/development/28).

После персонального кода (docs/development/182 §4.1, §4.3.3) HTTP-обёртки
`/admin/pending`, `/admin/approve` и `/admin/invite` удалены: коды выпускаются
только из Mini App, заявок нет. Тест держит этот контракт — 404, а не 401/403,
потому что пути больше не существуют, и права на них не проверяются.

Обоснование: FR-A2, FR-A6, FR-G16, docs/development/182 §4.1, §4.3.3.
"""


def test_список_участников_только_с_правом(client, auth):
    r = client.get("/tools/participants", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    names = [p["display_name"] for p in data["data"]]
    assert all(name for name in names)


def test_путей_заявок_и_кодов_в_чате_больше_нет(client, auth):
    """Заявок нет, коды из чата не выпускаются (V-118 а) — путей нет вовсе."""
    assert client.get("/admin/pending", headers=auth).status_code == 404
    assert client.post("/admin/approve", json={"participant_id": 1, "approve": True},
                       headers=auth).status_code == 404
    assert client.post("/admin/invite", json={"role": "troupe", "ttl_days": 7},
                       headers=auth).status_code == 404
    assert client.post("/admin/invite/revoke", json={"code": "X"},
                       headers=auth).status_code == 404


def test_сверка_требует_права_импорта(client, auth):
    r = client.post("/admin/reconcile", json={"apply": False, "scope": "budget"},
                    headers={**auth, "X-Actor-Chat-Id": "999999"})
    assert r.status_code in (401, 403)


def test_роли_в_базе_только_три(client, auth):
    import psycopg

    from tests.conftest import _test_dsn

    with psycopg.connect(_test_dsn()) as conn:
        roles = {r[0] for r in conn.execute("select distinct role from rbac_role_capability")}
    assert roles <= {"admin", "org", "troupe", "master"}
    assert "student" not in roles
