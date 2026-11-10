"""Скриншот обращения в `files_store`: не личный файл, не в квоте, не в поиске.

Э6 (карта t_f00525ab, docs/development/153 D1, D4, §4.2–§4.5). Скриншот живёт в
`file_object` со `scope='feedback'`, но ведёт себя не как файл человека:

  * не создаёт личную папку и не попадает в `file_folder` (К2);
  * не считается в квоте — иначе забитое место блокировало бы сообщение о дефекте (К3);
  * не виден в «Моих файлах», в поиске и не находится по имени (К4);
  * не проходит дедупликацию: иначе вернулась бы **личная** строка, а админ получил бы
    доступ к личному файлу по правилу «файл приложен к обращению» — дыра в FR-K9 (К5);
  * виден держателю `feedback.triage`, и только если файл приложен к обращению (К6);
  * не удаляется, пока обращение живо (К8).

Обоснование: FR-K9, FR-K14, V-055 (а); спека — карта t_1ba005e2 §5.

Проверки идут на подменённом коннекте: SQL читается, хранилище не трогается.
Живая схема проверяется в `tests/integration/test_feedback_attachment.py`.
"""

from __future__ import annotations

import pytest
from app import file_access, files_store

from tests.unit.test_files_store import PNG, _ScriptedConn

STORE = files_store
SHA = "a" * 64


def test_скриншот_без_личной_папки(monkeypatch):
    """К2: `scope='feedback'` не создаёт личную папку и связь с ней."""
    monkeypatch.setattr(STORE, "delete_object", lambda key: pytest.fail("объект трогать нельзя"))
    conn = _ScriptedConn([
        ("insert into file_object", [{"id": 91}]),
        ("select coalesce(sum(size_bytes), 0) as used", [{"used": 0}]),
    ])

    saved = STORE.save_committed(conn, 7, "IMG_0421.png", "image/png", 1800, None,
                                 "p7/shot.png", PNG, scope="feedback")

    assert saved.id == 91 and saved.folder_path == "", "у скриншота нет личной папки"
    index = [number for number, sql in enumerate(conn.sql)
             if "insert into file_object" in sql][0]
    assert "feedback" in conn.params[index], "scope записывается, а не берётся по умолчанию"
    assert not any("from folder" in sql for sql in conn.sql), "личная папка не создаётся"
    assert not any("insert into file_folder" in sql for sql in conn.sql), \
        "скриншот не попадает в «Мои файлы»"
    assert not any("for update" in sql for sql in conn.sql), \
        "квоту скриншот не проверяет — блокировка строки ему не нужна"


def test_скриншот_не_считается_в_квоте(monkeypatch):
    """К3: при полной квоте скриншот записывается, а `used_bytes` не растёт."""
    monkeypatch.setattr(STORE, "delete_object", lambda key: pytest.fail("объект трогать нельзя"))
    conn = _ScriptedConn([
        ("insert into file_object", [{"id": 92}]),
        ("select coalesce(sum(size_bytes), 0) as used", [{"used": STORE.QUOTA_BYTES}]),
    ])

    saved = STORE.save_committed(conn, 7, "IMG_0001.png", "image/png", 8 * 1024 ** 2, None,
                                 "p7/full.png", PNG, scope="feedback")

    assert saved.id == 92
    insert = [sql for sql in conn.sql if "insert into file_object" in sql][0]
    assert "where (select coalesce(sum(size_bytes), 0)" not in insert, \
        "условие по сумме для скриншота выключено (V-055 (а))"
    used = [sql for sql in conn.sql if "select coalesce(sum(size_bytes), 0) as used" in sql]
    assert used and "scope <> %s" in used[0], "квота считается без скриншотов обращения"


def test_квота_личных_файлов_осталась(monkeypatch):
    """К3, контроль: без `scope` тот же полный диск по-прежнему отказывает."""
    monkeypatch.setattr(STORE, "delete_object", lambda key: None)
    conn = _ScriptedConn([
        ("select id from participant", [{"id": 7}]),
        ("insert into file_object", []),
        ("select coalesce(sum(size_bytes), 0) as used", [{"used": STORE.QUOTA_BYTES}]),
    ])

    with pytest.raises(STORE.FileRejected) as caught:
        STORE.save_committed(conn, 7, "смета.png", "image/png", 500, None, "p7/a.png", PNG)

    assert caught.value.code == "file_quota_exceeded"
    insert = [sql for sql in conn.sql if "insert into file_object" in sql][0]
    assert "where (select coalesce(sum(size_bytes), 0)" in insert, \
        "фильтр не должен «выключать» квоту для личных файлов"


def test_скриншот_не_виден_в_списке(monkeypatch):
    """К4: `list_files` не показывает скриншоты обращения."""
    conn = _ScriptedConn([("from file_object f", [{"id": 5, "name": "смета.pdf"}])])

    rows = STORE.list_files(conn, 7)

    assert [row["id"] for row in rows] == [5]
    sql, params = conn.sql[0], conn.params[0]
    assert "f.scope <> %s" in sql and STORE.FEEDBACK_SCOPE in params


