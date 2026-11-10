"""Скриншот в обращении: схема, права, отправка, выдача и отказ в удалении (Э6).

Карта t_f00525ab, спецификация t_1ba005e2, дизайн `docs/development/153`, решения
D1–D8. Проверяется то, чего нельзя проверить на подменённом коннекте:

  * миграция 063 — таблица `feedback_attachment` и новое значение `scope='feedback'` (К1);
  * экран файла и выдача байтов по праву `feedback.triage`, а не по роли (К7, К19, К20);
  * удаление приложенного скриншота — отказ 409 с номером обращения (К8);
  * presign по `feedback.send` без учёта квоты (К9–К11);
  * отправка с вложениями: порядок, чужие ключи, пустой и большой файл, атомарность
    (К12–К15), ограничение в три файла (К16) и неизменность пути чата (К17);
  * очередь отдаёт `attachments` обоим входам (К18).

Хранилище — в памяти (`_Ram`): Garage в гейте не поднят, а проверяется код, а не
провайдер. Ветка с настоящим бакетом — `tests/integration/test_webapp_files.py`.

Обоснование: FR-K9 (личные файлы), FR-K14 (квота), FR-I1 (обратная связь), V-055 (а).
"""

from __future__ import annotations

import hashlib
import io
import pathlib

import psycopg
import pytest
from app import cold, core, files_store
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import BOT_TOKEN, init_data

PNG = b"\x89PNG\r\n\x1a\n" + b"shot" * 32
PDF = b"%PDF-1.7\n" + b"doc" * 32


