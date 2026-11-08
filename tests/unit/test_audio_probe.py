"""Длительность и битрейт читаются без ffmpeg (FR-M1, Ш-8).

Звук генерируется прямо в тесте `wave`: ни образцов в репозитории, ни внешних кодировщиков.
Битый файл не должен ломать приём — проверяем и это: без длительности музыка всё равно
принимается, просто без подписи «Длительность».
"""

from __future__ import annotations

import struct
import wave

from app import audio


def _wav(path, seconds: float = 1.0, rate: int = 8000) -> str:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return str(path)


def test_wav_читается_и_длительность_верна(tmp_path):
    meta = audio.probe(_wav(tmp_path / "трек.wav", seconds=2.0), "трек.wav")
    assert meta is not None
    assert meta["duration_sec"] == 2
    assert meta["format"] == "wav"
    assert meta["bitrate_kbps"] and meta["bitrate_kbps"] > 0


def test_формат_берётся_из_расширения(tmp_path):
    path = _wav(tmp_path / "голос.wav")
    assert audio.probe(path, "голос.wav")["format"] == "wav"


def test_битый_файл_не_роняет_приём(tmp_path):
    broken = tmp_path / "битый.mp3"
    broken.write_bytes(b"\x00\x01\x02not audio at all")
    assert audio.probe(str(broken), "битый.mp3") is None


def test_несуществующий_файл_тоже_none(tmp_path):
    assert audio.probe(str(tmp_path / "нет.wav"), "нет.wav") is None
