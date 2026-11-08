"""Запись в бюджет: идемпотентность и базовая валидация (docs/development/16 §4.4)."""


def _payload(**over):
    base = {"chat_id": 123456789, "tx_type": "expense", "amount": 3000,
            "category": "Реквизит и костюмы", "purpose": "тест",
            "idempotency_key": "tg:budget:123456789:1001"}
    base.update(over)
    return base


def test_запись_создаёт_операцию(client, auth):
    r = client.post("/tools/budget/commit", json=_payload(), headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["tx"]["amount"] == 3000
    assert data.get("deduped") is not True


def test_повторный_запрос_не_создаёт_вторую_операцию(client, auth):
    first = client.post("/tools/budget/commit", json=_payload(), headers=auth).json()
    second = client.post("/tools/budget/commit", json=_payload(), headers=auth).json()
    assert second["deduped"] is True
    assert second["tx"]["id"] == first["tx"]["id"]

    recent = client.get("/tools/budget/recent?limit=10", headers=auth).json()["data"]
    assert len(recent) == 1


def test_разные_сообщения_дают_две_операции(client, auth):
    client.post("/tools/budget/commit", json=_payload(), headers=auth)
    client.post("/tools/budget/commit",
                json=_payload(idempotency_key="tg:budget:123456789:1002"), headers=auth)
    recent = client.get("/tools/budget/recent?limit=10", headers=auth).json()["data"]
    assert len(recent) == 2


def test_нулевая_сумма_отклоняется(client, auth):
    r = client.post("/tools/budget/commit", json=_payload(amount=0), headers=auth)
    assert r.status_code == 400


def test_без_прав_на_бюджет_доступ_закрыт(client):
    r = client.post("/tools/budget/commit", json=_payload(),
                    headers={"Authorization": "Bearer test-token",
                             "X-Actor-Chat-Id": "999999"})
    assert r.status_code in (401, 403)


def test_аудит_содержит_создание(client, auth):
    tx_id = client.post("/tools/budget/commit", json=_payload(), headers=auth).json()["tx"]["id"]
    events = client.get("/tools/audit/recent?limit=5", headers=auth).json()["data"]
    assert any(e["action"] == "budget.tx.create" and str(e["entity_id"]) == str(tx_id)
               for e in events)
