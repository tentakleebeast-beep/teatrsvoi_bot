"""Роль «педагог» (master): права, дисциплины и инвариант (Э1, docs/architecture/140).

Проверки идут на живой схеме тестовой базы (`db/init` через Alembic), а не чтением
SQL глазами: `on conflict`, CHECK и view ломаются молча, если их только читать.

Что здесь проверяется (критерии карты Э1):
  Г1 — CHECK роли допускает `master` и не допускает `student` в обеих таблицах;
  Г2 — `POST /admin/grant {role:"master", disciplines:[…]}` даёт роль, связь, флаг
       `is_teacher`, семь возможностей и дисциплины в `/tools/participants/me`;
  Г3 — выдача без дисциплины проходит, ответ несёт предупреждение, ничего лишнего не пишется;
  Г4 — последнюю дисциплину у мастера снять можно, ответ несёт предупреждение;
  Г5 — у мастера нет ни одного денежного/административного права, деньги — 403;
  Г6 — `scripts/check_invariants.py` называет мастера без дисциплины как WARN и не падает;
  Г7 — инвайт-кодом мастер не выдаётся (422);
  Г8 — отзыв роли снимает права и оставляет предметную связь;
  Г13 — главная Mini App: подпись «педагог», строки «Мои взносы» нет;
  Г14 — `/webapp/slots/people` отдаёт `my_disciplines` мастера;
  Г18 — контакты: у мастера нет телефона в списке, в карточке он замаскирован;
  Г19 — карточка человека несёт `warnings` мастера без дисциплины;
  Г20 — отказ без `participants.manage` в Mini App сказан словами и попадает в аудит;
  Г21 — справочник дисциплин и анкета отдают дисциплины в порядке `sort_order`.

Обоснование: FR-A1 (роли и доступ), FR-A6 (выдача прав), docs/architecture/140-master-role.md,
docs/architecture/02-access-control.md.
"""

from __future__ import annotations

import os
import subprocess
import sys

import psycopg
import pytest
from app.person_key import make_key

from tests.conftest import TOKEN, _test_dsn

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Семь возможностей роли — ровно те, что вносит db/init/061 (140 §3). Список здесь
# повторён намеренно: тест должен падать, если миграцию «уточнят» молча.
MASTER_CAPS = {"files.access", "notes.write", "participants.view",
               "schedule.availability.self", "schedule.read.all", "schedule.slots",
               "schedule.write"}

NOT_MASTER_CAPS = {"budget.read", "budget.read.self", "budget.write", "budget.import",
                   "notify.send", "participants.manage", "participants.contact",
                   "registration.approve", "registration.code.issue", "admin.panel",
                   "audit.view", "quota.manage", "system.health", "calendar.write"}

ADMIN_CHAT = 700001
MASTER_CHAT = 700002


def headers(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def _admin(conn) -> int:
    """Администратор: только он выдаёт роли (`participants.manage`)."""
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s, 'Админ Теста', %s, 'admin', 'active') returning id",
        (ADMIN_CHAT, make_key("Админ Теста"))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s, 'admin')",
                 (pid,))
    conn.commit()
    return pid


def _person(conn, chat_id: int = MASTER_CHAT, name: str = "Педагог Теста") -> int:
    """Активный человек с ролью `troupe`: мастер выдаётся поверх неё (140 §1)."""
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s, %s, %s, 'troupe', 'active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s, 'troupe')",
                 (pid,))
    conn.commit()
    return pid


def _master_only(conn, chat_id: int = MASTER_CHAT, name: str = "Педагог Теста") -> int:
    """Человек только с ролью педагога — для проверки «ровно семь возможностей».

    Права в проекте складываются из всех ролей человека (`v_participant_capabilities`),
    поэтому набор роли проверяется на человеке без `troupe`: иначе к семи возможностям
    педагога добавились бы возможности студийца, и тест мерил бы союз, а не роль.
    """
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s, %s, %s, 'master', 'active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s, 'master')",
                 (pid,))
    conn.commit()
    return pid


def _no_roles(conn, chat_id: int = MASTER_CHAT, name: str = "Педагог Теста") -> int:
    """Активный человек без строк ролей: ровно случай Г2 «человек без ролей»."""
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s, %s, %s, 'troupe', 'active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.commit()
    return pid


def _teach(conn, pid: int, code: str) -> None:
    conn.execute("insert into participant_discipline (participant_id, discipline_id, relation) "
                 "select %s, id, 'teacher' from discipline where code = %s "
                 "on conflict (participant_id, discipline_id) "
                 "do update set relation = 'teacher'", (pid, code))
    conn.commit()


