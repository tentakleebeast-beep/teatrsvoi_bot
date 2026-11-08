"""Маяк загрузки Mini App: страница сообщает, что видит на старте.

Обоснование: обращение №41 (Mini App висит на загрузке) и разбор docs/development/108.
Зачем тест: 02.11.2026 Mini App не открывался на iPhone и в Desktop, а в журнале не было
ни одного POST /webapp/auth — проверить было нечем, запросы шли от прокси. Маяк должен
приниматься без авторизации (страница ещё не имеет токена) и не хранить initData.
"""

from __future__ import annotations

import logging


def test_маяк_принимается_без_авторизации(api):
    """Страница шлёт маяк до входа: эндпоинт обязан отвечать 200 без токена."""
    r = api.post("/webapp/boot", json={
        "platform": "ios", "init_len": 0, "sdk": "7.0",
        "url": "https://svoi-theater-samara.online/webapp/home",
        "ready": "interactive", "error": "",
    })
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_маяк_переживает_мусор(api):
    """Клиент может прислать что угодно: маяк не должен ронять страницу."""
    for payload in ({}, {"init_len": "много"}, {"init_len": None}, {"unknown": [1, 2]}):
        r = api.post("/webapp/boot", json=payload)
        assert r.status_code == 200, payload
        assert r.json()["ok"] is True


def test_маяк_не_принимает_init_data(api):
    """В теле может оказаться сам initData — сервер его не читает и не хранит."""
    r = api.post("/webapp/boot", json={"init_data": "query_id=secret&hash=deadbeef", "init_len": 42})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_страница_содержит_маяк(api):
    """Маяк ставится в разметку до shell.js, иначе он не успеет при падении оболочки."""
    html = api.get("/webapp/home").text
    assert "/webapp/boot" in html
    assert html.index("/webapp/boot") < html.index("/webapp/shell.js")


def test_перевод_строки_в_поле_не_подделывает_журнал(api, caplog):
    """Публичная ручка: перевод строки в поле не должен добавлять запись в журнал.

    Аудит 03.11.2026: `{"url": "x\\n2026-11-03 boot: platform=ios"}` дописывал в журнал
    строку, которой не было. Журнал `boot:` — инструмент разбора «Mini App не
    открывается», поэтому поле обязано попасть в него одной строкой.
    """
    caplog.set_level(logging.INFO, logger="tool-api")
    r = api.post("/webapp/boot", json={
        "platform": "ios\r\n2026-11-03 boot: platform=windows",
        "init_len": 0, "sdk": "7.0", "ready": "interactive",
        "error": "boom\nerr=0 url=https://evil.example",
        "url": "x\n2026-11-03 01:00:00 boot: platform=android init_len=999",
    })

    assert r.status_code == 200
    assert r.json() == {"ok": True}

    boot_records = [rec for rec in caplog.records
                    if rec.name == "tool-api" and rec.getMessage().startswith("boot:")]
    assert len(boot_records) == 1, [rec.getMessage() for rec in boot_records]
    line = boot_records[0].getMessage()
    # в журнал уходит ровно одна физическая строка: подделанных записей в файле не будет
    assert line.splitlines() == [line]
    assert line.startswith("boot: platform=ios 2026-11-03 boot:")  # \r\n схлопнут в пробел
    assert "x 2026-11-03 01:00:00 boot: platform=android init_len=999" in line
    assert "boom err=0 url=https://evil.example" in line

