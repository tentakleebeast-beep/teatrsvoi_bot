"""Каскад и журнал интентов: docs/development/21, docs/development/16 §4.3."""


def test_правила_пишут_в_журнал(client, auth):
    r = client.post("/tools/intent/resolve",
                    json={"text": "оплатили аренду 45 000", "chat_id": 123456789},
                    headers=auth)
    data = r.json()
    assert r.status_code == 200
    assert data["decision"]["source"] == "rules"
    assert data["decision"]["category_final"] == "Аренда помещения"


def test_неуверенная_модель_ведёт_к_кнопкам_и_логируется(client, auth):
    r = client.post("/tools/intent/resolve",
                    json={"text": "закупили всякое 500", "chat_id": 123456789,
                          "category_llm": "Реквизит и костюмы", "confidence": 0.4},
                    headers=auth)
    d = r.json()["decision"]
    assert d["need"] == "buttons"
    assert d["category_final"] is None

    rep = client.get("/tools/intent/report?days=7", headers=auth).json()
    assert rep["ok"] is True
    assert any(item["text"] == "закупили всякое 500" for item in rep["top_unmatched"])


def test_новая_категория_становится_кандидатом(client, auth):
    client.post("/tools/intent/resolve",
                json={"text": "купили реквизит 700", "chat_id": 123456789,
                      "category_llm": "Бутафория", "confidence": 0.9},
                headers=auth)
    rep = client.get("/tools/intent/report?days=7", headers=auth).json()
    names = [c["name"] for c in rep["category_candidates"]]
    assert "Бутафория" in names


def test_категории_закрытый_список(client, auth):
    r = client.get("/tools/intent/categories", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["threshold"] == 0.7
    assert "Аренда помещения" in body["categories"]