def _disciplines(conn, pid: int) -> list[str]:
    row = conn.execute("select teacher_disciplines from v_participant_disciplines "
                       "where participant_id = %s", (pid,)).fetchone()
    return list(row[0] or [])


def _me(client, chat_id: int) -> dict:
    res = client.get("/tools/participants/me", headers=headers(chat_id))
    assert res.status_code == 200, res.text
    return res.json()["data"]


# --------------------------------------------------------------- Г1: CHECK роли
def test_Г1_check_роли_допускает_мастера_и_не_допускает_студента(client):
    """CHECK в `participant` и `participant_role` — четыре роли, без `student`."""
    with psycopg.connect(_test_dsn()) as conn:
        for table in ("participant", "participant_role"):
            definition = conn.execute(
                "select pg_get_constraintdef(oid) from pg_constraint "
                "where conrelid = %s::regclass and conname = 'participant_role_check'",
                (table,)).fetchone()[0]
            assert "master" in definition, f"{table}: master не допускается"
            assert "student" not in definition, f"{table}: student вернулся"

        admin = _admin(conn)
        pid = _person(conn)
        conn.execute("insert into participant_role (participant_id, role) "
                     "values (%s, 'master')", (pid,))
        conn.commit()

    # недопустимая роль проверяется отдельным соединением: откат в том же
    # соединении снял бы и вставку мастера, и тогда проверка ничего не доказывает
    with psycopg.connect(_test_dsn()) as broken:
        with pytest.raises(psycopg.errors.CheckViolation):
            broken.execute("insert into participant_role (participant_id, role) "
                           "values (%s, 'student')", (admin,))

    with psycopg.connect(_test_dsn()) as check:
        assert check.execute("select count(*) from participant_role "
                             "where role = 'master'").fetchone()[0] == 1


# ------------------------------------------------- Г2: выдача роли с дисциплиной
def test_Г2_выдача_мастера_с_дисциплиной(client):
    """Роль + связь + `is_teacher` + семь возможностей в одном запросе."""
    with psycopg.connect(_test_dsn()) as conn:
        _admin(conn)
        pid = _no_roles(conn)

    res = client.post("/admin/grant", headers=headers(ADMIN_CHAT), json={
        "participant_id": pid, "role": "master", "enabled": True,
        "disciplines": ["music"]})
    assert res.status_code == 200, res.text
    assert res.json()["roles"] == ["master"]

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select revoked_at from participant_role "
                           "where participant_id = %s and role = 'master'",
                           (pid,)).fetchone()
        assert row is not None and row[0] is None
        assert _disciplines(conn, pid) == ["music"]
        assert conn.execute("select is_teacher from participant where id = %s",
                            (pid,)).fetchone()[0] is True
        audited = conn.execute(
            "select after from audit_event where action = 'rbac.role.grant' "
            "order by id desc limit 1").fetchone()[0]
        assert audited["disciplines"] == ["music"], f"в аудите нет дисциплин: {audited}"

    me = _me(client, MASTER_CHAT)
    assert me["roles"] == ["master"]
    assert me["disciplines"] == [{"code": "music", "name": "Музыка"}]
    assert set(me["capabilities"]) == MASTER_CAPS


