"""Направление статьи проверяется на сервере, а не только в разборе текста.

Дефект (UX-прогон 30.10.2026): «Аренда помещения» принималась для дохода —
`_ensure_category` искала статью только по имени. Теперь сервер отказывает.

Правила и требования раздела: docs/architecture/03-budget.md.
"""

from __future__ import annotations

from tests.conftest import TOKEN

HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": "123456789"}


def _post(client, **payload):
    body = {"amount": 1000, "tx_date": "2026-10-28", "idempotency_key": f"dir:{payload.get('category')}"}
    body.update(payload)
    return client.post("/tools/budget/commit", json=body, headers=HEADERS)


def test_доход_в_расходной_статье_отклоняется(client):
    response = _post(client, tx_type="income", category="Аренда помещения")
    assert response.status_code == 422
    assert "category_direction" in response.text


def test_расход_в_доходной_статье_отклоняется(client):
    response = _post(client, tx_type="expense", category="Взносы студийцев")
    assert response.status_code == 422
    assert "category_direction" in response.text


def test_совпадающее_направление_пишется(client):
    response = _post(client, tx_type="income", category="Прочее (доход)")
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
