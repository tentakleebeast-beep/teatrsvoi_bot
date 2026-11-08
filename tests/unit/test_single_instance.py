"""
Ш-28: второй экземпляр `tg-gateway` не должен стартовать.

Дефект найден на Ш-06: после перезапуска в системе жили два процесса поллера, оба в
polling. Telegram отдаёт апдейт только одному из них, второй получает `409 Conflict`,
и бот «мигает»: кнопки не отвечают, формы обрываются, сообщения теряются. launchd
теперь держит один процесс, но ручной запуск (отладка, старая инструкция с `nohup`)
по-прежнему мог дать второй — здесь проверяется замок, который такого не пускает.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE = ROOT / "services" / "tg-gateway" / "app" / "single_instance.py"


@pytest.fixture(scope="module")
def si():
    spec = importlib.util.spec_from_file_location("gw_single_instance", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gw_single_instance"] = module
    spec.loader.exec_module(module)
    yield module
    module.release()


@pytest.fixture(autouse=True)
def clean_lock(si):
    si.release()
    yield
    si.release()


def test_замок_создаётся_и_пишет_pid(si, tmp_path):
    target = tmp_path / "state" / "gw.lock"
    assert si.acquire(target) == target
    assert target.is_file()
    assert si.owner_pid(target) == os.getpid()


def test_второй_экземпляр_в_том_же_процессе_не_пускается(si, tmp_path):
    target = tmp_path / "gw.lock"
    si.acquire(target)
    with pytest.raises(si.AlreadyRunning) as exc:
        si.acquire(target)
    message = str(exc.value)
    assert "уже запущен" in message
    assert str(os.getpid()) in message
    assert "409" in message


def test_после_освобождения_замок_берётся_снова(si, tmp_path):
    target = tmp_path / "gw.lock"
    si.acquire(target)
    si.release()
    si.acquire(target)
    assert si.owner_pid(target) == os.getpid()


def test_мёртвый_pid_в_файле_не_мешает(si, tmp_path):
    # файл остался от убитого процесса: flock снят ядром, замок берётся
    target = tmp_path / "gw.lock"
    target.write_text("999999\n", encoding="utf-8")
    si.acquire(target)
    assert si.owner_pid(target) == os.getpid()


def test_второй_процесс_получает_отказ(si, tmp_path):
    holder = tmp_path / "holder.py"
    ready = tmp_path / "ready"
    holder.write_text(
        "import importlib.util, pathlib, sys, time\n"
        f"spec = importlib.util.spec_from_file_location('si', {str(MODULE)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        f"lock = pathlib.Path({str(tmp_path / 'gw.lock')!r})\n"
        "module.acquire(lock)\n"
        f"open({str(ready)!r}, 'w').write(str(module.owner_pid(lock)))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen([sys.executable, str(holder)])
    try:
        deadline = time.time() + 10
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "дочерний процесс не взял замок"
        child_pid = int(ready.read_text(encoding="utf-8"))
        with pytest.raises(si.AlreadyRunning) as exc:
            si.acquire(tmp_path / "gw.lock")
        assert str(child_pid) in str(exc.value)
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_путь_из_окружения(si, tmp_path, monkeypatch):
    target = tmp_path / "custom.lock"
    monkeypatch.setenv("SVOI_GATEWAY_LOCK", str(target))
    assert si.lock_path() == target


def test_путь_по_умолчанию(si, monkeypatch):
    monkeypatch.delenv("SVOI_GATEWAY_LOCK", raising=False)
    assert si.lock_path().name == "tg-gateway.lock"
    assert si.lock_path().parent.name == "svoi"


def test_владелец_нет_файла(si, tmp_path):
    assert si.owner_pid(tmp_path / "нет.lock") is None