# ------------------------- Г3: без дисциплины роль выдаётся, ответ предупреждает
def test_Г3_выдача_без_дисциплины_проходит_с_предупреждением(client):
    """Роль записана, связей нет, ответ несёт предупреждение (V-037, D1-D2)."""
    with psycopg.connect(_test_dsn()) as conn:
        _admin(conn)
        pid = _person(conn)

    res = client.post("/admin/grant", headers=headers(ADMIN_CHAT), json={
        "participant_id": pid, "role": "master", "enabled": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "master" in body["roles"], body
    assert body["warnings"][0]["code"] == "master_without_discipline", body
    assert body["warnings"][0]["text"], "предупреждение без текста"

    # Опечатка в коде — по-прежнему отказ до записи: связей не появляется
    unknown = client.post("/admin/grant", headers=headers(ADMIN_CHAT), json={
        "participant_id": pid, "role": "master", "enabled": True,
        "disciplines": ["jazz"]})
    assert unknown.status_code == 400
    assert unknown.json()["code"] == "unknown_discipline"

    with psycopg.connect(_test_dsn()) as conn:
        assert conn.execute("select count(*) from participant_role "
                            "where participant_id = %s and role = 'master' "
                            "and revoked_at is null", (pid,)).fetchone()[0] == 1
        assert _disciplines(conn, pid) == []
        assert conn.execute("select is_teacher from participant where id = %s",
                            (pid,)).fetchone()[0] is not True


# --------------------------- Г4: последняя дисциплина снимается с предупреждением
def test_Г4_последняя_дисциплина_снимается_с_предупреждением(client):
    """`200` + `warnings`, связь снята, view «мастер без дисциплины» непуст (D2)."""
    with psycopg.connect(_test_dsn()) as conn:
        _admin(conn)
        pid = _person(conn)

    client.post("/admin/grant", headers=headers(ADMIN_CHAT), json={
        "participant_id": pid, "role": "master", "enabled": True,
        "disciplines": ["music"]})

    res = client.post("/admin/participant/discipline", headers=headers(ADMIN_CHAT), json={
        "participant_id": pid, "discipline": "music", "enabled": False})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["disciplines"] == []
    assert body["warnings"][0]["code"] == "master_without_discipline", body

    with psycopg.connect(_test_dsn()) as conn:
        assert _disciplines(conn, pid) == []
        assert conn.execute("select count(*) from v_master_without_discipline").fetchone()[0] > 0


# --------------------------------------------------- Г5: нет денег и админ-панели
def test_Г5_мастер_без_денег_и_админ_прав(client):
    """Семь возможностей, ни одной запрещённой; бюджет — 403."""
    with psycopg.connect(_test_dsn()) as conn:
        _admin(conn)
        pid = _no_roles(conn)

    client.post("/admin/grant", headers=headers(ADMIN_CHAT), json={
        "participant_id": pid, "role": "master", "enabled": True,
        "disciplines": ["music"]})

    me = _me(client, MASTER_CHAT)
    caps = set(me["capabilities"])
    assert caps == MASTER_CAPS
    assert not caps & NOT_MASTER_CAPS, f"лишние права: {caps & NOT_MASTER_CAPS}"

    for path in ("/tools/budget/summary", "/tools/dues/me"):
        res = client.get(path, headers=headers(MASTER_CHAT))
        assert res.status_code == 403, f"{path} открыт мастеру: {res.status_code}"


# -------------------------------- Г6: инвариант «мастер без дисциплины» — отчёт
def test_Г6_инвариант_M1_предупреждает_а_не_падает(client):
    """`WARN M1` со списком и код 0; на пустом view — `OK M1` (V-037, D6).

    Код возврата проверяется функцией, а не процессом целиком: на общей dev-базе
    I1 («баланс = накопленный итог») красный по данным независимо от ветки, и
    процесс вернёт 1 не из-за M1. Контракт карты — «M1 печатается как WARN и не
    добавляет падений», поэтому мерим именно `check_master_disciplines()`.
    """
    import contextlib
    import importlib.util
    import io
    import pathlib

    with psycopg.connect(_test_dsn()) as conn:
        _admin(conn)
        pid = _person(conn, name="Педагог Без Дисциплины")
        # роль выдана в обход маршрута: так и появляется мастер без дисциплины
        conn.execute("insert into participant_role (participant_id, role) "
                     "values (%s, 'master')", (pid,))
        conn.commit()

    env = dict(os.environ, DATABASE_URL=_test_dsn())
    warned = subprocess.run([sys.executable, "scripts/check_invariants.py"], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=120)
    assert "WARN M1" in warned.stdout, warned.stdout
    assert "FAIL M1" not in warned.stdout, warned.stdout
    assert "Педагог Без Дисциплины" in warned.stdout, warned.stdout

    os.environ["DATABASE_URL"] = _test_dsn()
    sys.path.insert(0, str(pathlib.Path(ROOT) / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "check_invariants_under_test", pathlib.Path(ROOT) / "scripts" / "check_invariants.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert module.check_master_disciplines() == 0, out.getvalue()
    assert "WARN M1" in out.getvalue(), out.getvalue()

    with psycopg.connect(_test_dsn()) as conn:
        _teach(conn, pid, "music")
    fixed = subprocess.run([sys.executable, "scripts/check_invariants.py"], cwd=ROOT,
                           env=env, capture_output=True, text=True, timeout=120)
    assert "OK   M1" in fixed.stdout, fixed.stdout
    assert "WARN M1" not in fixed.stdout, fixed.stdout
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert module.check_master_disciplines() == 0, out.getvalue()
    assert "OK   M1" in out.getvalue(), out.getvalue()


# ------------------------------------------------------- Г7: инвайт без мастера
def test_Г7_инвайт_мастера_отклонён(client):
    """422: код приглашения не несёт дисциплины, поэтому мастер им не выдаётся."""
    with psycopg.connect(_test_dsn()) as conn:
        _admin(conn)

    res = client.post("/admin/invite", headers=headers(ADMIN_CHAT),
                      json={"role": "master", "ttl_days": 7})
    assert res.status_code == 422, res.text
    with psycopg.connect(_test_dsn()) as conn:
        assert conn.execute("select count(*) from invite_code").fetchone()[0] == 0


# --------------------------------------------------------- Г8: отзыв роли
def test_Г8_отзыв_роли_оставляет_предметную_связь(client):
    """Права снимаются, `teacher`-связь и `is_teacher` остаются (140 §1)."""
    with psycopg.connect(_test_dsn()) as conn:
        _admin(conn)
        pid = _person(conn)

    client.post("/admin/grant", headers=headers(ADMIN_CHAT), json={
        "participant_id": pid, "role": "master", "enabled": True,
        "disciplines": ["music"]})
    res = client.post("/admin/grant", headers=headers(ADMIN_CHAT), json={
        "participant_id": pid, "role": "master", "enabled": False,
        "reason": "отпуск"})
    assert res.status_code == 200
    assert "master" not in res.json()["roles"]

    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select revoked_at from participant_role "
                           "where participant_id = %s and role = 'master'",
                           (pid,)).fetchone()
        assert row[0] is not None, "отзыв — пометка строки, а не удаление"
        assert _disciplines(conn, pid) == ["music"], "предметная связь осталась"
        assert conn.execute("select is_teacher from participant where id = %s",
                            (pid,)).fetchone()[0] is True

    me = _me(client, MASTER_CHAT)
    # возможности педагога сняты; остаться могут только те, что даёт роль студийца
    master_only = MASTER_CAPS - {"schedule.availability.self", "notes.write", "files.access"}
    assert not master_only & set(me["capabilities"]), f"права мастера остались: {me['capabilities']}"


# ------------------------------------------------- Г13: главная Mini App мастера
def test_Г13_главная_мастера_без_взносов(client):
    """`role_label` — «педагог»; строки «Мои взносы» нет; подпись с дисциплиной."""
    with psycopg.connect(_test_dsn()) as conn:
        _admin(conn)
        pid = _master_only(conn)
        _teach(conn, pid, "music")

    from app.api import webapp_api

    data = webapp_api.webapp_home_data(who={"id": pid, "chat_id": MASTER_CHAT,
                                            "status": "active",
                                            "display_name": "Педагог Теста"})
    assert data["role_label"] == "педагог"
    assert data["disciplines"] == [{"code": "music", "name": "Музыка"}]
    titles = {row["title"] for row in data["more"]["personal"]}
    # «Файлы студии» мастеру открыты решением V-039 (в) — право `files.access` (061),
    # а «Мои взносы» — нет: взносы не задача педагога (140 §4).
    assert "Мои взносы" not in titles
    assert "Файлы студии" in titles, f"файлы мастеру не открыты: {titles}"
    assert any("Заметк" in title for title in titles), f"заметок нет: {titles}"


# --------------------------------------- Г14: дисциплины мастера в «Поиске окон»
def test_Г14_экран_поиска_окон_знает_дисциплины(client):
    """`/webapp/slots/people` отдаёт `my_disciplines`; у администратора список пуст."""
    with psycopg.connect(_test_dsn()) as conn:
        admin = _admin(conn)
        pid = _master_only(conn)
        _teach(conn, pid, "music")

    from app.api import schedule as schedule_api

    mine = schedule_api.webapp_slots_people(who={"id": pid, "chat_id": MASTER_CHAT,
                                                 "status": "active",
                                                 "display_name": "Педагог Теста"})
    assert mine["my_disciplines"] == [{"code": "music", "name": "Музыка"}]

    root = schedule_api.webapp_slots_people(who={"id": admin, "chat_id": ADMIN_CHAT,
                                                 "status": "active",
                                                 "display_name": "Админ Теста"})
    assert root["my_disciplines"] == []


# ------------------------------------------------------- Г18: контакты мастера
def test_Г18_телефон_чужого_человека_мастеру_не_виден(client):
    """Список без `phone`, карточка — с маской: контакты только по `participants.contact`."""
    with psycopg.connect(_test_dsn()) as conn:
        _admin(conn)
        _master_only(conn)
        other = _person(conn, 700003, "Другой Человек")
        conn.execute("update participant set phone = '+79990000000' where id = %s", (other,))
        conn.commit()

    rows = client.get("/tools/participants", headers=headers(MASTER_CHAT)).json()["data"]
    assert rows, "мастер должен видеть состав (participants.view)"
    assert all("phone" not in row for row in rows)

    card = client.get(f"/tools/participants/{other}/card", headers=headers(MASTER_CHAT)).json()
    assert card["data"]["phone"] != "+799****0000", "телефон не маскирован"


# ------------------------------------ Г19: карточка несёт предупреждение
def test_Г19_карточка_несёт_предупреждение(client):
    """`data.warnings` — из `core.discipline_warnings`; после дисциплины список пуст."""
    with psycopg.connect(_test_dsn()) as conn:
        _admin(conn)
        pid = _master_only(conn, 700011, "Педагог Без Предмета")

    card = client.get(f"/tools/participants/{pid}/card", headers=headers(ADMIN_CHAT)).json()
    warnings = card["data"]["warnings"]
    assert warnings and warnings[0]["code"] == "master_without_discipline", card
    assert warnings[0]["text"], "у предупреждения нет текста"

    with psycopg.connect(_test_dsn()) as conn:
        _teach(conn, pid, "music")
    after = client.get(f"/tools/participants/{pid}/card", headers=headers(ADMIN_CHAT)).json()
    assert after["data"]["warnings"] == []
    assert [item["code"] for item in after["data"]["disciplines"]] == ["music"]


# ------------------- Г20: отказ без права на дисциплины сказан словами и в аудите
def test_Г20_нет_права_на_дисциплину_словами(client):
    """`403 participants_manage_forbidden` с текстом Б3 и запись `access.denied`."""
    from tests.integration.test_webapp_profile import _api, init_data

    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _master_only(conn, 700012, "Педагог Без Права")
        target = _person(conn, 700013, "Цель Правки")

    token = client.post("/webapp/auth", json={"init_data": init_data(700012)}).json()["token"]
    headers_w = {"Authorization": f"Bearer {token}"}

    for path, payload in (
        ("/webapp/admin/discipline", {"participant_id": target, "discipline": "music",
                                      "enabled": True}),
        ("/webapp/admin/grant", {"participant_id": target, "role": "master",
                                 "enabled": True}),
    ):
        res = client.post(path, json=payload, headers=headers_w)
        assert res.status_code == 403, f"{path}: {res.text}"
        assert res.json()["code"] == "participants_manage_forbidden", res.text
        assert res.json()["detail"] == "Роли и дисциплины педагога меняет администратор."

    with psycopg.connect(_test_dsn()) as conn:
        denied = conn.execute(
            "select after from audit_event where action = 'access.denied' "
            "and after->>'via' = 'webapp' order by id desc").fetchall()
        assert denied, "отказ должен попасть в аудит"
        assert all(row[0]["required"] == "participants.manage" for row in denied)
        assert _disciplines(conn, target) == []


# ------------------------------- Г21: справочник и анкета отдают дисциплины
def test_Г21_чтение_списка_дисциплин(client):
    """Порядок `sort_order` в справочнике и в анкете мастера; у труппы — пусто."""
    from tests.integration.test_webapp_profile import _api, init_data

    _api()
    with psycopg.connect(_test_dsn()) as conn:
        master = _master_only(conn, 700014, "Педагог Со Списком")
        _teach(conn, master, "music")
        _person(conn, 700015, "Студиец Без Предмета")

    master_token = client.post("/webapp/auth",
                               json={"init_data": init_data(700014)}).json()["token"]
    troupe_token = client.post("/webapp/auth",
                               json={"init_data": init_data(700015)}).json()["token"]
    h_master = {"Authorization": f"Bearer {master_token}"}
    h_troupe = {"Authorization": f"Bearer {troupe_token}"}

    form = client.get("/webapp/form-data", headers=h_master).json()
    assert [c["code"] for c in form["disciplines"]] == ["acting", "music", "dance", "speech"]

    mine = client.get("/webapp/profile", headers=h_master).json()
    assert mine["disciplines"] == [{"code": "music", "name": "Музыка"}]
    assert mine["warnings"] == []

    other = client.get("/webapp/profile", headers=h_troupe).json()
    assert other["disciplines"] == []
    assert other["warnings"] == []


# ------------------------- Г21б: пустой список у мастера — предупреждение в анкете
def test_Г21б_анкета_мастера_без_дисциплины_предупреждает(client):
    """`warnings` анкеты — `audience="self"`, ключи есть всегда (D3, D5)."""
    from tests.integration.test_webapp_profile import _api, init_data

    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _master_only(conn, 700016, "Педагог Без Анкеты")

    token = client.post("/webapp/auth", json={"init_data": init_data(700016)}).json()["token"]
    body = client.get("/webapp/profile",
                      headers={"Authorization": f"Bearer {token}"}).json()
    assert body["disciplines"] == []
    assert body["warnings"][0]["code"] == "master_without_discipline"
    assert "Поменять её может администратор." in body["warnings"][0]["text"]