def test_скриншот_не_находится_поиском(monkeypatch):
    """К4: обе ветки поиска — полнотекстовая и `ilike` — фильтруют скриншоты."""
    full_text = _ScriptedConn([("websearch_to_tsquery", [{"id": 5, "name": "смета.pdf"}])])
    assert [row["id"] for row in STORE.search(full_text, 7, "смета")] == [5]
    assert "f.scope <> %(scope)s" in full_text.sql[0]
    assert STORE.FEEDBACK_SCOPE in str(full_text.params[0])

    # полнотекстовый путь не нашёл ничего — включается запасной `ilike`
    short = _ScriptedConn([("websearch_to_tsquery", []),
                           ("f.name ilike", [{"id": 5, "name": "смета.pdf"}])])
    assert [row["id"] for row in STORE.search(short, 7, "смт")] == [5]
    assert "f.scope <> %s" in short.sql[1]
    assert STORE.FEEDBACK_SCOPE in short.params[1]


def test_дедупликация_не_подменяет_скриншот_личным(monkeypatch):
    """К5: тот же sha256 — всё равно новая строка, личный файл не переиспользуется."""
    monkeypatch.setattr(STORE, "delete_object", lambda key: pytest.fail("объект трогать нельзя"))
    conn = _ScriptedConn([
        ("insert into file_object", [{"id": 93}]),
        ("select coalesce(sum(size_bytes), 0) as used", [{"used": 0}]),
    ])

    saved = STORE.save_committed(conn, 7, "IMG_0421.png", "image/png", 1800, SHA,
                                 "p7/dup.png", PNG, scope="feedback")

    assert saved.id == 93 and saved.duplicate is False
    assert not any("sha256 = %s" in sql for sql in conn.sql), \
        "поиск дубля для скриншота не выполняется (§4.3)"


def test_поиск_дубля_не_видит_скриншоты(monkeypatch):
    """К5, обратная сторона: личная загрузка не «находит» скриншот в дублях."""
    conn = _ScriptedConn([("sha256 = %s", [])])

    STORE.find_duplicate(conn, 7, SHA)

    assert "scope <> %s" in conn.sql[0]
    assert STORE.FEEDBACK_SCOPE in conn.params[0]


def _file_row(**extra) -> dict:
    row = {"id": 91, "name": "IMG_0421.png", "mime": "image/png", "size_bytes": 1800,
           "kind": "image", "object_key": "p7/shot.png", "bucket": "svoi-private",
           "sha256": None, "scope": "feedback", "participant_id": 9, "created_at": None,
           "cold_at": None, "folder": None}
    row.update(extra)
    return row


def test_приложенный_скриншот_видит_держатель_triage(monkeypatch):
    """К6: право `feedback.triage` + живая связь — единственное исключение из FR-K9."""
    monkeypatch.setattr(file_access, "roles_of", lambda who: ["admin"])
    monkeypatch.setattr(file_access, "active_grant", lambda conn, fid, pid: None)
    conn = _ScriptedConn([
        ("from file_object f", [_file_row()]),
        ("folder fo join", []),
        ("v_participant_capabilities", [{"capability": "feedback.triage"}]),
        ("from feedback_attachment", [{"file_id": 91}]),
    ])

    row, as_admin = STORE.file_for_reader(conn, 91, {"id": 1, "roles": ["admin"]})

    assert row is not None and as_admin is True, "в аудите видно, что открыл не владелец"
    assert "capability = 'feedback.triage'" in conn.sql[2]


def test_скриншот_без_связи_не_виден_даже_админу(monkeypatch):
    """К6, контроль: файл со `scope='feedback'`, но без строки связи — 404."""
    monkeypatch.setattr(file_access, "roles_of", lambda who: ["admin"])
    monkeypatch.setattr(file_access, "active_grant", lambda conn, fid, pid: None)
    conn = _ScriptedConn([
        ("from file_object f", [_file_row()]),
        ("folder fo join", []),
        ("v_participant_capabilities", [{"capability": "feedback.triage"}]),
        ("from feedback_attachment", []),
    ])

    assert STORE.file_for_reader(conn, 91, {"id": 1, "roles": ["admin"]}) == (None, False)


def test_личный_файл_автора_остался_невидимым(monkeypatch):
    """К6, контроль: FR-K9 в силе — чужой личный файл без связи не отдаётся."""
    monkeypatch.setattr(file_access, "roles_of", lambda who: ["admin"])
    monkeypatch.setattr(file_access, "active_grant", lambda conn, fid, pid: None)
    conn = _ScriptedConn([
        ("from file_object f", [_file_row(scope="personal", participant_id=9)]),
        ("folder fo join", []),
        ("v_participant_capabilities", [{"capability": "feedback.triage"}]),
        ("from feedback_attachment", []),
    ])

    assert STORE.file_for_reader(conn, 91, {"id": 1}) == (None, False)


def test_удаление_приложенного_скриншота_отказ(monkeypatch):
    """К8: пока обращение живо, файл не удаляется — иначе в обращении битая ссылка."""
    conn = _ScriptedConn([
        ("from file_object f", [_file_row()]),
        ("from feedback_attachment", [{"feedback_id": 37}]),
    ])

    with pytest.raises(STORE.FileRejected) as caught:
        STORE.remove(conn, {"id": 9}, 91)

    assert caught.value.code == "file_in_feedback" and caught.value.status == 409
    assert "№37" in caught.value.message
    assert not any("update file_object" in sql for sql in conn.sql)


def test_скриншот_без_связи_удаляется(monkeypatch):
    """К8, контроль: связь снята (обращение удалено каскадом) — удаление как обычно."""
    conn = _ScriptedConn([
        ("from file_object f", [_file_row()]),
        ("from feedback_attachment", []),
        ("delete from file_folder", []),
        ("update file_object set deleted_at", []),
    ])

    result = STORE.remove(conn, {"id": 9}, 91)

    assert result["id"] == 91
    assert any("update file_object set deleted_at" in sql for sql in conn.sql)
