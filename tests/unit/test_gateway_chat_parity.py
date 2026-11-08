"""Паритет чата с экраном: архив человека, отзыв инвайта, права, тариф.

Карта `t_6f32dc2f` (AUD-3), реестр действий `docs/architecture/25` §9.4 —
строки Л6 (архив), Л8 (отзыв кода), Л4 (права), Д8 (тариф взноса).
Требования: FR-A7 (архив), FR-A2 (код приглашения), FR-A6 (права), FR-B7 (тариф).

Правило, которое здесь проверяется (ADR-13, §9.2 п. 4–5): оболочка показывает
пункт по тому же праву, что проверяет сервер у пути записи, и сама не ведёт
человека в отказ; сервер проверяет право ещё раз — это второй рубеж.
"""

import asyncio
import types

import gateway_modules
import pytest


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


# Права ролей — по db/init/008_rbac_capabilities.sql, а не по памяти:
# у организатора есть participants.view и budget.write, но нет participants.manage
# и нет admin.panel; коды приглашений он не выпускает.
ADMIN = ["*"]
ORG = ["registration.approve", "participants.view", "budget.read", "budget.read.self",
       "budget.write", "budget.import", "schedule.read.all", "schedule.availability.self",
       "schedule.slots", "calendar.write", "notes.write", "files.access", "notify.send",
       "system.health"]
ORG_WITH_CODES = ORG + ["registration.code.issue"]
MANAGER = ["participants.view", "participants.manage"]


class FakeMessage:
    """Сообщение с edit_text: панель правит то же сообщение, а не шлёт новое."""

    def __init__(self, text: str = ""):
        self.text = text
        self.chat = types.SimpleNamespace(id=1)
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.answers.append((text, reply_markup))


class FakeCb:
    def __init__(self, data: str):
        self.data = data
        self.message = FakeMessage("")
        self.alerts: list[str] = []

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append(text)


class FakeState:
    def __init__(self):
        self.state = None
        self.data: dict = {}

    async def set_state(self, value):
        self.state = value

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return self.data


def labels(markup) -> list[str]:
    return [] if markup is None else [b.text for row in markup.inline_keyboard for b in row]


def datas(markup) -> list[str]:
    return [] if markup is None else [b.callback_data for row in markup.inline_keyboard for b in row]


def keyboard_texts(markup) -> list[str]:
    """Подписи постоянной клавиатуры: у неё `keyboard`, а не `inline_keyboard`."""
    return [button.text for row in markup.keyboard for button in row]


def panel_labels(gw, caps) -> list[str]:
    return [text for row in gw.panel_rows(caps) for text, _ in row]


@pytest.fixture()
def fake_api(monkeypatch):
    """Подмена tool-api: запоминаем путь и тело, отвечаем по словарю ответов."""
    calls: list[tuple[str, dict | None, str]] = []
    answers: dict[str, dict] = {}

    async def api(path, payload, chat_id, method="POST"):
        calls.append((path, payload, method))
        return answers.get(path.split("?")[0], {"ok": True})

    monkeypatch.setattr(gateway_modules.load("core"), "api", api)
    return types.SimpleNamespace(calls=calls, answers=answers)


@pytest.fixture()
def caps(monkeypatch):
    """Права зрителя: обработчик спрашивает их у core.caps_of."""
    holder = {"caps": ADMIN}

    async def caps_of(chat_id):
        return holder["caps"]

    monkeypatch.setattr(gateway_modules.load("core"), "caps_of", caps_of)
    return holder


PEOPLE_WITH_ARCHIVE = {"ok": True, "data": [
    {"id": 2, "display_name": "Арина", "status": "active", "roles": ["troupe"]},
    {"id": 3, "display_name": "Ушедший", "status": "archived", "roles": ["troupe"]},
]}


# --------------------------------------------------------------- Л6: архив человека
def test_Л6_архив_виден_по_manage_а_не_по_просмотру(gw):
    """FR-A7: пункт «Архив» — у того, кто может менять людей (participants.manage)."""
    assert "Архив" in panel_labels(gw, MANAGER)
    assert "Архив" not in panel_labels(gw, ORG)
    assert ("Архив", "panel:archive") in [cell for row in gw.panel_rows(MANAGER) for cell in row]


