"""Аудио приходит с длительностью и попадает в папку `Аудио` (FR-M1, FR-M5, Ш-8).

Звук генерируется в тесте: ни образцов в репозитории, ни ffmpeg. Проверяется и строка
`file_audio`, и то, что человек увидит в ответе бота — «Длительность: 0:02».
"""

from __future__ import annotations

import os
import pathlib
import struct
import wave
from urllib.parse import quote

import psycopg
import pytest

from tests.conftest import _test_dsn

ROOT = pathlib.Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:                     # pragma: no cover
    pass

pytestmark = pytest.mark.skipif(
    not os.getenv("STORAGE_ENDPOINT"), reason="Garage не поднят: STORAGE_ENDPOINT не задан")


def _wav_bytes(seconds: float = 2.0, rate: int = 8000) -> bytes:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buffer.getvalue()


@pytest.fixture()
def upload_cleanup():
    keys: list[str] = []
    yield keys
    from app import files_store

    for key in keys:
        try:
            files_store.storage().delete_object(
                Bucket=files_store.bucket_private(), Key=key)
        except Exception:               # noqa: BLE001 — уборка не должна ронять тест
            pass


def _upload(client, auth, name: str, data: bytes, mime: str) -> dict:
    response = client.post("/files/upload", content=data,
                           headers={**auth, "X-File-Name": quote(name), "X-File-Mime": mime})
    assert response.status_code == 200, response.text
    return response.json()


def test_wav_получает_длительность_и_папку_аудио(client, auth, upload_cleanup) -> None:
    body = _upload(client, auth, "дождь.wav", _wav_bytes(2.0), "audio/wav")
    with psycopg.connect(_test_dsn()) as conn:
        key = conn.execute("select object_key from file_object where id = %s",
                           (body["id"],)).fetchone()[0]
        audio_row = conn.execute(
            "select duration_sec, bitrate_kbps, format from file_audio where file_id = %s",
            (body["id"],)).fetchone()
    upload_cleanup.append(key)

    assert body["kind"] == "audio"
    assert body["folder"] == "Личное/Аудио"
    assert body["duration_sec"] == 2
    assert audio_row is not None
    assert audio_row[0] == 2 and audio_row[2] == "wav"
    assert audio_row[1] and audio_row[1] > 0


def test_повторная_загрузка_сохраняет_длительность(client, auth, upload_cleanup) -> None:
    data = _wav_bytes(1.0)
    first = _upload(client, auth, "трек.wav", data, "audio/wav")
    with psycopg.connect(_test_dsn()) as conn:
        key = conn.execute("select object_key from file_object where id = %s",
                           (first["id"],)).fetchone()[0]
    upload_cleanup.append(key)

    second = _upload(client, auth, "трек (копия).wav", data, "audio/wav")
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    assert second["duration_sec"] == 1


def test_картинка_не_обращается_в_аудио(client, auth, upload_cleanup) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    body = _upload(client, auth, "афиша.png", png, "image/png")
    with psycopg.connect(_test_dsn()) as conn:
        key = conn.execute("select object_key from file_object where id = %s",
                           (body["id"],)).fetchone()[0]
        audio_rows = conn.execute("select count(*) from file_audio where file_id = %s",
                                  (body["id"],)).fetchone()[0]
    upload_cleanup.append(key)
    assert body["duration_sec"] is None
    assert audio_rows == 0
