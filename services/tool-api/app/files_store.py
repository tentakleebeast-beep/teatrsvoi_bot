"""Файлы бота: приём, проверки, папки и запись в объектное хранилище (Ш-3).

Требования — docs/development/86. Здесь только логика, которая не зависит от Telegram:
тип файла, лимиты, дедупликация, личная папка, ключ объекта и запись в `file_object`.
Эндпоинт `/files/upload` в `main.py` — тонкая обёртка над этим модулем.

Три правила, из которых следует всё остальное:
  * байты кладём в Garage по ключу без имён людей и без кириллицы (NFR-K9, инвариант F4);
  * подозрительный файл отбиваем по расширению И по сигнатуре: `.docx` — это zip,
    но `.zip` — это архив, и путать их нельзя (FR-K22);
  * квота и общий потолок считаются по БД и диску, а не «на глаз» (FR-K14).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import quote

from . import audio, extract
from .errors import ApiError
from .external import SQL_ERRORS, STORAGE_ERRORS
from .file_kind import (
    extension,
    kind_by_name,
    kind_predicate,
    kind_word,
)
from .settings import DEFAULT_ZIP_MAX_BYTES

log = logging.getLogger("files_store")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024          # что бот вообще может скачать из Telegram
MAX_CHAT_SEND_BYTES = 50 * 1024 * 1024       # что бот может отправить обратно (Ш-4)
QUOTA_BYTES = 2 * 1024 ** 3                  # 2 ГБ на человека (FR-K14)
MAX_MINIAPP_BYTES = 200 * 1024 * 1024        # Mini App грузит мимо Telegram: потолок выше 20 МБ
FEEDBACK_MAX_FILES = 3                       # скриншотов на обращение (Э6, D8)
FEEDBACK_MAX_BYTES = 8 * 1024 ** 2           # потолок одного скриншота: 8 МБ
# Скриншот обращения живёт в `file_object`, но не в личном месте: он не виден в «Моих
# файлах» и поиске, не занимает квоту и не удаляется, пока обращение не разобрано.
FEEDBACK_SCOPE = "feedback"
# Типы вложения обращения: только изображения (D8). `<input accept>` на экране —
# удобство, а не защита: тип проверяет сервер по имени, mime и сигнатуре.
FEEDBACK_MIME = ("image/png", "image/jpeg", "image/webp", "image/gif")
FEEDBACK_TYPE_MESSAGE = ("К обращению можно приложить только изображение — png, jpeg, "
                         "webp или gif.")
FEEDBACK_TOO_LARGE_MESSAGE = "Скриншот больше 8 МБ — пришлите меньше."

PRESIGN_TTL = 900                            # ссылка на загрузку живёт 15 минут
MAGIC_BYTES = 16

# Наборы расширений живут в `file_kind.py` — единственном месте, где решается вид файла.
# Здесь остаются только те, что про приём, а не про слово: архив, видео, исполняемое, Office.

ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg"}
EXEC_EXT = {".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".sh", ".ps1", ".msi", ".app",
            ".jar", ".apk", ".dmg", ".pkg"}

# Контейнеры Office: по сигнатуре это zip, но по смыслу — документ.
OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".odt", ".ods"}

FOLDER_BY_KIND = {"text": "Мои документы", "image": "Фото", "audio": "Аудио"}
PERSONAL_ROOT = "Личное"


class FileRejected(ApiError):
    """Ошибка операции: несёт машинный `code` и человеческий текст в конверте API.

    Наследуется от `ApiError`, поэтому до клиента доходит тем же телом ошибки, что и
    остальные отказы (docs/architecture/85), без переупаковки в ручках.
    """

    def __init__(self, code: str, message: str, status: int = 415) -> None:
        super().__init__(status, message, code=code)
        self.message = message
        self.status = status


@dataclass
class SavedFile:
    id: int
    name: str
    kind: str
    size_bytes: int
    folder_path: str
    duplicate: bool
    quota_left: int
    duration_sec: int | None = None
    warning: str | None = None
    storage: str = "garage"
    description: str | None = None
    folder_id: int | None = None


MAX_DESCRIPTION = 500


def clean_description(raw: str | None) -> str | None:
    """Описание от загрузившего: обрезка по краям, пустая строка → `null` (V-124).

    Описание — по желанию: поле необязательное, и «ничего не написал» не отличается от
    «стёр написанное». Поэтому пустое значение хранится как `null`, а не как `''`.
    Длину проверяет ручка (400 словами); здесь — защита домена от обхода.
    """
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    if not text:
        return None
    return text[:MAX_DESCRIPTION]


# ------------------------------------------------------------------ тип файла
# `extension` живёт в `file_kind` и импортирована выше: одно правило на Python и SQL.


def sniff(head: bytes) -> str | None:
    """Определить формат по первым байтам. None — не узнали (тогда решает расширение)."""
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        # m4a — это тоже ftyp: отличаем аудио-контейнер от видео по бренду
        return "m4a" if brand[:4] in (b"M4A ", b"M4B ", b"M4P ") else "mp4"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"Rar!"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf"):
        return "7z"
    if head.startswith(b"MZ"):
        return "exe"
    return None


def detect_kind(name: str, mime: str, head: bytes = b"") -> str:
    """Вид файла: text | image | audio. Бросает FileRejected, если тип не принимаем."""
    ext = extension(name)
    fmt = sniff(head)

    if ext in EXEC_EXT:
        raise FileRejected("file_executable", "Исполняемые файлы бот не принимает.")
    if ext in VIDEO_EXT or fmt == "mp4":
        raise FileRejected("file_video", "Видео бот не принимает: слишком тяжёлое для хранилища.")
    if ext in ARCHIVE_EXT:
        raise FileRejected("file_archive", "Архивы бот не принимает — пришлите файлы по отдельности.")
    if fmt in ("zip", "rar", "7z") and ext not in OFFICE_EXT:
        raise FileRejected("file_archive", "Похоже на архив без понятного расширения — не принимаю.")
    if fmt == "exe":
        raise FileRejected("file_executable", "Исполняемые файлы бот не принимает.")

    # Положительная ветка — из одного правила (`file_kind`): pdf/docx → text, картинка → image.
    by_name = kind_by_name(name)
    if by_name:
        return by_name
    if fmt in ("png", "jpeg", "gif", "webp"):
        return "image"
    if fmt in ("mp3", "wav", "ogg", "flac", "m4a"):
        return "audio"

    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/") or mime == "application/pdf":
        return "text"
    raise FileRejected("file_type_not_allowed",
                       "Такой тип файла пока не принимаю. Можно: документы, картинки, аудио.")


# ------------------------------------------------------------------ хранилище
def storage():  # noqa: ANN201 — boto3-клиент без типов в зависимостях
    """S3-клиент под настройки проекта. Создаётся один раз на процесс."""
    global _CLIENT
    if _CLIENT is None:
        import boto3
        from botocore.config import Config

        endpoint = os.getenv("STORAGE_ENDPOINT", "").strip()
        if not endpoint:
            raise FileRejected("storage_unavailable",
                               "Хранилище файлов не настроено — сообщите администратору.", 503)
        _CLIENT = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.getenv("STORAGE_ACCESS_KEY", "").strip(),
            aws_secret_access_key=os.getenv("STORAGE_SECRET_KEY", "").strip(),
            region_name=os.getenv("STORAGE_REGION", "svoi").strip() or "svoi",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                          retries={"max_attempts": 3, "mode": "standard"}),
        )
    return _CLIENT


_CLIENT = None
_PUBLIC = None


def public_client():
    """Клиент для ссылок, которые открывает браузер: адрес берётся из STORAGE_PUBLIC_ENDPOINT.

    Сервер ходит в хранилище по локальному адресу (127.0.0.1:3900), а телефон — нет:
    `http://127.0.0.1` в браузере телефона означает сам телефон. Поэтому подпись для браузера
    считается отдельным клиентом с публичным адресом (`https://files.домен`), а внутренние
    операции остаются на локальном. Если публичный адрес не задан — используем локальный:
    в разработке это честно, а в проде его отсутствие видно сразу по нерабочей ссылке.
    """
    global _PUBLIC
    if _PUBLIC is None:
        import boto3
        from botocore.config import Config

        endpoint = (os.getenv("STORAGE_PUBLIC_ENDPOINT", "").strip()
                    or os.getenv("STORAGE_ENDPOINT", "").strip())
        if not endpoint:
            raise FileRejected("storage_unavailable",
                               "Хранилище файлов не настроено — сообщите администратору.", 503)
        _PUBLIC = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.getenv("STORAGE_ACCESS_KEY", "").strip(),
            aws_secret_access_key=os.getenv("STORAGE_SECRET_KEY", "").strip(),
            region_name=os.getenv("STORAGE_REGION", "svoi").strip() or "svoi",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    return _PUBLIC


def bucket_private() -> str:
    return os.getenv("STORAGE_BUCKET_PRIVATE", "svoi-private").strip()


# ------------------------------------------------------------------ БД
def ensure_personal_folder(conn, participant_id: int, kind: str) -> tuple[int, str]:
    """Личное дерево человека: `Личное` → `Мои документы` / `Фото` / `Аудио` (FR-K11).

    Идемпотентно: создаётся при первом файле и дальше переиспользуется.
    """
    root_id = _ensure_folder(conn, participant_id, None, PERSONAL_ROOT, PERSONAL_ROOT)
    sub = FOLDER_BY_KIND.get(kind, "Мои документы")
    path = f"{PERSONAL_ROOT}/{sub}"
    folder_id = _ensure_folder(conn, participant_id, root_id, sub, path)
    return folder_id, path


def _ensure_folder(conn, participant_id: int, parent_id: int | None,
                   name: str, path: str) -> int:
    row = conn.execute(
        "select id from folder where scope = 'personal' and participant_id = %s"
        " and path = %s and deleted_at is null", (participant_id, path)).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "insert into folder (scope, participant_id, parent_id, name, path)"
        " values ('personal', %s, %s, %s, %s) returning id",
        (participant_id, parent_id, name, path)).fetchone()
    return row["id"]


# Один SQL на оба места — чтение и вставку (V-125, docs/development/192 §5).
#
# Личное место считает только то, что (а) моё, (б) не удалено, (в) лежит байтами в Garage,
# (г) не скриншот обращения, (д) не лежит ни в одной общей папке. Файл в общей папке,
# ссылка на Drive и вложение обращения личного места не занимают: за общее место отвечает
# потолок диска (FR-K15, отдельная карта), а не личные 2 ГБ.
_USED_BYTES_SQL = (
    "select coalesce(sum(f.size_bytes), 0) as used"
    "  from file_object f"
    " where f.participant_id = %(pid)s"
    "   and f.deleted_at is null"
    "   and f.storage = 'garage'"
    "   and f.scope <> %(scope)s"
    "   and not exists (select 1 from file_folder ff join folder fo on fo.id = ff.folder_id"
    "                    where ff.file_id = f.id and fo.deleted_at is null"
    "                      and fo.scope = 'shared')"
)


def used_bytes(conn, participant_id: int) -> int:
    """Занятое личное место по правилу V-125 (docs/development/192 §5).

    Скриншоты обращений не считаются (V-055 (а)): иначе человек с забитыми 2 ГБ не мог бы
    даже сообщить о дефекте. Ссылка на Drive не считается: байтов у нас нет, `size_bytes`
    у неё `null`. Файл в общей папке не считается: он занимает общий диск, а не личный.
    """
    row = conn.execute(_USED_BYTES_SQL,
                       {"pid": participant_id, "scope": FEEDBACK_SCOPE}).fetchone()
    return int(row["used"] or 0)


def _quota_error(conn, participant_id: int, size: int) -> FileRejected:
    """Понятный отказ по квоте: сколько свободно и сколько занимает файл (FR-K14)."""
    left = max(QUOTA_BYTES - used_bytes(conn, participant_id), 0)
    return FileRejected(
        "file_quota_exceeded",
        f"Не помещается в личное место: свободно {left / (1024 ** 2):.0f} МБ, "
        f"а файл {size / (1024 ** 2):.1f} МБ. Удалите что-нибудь или пришлите меньше.",
        413)


def _insert_with_quota(conn, *, participant_id: int, name: str, mime: str, size: int,
                       via: str, key: str, digest: str | None, kind: str,
                       scope: str = "personal", storage: str = "garage",
                       description: str | None = None,
                       quota: bool = True) -> int | None:
    """Вставить строку файла, только если личное место ещё есть — одним запросом.

    Отдельный `select used_bytes` перед вставкой гоняется с параллельным подтверждением:
    оба запроса видят одинаковую сумму, оба проходят, и квота превышается на размер
    второго файла (аудит ponytail). Поэтому сумма проверяется в том же `insert ... select`,
    что пишет строку, а строка участника берётся `for update`: второй коммит ждёт первый
    и считает сумму уже с его файлом. `None` — места не хватило, строки нет.

    Сумма — по тому же правилу, что `used_bytes` (§5): только `storage='garage'`, без
    скриншотов обращений и без файлов в общей папке. Раньше проверка при вставке
    `scope='feedback'` не исключала, а `used_bytes` исключал — и человек с заполненным
    личным местом получал отказ на файл, который счётчик считал свободным.

    `quota=False` — загрузка в общую папку: файл личного места не занимает вовсе, поэтому
    проверки нет. Скриншот обращения — та же причина (V-055 (а)).
    """
    if scope == FEEDBACK_SCOPE or not quota:
        row = conn.execute(
            "insert into file_object (participant_id, name, mime, size_bytes, scope, uploaded_via,"
            " object_key, bucket, sha256, kind, storage, description)"
            " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (participant_id, name, mime, size, scope, via, key, bucket_private(), digest,
             kind, storage, description)).fetchone()
        return None if row is None else int(row["id"])
    conn.execute("select id from participant where id = %s for update", (participant_id,))
    row = conn.execute(
        "insert into file_object (participant_id, name, mime, size_bytes, scope, uploaded_via,"
        " object_key, bucket, sha256, kind, storage, description)"
        " select %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s"
        "  where (select coalesce(sum(f.size_bytes), 0) from file_object f"
        "          where f.participant_id = %s and f.deleted_at is null"
        "            and f.storage = 'garage' and f.scope <> %s"
        "            and not exists (select 1 from file_folder ff"
        "                              join folder fo on fo.id = ff.folder_id"
        "                             where ff.file_id = f.id and fo.deleted_at is null"
        "                               and fo.scope = 'shared')) + %s <= %s"
        " returning id",
        (participant_id, name, mime, size, scope, via, key, bucket_private(), digest, kind,
         storage, description, participant_id, FEEDBACK_SCOPE, size, QUOTA_BYTES)).fetchone()
    return None if row is None else int(row["id"])


def find_duplicate(conn, participant_id: int, sha256: str) -> dict | None:
    """Живой личный файл с теми же байтами. Скриншоты обращений дублями не считаются.

    Иначе человек, отправивший скриншот в обращение, а потом загрузивший тот же файл
    в «Файлы», получил бы `duplicate` на строку со `scope='feedback'`: файл не появился
    бы в списке, а байты были бы удалены (Э6, §4.3).
    """
    return conn.execute(
        "select id, name, kind, size_bytes from file_object"
        " where participant_id = %s and sha256 = %s and deleted_at is null"
        "   and scope <> %s"
        " order by id limit 1", (participant_id, sha256, FEEDBACK_SCOPE)).fetchone()


# ------------------------------------------------------------------ выдача
def file_for(conn, file_id: int, participant_id: int) -> dict | None:
    """Файл владельца. Чужой и несуществующий неотличимы — оба `None` (FR-K8).

    Возвращать 403 на чужой файл нельзя: это подтверждает, что файл существует.
    Поэтому наружу всегда 404, а разница остаётся в аудите.
    """
    return conn.execute(
        f"{_FILE_SELECT} where f.id = %s and f.participant_id = %s and f.deleted_at is null",
        (file_id, participant_id)).fetchone()


_FILE_SELECT = (
    "select f.id, f.name, f.mime, f.size_bytes, f.kind, f.object_key, f.bucket,"
    "       f.sha256, f.scope, f.participant_id, f.created_at, f.cold_at,"
    "       f.storage, f.description, f.drive_url, f.drive_path, f.drive_file_id,"
    "       (select path from folder fo join file_folder ff on ff.folder_id = fo.id"
    "         where ff.file_id = f.id and fo.deleted_at is null limit 1) as folder"
    "  from file_object f"
)


def file_for_viewer(conn, file_id: int, who: dict) -> tuple[dict | None, bool]:
    """Файл глазами конкретного человека: (строка, «видно по праву админа»).

    Три законных случая: свой файл; общий файл (его читают все роли, FR-K10); личный —
    если админ открыл доступ через `access_grant` (FR-K9). Во всех остальных — `None`.
    Проверка идёт в БД на каждом запросе: отзыв доступа действует сразу, кэша прав нет.
    """
    from . import file_access

    row = conn.execute(f"{_FILE_SELECT} where f.id = %s and f.deleted_at is null",
                       (file_id,)).fetchone()
    if row is None:
        return None, False
    if int(row["participant_id"] or 0) == int(who.get("id") or 0):
        return row, False
    if row["scope"] == "shared":
        return row, False
    if "admin" in file_access.roles_of(who):
        grant = file_access.active_grant(conn, file_id, int(who.get("id") or 0))
        if grant is not None:
            return row, True
    return None, False


def file_for_reader(conn, file_id: int, who: dict) -> tuple[dict | None, bool]:
    """Файл глазами читателя Mini App: (строка, «видно по праву админа»).

    Одно правило на все три действия экрана файла (`/view`, будущее скачивание,
    открытие в браузере) — чтобы «аудио в общей папке слышу, pdf рядом — 404»
    не повторялось (docs/development/150 §3 D6). Законных случаев четыре:

    * свой файл;
    * файл лежит в папке с `folder.scope = 'shared'` — её читают все роли (FR-K10);
      истина здесь — папка, а не `file_object.scope`: колонка ставится константой
      при вставке и в `shared` не переходит (замер 09.11.2026 — 4/4 на проде);
    * держатель `feedback.triage` — файл, приложенный к обращению (Э6, D4). Право —
      именно возможность, а не роль: `feedback.triage` живёт в
      `v_participant_capabilities`, а в `roles_of` лежат роли (`admin`, `org`, …), и
      проверка по ролям не сработала бы никогда. Узко и проверяемо: не «админ видит
      всё», а «файл, приложенный к обращению, видит тот, кто разбирает обращения»;
    * админ с активным грантом `file_access` — тогда второй элемент `True`.

    Иначе `(None, False)`: чужой закрытый и несуществующий неотличимы (FR-K8).
    Мёртвую ветку `file_object.scope = 'shared'` не трогаем — она осталась в
    `file_for_viewer` до отдельной карты про данные.
    """
    from . import file_access

    row = conn.execute(f"{_FILE_SELECT} where f.id = %s and f.deleted_at is null",
                       (file_id,)).fetchone()
    if row is None:
        return None, False
    if int(row["participant_id"] or 0) == int(who.get("id") or 0):
        return row, False
    shared = conn.execute(
        "select 1 from folder fo join file_folder ff on ff.folder_id = fo.id"
        " where ff.file_id = %s and fo.deleted_at is null and fo.scope = 'shared'"
        " limit 1", (file_id,)).fetchone()
    if shared is not None:
        return row, False
    triage = conn.execute(
        "select 1 from v_participant_capabilities"
        " where participant_id = %s and capability = 'feedback.triage'",
        (int(who.get("id") or 0),)).fetchone()
    if triage is not None:
        attached = conn.execute(
            "select 1 from feedback_attachment where file_id = %s", (file_id,)).fetchone()
        if attached is not None:
            return row, True
    if "admin" in file_access.roles_of(who):
        grant = file_access.active_grant(conn, file_id, int(who.get("id") or 0))
        if grant is not None:
            return row, True
    return None, False


def feedback_attachments(conn, feedback_ids: list[int]) -> dict[int, list[dict]]:
    """Вложения обращений одним запросом: `{feedback_id: [{id, name, mime, size_bytes}]}`.

    Порядок — `position`, затем `id`: человек приложил три файла в известном порядке,
    и очередь разбора должна видеть их так же. Удалённый файл в список не попадает,
    а обращение без вложений — пустой список, а не отсутствие ключа: экран и мост
    читают ответ одинаково (Э6 §3.3, §3.4).
    """
    ids = [int(item) for item in feedback_ids]
    if not ids:
        return {}
    out: dict[int, list[dict]] = {item: [] for item in ids}
    rows = conn.execute(
        "select fa.feedback_id, f.id, f.name, f.mime, f.size_bytes"
        "  from feedback_attachment fa join file_object f on f.id = fa.file_id"
        " where fa.feedback_id = any(%s) and f.deleted_at is null"
        " order by fa.feedback_id, fa.position, f.id", (ids,)).fetchall()
    for row in rows:
        item = dict(row)
        out.setdefault(int(item.pop("feedback_id")), []).append(item)
    return out


def remove(conn, who: dict, file_id: int) -> dict:
    """Мягко удалить свой файл: строка остаётся, но больше нигде не видна (FR-M8, FR-L8).

    Удаляет только владелец: чужой личный файл отвечает 404, как несуществующий (FR-K8),
    и админ здесь не исключение — доступ админа к чужому файлу открывается grant'ом
    (`file_access`), а не правом стереть его. Связи с папками снимаются, сами папки
    остаются: файл уходит из выдачи, списков и архивов папок (FR-M8).

    Байты в хранилище не трогаем: `deleted_at` — обратимая метка (`db/init/046`), и
    второй записи о том же файле не появится, пока строка жива. Квота освобождается
    сразу — `used_bytes` считает только `deleted_at is null`.
    """
    row = file_for(conn, file_id, int(who.get("id") or 0))
    if row is None:
        raise FileRejected("file_not_found", "Файл не найден.", 404)
    if row.get("scope") == FEEDBACK_SCOPE:
        link = conn.execute(
            "select feedback_id from feedback_attachment where file_id = %s",
            (file_id,)).fetchone()
        if link is not None:
            raise FileRejected(
                "file_in_feedback",
                f"Скриншот приложен к обращению №{link['feedback_id']} — он нужен, пока "
                "обращение не разобрано.", 409)
    detached = conn.execute(
        "delete from file_folder where file_id = %s returning folder_id", (file_id,)).fetchall()
    conn.execute(
        "update file_object set deleted_at = now() where id = %s and deleted_at is null",
        (file_id,))
    return {"id": file_id, "name": row["name"], "folders_detached": len(detached)}


def list_files(conn, participant_id: int, limit: int = 20) -> list[dict]:
    """Последние файлы человека: то, что бот покажет списком с кнопками.

    Скриншоты обращений не показываются (V-055 (а), §4.5): они живут при обращении,
    а не в «Моих файлах» и не в «Фото». Строка несёт `kind_word`, `storage` и `description`:
    слово вида приходит с сервера, а не собирается в браузере (docs/development/192 §4.4).
    """
    rows = conn.execute(
        "select f.id, f.name, f.kind, f.size_bytes, f.created_at, a.duration_sec,"
        "       f.storage, f.description, f.drive_url, f.drive_path,"
        "       (select path from folder fo join file_folder ff on ff.folder_id = fo.id"
        "         where ff.file_id = f.id and fo.deleted_at is null limit 1) as folder,"
        "       (select fo.id from folder fo join file_folder ff on ff.folder_id = fo.id"
        "         where ff.file_id = f.id and fo.deleted_at is null limit 1) as folder_id"
        "  from file_object f left join file_audio a on a.file_id = f.id"
        " where f.participant_id = %s and f.deleted_at is null and f.scope <> %s"
        " order by f.created_at desc, f.id desc limit %s",
        (participant_id, FEEDBACK_SCOPE, limit)).fetchall()
    return [_with_word(dict(row)) for row in rows]


def _with_word(row: dict) -> dict:
    """Добавить слово вида к строке файла: одно правило — `file_kind.kind_word`."""
    row["kind_word"] = kind_word(row.get("kind"), row.get("name") or "")
    return row


def open_stream(key: str):
    """Открыть объект из хранилища для потоковой отдачи. Возвращает (тело, mime, размер)."""
    obj = storage().get_object(Bucket=bucket_private(), Key=key)
    return obj["Body"], obj.get("ContentType") or "application/octet-stream", obj["ContentLength"]


# Управляющие символы, в том числе CR и LF, в значении заголовка недопустимы.
_HEADER_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# В ASCII-запасное имя не должны попадать символы, ломающие синтаксис заголовка:
# кавычка закрывает значение, точка с запятой начинает новый параметр, равно делает
# из имени второй `filename=`, обратный слэш экранирует закрывающую кавычку.
_HEADER_UNSAFE = str.maketrans({'"': "_", ";": "_", "\\": "_", "=": "_"})


def _header_ascii_fallback(name: str) -> str:
    """ASCII-имя для старых клиентов: только безопасные символы и никогда не пустое.

    Кириллица в ASCII не переводится, поэтому от «Смета.xlsx» остаётся «.xlsx» — это
    не имя файла. Если от имени не осталось ни буквы, ни цифры, отдаём «file» с прежним
    расширением: пустая строка или голое «.xlsx» в заголовке бесполезны.
    """
    raw = name.encode("ascii", "ignore").decode().translate(_HEADER_UNSAFE)
    stem = raw.rsplit(".", 1)[0] if "." in raw else raw
    if not any(char.isalnum() for char in stem):
        ext = extension(name)
        attachable = ext and all(c.isascii() and (c.isalnum() or c == ".") for c in ext)
        return "file" + ext if attachable else "file"
    return raw.strip(" .") or "file"


def attachment_header(name: str) -> str:
    """Значение `Content-Disposition` для скачивания: одно имя в двух записях.

    Имя приходит от человека (Telegram, Mini App), поэтому в заголовок оно попадает
    только в двух безопасных видах:
      * `filename` — ASCII-запасное, без кавычек, точек с запятой, равно и переводов строк;
      * `filename*` — исходное имя по RFC 5987, где всё опасное закодировано процентами.
    Так имя `smeta"; filename="virus.exe` не рождает второй параметр, а CR/LF не рвут
    заголовок на две строки (docs/development/26: проверка на границе доверия).
    """
    clean = _HEADER_CONTROL.sub("", name or "").strip() or "file"
    return (f'attachment; filename="{_header_ascii_fallback(clean)}"; '
            f"filename*=UTF-8''{quote(clean, safe='')}")


def presigned_url(key: str, name: str, ttl: int = 900) -> str:
    """Ссылка на объект на 15 минут. Имя файла уходит в Content-Disposition.

    Сборка заголовка — в `attachment_header`: одно место на все выдачи файла.
    """
    return public_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_private(), "Key": key,
                "ResponseContentDisposition": attachment_header(name)},
        ExpiresIn=ttl)


def presigned_view_url(key: str, ttl: int = PRESIGN_TTL) -> str:
    """Ссылка на просмотр: браузер показывает файл, а не скачивает его.

    `presigned_url` всегда ставит `attachment` — по такой ссылке pdf уходит в
    «Загрузки» вместо того, чтобы открыться (замер 150 §2.4). Здесь тот же GET,
    но `inline`: картинка рисуется, pdf открывается встроенным просмотрщиком.
    Ключ объекта наружу не отдаётся — только подпись, и живёт она 15 минут.
    """
    return public_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_private(), "Key": key,
                "ResponseContentDisposition": "inline"},
        ExpiresIn=ttl)


# ------------------------------------------------------------------ приём
def _spool() -> Any:
    """Временный файл на диске: байты файла не должны жить в памяти процесса (NFR-K2)."""
    return tempfile.NamedTemporaryFile(delete=True, suffix=".upload")


async def read_request(request, limit: int | None = None) -> tuple[Any, int, str, bytes]:
    """Прочитать тело запроса в временный файл, считая размер и sha256 по дороге.

    В памяти держим только первые байты для проверки сигнатуры; весь файл уходит на диск
    потоком. Превышение лимита отбивается до того, как файл дописан до конца.
    """
    limit = MAX_UPLOAD_BYTES if limit is None else limit
    digest = hashlib.sha256()
    size = 0
    head = b""
    handle = _spool()
    async for chunk in request.stream():
        if not chunk:
            continue
        size += len(chunk)
        if size > limit:
            handle.close()
            raise FileRejected(
                "file_too_large",
                f"Файл больше {limit // (1024 * 1024)} МБ — бот такой не скачает. "
                "Загрузите через Mini App.", 413)
        if len(head) < MAGIC_BYTES:
            head += chunk[:MAGIC_BYTES - len(head)]
        digest.update(chunk)
        handle.write(chunk)
    handle.flush()
    handle.seek(0)
    return handle, size, digest.hexdigest(), head


def object_key(participant_id: int, ext: str) -> str:
    """Ключ без имён и кириллицы: `p<id>/<uuid><ext>` (NFR-K9, инвариант F4)."""
    clean = ext if ext and len(ext) <= 10 else ""
    return f"p{participant_id}/{uuid.uuid4().hex}{clean}"


def ext_for(name: str, mime: str) -> str:
    ext = extension(name)
    if ext:
        return ext
    return {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png",
            "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "text/plain": ".txt"}.get(mime, "")


def put_stream(key: str, handle, size: int, mime: str) -> None:
    """Положить поток байтов в хранилище. Единственное место записи объекта.

    Через него идут оба входа: браузер кладёт файл сам по presigned-ссылке (тогда сюда
    не заходят вовсе), а бот — этим вызовом, потому что байты уже у сервера.
    """
    try:
        storage().put_object(Bucket=bucket_private(), Key=key, Body=handle,
                             ContentLength=size, ContentType=mime or "application/octet-stream")
    except FileRejected:
        raise
    except STORAGE_ERRORS as exc:                 # адаптер хранилища: наружу — понятный код
        raise FileRejected("storage_unavailable",
                           "Хранилище файлов не ответило — попробуйте позже.", 503) from exc


def upload_stream(conn, participant_id: int, name: str, mime: str,
                  handle, size: int, sha256: str, head: bytes) -> SavedFile:
    """Файл, пришедший от бота байтами: в хранилище — потоком, в БД — общим коммитом.

    Второго пути записи нет: ключ, дедупликация, квота, личная папка и разбор аудио живут
    в `save_committed` — том же, что подтверждает загрузку из Mini App. Разница только
    в том, кто положил байты (браузер по ссылке или сервер потоком), и в поле `uploaded_via`.
    """
    key = object_key(participant_id, ext_for(name, mime))
    put_stream(key, handle, size, mime)
    return save_committed(conn, participant_id, name, mime, size, sha256, key, head,
                          via="human_telegram")


def _store_audio(conn, file_id: int, path: str, name: str) -> int | None:
    """Характеристики трека из mutagen. Не разобрали — файл всё равно остаётся (FR-M1)."""
    meta = audio.probe(path, name)
    if not meta:
        return None
    conn.execute(
        "insert into file_audio (file_id, duration_sec, bitrate_kbps, format, title, artist)"
        " values (%s,%s,%s,%s,%s,%s)"
        " on conflict (file_id) do update set duration_sec = excluded.duration_sec,"
        "   bitrate_kbps = excluded.bitrate_kbps, format = excluded.format,"
        "   title = excluded.title, artist = excluded.artist, probed_at = now()",
        (file_id, meta["duration_sec"], meta["bitrate_kbps"], meta["format"],
         meta["title"], meta["artist"]))
    return meta["duration_sec"]


def _warning(kind: str, size: int) -> str | None:
    """Предупреждение, но не запрет: wav в 50 МБ — это пять минут музыки (FR-M2)."""
    if kind == "audio" and size > MAX_CHAT_SEND_BYTES:
        return ("Файл больше 50 МБ: если это wav — он занимает место втрое быстрее mp3. "
                "Загрузился, но место расходуется заметно.")
    return None


# ------------------------------------------------------------------ загрузка из Mini App
def presign_put(participant_id: int, name: str, mime: str) -> dict:
    """Ссылка на прямую загрузку в хранилище. Ключ выбирает сервер, не браузер.

    Ключ обязан начинаться с `p<id>/`: иначе человек сможет записать поверх чужого файла,
    подставив чужой ключ в presigned URL.
    """
    key = object_key(participant_id, ext_for(name, mime))
    url = public_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket_private(), "Key": key,
                "ContentType": mime or "application/octet-stream"},
        ExpiresIn=PRESIGN_TTL)
    return {"upload_url": url, "object_key": key, "expires_in": PRESIGN_TTL}


def head_of(key: str) -> tuple[int, str] | None:
    """Размер и тип объекта в хранилище. None — объекта нет (загрузку не подтвердили)."""
    try:
        head = storage().head_object(Bucket=bucket_private(), Key=key)
    except STORAGE_ERRORS:                        # адаптер хранилища: нет объекта или нет связи
        return None
    return int(head["ContentLength"]), str(head.get("ContentType") or "")


def head_bytes(key: str, size: int = MAGIC_BYTES) -> bytes:
    """Первые байты объекта: нужны для проверки сигнатуры без скачивания файла."""
    try:
        obj = storage().get_object(Bucket=bucket_private(), Key=key,
                                   Range=f"bytes=0-{size - 1}")
        return obj["Body"].read(size)
    except STORAGE_ERRORS:                        # адаптер хранилища: сигнатуру проверим по имени
        return b""


def delete_object(key: str) -> None:
    try:
        storage().delete_object(Bucket=bucket_private(), Key=key)
    except STORAGE_ERRORS as exc:                 # уборка не должна ломать ответ
        log.error("не удалось удалить объект %s: %s", key, exc)


def save_committed(conn, participant_id: int, name: str, mime: str, size: int,
                   sha256: str | None, key: str, head: bytes,
                   via: str = "webapp", scope: str = "personal",
                   folder_id: int | None = None,
                   description: str | None = None) -> SavedFile:
    """Записать в БД файл, который уже лежит в хранилище.

    Один путь записи на оба входа: браузер положил байты по presigned-ссылке, бот —
    потоком через `upload_stream`. Отличается только `uploaded_via`.

    `folder_id` — выбранная при загрузке папка (docs/development/192 §6.2): файл получает
    связь с этой папкой вместо `Личное/<вид>`, а `scope` берётся у папки (`shared` для
    общей). Загрузка в общую папку проверку квоты не выполняет: файл личного места не
    занимает (V-125).

    `description` — подпись загрузившего (V-124): по желанию, пустое — `null`.

    `scope='feedback'` — скриншот обращения (Э6, D1). Тогда: личной папки и связи
    `file_folder` не создаётся (файл не появляется в «Моих файлах»), квота не считается
    (V-055 (а)), а дедупликация выключена (§4.3): иначе тот же скриншот, уже лежащий
    личным файлом, вернул бы **личную** строку — и админ получил бы доступ к личному
    файлу по правилу §4.4, то есть дыру в FR-K9. Повторная отправка того же скриншота
    в другое обращение упёрлась бы в `unique (file_id)` в `feedback_attachment`.
    """
    kind = detect_kind(name, mime, head)
    digest = (sha256 or "").strip().lower() or None
    if digest and len(digest) != 64:
        digest = None
    note = clean_description(description)

    folder = None
    if folder_id is not None and scope != FEEDBACK_SCOPE:
        folder = conn.execute(
            "select id, path, scope from folder where id = %s and deleted_at is null",
            (folder_id,)).fetchone()
        if folder is None:
            raise FileRejected("folder_not_found", "Папка не найдена.", 404)
        scope = folder["scope"]

    if digest and scope != FEEDBACK_SCOPE:
        dup = find_duplicate(conn, participant_id, digest)
        if dup is not None:
            delete_object(key)                    # байты уже есть: второй экземпляр не нужен
            if folder is not None:
                # Выбранная папка не должна молча остаться без файла: у найденной строки
                # та же байтовая копия, поэтому связь добавляется к ней (docs 192 §6.2).
                add_to_folder(conn, int(dup["id"]), int(folder["id"]))
                path = folder["path"]
            else:
                _, path = ensure_personal_folder(conn, participant_id, dup["kind"] or kind)
            known = conn.execute(
                "select duration_sec from file_audio where file_id = %s",
                (dup["id"],)).fetchone()
            return SavedFile(int(dup["id"]), dup["name"], dup["kind"] or kind,
                             int(dup["size_bytes"]), path, True,
                             QUOTA_BYTES - used_bytes(conn, participant_id),
                             duration_sec=known["duration_sec"] if known else None,
                             description=note,
                             folder_id=int(folder["id"]) if folder is not None else None)

    try:
        file_id = _insert_with_quota(
            conn, participant_id=participant_id, name=name, mime=mime, size=size,
            via=via, key=key, digest=digest, kind=kind, scope=scope,
            storage="garage", description=note,
            quota=(folder is None or folder["scope"] != "shared"))
    except SQL_ERRORS:
        delete_object(key)                        # тот же принцип: без записи байты не нужны
        raise
    if file_id is None:
        # Квота проверена тем же запросом, что писал строку: места не хватило.
        delete_object(key)
        raise _quota_error(conn, participant_id, size)
    if scope == FEEDBACK_SCOPE:
        # Скриншот обращения живёт без папки: он не «файл» в личном месте.
        return SavedFile(file_id, name, kind, size, "", False,
                         QUOTA_BYTES - used_bytes(conn, participant_id),
                         warning=_warning(kind, size), description=note)

    if folder is not None:
        path = folder["path"]
        files_folder_id = int(folder["id"])
        conn.execute("insert into file_folder (file_id, folder_id) values (%s,%s)"
                     " on conflict do nothing", (file_id, files_folder_id))
    else:
        files_folder_id, path = ensure_personal_folder(conn, participant_id, kind)
        conn.execute("insert into file_folder (file_id, folder_id) values (%s,%s)"
                     " on conflict do nothing", (file_id, files_folder_id))
    if kind == "text":
        tmp_text = _download_tmp(key, suffix=".doc")
        try:
            _store_text(conn, file_id, tmp_text, name)
        finally:
            os.unlink(tmp_text)
    duration = None
    if kind == "audio":
        tmp = _download_tmp(key)
        try:
            duration = _store_audio(conn, file_id, tmp, name)
        finally:
            os.unlink(tmp)                        # временная копия трека жить не должна
    return SavedFile(file_id, name, kind, size, path, False,
                     QUOTA_BYTES - used_bytes(conn, participant_id),
                     duration_sec=duration, warning=_warning(kind, size),
                     description=note, folder_id=files_folder_id)


def _store_text(conn, file_id: int, path: str, name: str) -> None:
    """Текст документа в `file_text`. Триггер 046 сразу пересчитывает поисковый индекс."""
    body, status = extract.text_of(path, name)
    conn.execute(
        "insert into file_text (file_id, body, status) values (%s,%s,%s)"
        " on conflict (file_id) do update set body = excluded.body, status = excluded.status,"
        "   extracted_at = now()", (file_id, body, status))


def _download_tmp(key: str, suffix: str = ".audio") -> str:
    """Скачать объект во временный файл: mutagen читает только путь, не поток.

    Для характеристики трека это неизбежно — но только для аудио и только один раз,
    сразу после подтверждения загрузки.
    """
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        storage().download_fileobj(bucket_private(), key, handle)
    finally:
        handle.close()
    return handle.name


# ------------------------------------------------------------------ порядок и папки
def natural_key(name: str) -> tuple[int, str]:
    """Ключ сортировки «как в плеере»: «02 — выход» идёт раньше «10 — финал».

    Числовой префикс в имени — это и есть порядок треков (FR-M4). Обычная сортировка
    поставила бы «10» перед «2», и музыка на показе пошла бы не в том порядке.
    """
    import re

    match = re.match(r"\s*(\d+)", name or "")
    return (int(match.group(1)) if match else 10 ** 6, (name or "").lower())


def folder_files(conn, folder_id: int, *, position_first: bool = True) -> list[dict]:
    """Файлы папки в порядке воспроизведения: сперва `position`, потом числовой префикс.

    Ссылка на Drive (`storage='drive'`) приходит сюда же: слово вида, адрес и описание
    отдаются строке, а плеер и скачивание решает экран (docs/development/192 §6.7).
    """
    rows = conn.execute(
        "select f.id, f.name, f.kind, f.size_bytes, f.mime, f.object_key, f.created_at,"
        "       f.storage, f.description, f.drive_url, f.drive_path,"
        "       ff.position, a.duration_sec, a.bitrate_kbps"
        "  from file_folder ff join file_object f on f.id = ff.file_id"
        "  left join file_audio a on a.file_id = f.id"
        " where ff.folder_id = %s and f.deleted_at is null",
        (folder_id,)).fetchall()
    items = [_with_word(dict(row)) for row in rows]
    if position_first:
        items.sort(key=lambda item: (item["position"] is None,
                                     item["position"] if item["position"] is not None else 0,
                                     *natural_key(item["name"])))
    else:
        items.sort(key=lambda item: natural_key(item["name"]))
    return items


def set_order(conn, folder_id: int, file_ids: list[int]) -> int:
    """Записать порядок перетаскиванием: `position` = 0,1,2… в порядке списка."""
    updated = 0
    for index, file_id in enumerate(file_ids):
        row = conn.execute(
            "update file_folder set position = %s where folder_id = %s and file_id = %s"
            " returning file_id", (index, folder_id, int(file_id))).fetchone()
        updated += 1 if row else 0
    return updated


def add_to_folder(conn, file_id: int, folder_id: int) -> None:
    """Положить файл в папку. Один трек может быть в нескольких папках (FR-M7)."""
    order = conn.execute(
        "select coalesce(max(position), -1) + 1 as next from file_folder where folder_id = %s",
        (folder_id,)).fetchone()
    conn.execute(
        "insert into file_folder (file_id, folder_id, position) values (%s,%s,%s)"
        " on conflict (file_id, folder_id) do nothing",
        (file_id, folder_id, int(order["next"])))


def remove_from_folder(conn, file_id: int, folder_id: int) -> bool:
    row = conn.execute(
        "delete from file_folder where file_id = %s and folder_id = %s returning file_id",
        (file_id, folder_id)).fetchone()
    return row is not None


def folder_zip_name(folder: dict) -> str:
    """Имя архива: путь папки, где «/» заменён на « — », чтобы файл не терял имя."""
    safe = (folder.get("path") or folder.get("name") or "папка").replace("/", " — ")
    return f"{safe}.zip"


def zip_entry_name(index: int, name: str, width: int = 2) -> str:
    """Имя файла в архиве: префикс-номер, если его ещё нет.

    «01 — пролог» остаётся собой; «пролог» становится «03 — пролог». Без этого распаковка
    снова отсортирует по алфавиту и порядок спектакля потеряется.
    """
    if (name or "")[:1].isdigit():
        return name
    return f"{index:0{width}d} — {name}"


def stream_folder_zip(conn, folder: dict, *,
                      max_bytes: int = DEFAULT_ZIP_MAX_BYTES) -> tuple[Any, list[str]]:
    """Собрать ZIP папки во временном файле. Возвращает (файл, список пропущенных).

    Порядок в архиве — тот же, что на экране: имя файла в архиве получает числовой
    префикс, иначе распаковка снова перемешает треки.

    Потолок проверяется до начала сборки: архив пишется во временный файл целиком,
    и папка на 200 МБ заняла бы 200 МБ в /tmp сервера (аудит ponytail). Сумма берётся
    из `file_object.size_bytes` — это размер, подтверждённый при загрузке.
    """
    import zipfile

    items = folder_files(conn, int(folder["id"]))
    total = sum(int(item.get("size_bytes") or 0) for item in items if item.get("object_key"))
    if total > max_bytes:
        limit_mb = max(max_bytes // (1024 * 1024), 1)
        raise FileRejected(
            "folder_zip_too_large",
            f"Архив больше {limit_mb} МБ — скачивайте по файлам.", 413)
    handle = tempfile.TemporaryFile()
    skipped: list[str] = []
    with zipfile.ZipFile(handle, "w", zipfile.ZIP_STORED) as archive:
        width = max(2, len(str(len(items))))
        for index, item in enumerate(items, start=1):
            name = item["name"] or f"файл-{index}"
            safe = zip_entry_name(index, name, width)
            if not item.get("object_key"):
                skipped.append(name)
                continue
            try:
                body = storage().get_object(Bucket=bucket_private(),
                                            Key=item["object_key"])["Body"]
                archive.writestr(safe, body.read())
            except STORAGE_ERRORS as exc:         # один сбой хранилища не рушит архив
                log.error("не удалось упаковать %s: %s", name, exc)
                skipped.append(name)
    handle.seek(0)
    return handle, skipped


def stream_zip_size(handle) -> int:
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(0)
    return size


# ------------------------------------------------------------------ поиск
def search_files(conn, who: dict, q: str = "", folder: str = "", kind: str = "",
                 storage: str = "", limit: int = 100, *, only_mine: bool = False) -> list[dict]:
    """Поиск по файлам: имя, текст, папка, тип, место (docs/development/192 §6.6).

    Видимость — существующее правило `file_for_reader`, переведённое в предикат списка:
    свой файл **или** файл в общей папке. Скриншоты обращений не попадают никогда,
    грант `access_grant` поштучный и в список не идёт. `only_mine=True` — чат: он ищет
    только свои файлы, как и раньше.

    Почему в выдаче есть файлы общей папки: их уже отдаёт экран папки, и поиск, который
    не находит файл, только что открытый человеком, — это ложь.

    Совпадение по имени выше остальных (`order by ... desc` у булева выражения), дальше
    свежие сверху. Запрос из двух знаков («пд») полнотекстовый поиск не берёт, но имя
    находит: `ilike` стоит в том же условии, а не в запасном проходе.
    """
    me = int(who.get("id") or 0)
    where = ["f.deleted_at is null", "f.scope <> %(feedback)s"]
    params: dict = {"feedback": FEEDBACK_SCOPE, "limit": max(1, min(int(limit), 200))}
    if only_mine:
        where.append("f.participant_id = %(me)s")
    else:
        where.append("(f.participant_id = %(me)s or exists ("
                     "select 1 from file_folder vf join folder vfo on vfo.id = vf.folder_id"
                     " where vf.file_id = f.id and vfo.deleted_at is null"
                     "   and vfo.scope = 'shared'))")
    params["me"] = me

    text = " ".join((q or "").split())
    order = "f.created_at desc, f.id desc"
    if text:
        where.append("(f.name ilike %(like)s"
                     " or f.search_tsv @@ websearch_to_tsquery('russian', %(q)s))")
        params["like"] = f"%{text}%"
        params["q"] = text
        # Совпадение по имени выше остальных: текст нашёлся через индекс, а имя человек видит.
        order = "(f.name ilike %(like)s) desc, f.created_at desc, f.id desc"

    path = " ".join((folder or "").split())
    if path:
        where.append("(exists (select 1 from file_folder qf join folder qfo on qfo.id = qf.folder_id"
                     " where qf.file_id = f.id and qfo.deleted_at is null"
                     "   and (qfo.path = %(path)s or qfo.path like %(path_like)s))"
                     " or (f.drive_path is not null and f.drive_path ilike %(path_any)s))")
        params["path"] = path
        params["path_like"] = f"{path}/%"
        params["path_any"] = f"%{path}%"

    if kind:
        clause, values = _kind_where(kind)
        where.append(clause)
        params["kind_0"] = values[0]
        if len(values) > 1:
            params["kind_1"] = values[1]

    if storage:
        where.append("f.storage = %(storage)s")
        params["storage"] = storage

    rows = conn.execute(
        "select f.id, f.name, f.kind, f.size_bytes, f.created_at, f.storage, f.description,"
        "       f.drive_url, f.drive_path,"
        "       (select path from folder fo join file_folder ff on ff.folder_id = fo.id"
        "         where ff.file_id = f.id and fo.deleted_at is null limit 1) as folder,"
        "       (select fo.id from folder fo join file_folder ff on ff.folder_id = fo.id"
        "         where ff.file_id = f.id and fo.deleted_at is null limit 1) as folder_id,"
        "       ts_rank(f.search_tsv, websearch_to_tsquery('russian', %(tsq)s)) as rank"
        "  from file_object f"
        f" where {' and '.join(where)}"
        f" order by {order} limit %(limit)s",
        {**params, "tsq": text}).fetchall()
    return [_with_word(dict(row)) for row in rows]


def _kind_where(word: str) -> tuple[str, list]:
    """Предикат чипа с именованными параметрами: `kind_predicate` — из одного списка.

    `kind_predicate` отдаёт `(sql, [массив, …])`: у «текста» два массива (простые и
    документные расширения), у остальных один. Здесь плейсхолдеры получают имена, чтобы
    параметры не зависели от порядка `dict`.
    """
    clause, values = kind_predicate(word)
    clause = clause.replace("= any(%s)", "= any(%(kind_0)s)", 1)
    if "= any(%s)" in clause:
        clause = clause.replace("= any(%s)", "= any(%(kind_1)s)", 1)
    return clause, values

def search(conn, participant_id: int, query: str, limit: int = 20) -> list[dict]:
    """Поиск по своим файлам для чата: та же ручка, но без общих папок (FR-K15).

    Чат-контракт по форме не меняется: имя, теги, извлечённый текст — и только свои файлы.
    """
    text = " ".join((query or "").split())
    if not text:
        return []
    return search_files(conn, {"id": participant_id}, text, limit=limit, only_mine=True)



# ------------------------------------------------------------------ ссылка на Drive
DRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
DRIVE_DOC_KINDS = ("document", "spreadsheets", "presentation", "forms", "drawings")
_DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{10,}")
# Формы адреса разбираются правилом, а не списком: `/file/d/<id>`, `/drive/folders/<id>`,
# `/drive/u/<n>/folders/<id>`, `/<вид>/d/<id>`, `open?id=<id>` и `?id=<id>` (§6.3).
_DRIVE_PATHS = (
    re.compile(r"/file/d/([A-Za-z0-9_-]+)"),
    re.compile(r"/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)"),
    re.compile(r"/(?:" + "|".join(DRIVE_DOC_KINDS) + r")/d/([A-Za-z0-9_-]+)"),
)
_DRIVE_QUERY = re.compile(r"[?&]id=([A-Za-z0-9_-]+)")


def parse_drive_url(raw: str) -> dict:
    """Разобрать ссылку на Google Drive без сетевых запросов (docs/development/192 §6.3).

    Возвращает `{file_id, drive_url, is_folder}`; отказы — `FileRejected` с кодом и словами.
    Схема — только `https`: `http://` отвергается до разбора хоста, иначе человек получит
    ссылку, которую браузер откроет, а мы — нет.
    """
    from urllib.parse import urlsplit

    url = (raw or "").strip()
    if not url.lower().startswith("https://"):
        raise FileRejected("link_not_https", "Ссылка должна начинаться с https://", 400)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in DRIVE_HOSTS:
        raise FileRejected(
            "link_host_not_allowed",
            "Ссылку можно добавить только из Google Drive — drive.google.com или docs.google.com.",
            400)

    target = parts.path or ""
    is_folder = False
    match = None
    for pattern in _DRIVE_PATHS:
        match = pattern.search(target)
        if match:
            is_folder = "folders" in pattern.pattern
            break
    if match is None:
        match = _DRIVE_QUERY.search("?" + (parts.query or ""))
        is_folder = False
    if match is None or not _DRIVE_ID.fullmatch(match.group(1)):
        raise FileRejected(
            "link_not_parsed",
            "Не понял адрес: скопируйте ссылку из Drive целиком — в ней есть «/file/d/…», "
            "«/document/d/…» или «/drive/folders/…».", 400)

    file_id = match.group(1)
    if is_folder:
        canonical = f"https://drive.google.com/drive/folders/{file_id}"
    else:
        canonical = f"https://drive.google.com/file/d/{file_id}/view"
    return {"file_id": file_id, "drive_url": canonical, "is_folder": is_folder}


_DRIVE_KNOWN_SELECT = (
    "select f.id, f.name, f.participant_id, f.deleted_at,"
    "       exists (select 1 from file_folder ff join folder fo on fo.id = ff.folder_id"
    "                where ff.file_id = f.id and fo.deleted_at is null"
    "                  and fo.scope = 'shared') as in_shared"
    "  from file_object f where f.drive_file_id = %s limit 1"
)


def _drive_row(file_id: int, payload: dict, *, revived: bool) -> dict:
    """Ответ ручки по строке ссылки; `revived` — служебный флаг для аудита, не для клиента."""
    folder = payload["folder"]
    return {"id": file_id, "name": payload["title"], "kind": payload["kind"],
            "kind_word": kind_word(payload["kind"], payload["title"]),
            "storage": "drive", "description": payload["note"],
            "folder": folder["path"] if folder is not None else None,
            "folder_id": int(folder["id"]) if folder is not None else None,
            "drive_url": payload["drive_url"], "drive_path": payload["path"],
            "size_bytes": None, "revived": revived}


def _drive_revive(conn, known, payload: dict) -> dict:
    """Поднять мягко удалённую свою ссылку: `id` тот же, поля — из запроса.

    `deleted_at` в схеме не мешает уникальности `drive_file_id` (`db/init/001`), поэтому
    повторная регистрация того же адреса упиралась в `UniqueViolation` и 500. Экрана
    восстановления удалённых файлов нет — отвечать «эта ссылка уже есть» на невидимую
    строку значит ставить человека в тупик, поэтому строка возвращается в жизнь.
    """
    row_id = int(known["id"])
    conn.execute(
        "update file_object set deleted_at = null, name = %s, description = %s, kind = %s,"
        " scope = %s, drive_url = %s, drive_path = %s where id = %s",
        (payload["title"], payload["note"], payload["kind"], payload["scope"],
         payload["drive_url"], payload["path"], row_id))
    folder = payload["folder"]
    if folder is not None:
        conn.execute("insert into file_folder (file_id, folder_id) values (%s,%s)"
                     " on conflict do nothing", (row_id, int(folder["id"])))
    return _drive_row(row_id, payload, revived=True)


def _drive_known(conn, file_id: str):
    """Строка с этим `drive_file_id` — живая или мягко удалённая (`deleted_at` в выборке)."""
    return conn.execute(_DRIVE_KNOWN_SELECT, (file_id,)).fetchone()


def _reject_live_drive(known, owner: int) -> NoReturn:
    """Живая строка занята: своя или из общей папки — 409 с именем, чужая закрытая — без."""
    if int(known["participant_id"] or 0) == owner or known["in_shared"]:
        raise FileRejected("link_exists", f"Эта ссылка уже есть: «{known['name']}».", 409)
    raise FileRejected("link_exists_other", "Эта ссылка уже добавлена в студию.", 409)


def _drive_conflict(conn, known, owner: int, payload: dict) -> dict:
    """Разобрать занятый `drive_file_id`: живая — отказ, удалённая своя — воскрешение.

    Удалённая чужая — 409 `link_exists_other` **без имени**: иначе `FR-K8` нарушается
    перебором адресов (человек узнаёт, что чужая закрытая ссылка существует и как зовётся).
    """
    if known is not None and known["deleted_at"] is not None:
        if int(known["participant_id"] or 0) == owner:
            return _drive_revive(conn, known, payload)
        raise FileRejected("link_exists_other", "Эта ссылка уже добавлена в студию.", 409)
    if known is None:
        raise FileRejected("link_exists", "Эту ссылку добавили только что — откройте список.", 409)
    return _reject_live_drive(known, owner)               # всегда поднимает отказ


def link_drive(conn, who: dict, url: str, *, name: str | None = None,
               description: str | None = None, folder_id: int | None = None,
               drive_path: str | None = None) -> dict:
    """Зарегистрировать ссылку на Drive отдельной записью (V-123, docs/development/192 §6.3).

    Байтов у нас нет: строку пишем прямой вставкой, `save_committed` не участвует —
    квота, дедупликация по `sha256` и разбор аудио/текста не вызываются.

    Дубль: своя живая строка или живая из общей папки — 409 `link_exists` с именем; чужая
    закрытая (живая или удалённая) — 409 `link_exists_other` без имени. Своя **удалённая**
    строка не отказ, а воскрешение (`deleted_at = null`, `id` прежний, поля из запроса):
    иначе человек, удаливший ссылку, навсегда получал 500 на тот же адрес.

    Гонка двух регистраций того же адреса закрыта вставкой `on conflict (drive_file_id)
    do nothing` с перечитыванием строки: проверка и вставка не атомарны, а уникальный
    индекс переживает мягкое удаление, и без этой формы второй запрос падал бы 500.
    """
    parsed = parse_drive_url(url)
    file_id = parsed["file_id"]
    is_folder = parsed["is_folder"]
    owner = int(who.get("id") or 0)

    known = _drive_known(conn, file_id)
    if known is not None and known["deleted_at"] is None:
        _reject_live_drive(known, owner)                 # живая строка — всегда отказ

    title = " ".join((name or "").split())[:200]
    if not title:
        title = "Папка в Drive" if is_folder else "Ссылка на Drive"
    kind = "folder" if is_folder else kind_by_name(title)
    note = clean_description(description)
    path = " ".join((drive_path or "").split())[:200] or None

    folder = None
    if folder_id is not None:
        folder = conn.execute(
            "select id, path, scope from folder where id = %s and deleted_at is null",
            (folder_id,)).fetchone()
        if folder is None:
            raise FileRejected("folder_not_found", "Папка не найдена.", 404)
    scope = folder["scope"] if folder is not None else "personal"

    payload = {"title": title, "kind": kind, "note": note, "path": path,
               "drive_url": parsed["drive_url"], "scope": scope, "folder": folder}
    if known is not None:
        return _drive_conflict(conn, known, owner, payload)

    row = conn.execute(
        "insert into file_object (participant_id, name, mime, size_bytes, scope, uploaded_via,"
        " drive_file_id, drive_url, drive_path, drive_mime, description, kind, storage)"
        " values (%s,%s,null,null,%s,'human_drive',%s,%s,%s,null,%s,%s,'drive')"
        " on conflict (drive_file_id) do nothing"
        " returning id", (owner, title, scope, file_id, parsed["drive_url"], path, note, kind)
    ).fetchone()
    if row is None:
        # Строку вставил параллельный запрос: читаем её и разбираем как дубль, а не 500.
        known = _drive_known(conn, file_id)
        return _drive_conflict(conn, known, owner, payload)
    new_id = int(row["id"])
    if folder is not None:
        conn.execute("insert into file_folder (file_id, folder_id) values (%s,%s)"
                     " on conflict do nothing", (new_id, int(folder["id"])))
    return _drive_row(new_id, payload, revived=False)


def update_meta(conn, who: dict, file_id: int, *, name: str | None = None,
                description: str | None = None, touch_name: bool = False,
                touch_description: bool = False) -> dict:
    """Название и описание файла: только владелец (V-124, docs/development/192 §6.4).

    Чужой и несуществующий неотличимы — 404, а не 403 (`FR-K8`). `kind`, `storage`, связи
    с папками и `created_at` не меняются; переименование garage-файла меняет слово и чип
    (расширение в имени) — это ожидаемо.
    """
    row = file_for(conn, file_id, int(who.get("id") or 0))
    if row is None:
        raise FileRejected("file_not_found", "Файл не найден.", 404)

    before: dict = {}
    after: dict = {}
    new_name = row["name"]
    new_note = row["description"]
    if touch_name:
        clean = " ".join((name or "").split())
        if not clean:
            raise FileRejected("file_name_empty", "Название не может быть пустым.", 400)
        if len(clean) > 200:
            raise FileRejected("file_name_empty", "Название длиннее 200 знаков — сократите.", 400)
        if clean != row["name"]:
            before["name"] = row["name"]
            after["name"] = clean
            new_name = clean
    if touch_description:
        clean_note = clean_description(description)
        if description is not None and len(" ".join(description.split())) > MAX_DESCRIPTION:
            raise FileRejected("file_description_too_long",
                               "Описание длиннее 500 знаков — сократите.", 400)
        if clean_note != row["description"]:
            before["description"] = row["description"]
            after["description"] = clean_note
            new_note = clean_note
    if after:
        conn.execute("update file_object set name = %s, description = %s where id = %s",
                     (new_name, new_note, file_id))
    kind = row["kind"]
    return {"id": file_id, "name": new_name, "description": new_note, "kind": kind,
            "kind_word": kind_word(kind, new_name), "before": before, "after": after}
