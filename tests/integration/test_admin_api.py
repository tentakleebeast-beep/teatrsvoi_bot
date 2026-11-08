"""Админ-панель: права, заявки, коды, сверка (docs/development/28 UC-05…UC-10)."""


def test_список_участников_только_с_правом(client, auth):
    r = client.get("/tools/participants", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    names = [p["display_name"] for p in data["data"]]
    assert all(name for name in names)


def test_заявки_требуют_права(client, auth):
    r = client.get("/admin/pending", headers={**auth, "X-Actor-Chat-Id": "999999"})
    assert r.status_code in (401, 403)


def test_выпуск_кода_и_проверка(client, auth):
    r = client.post("/admin/invite", json={"role": "troupe", "days": 7}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["code"]) >= 6


def test_код_только_для_трёх_ролей(client, auth):
    r = client.post("/admin/invite", json={"role": "student", "days": 7}, headers=auth)
    assert r.status_code in (400, 422)


def test_сверка_требует_права_импорта(client, auth):
    r = client.post("/admin/reconcile", json={"apply": False, "scope": "budget"},
                    headers={**auth, "X-Actor-Chat-Id": "999999"})
    assert r.status_code in (401, 403)


def test_роли_в_базе_только_три(client, auth):
    import psycopg

    from tests.conftest import _test_dsn

    with psycopg.connect(_test_dsn()) as conn:
        roles = {r[0] for r in conn.execute("select distinct role from rbac_role_capability")}
    assert roles <= {"admin", "org", "troupe"}
    assert "student" not in roles
