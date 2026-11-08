"""Последние операции бюджета: месяц и подпись периода (обращение №15)."""


def _tx(client, auth, key: str, tx_date: str | None = None, amount: int = 1000):
    body = {"chat_id": 123456789, "tx_type": "expense", "amount": amount,
            "category": "Реквизит и костюмы", "purpose": "тест периода",
            "idempotency_key": key}
    if tx_date:
        body["tx_date"] = tx_date
    res = client.post("/tools/budget/commit", json=body, headers=auth).json()
    assert res.get("ok") is True, res
    return res["tx"]


def test_по_умолчанию_текущий_месяц(client, auth):
    now_tx = _tx(client, auth, "tg:budget:123456789:2001")
    res = client.get("/tools/budget/recent?limit=5", headers=auth).json()
    assert res["ok"] is True
    assert res["period"] == now_tx["period"]
    assert res["current"] is True
    assert [r["id"] for r in res["data"]] == [now_tx["id"]]


def test_будущие_авансы_не_вытесняют_текущий_месяц(client, auth):
    """Дефект: сортировка по tx_date поднимала декабрьские авансы выше октября."""
    now_tx = _tx(client, auth, "tg:budget:123456789:2002")
    for i, day in enumerate(("2026-12-08", "2026-12-19", "2026-12-25",
                             "2026-12-26", "2026-12-28", "2026-12-30")):
        _tx(client, auth, f"tg:budget:123456789:20{i + 10}", tx_date=day)
    res = client.get("/tools/budget/recent?limit=5", headers=auth).json()
    assert res["period"] == now_tx["period"]
    assert [r["id"] for r in res["data"]] == [now_tx["id"]]


def test_другой_месяц_по_параметру(client, auth):
    # прошлый месяц внутри журнала: будущее с Ш-43 (V-003) не листается
    sep = _tx(client, auth, "tg:budget:123456789:2003", tx_date="2026-09-19")
    res = client.get("/tools/budget/recent?limit=5&period=2026-09", headers=auth).json()
    assert res["period"] == "2026-09"
    assert res["period_title"] == "сентябрь 2026"
    assert res["current"] is False
    assert [r["id"] for r in res["data"]] == [sep["id"]]
    assert res["data"][0]["period"] == "2026-09"


def test_неверный_период_отклоняется(client, auth):
    for bad in ("2026-13", "2026-1", "ноябрь", "2026-12-01"):
        r = client.get(f"/tools/budget/recent?period={bad}", headers=auth)
        assert r.status_code == 422, bad


def test_границы_листания_приводят_запрос_к_краю(client, auth):
    """Ш-43 (V-003): за границы журнала уйти нельзя, даже старыми кнопками."""
    base = client.get("/tools/budget/recent?limit=5", headers=auth).json()
    first, cur = base["first_period"], base["current_period"]
    assert first <= cur
    low = _shift(first, -1)
    res = client.get(f"/tools/budget/recent?limit=5&period={low}", headers=auth).json()
    assert res["period"] == first
    high = "2027-12"
    res = client.get(f"/tools/budget/recent?limit=5&period={high}", headers=auth).json()
    assert res["period"] == cur
    assert res["current"] is True


def _shift(period: str, delta: int) -> str:
    year, month = (int(x) for x in period.split("-"))
    index = year * 12 + (month - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"
