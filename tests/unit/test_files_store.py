"""Тип файла решает сигнатура, а не расширение (FR-K22, docs/development/86).

Ключевой случай — `.docx`: по сигнатуре это zip-контейнер, но по смыслу документ.
Если проверять только «PK → архив», половина пьес и смет не загрузится; если только
расширение — `.zip`, переименованный в `.docx`, пройдёт. Поэтому проверяются оба признака.
"""

import pathlib

import pytest
from app import files_store

PDF = b"%PDF-1.7\n"
PNG = b"\x89PNG\r\n\x1a\n"
ZIP = b"PK\x03\x04" + b"\x00" * 12
MP3 = b"ID3\x04\x00\x00"
WAV = b"RIFF\x24\x08\x00\x00WAVEfmt "
M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00"
MP4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00"
EXE = b"MZ\x90\x00"


STORE = files_store

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.mark.parametrize(("name", "mime", "head", "kind"), [
    ("пьеса.pdf", "application/pdf", PDF, "text"),
    ("сцена.docx", DOCX_MIME, ZIP, "text"),             # zip по сигнатуре, документ по смыслу
    ("заметка.md", "text/markdown", b"# note", "text"),
    ("фото.png", "image/png", PNG, "image"),
    ("трек.mp3", "audio/mpeg", MP3, "audio"),
    ("запись.wav", "audio/wav", WAV, "audio"),
    ("трек.m4a", "audio/mp4", M4A, "audio"),            # ftyp, но аудио-контейнер
    ("без_расширения", "application/pdf", PDF, "text"),
])
def test_принимаемые_типы(name, mime, head, kind):
    assert STORE.detect_kind(name, mime, head) == kind


@pytest.mark.parametrize(("name", "mime", "head", "code"), [
    ("архив.zip", "application/zip", ZIP, "file_archive"),
    ("архив.rar", "application/x-rar", b"Rar!\x1a\x07", "file_archive"),
    (".docx", DOCX_MIME, ZIP, "text"),                  # пустое имя с точкой — не архив
    ("фильм.mp4", "video/mp4", MP4, "file_video"),
    ("клип.mov", "video/quicktime", b"\x00\x00\x00\x14ftypqt  ", "file_video"),
    ("программа.exe", "application/octet-stream", EXE, "file_executable"),
    ("скрипт.sh", "text/x-sh", b"#!/bin/sh\n", "file_executable"),
    ("нечто.bin", "application/x-msdownload", b"\x00\x01", "file_type_not_allowed"),
    ("скачанное", "application/octet-stream", ZIP, "file_archive"),
])
def test_запрещённые_типы(name, mime, head, code):
    if code == "text":
        assert STORE.detect_kind(name, mime, head) == "text"
        return
    with pytest.raises(STORE.FileRejected) as caught:
        STORE.detect_kind(name, mime, head)
    assert caught.value.code == code


def test_переименованный_архив_не_проходит():
    """`.zip`, переименованный в `.docx`, выдаёт себя сигнатурой — но и расширение важно.

    Здесь проверяем обратное: офисное расширение с zip-сигнатурой — это норма, а
    zip-сигнатура без офисного расширения — архив.
    """
    with pytest.raises(STORE.FileRejected):
        STORE.detect_kind("пьеса.bin", "application/octet-stream", ZIP)


def test_ключ_объекта_без_имён_и_кириллицы():
    key = STORE.object_key(42, ".mp3")
    assert key.startswith("p42/") and key.endswith(".mp3")
    assert not any("а" <= ch.lower() <= "я" for ch in key)
    assert STORE.object_key(42, ".mp3") != STORE.object_key(42, ".mp3")


def test_расширение_по_mime_если_имени_нет():
    assert STORE.ext_for("документ", "application/pdf") == ".pdf"
    assert STORE.ext_for("трек", "audio/mpeg") == ".mp3"
    assert STORE.ext_for("имя.docx", "application/pdf") == ".docx"


def test_лимиты_совпадают_с_документом():
    """20 МБ — предел Bot API, 50 МБ — предел отправки ботом, 2 ГБ — квота (FR-K14)."""
    assert STORE.MAX_UPLOAD_BYTES == 20 * 1024 * 1024
    assert STORE.MAX_CHAT_SEND_BYTES == 50 * 1024 * 1024
    assert STORE.QUOTA_BYTES == 2 * 1024 ** 3


# Имя файла приходит от человека и уходит в заголовок ответа. Заголовок собирается
# ровно в одном месте — `attachment_header`; тесты ниже держат и сборку, и её вызовы.

def test_обычное_имя_отдаётся_в_двух_видах():
    header = STORE.attachment_header("report.pdf")
    assert header == "attachment; filename=\"report.pdf\"; filename*=UTF-8''report.pdf"