def test_Л6_архив_показывает_людей_и_кнопку_вернуть(gw, fake_api, caps):
    """FR-A7: список архива и возврат — кнопками, без Mini App."""
    fake_api.answers["/tools/participants"] = PEOPLE_WITH_ARCHIVE
    cb = FakeCb("panel:archive")
    asyncio.run(gw.panel_archive(cb))

    assert fake_api.calls[0][0] == "/tools/participants?include_archived=true"
    text, markup = cb.message.answers[-1]
    assert "В архиве: 1" in text and "Ушедший" in text
    assert "Арина" not in text, "в архивном списке не должно быть действующих"
    assert "panel:unarchive:3" in datas(markup)
    assert "Вернуть" in " ".join(labels(markup))


def test_Л6_возврат_зовёт_путь_записи(gw, fake_api, caps):
    """FR-A7: «Вернуть» пишет через `/admin/unarchive` — тот же обработчик, что у экрана."""
    fake_api.answers["/tools/participants"] = PEOPLE_WITH_ARCHIVE
    cb = FakeCb("panel:unarchive:3")
    asyncio.run(gw.panel_person_unarchive(cb))

    assert ("/admin/unarchive", {"participant_id": 3, "reason": "chat"}, "POST") in fake_api.calls
    assert "Возвращён" in cb.alerts


def test_Л6_карточка_отправляет_в_архив(gw, fake_api, caps):
    """FR-A7: из карточки человека — «В архив»; без participants.manage кнопки нет."""
    fake_api.answers["/tools/participants/2/card"] = {
        "ok": True, "data": {"id": 2, "display_name": "Арина", "roles": ["troupe"],
                             "capabilities": [], "tariff": {"tariff": None}}}

    caps["caps"] = MANAGER
    cb = FakeCb("panel:person:2")
    asyncio.run(gw.panel_person(cb))
    assert "panel:archive:2" in datas(cb.message.answers[-1][1])

    caps["caps"] = ORG
    cb = FakeCb("panel:person:2")
    asyncio.run(gw.panel_person(cb))
    assert "panel:archive:2" not in datas(cb.message.answers[-1][1])

    fake_api.answers["/admin/archive"] = {"ok": True, "name": "Арина"}
    caps["caps"] = MANAGER
    cb = FakeCb("panel:archive:2")
    asyncio.run(gw.panel_person_archive(cb))
    assert ("/admin/archive", {"participant_id": 2, "reason": "chat"}, "POST") in fake_api.calls
    assert "в архиве" in cb.message.answers[-1][0]


def test_Л6_архив_отказывает_без_права(gw, fake_api, caps):
    """FR-A7: кнопка из старого меню не читает архив — отказ до запроса."""
    caps["caps"] = ORG
    cb = FakeCb("panel:archive")
    asyncio.run(gw.panel_archive(cb))

    assert cb.alerts == ["Нет права"]
    assert not fake_api.calls, "без participants.manage запрос в tool-api не идёт"


# ----------------------------------------------------------- Л8: отзыв инвайт-кода
def test_Л8_коды_видны_по_праву_выпуска(gw):
    """FR-A2: пункт «Коды приглашений» — по registration.code.issue, как у отзыва."""
    assert "Коды приглашений" in panel_labels(gw, ORG_WITH_CODES)
    assert "Коды приглашений" not in panel_labels(gw, MANAGER)
    assert "Коды приглашений" not in panel_labels(gw, ORG)


def test_Л8_список_кодов_и_кнопка_отозвать(gw, fake_api, caps):
    """FR-A2: открытые коды перечислены, у каждого — «Отозвать»."""
    caps["caps"] = ORG_WITH_CODES
    fake_api.answers["/admin/invites"] = {"ok": True, "data": [
        {"code": "ABCD1234", "role": "troupe", "expires_at": "2026-11-11T10:00:00+04:00",
         "uses": 0, "max_uses": 1}]}
    cb = FakeCb("panel:codes")
    asyncio.run(gw.panel_codes(cb))

    text, markup = cb.message.answers[-1]
    assert "ABCD1234" in text and "актёр труппы" in text
    assert "inv:revoke:ABCD1234" in datas(markup)