class _Ram:
    """Хранилище в памяти: те же вызовы, что у boto3, без сети и без бакета."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_object(self, Bucket, Key, Body, ContentLength=None, ContentType=None):  # noqa: N803
        data = Body.read() if hasattr(Body, "read") else bytes(Body)
        self.objects[Key] = (data, ContentType or "application/octet-stream")

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise OSError("нет такого объекта")
        data, mime = self.objects[Key]
        return {"ContentLength": len(data), "ContentType": mime}

    def get_object(self, Bucket, Key, Range=None):  # noqa: N803
        if Key not in self.objects:
            raise OSError("нет такого объекта")
        data, mime = self.objects[Key]
        if Range:
            start = int(Range.replace("bytes=", "").split("-")[0])
            data = data[start:start + files_store.MAGIC_BYTES]
        return {"Body": io.BytesIO(data), "ContentType": mime, "ContentLength": len(data)}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)


class _Urls:
    """Подпись ссылок: адрес не нужен, важно что он есть и на что указывает."""

    def generate_presigned_url(self, method, Params, ExpiresIn):  # noqa: N803
        return f"https://storage.test/{method}/{Params['Key']}?ttl={ExpiresIn}"


@pytest.fixture()
def storage(monkeypatch) -> _Ram:
    store = _Ram()
    monkeypatch.setattr(files_store, "storage", lambda: store)
    monkeypatch.setattr(files_store, "public_client", lambda: _Urls())
    return store


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _webapp(client, chat_id: int) -> dict:
    core.BOT_TOKEN = BOT_TOKEN
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _bot(chat_id: int) -> dict:
    """Путь чата: сервисный токен и автор в заголовке (как у бота)."""
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _upload(client, store: _Ram, headers: dict, name: str, data: bytes,
            mime: str = "image/png") -> dict:
    """То же, что делает страница: presign отдаёт ключ, браузер кладёт байты."""
    slot = client.post("/webapp/feedback/presign",
                       json={"name": name, "mime": mime, "size": len(data)},
                       headers=headers)
    assert slot.status_code == 200, slot.text
    body = slot.json()
    store.objects[body["object_key"]] = (data, mime)
    return {"object_key": body["object_key"], "name": name, "mime": mime,
            "sha256": hashlib.sha256(data).hexdigest()}


def _send(client, headers: dict, attachments: list[dict], text: str = "Не работает кнопка"):
    return client.post("/webapp/feedback",
                       json={"kind": "bug", "text": text, "attachments": attachments},
                       headers=headers)


def _counts(dsn: str) -> tuple[int, int, int]:
    with psycopg.connect(dsn) as conn:
        return tuple(conn.execute("select (select count(*) from feedback),"
                                  " (select count(*) from feedback_attachment),"
                                  " (select count(*) from file_object)").fetchone())


# --------------------------------------------------------------- схема (К1)

def test_миграция_создаёт_связь_и_разрешает_scope_feedback(client) -> None:
    """К1: таблица, ключи и каскады; `scope='feedback'` проходит, `bogus` падает."""
    with psycopg.connect(_test_dsn()) as conn:
        columns = {row[0] for row in conn.execute(
            "select column_name from information_schema.columns"
            " where table_name = 'feedback_attachment'").fetchall()}
        assert columns == {"feedback_id", "file_id", "position", "created_at"}

        constraints = dict(conn.execute(
            "select conname, contype from pg_constraint"
            " where conrelid = 'feedback_attachment'::regclass").fetchall())
        assert constraints.get("feedback_attachment_file_id_key") == "u", "unique (file_id)"
        assert sorted(name for name, kind in constraints.items() if kind == "f") == \
            ["feedback_attachment_feedback_id_fkey", "feedback_attachment_file_id_fkey"]
        cascades = conn.execute(
            "select count(*) from pg_constraint where conrelid = 'feedback_attachment'::regclass"
            " and contype = 'f' and confdeltype = 'c'").fetchone()[0]
        assert cascades == 2, "обе связи снимаются каскадом"

        owner = _person(conn, 260500, "Владелец Схемы")
        check = conn.execute(
            "select pg_get_constraintdef(oid) from pg_constraint"
            " where conname = 'file_object_scope_check'").fetchone()[0]
        assert "'feedback'" in check
        conn.execute(
            "insert into file_object (participant_id, name, mime, size_bytes, scope,"
            " uploaded_via, object_key) values (%s,'скрин.png','image/png',10,'feedback',"
            "'webapp','p'||%s||'/schema.png')", (owner, owner))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "insert into file_object (participant_id, name, mime, size_bytes, scope,"
                " uploaded_via, object_key) values (%s,'x.png','image/png',10,'bogus',"
                "'webapp','p'||%s||'/bogus.png')", (owner, owner))


# ---------------------------------------------------- presign: право и лимиты (К9–К11)

def test_presign_по_праву_feedback_send(client, storage) -> None:
    """К9: admin, org и troupe получают ссылку, мастеру отказ по праву."""
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 260501, "Труппа Прес", "troupe")
        _person(conn, 260502, "Орг Прес", "org")
        _person(conn, 260503, "Админ Прес", "admin")
        _person(conn, 260504, "Мастер Прес", "master")

    for chat in (260501, 260502, 260503):
        answer = client.post("/webapp/feedback/presign",
                             json={"name": "a.png", "mime": "image/png", "size": 1024},
                             headers=_webapp(client, chat))
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["upload_url"] and body["object_key"].startswith("p")
        assert body["expires_in"] == 900
        assert body["max_files"] == 3 and body["max_bytes"] == 8 * 1024 ** 2

    denied = client.post("/webapp/feedback/presign",
                         json={"name": "a.png", "mime": "image/png", "size": 1024},
                         headers=_webapp(client, 260504))
    assert denied.status_code == 403, denied.text
    assert "feedback.send" in denied.text


def test_presign_отбивает_не_картинку_и_перебор(client, storage) -> None:
    """К10: pdf — 422, больше 8 МБ — 413, ровно 8 МБ — 200 (граница включительно)."""
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 260510, "Автор Лимита")
    headers = _webapp(client, 260510)

    pdf = client.post("/webapp/feedback/presign",
                      json={"name": "smeta.pdf", "mime": "application/pdf", "size": 1024},
                      headers=headers)
    assert pdf.status_code == 422
    assert pdf.json()["code"] == "file_type_not_allowed"
    assert "только изображение" in pdf.json()["detail"]
    assert "upload_url" not in pdf.json()

    big = client.post("/webapp/feedback/presign",
                      json={"name": "photo.png", "mime": "image/png",
                            "size": 8 * 1024 ** 2 + 1}, headers=headers)
    assert big.status_code == 413
    assert big.json()["code"] == "file_too_large"
    assert "8 МБ" in big.json()["detail"]
    assert "upload_url" not in big.json()

    edge = client.post("/webapp/feedback/presign",
                       json={"name": "photo.png", "mime": "image/png",
                             "size": 8 * 1024 ** 2}, headers=headers)
    assert edge.status_code == 200, edge.text


def test_presign_не_смотрит_на_квоту(client, storage) -> None:
    """К11: забитое личное место не мешает приложить скриншот (V-055 (а))."""
    with psycopg.connect(_test_dsn()) as conn:
        owner = _person(conn, 260520, "Автор Квоты")
        conn.execute(
            "insert into file_object (participant_id, name, mime, size_bytes, scope,"
            " uploaded_via, object_key) values (%s,'большой.bin','application/octet-stream',"
            " %s,'personal','webapp','p'||%s||'/big.bin')",
            (owner, files_store.QUOTA_BYTES, owner))
        conn.commit()
    headers = _webapp(client, 260520)

    answer = client.post("/webapp/feedback/presign",
                         json={"name": "shot.png", "mime": "image/png",
                               "size": 2 * 1024 ** 2}, headers=headers)

    assert answer.status_code == 200, answer.text
    assert answer.json()["upload_url"]


# ------------------------------------------------------------ отправка (К12–К17)

def test_отправка_связывает_три_файла_по_порядку(client, storage) -> None:
    """К12: три вложения — три строки с `position` 0, 1, 2 и тот же список в `mine`."""
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 260530, "Автор Трёх")
    headers = _webapp(client, 260530)
    shots = [_upload(client, storage, headers, f"shot{i}.png", PNG + bytes([i]))
             for i in range(3)]

    answer = _send(client, headers, shots, text="Не работает неделя")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["ok"] is True and body["id"]
    assert [item["name"] for item in body["attachments"]] == \
        ["shot0.png", "shot1.png", "shot2.png"]
    assert all(item["id"] and item["size_bytes"] == len(PNG) + 1
               for item in body["attachments"])

    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select position, file_id from feedback_attachment"
            " where feedback_id = %s order by position", (body["id"],)).fetchall()
        scopes = conn.execute("select scope from file_object").fetchall()
    assert [row[0] for row in rows] == [0, 1, 2]
    assert {row[0] for row in scopes} == {"feedback"}

    mine = client.get("/webapp/feedback/mine", headers=headers).json()
    item = next(entry for entry in mine["data"] if entry["id"] == body["id"])
    assert [shot["name"] for shot in item["attachments"]] == \
        ["shot0.png", "shot1.png", "shot2.png"]
    assert mine["limits"] == {"files": 3, "bytes": 8 * 1024 ** 2,
                              "mime": ["image/png", "image/jpeg", "image/webp", "image/gif"]}
    assert item["attachments"][0]["id"] == rows[0][1]


def test_чужой_ключ_пропавший_объект_и_пустой_файл(client, storage) -> None:
    """К13: 403 на чужой ключ, 404 на пропавший объект, 400 на пустой — без строк."""
    with psycopg.connect(_test_dsn()) as conn:
        owner = _person(conn, 260540, "Автор Ключа")
    headers = _webapp(client, 260540)
    before = _counts(_test_dsn())

    foreign = _send(client, headers, [{"object_key": f"p{owner + 1}/x.png",
                                       "name": "x.png", "mime": "image/png",
                                       "sha256": None}])
    assert foreign.status_code == 403
    assert foreign.json()["code"] == "file_key_foreign"

    missing = _send(client, headers, [{"object_key": f"p{owner}/нет.png",
                                       "name": "нет.png", "mime": "image/png",
                                       "sha256": None}])
    assert missing.status_code == 404
    assert missing.json()["code"] == "file_not_uploaded"
    assert "не долетел" in missing.json()["detail"]

    empty = _send(client, headers, [{"object_key": f"p{owner}/empty.png",
                                     "name": "empty.png", "mime": "image/png",
                                     "sha256": None}])
    assert empty.status_code == 404, "ключа нет в хранилище — та же ветка"

    storage.objects[f"p{owner}/empty.png"] = (b"", "image/png")
    empty = _send(client, headers, [{"object_key": f"p{owner}/empty.png",
                                     "name": "empty.png", "mime": "image/png",
                                     "sha256": None}])
    assert empty.status_code == 400
    assert empty.json()["code"] == "file_empty"

    assert _counts(_test_dsn()) == before, "отказы не оставляют строк"


def test_не_картинка_и_перебор_размера_на_отправке(client, storage) -> None:
    """К14: pdf по сигнатуре — 422, 9 МБ — 413; объект удаляется, строк нет."""
    with psycopg.connect(_test_dsn()) as conn:
        owner = _person(conn, 260550, "Автор Тяжёлого")
    headers = _webapp(client, 260550)
    before = _counts(_test_dsn())

    # pdf отбивает уже presign, поэтому объект кладём в хранилище напрямую: проверяем
    # проверку на отправке, а её presign покрыт в К10
    pdf_key = f"p{owner}/smeta.pdf"
    storage.objects[pdf_key] = (PDF, "application/pdf")
    pdf = {"object_key": pdf_key, "name": "smeta.pdf", "mime": "application/pdf",
           "sha256": hashlib.sha256(PDF).hexdigest()}
    big = _upload(client, storage, headers, "heavy.png", PNG)

    answer = _send(client, headers, [pdf])
    assert answer.status_code == 422, answer.text
    assert answer.json()["code"] == "file_type_not_allowed"
    assert pdf["object_key"] not in storage.objects, "не-картинка снимается из бакета"

    # размер проверяется по объекту в хранилище, а не по заявленному в presign
    storage.objects[big["object_key"]] = (b"x" * (files_store.FEEDBACK_MAX_BYTES + 1),
                                          "image/png")
    answer = _send(client, headers, [big])
    assert answer.status_code == 413, answer.text
    assert answer.json()["code"] == "file_too_large"
    assert big["object_key"] not in storage.objects

    assert _counts(_test_dsn()) == before


def test_отказ_на_втором_вложении_не_оставляет_половины(client, storage) -> None:
    """К15: валидный первый и чужой второй — 403, ни обращения, ни файла, ни связи."""
    with psycopg.connect(_test_dsn()) as conn:
        owner = _person(conn, 260560, "Автор Половины")
    headers = _webapp(client, 260560)
    before = _counts(_test_dsn())
    good = _upload(client, storage, headers, "good.png", PNG)

    answer = _send(client, headers, [good, {"object_key": f"p{owner + 1}/evil.png",
                                            "name": "evil.png", "mime": "image/png",
                                            "sha256": None}])

    assert answer.status_code == 403, answer.text
    assert _counts(_test_dsn()) == before, "транзакция откатилась целиком"
    assert good["object_key"] not in storage.objects, "принятый объект снят"
    assert f"p{owner + 1}/evil.png" not in storage.objects


def test_четыре_вложения_отбиваются_валидацией(client, storage) -> None:
    """К16: `max_length=3` — 422 от pydantic, обращения нет."""
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 260570, "Автор Четырёх")
    headers = _webapp(client, 260570)
    before = _counts(_test_dsn())

    answer = _send(client, headers, [{"object_key": f"p1/x{i}.png", "name": f"x{i}.png",
                                      "mime": "image/png", "sha256": None}
                                     for i in range(4)])

    assert answer.status_code == 422, answer.text
    assert _counts(_test_dsn()) == before


def test_чат_вложения_не_принимает(client, storage) -> None:
    """К17: `/tools/feedback` работает как раньше и связей не создаёт."""
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 260580, "Автор Чата")
    before = _counts(_test_dsn())

    answer = client.post("/tools/feedback", headers=_bot(260580),
                         json={"kind": "bug", "text": "Кнопка не работает",
                               "attachments": [{"object_key": "p1/x.png", "name": "x.png",
                                                "mime": "image/png", "sha256": None}]})

    assert answer.status_code == 200, answer.text
    assert answer.json()["ok"] is True and "attachments" not in answer.json()
    assert _counts(_test_dsn()) == (before[0] + 1, before[1], before[2]), \
        "поле attachments моделью чата не принимается — и строк связи не появляется"


# ------------------------------------------------------- очередь и выдача (К18–К20)

def _with_shot(client, storage) -> tuple[int, int, dict]:
    """Обращение со скриншотом: `(feedback_id, file_id, заголовки автора)`."""
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 260590, "Автор Очереди")
        _person(conn, 260500, "Админ Очереди", "admin")
    headers = _webapp(client, 260590)
    shot = _upload(client, storage, headers, "queue.png", PNG)
    created = _send(client, headers, [shot], text="Не работает очередь").json()
    return created["id"], created["attachments"][0]["id"], headers


def test_очередь_отдаёт_вложения_обоим_входам(client, storage) -> None:
    """К18: `/tools/feedback` и `/webapp/admin/feedback` — одна форма ответа."""
    feedback_id, file_id, _headers = _with_shot(client, storage)
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 260591, "Орг Очереди", "org")

    expected = [{"id": file_id, "name": "queue.png", "mime": "image/png",
                 "size_bytes": len(PNG)}]
    tools = client.get("/tools/feedback?open_only=true&limit=100", headers=_bot(260500))
    assert tools.status_code == 200, tools.text
    item = next(entry for entry in tools.json()["data"] if entry["id"] == feedback_id)
    assert item["attachments"] == expected, "мост dev не получает вложение"

    panel = client.get("/webapp/admin/feedback", headers=_webapp(client, 260500))
    assert panel.status_code == 200, panel.text
    item = next(entry for entry in panel.json()["data"] if entry["id"] == feedback_id)
    assert item["attachments"] == expected, "очередь Mini App и мост видят разное"

    assert client.get("/tools/feedback", headers=_bot(260591)).status_code == 403
    assert client.get("/webapp/admin/feedback",
                      headers=_webapp(client, 260591)).status_code == 403


def test_поток_байтов_моста(client, storage) -> None:
    """К19: байты совпадают, заголовки на месте, аудит `file.view` с `via=feedback`."""
    feedback_id, file_id, _headers = _with_shot(client, storage)

    answer = client.get(f"/tools/feedback/{feedback_id}/attachment/{file_id}",
                        headers=_bot(260500))

    assert answer.status_code == 200, answer.text
    assert answer.content == PNG
    assert answer.headers["content-type"] == "image/png"
    assert "filename*=UTF-8''queue.png" in answer.headers["content-disposition"]
    assert answer.headers["x-file-id"] == str(file_id)

    with psycopg.connect(_test_dsn()) as conn:
        audit = conn.execute(
            "select after from audit_event where action = 'file.view'"
            " and entity_id = %s order by id desc limit 1", (str(file_id),)).fetchone()[0]
    assert audit["via"] == "feedback" and audit["as_admin"] is True

    wrong = client.get(f"/tools/feedback/{feedback_id + 1}/attachment/{file_id}",
                       headers=_bot(260500))
    assert wrong.status_code == 404
    assert wrong.json()["code"] == "file_not_found"


def test_поток_закрыт_правом_и_восстанавливает_холод(client, storage, monkeypatch) -> None:
    """К20: org — 403; холодный файл возвращается, без копии — 410."""
    feedback_id, file_id, _headers = _with_shot(client, storage)
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 260592, "Орг Холода", "org")

    denied = client.get(f"/tools/feedback/{feedback_id}/attachment/{file_id}",
                        headers=_bot(260592))
    assert denied.status_code == 403, denied.text

    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update file_object set cold_at = now() where id = %s", (file_id,))
        conn.commit()
    restored = {}
    monkeypatch.setattr(cold, "restore", lambda conn, fid: restored.setdefault("id", fid) and True)

    warm = client.get(f"/tools/feedback/{feedback_id}/attachment/{file_id}",
                      headers=_bot(260500))
    assert warm.status_code == 200 and restored["id"] == file_id
    with psycopg.connect(_test_dsn()) as conn:
        audit = conn.execute(
            "select after from audit_event where action = 'file.view'"
            " and entity_id = %s order by id desc limit 1", (str(file_id),)).fetchone()[0]
    assert audit["restored"] is True

    def missing(conn, fid):
        raise FileNotFoundError(fid)

    monkeypatch.setattr(cold, "restore", missing)
    cold_file = client.get(f"/tools/feedback/{feedback_id}/attachment/{file_id}",
                           headers=_bot(260500))
    assert cold_file.status_code == 410
    assert cold_file.json()["code"] == "file_cold_missing"


def test_экран_файла_открывает_скриншот_админу(client, storage) -> None:
    """К7: `/webapp/files/{id}/view` отдаёт ссылку админу и пишет `as_admin=true`."""
    _feedback_id, file_id, _headers = _with_shot(client, storage)

    answer = client.get(f"/webapp/files/{file_id}/view", headers=_webapp(client, 260500))

    assert answer.status_code == 200, answer.text
    assert answer.json()["url"].startswith("https://storage.test/")
    assert answer.json()["as_admin"] is True
    with psycopg.connect(_test_dsn()) as conn:
        audit = conn.execute(
            "select after from audit_event where action = 'file.view'"
            " and entity_id = %s order by id desc limit 1", (str(file_id),)).fetchone()[0]
    assert audit["as_admin"] is True

    with psycopg.connect(_test_dsn()) as conn:
        linkless = conn.execute(
            "insert into file_object (participant_id, name, mime, size_bytes, scope,"
            " uploaded_via, object_key)"
            " select id, 'без связи.png', 'image/png', 10, 'feedback', 'webapp',"
            " 'p' || id || '/linkless.png' from participant where tg_chat_id = 260590"
            " returning id").fetchone()[0]
        conn.commit()
    control = client.get(f"/webapp/files/{linkless}/view", headers=_webapp(client, 260500))
    assert control.status_code == 404
    assert control.json()["code"] == "file_not_found"


def test_приложенный_скриншот_не_удаляется(client, storage) -> None:
    """К8: 409 с номером обращения; после снятия связи — обычное мягкое удаление.

    Удаление идёт путём чата (`DELETE /files/{id}`): у студийца права `files.access`
    нет (миграция 062), и Mini App ему этот экран закрыт. Домен удаления один и тот же
    — `files_store.remove` отвечает отказом независимо от входа.
    """
    feedback_id, file_id, _headers = _with_shot(client, storage)
    headers = _bot(260590)

    denied = client.delete(f"/files/{file_id}", headers=headers)

    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "file_in_feedback"
    assert f"№{feedback_id}" in denied.json()["detail"]
    with psycopg.connect(_test_dsn()) as conn:
        deleted = conn.execute("select deleted_at from file_object where id = %s",
                               (file_id,)).fetchone()[0]
    assert deleted is None

    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("delete from feedback_attachment where file_id = %s", (file_id,))
        conn.commit()
    allowed = client.delete(f"/files/{file_id}", headers=headers)
    assert allowed.status_code == 200, allowed.text
    with psycopg.connect(_test_dsn()) as conn:
        deleted = conn.execute("select deleted_at from file_object where id = %s",
                               (file_id,)).fetchone()[0]
    assert deleted is not None