@pytest.mark.parametrize("name", [
    'smeta"; filename="virus.exe',
    'смета"; filename="virus.exe',
    'a;b=c.xlsx',
    'back\\slash.pdf',
])
def test_опасные_символы_не_рождают_второй_filename(name):
    """Кавычка, точка с запятой и равно не должны давать в заголовке второй параметр."""
    header = STORE.attachment_header(name)
    assert header.count("filename=") == 1
    assert header.count("filename*=") == 1
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert ";" not in fallback and "\\" not in fallback and "=" not in fallback


@pytest.mark.parametrize("name", ["отчёт\r\nX-Injected: 1.pdf", "строка\nвторая.txt", "cr\r.pdf"])
def test_перевод_строки_не_попадает_в_заголовок(name):
    header = STORE.attachment_header(name)
    assert "\r" not in header and "\n" not in header


@pytest.mark.parametrize(("name", "fallback", "encoded"), [
    ("Смета.xlsx", "file.xlsx", "%D0%A1%D0%BC%D0%B5%D1%82%D0%B0.xlsx"),
    ("Пьеса.pdf", "file.pdf", "%D0%9F%D1%8C%D0%B5%D1%81%D0%B0.pdf"),
    ("смета 2026.xlsx", "2026.xlsx", "%D1%81%D0%BC%D0%B5%D1%82%D0%B0%202026.xlsx"),
])
def test_русское_имя_есть_и_в_filename_звёздочка_и_в_ascii(name, fallback, encoded):
    """Кириллицу в `filename` не положить, но пустое имя или голое «.xlsx» — не ответ."""
    header = STORE.attachment_header(name)
    assert header == f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


@pytest.mark.parametrize("name", ["", "   ", "файл", "документ.docx"])
def test_запасное_имя_никогда_не_пустое(name):
    header = STORE.attachment_header(name)
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert fallback and fallback.strip() == fallback
    assert fallback.isascii()


def test_presigned_url_собирает_заголовок_тем_же_помощником(monkeypatch):
    captured = {}

    class FakeClient:
        def generate_presigned_url(self, method, Params, ExpiresIn):  # noqa: N803 - так у boto3
            captured.update(Params)
            return "https://storage.example/p1/x"

    monkeypatch.setattr(STORE, "public_client", lambda: FakeClient())
    url = STORE.presigned_url("p1/x.xlsx", 'Смета"; filename="virus.exe')
    assert url == "https://storage.example/p1/x"
    assert captured["ResponseContentDisposition"] == STORE.attachment_header(
        'Смета"; filename="virus.exe')


def test_заголовок_собирается_только_в_files_store():
    """Копий сборки быть не должно: любая новая копия обходит проверку имени."""
    root = pathlib.Path(__file__).resolve().parents[2] / "services" / "tool-api" / "app"
    sources = sorted(root.rglob("*.py"))
    assert sources
    copies = {path.relative_to(root).as_posix(): text.count("attachment; filename")
              for path, text in ((p, p.read_text(encoding="utf-8")) for p in sources)
              if "attachment; filename" in text}
    assert list(copies) == ["files_store.py"], f"копии сборки заголовка: {copies}"
    assert copies["files_store.py"] == 1


# ---------------------------------------------------------------- квота: одно действие
# Квота проверялась отдельным `select`, а писалась следующим запросом: два параллельных
# подтверждения видели одинаковый `used` и вдвоём превышали место (аудит ponytail).
# Тесты ниже держат два свойства: строка вставляется с условием по сумме в том же
# запросе, а отказ снимает уже загруженный объект.

class _Rows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _ScriptedConn:
    """Подставной коннект: на запрос с известной подстрокой отдаёт заданные строки."""

    def __init__(self, answers: list[tuple[str, list[dict]]]) -> None:
        self.answers = answers
        self.sql: list[str] = []
        self.params: list[tuple] = []

    def execute(self, sql: str, params: tuple = ()) -> _Rows:
        self.sql.append(sql)
        self.params.append(params)
        for marker, rows in self.answers:
            if marker in sql:
                return _Rows(rows)
        raise AssertionError(f"неожиданный запрос: {sql}")