def test_Л8_отзыв_зовёт_путь_записи(gw, fake_api, caps):
    """FR-A2: «Отозвать» пишет через `/admin/invite/revoke`, а не молчит."""
    caps["caps"] = ORG_WITH_CODES
    fake_api.answers["/admin/invite/revoke"] = {"ok": True, "code": "ABCD1234"}
    fake_api.answers["/admin/invites"] = {"ok": True, "data": []}
    cb = FakeCb("inv:revoke:ABCD1234")
    asyncio.run(gw.invite_revoke(cb))

    assert ("/admin/invite/revoke", {"code": "ABCD1234"}, "POST") in fake_api.calls
    assert "Код отозван" in cb.alerts
    # после отзыва список перечитывается: отозванного кода в нём уже нет
    assert "/admin/invites" in [path for path, _, _ in fake_api.calls]


def test_Л8_отзыв_отказывает_без_права(gw, fake_api, caps):
    """FR-A2: без registration.code.issue отзыв не уходит в tool-api."""
    caps["caps"] = MANAGER
    cb = FakeCb("inv:revoke:ABCD1234")
    asyncio.run(gw.invite_revoke(cb))

    assert cb.alerts == ["Нет права"]
    assert not fake_api.calls


# ------------------------------------------------------------ Л4: права организатора
def test_Л4_права_только_по_manage(gw):
    """FR-A6: «Права» — по participants.manage, как выдача прав на экране."""
    assert "Права" in panel_labels(gw, MANAGER)
    assert "Права" not in panel_labels(gw, ORG)


def test_Л4_экран_прав_отказывает_без_manage(gw, fake_api, caps):
    """FR-A6: организатор не получает матрицу прав — отказ оболочки, без запроса."""
    caps["caps"] = ORG
    cb = FakeCb("panel:rights")
    asyncio.run(gw.panel_rights(cb))

    assert cb.alerts == ["Нет права"]
    assert not fake_api.calls


def test_Л4_вход_в_панель_только_у_админа(gw):
    """FR-A6: пункт «Админ-панель» в разделе «Админ» — по admin.panel.

    Организатор видит там свои действия (участники, заявки, приглашения), а вход
    в панель — нет: иначе он нажимает пункт и получает отказ.
    """
    org_items = dict(gw.menu_groups(ORG))["Админ"]
    assert "Админ-панель" not in org_items
    assert {"Участники", "Заявки"} <= set(org_items), "свои действия организатор не теряет"
    assert "Админ-панель" in dict(gw.menu_groups(ADMIN))["Админ"]


# ------------------------------------------------------------- Д8: тариф взноса
def test_Д8_тариф_виден_по_budget_write(gw, fake_api, caps):
    """FR-B7: «Люди» → человек → «Тариф взноса» у организатора с budget.write.

    Ни participants.manage, ни admin.panel у него нет: пункт должен быть виден
    по праву пути записи (`POST /tools/participants/{id}/dues-tariff`).
    """
    caps["caps"] = ORG
    fake_api.answers["/tools/participants"] = PEOPLE_WITH_ARCHIVE
    message = FakeMessage()
    asyncio.run(gw.send_people(message))
    assert "panel:person:2" in datas(message.answers[-1][1]), "список людей недоступен"

    fake_api.answers["/tools/participants/2/card"] = {
        "ok": True, "data": {"id": 2, "display_name": "Арина", "roles": ["troupe"],
                             "capabilities": [], "tariff": {"tariff": 3000}}}
    cb = FakeCb("panel:person:2")
    asyncio.run(gw.panel_person(cb))
    assert "Тариф взноса" in labels(cb.message.answers[-1][1])


def test_Д8_тариф_не_виден_без_budget_write(gw, fake_api, caps):
    """FR-B7: без budget.write кнопки тарифа нет — она вела бы в отказ."""
    caps["caps"] = ["participants.view"]
    fake_api.answers["/tools/participants/2/card"] = {
        "ok": True, "data": {"id": 2, "display_name": "Арина", "roles": ["troupe"],
                             "capabilities": [], "tariff": {"tariff": 3000}}}
    cb = FakeCb("panel:person:2")
    asyncio.run(gw.panel_person(cb))
    assert "Тариф взноса" not in labels(cb.message.answers[-1][1])


