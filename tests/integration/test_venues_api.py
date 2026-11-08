"""Пространства через API: список и параллельные аренды (docs/architecture/47).

Сценарии «событие помечается пространством» и «по умолчанию сцена» вместе с путём
`POST /tools/calendar/event` удалены картой AUD-8 (t_feaf85e1, docs/reports/124):
путь был дублем `POST /tools/events/assign`, а свойства пространства в календаре
ставит именно он (services/tool-api/app/api/events.py, `private["svoi_venue"]`).
Проверять удалённый путь нечем — тест удалён вместе с ним.
"""


def test_список_пространств(client):
    body = client.get("/tools/venues", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}).json()
    codes = [v["code"] for v in body["venues"]]
    assert codes == ["stage", "dressing", "backstage"], codes
    assert body["default"] == "stage"


def test_аренда_по_пространствам_не_пересекается(client, monkeypatch):
    """Сцена занята, гримёрка свободна: окна аренды разные."""
    from app import core as api

    events = [{
        "id": "ev-1", "summary": "Показ - Тест",
        "start": {"dateTime": "2026-12-28T18:00:00+04:00"},
        "end": {"dateTime": "2026-12-28T22:00:00+04:00"},
        "extendedProperties": {"private": {"svoi_kind": "show", "svoi_venue": "stage"}},
    }]
    monkeypatch.setattr(api, "fetch_events", lambda *a, **k: events)

    stage = client.post("/tools/schedule/slots", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}, json={
        "policy": "rental", "venue": "stage", "date_from": "2026-12-28",
        "date_to": "2026-12-28", "min_hours": 2, "block_events": False}).json()

    dressing = client.post("/tools/schedule/slots", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}, json={
        "policy": "rental", "venue": "dressing", "date_from": "2026-12-28",
        "date_to": "2026-12-28", "min_hours": 2, "block_events": False}).json()

    stage_spans = [(w["from_hour"], w["to_hour"]) for w in stage["windows"]]
    dressing_spans = [(w["from_hour"], w["to_hour"]) for w in dressing["windows"]]

    assert dressing_spans and (8, 24) in dressing_spans, "гримёрка свободна весь день"
    assert (8, 18) in stage_spans, "на сцене свободно до показа"
    assert all(not (start <= 18 < end) for start, end in stage_spans), \
        "окно аренды не должно попадать на показ"


def test_аренда_по_умолчанию_считает_всю_студию(client, monkeypatch):
    from app import core as api

    events = [{
        "id": "ev-dressing", "summary": "Репетиция - Разбор роли",
        "start": {"dateTime": "2026-12-29T18:00:00+04:00"},
        "end": {"dateTime": "2026-12-29T20:00:00+04:00"},
        "extendedProperties": {"private": {"svoi_venue": "dressing"}},
    }]
    monkeypatch.setattr(api, "fetch_events", lambda *a, **k: events)

    body = client.post("/tools/schedule/slots", headers={
        "Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}, json={
        "policy": "rental", "date_from": "2026-12-29", "date_to": "2026-12-29",
        "min_hours": 2, "block_events": False}).json()

    assert body["venue"]["venue"] == "studio"
    spans = [(w["from_hour"], w["to_hour"]) for w in body["windows"]]
    assert (8, 18) in spans
    assert all(not (start <= 18 < end) for start, end in spans), \
        "занятие в гримёрке занимает студию"