def test_квота_проверяется_тем_же_запросом_что_вставляет_строку(monkeypatch):
    """`insert ... select ... where` с суммой по `file_object` — а не select, потом insert."""
    deleted: list[str] = []
    monkeypatch.setattr(STORE, "delete_object", deleted.append)
    conn = _ScriptedConn([
        ("select id from participant", [{"id": 7}]),
        ("insert into file_object", []),                       # места не хватило
        ("select coalesce(sum(size_bytes), 0) as used", [{"used": STORE.QUOTA_BYTES}]),
    ])

    with pytest.raises(STORE.FileRejected) as caught:
        STORE.save_committed(conn, 7, "пьеса.png", "image/png", 500, None,
                             "p7/one.png", PNG)

    assert caught.value.code == "file_quota_exceeded"
    assert caught.value.status == 413
    assert "Не помещается в личное место" in caught.value.message
    assert deleted == ["p7/one.png"], "отказ обязан снять уже загруженный объект"

    insert = [sql for sql in conn.sql if "insert into file_object" in sql]
    assert len(insert) == 1
    assert "where (select coalesce(sum(size_bytes), 0) from file_object" in insert[0]
    assert "where participant_id = %s and deleted_at is null) + %s <= %s" in insert[0]
    assert "from participant where id = %s for update" in conn.sql[0], \
        "параллельные коммиты сериализуются блокировкой строки участника"


def test_квота_считается_в_том_же_запросе_и_при_успехе(monkeypatch):
    """Успешная вставка берёт `left` из БД, а не из предварительно прочитанной суммы."""
    monkeypatch.setattr(STORE, "delete_object", lambda key: pytest.fail("объект трогать нельзя"))
    conn = _ScriptedConn([
        ("select id from participant", [{"id": 7}]),
        ("insert into file_object", [{"id": 55}]),
        ("select coalesce(sum(size_bytes), 0) as used", [{"used": 1024}]),
        ("from folder", [{"id": 3, "path": "Личное/Фото"}]),
        ("insert into file_folder", []),
    ])

    saved = STORE.save_committed(conn, 7, "фото.png", "image/png", 500, None,
                                 "p7/one.png", PNG)

    assert saved.id == 55
    assert saved.quota_left == STORE.QUOTA_BYTES - 1024


# ---------------------------------------------------------------- ZIP: потолок до сборки

def _folder_conn(size_bytes: int, *, object_key: str | None = "p1/a.png") -> _ScriptedConn:
    rows = [{"id": 1, "name": "афиша.png", "kind": "image", "size_bytes": size_bytes,
             "mime": "image/png", "object_key": object_key, "created_at": None,
             "position": None, "duration_sec": None, "bitrate_kbps": None}]
    return _ScriptedConn([("from file_folder", rows)])


def test_zip_отказывает_до_сборки_когда_файлы_больше_потолка(monkeypatch):
    """Папка на 200 МБ не должна занять 200 МБ в /tmp: отказ до первой записи в архив."""
    monkeypatch.setattr(STORE, "storage",
                        lambda: pytest.fail("хранилище не должно открываться при отказе"))
    conn = _folder_conn(200 * 1024 * 1024)

    with pytest.raises(STORE.FileRejected) as caught:
        STORE.stream_folder_zip(conn, {"id": 1, "path": "Спектакли/Пурга/музыка"},
                                max_bytes=100 * 1024 * 1024)

    assert caught.value.code == "folder_zip_too_large"
    assert caught.value.status == 413
    assert "100 МБ" in caught.value.message
    assert "скачивайте по файлам" in caught.value.message


def test_zip_считает_только_файлы_у_которых_есть_объект(monkeypatch):
    """Строка без ключа в архив не попадает — её размер потолок не съедает."""
    monkeypatch.setattr(STORE, "storage",
                        lambda: pytest.fail("хранилище не должно открываться при отказе"))
    conn = _folder_conn(200 * 1024 * 1024, object_key=None)

    handle, skipped = STORE.stream_folder_zip(conn, {"id": 1}, max_bytes=100 * 1024 * 1024)

    handle.close()
    assert skipped == ["афиша.png"]


def test_zip_меньше_потолка_собирается(monkeypatch):
    class _Storage:
        def get_object(self, Bucket, Key):  # noqa: N803 - так у boto3
            import io

            return {"Body": io.BytesIO(b"x" * 32)}

    monkeypatch.setattr(STORE, "storage", _Storage)
    conn = _folder_conn(32)

    handle, skipped = STORE.stream_folder_zip(conn, {"id": 1}, max_bytes=100 * 1024 * 1024)

    assert skipped == []
    assert STORE.stream_zip_size(handle) > 32
    handle.close()


def test_потолок_архива_сто_мегабайт_по_умолчанию():
    from app.settings import DEFAULT_ZIP_MAX_BYTES, Settings

    assert DEFAULT_ZIP_MAX_BYTES == 100 * 1024 * 1024
    assert Settings().zip_max_bytes == DEFAULT_ZIP_MAX_BYTES
    assert Settings.from_env({"ZIP_MAX_BYTES": "5"}).zip_max_bytes == 5