def test_Д8_тариф_отказывает_без_budget_write(gw, fake_api, caps):
    """FR-B7: без budget.write бот не спрашивает сумму — отказ до ввода."""
    caps["caps"] = ["participants.view"]
    state = FakeState()
    cb = FakeCb("panel:tariff:2")
    asyncio.run(gw.panel_person_tariff(cb, state))

    assert cb.alerts == ["Нет права"]
    assert state.state is None, "форма тарифа не должна открываться"
    assert not fake_api.calls


# ----------------------------------------------------------- Л7: блокировка человека
def test_Л7_карточка_даёт_кнопку_блокировки(gw, fake_api, caps):
    """FR-A9: у того, кто правит людей, в карточке есть «Заблокировать»."""
    fake_api.answers["/tools/participants/2/card"] = {
        "ok": True, "data": {"id": 2, "display_name": "Арина", "roles": ["troupe"],
                             "capabilities": [], "status": "active"}}
    caps["caps"] = MANAGER
    cb = FakeCb("panel:person:2")
    asyncio.run(gw.panel_person(cb))
    assert "panel:block:2" in datas(cb.message.answers[-1][1])

    caps["caps"] = ORG
    cb = FakeCb("panel:person:2")
    asyncio.run(gw.panel_person(cb))
    assert "panel:block:2" not in datas(cb.message.answers[-1][1]), \
        "участнику и организатору блокировка не открывается"


def test_Л7_заблокированному_показана_кнопка_разблокировки(gw, fake_api, caps):
    """FR-A9: состояние роли в карточке — «заблокирован», кнопка обратного действия."""
    fake_api.answers["/tools/participants/2/card"] = {
        "ok": True, "data": {"id": 2, "display_name": "Арина", "roles": ["troupe"],
                             "capabilities": [], "status": "blocked"}}
    caps["caps"] = MANAGER
    cb = FakeCb("panel:person:2")
    asyncio.run(gw.panel_person(cb))
    assert "panel:unblock:2" in datas(cb.message.answers[-1][1])
    assert "Статус: заблокирован" in cb.message.answers[-1][0]


def test_Л7_кнопка_зовёт_путь_записи(gw, fake_api, caps):
    """FR-A9: кнопка чата идёт в тот же `/admin/block`, что и экран."""
    fake_api.answers["/admin/block"] = {"ok": True, "name": "Арина", "status": "blocked"}
    caps["caps"] = MANAGER
    cb = FakeCb("panel:block:2")
    asyncio.run(gw.panel_person_block(cb))

    assert ("/admin/block", {"participant_id": 2, "blocked": True, "reason": "chat"},
            "POST") in fake_api.calls
    assert "заблокирован" in cb.message.answers[-1][0]
    assert "panel:unblock:2" in datas(cb.message.answers[-1][1])


def test_Л7_без_права_кнопка_не_зовёт_сервер(gw, fake_api, caps):
    """FR-A9: без participants.manage отказ в оболочке, запроса нет."""
    caps["caps"] = ORG
    cb = FakeCb("panel:block:2")
    asyncio.run(gw.panel_person_block(cb))

    assert cb.alerts == ["Нет права"]
    assert not fake_api.calls


def test_Л7_заблокированный_виден_в_списке_людей(gw, fake_api, caps):
    """FR-A9: заблокированный не исчезает из «Люди» — иначе его не разблокировать."""
    fake_api.answers["/tools/participants"] = {"ok": True, "data": [
        {"id": 2, "display_name": "Арина", "status": "active", "roles": ["troupe"]},
        {"id": 3, "display_name": "Тимур", "status": "blocked", "roles": ["troupe"]},
    ]}
    caps["caps"] = MANAGER
    cb = FakeCb("panel:people")
    asyncio.run(gw.panel_people(cb))

    markup = cb.message.answers[-1][1]
    assert "panel:person:3" in datas(markup)
    assert "заблокированных 1" in cb.message.answers[-1][0]
