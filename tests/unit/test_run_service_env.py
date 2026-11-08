"""Две среды запуска сервисов: dev отдельно от прода (карта t_4aa28fe9).

Проверяется то, из-за чего был инцидент 30.10.2026: локальный запуск гейтвея взял
боевой токен из основного `.env`, перехватил getUpdates у прода и уронил бота на
четыре часа. Здесь зафиксировано: dev читает только `.env.dev`, прод-токен в dev-режиме
не подставляется никогда, а без `DEV_BOT_TOKEN` запуск отказывается.

Обоснование: docs/infrastructure/08 §1a (две среды и их guard-ы), вопрос V-025
(отдельный dev-бот вместо второго токена на проде).
"""

from __future__ import annotations

import json

import pytest

from scripts import run_service

DEV_TOKEN = "7000000001:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
PROD_TOKEN = "8227000011:ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
LOCAL_DSN = "postgresql://svoi:svoi_dev_password@127.0.0.1:5433/svoi"


def write_env(path, **values: str):
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()),
                    encoding="utf-8")
    return path


def dev_file(tmp_path, **overrides):
    values = {"ENV": "dev", "DEV_BOT_TOKEN": DEV_TOKEN, "DATABASE_URL": LOCAL_DSN,
              "ADMIN_CHAT_ID": "123456789", "TOOL_API_URL": "http://127.0.0.1:8000"}
    values.update(overrides)
    return write_env(tmp_path / ".env.dev", **values)


def test_по_умолчанию_среда_разработки(monkeypatch):
    assert run_service.DEFAULT_ENVIRONMENT == "dev"
    monkeypatch.setattr(run_service, "run",
                        lambda service, port=8000, check=False, environment="", env_file=None:
                        environment == "dev")
    assert run_service.main(["gateway"]) is True


def test_файлы_сред_разные():
    assert run_service.env_file_for("dev").name == ".env.dev"
    assert run_service.env_file_for("prod") == run_service.PROD_ENV_FILE
    assert run_service.env_file_for("dev") != run_service.PROJECT / ".env"


def test_прод_файл_переопределяется_переменной(monkeypatch, tmp_path):
    monkeypatch.setenv(run_service.PROD_ENV_VAR, str(tmp_path / "prod.env"))
    assert run_service.env_file_for("prod") == tmp_path / "prod.env"


def test_dev_берёт_токен_из_dev_бота(tmp_path):
    env_file = dev_file(tmp_path)
    env = run_service.service_env("gateway", "dev", base={}, env_file=env_file)
    assert env["TELEGRAM_TOKEN"] == DEV_TOKEN
    assert env["ENV"] == "dev"
    assert env["DATABASE_URL"] == LOCAL_DSN


def test_dev_вытесняет_унаследованный_прод_токен(tmp_path):
    """Даже если в оболочке остался боевой токен, гейтвей пойдёт на dev-боте."""
    env_file = dev_file(tmp_path)
    env = run_service.service_env("gateway", "dev",
                                  base={"TELEGRAM_TOKEN": PROD_TOKEN}, env_file=env_file)
    assert env["TELEGRAM_TOKEN"] == DEV_TOKEN != PROD_TOKEN


def test_без_dev_токена_отказ_и_прод_не_подставляется(tmp_path):
    env_file = write_env(tmp_path / ".env.dev", ENV="dev", DATABASE_URL=LOCAL_DSN)
    with pytest.raises(run_service.DevEnvNotConfigured) as exc:
        run_service.service_env("gateway", "dev", base={}, env_file=env_file)
    message = str(exc.value)
    assert "среда разработки не настроена" in message
    assert "DEV_BOT_TOKEN" in message
    assert PROD_TOKEN not in message
    assert exc.value.code == 2


def test_main_печатает_отказ_и_возвращает_код(tmp_path, monkeypatch, capsys):
    """Отказ виден человеку в логе launchd, а не только кодом выхода."""
    env_file = write_env(tmp_path / ".env.dev", ENV="dev")
    monkeypatch.setattr(run_service.os, "chdir", lambda path: None)
    monkeypatch.setenv(run_service.DEV_ENV_VAR, str(env_file))
    assert run_service.main(["gateway"]) == 2
    captured = capsys.readouterr()
    assert "среда разработки не настроена" in captured.err


def test_dev_читает_только_свой_файл(tmp_path, monkeypatch):
    """Основной `.env` в dev-режиме не открывается вообще."""
    env_file = dev_file(tmp_path)
    read: list = []

    def record(path):
        read.append(path)
        return {key: value for key, value in
                (line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines())}

    monkeypatch.setattr(run_service, "load_env", record)
    env = run_service.service_env("gateway", "dev", base={}, env_file=env_file)
    assert env["TELEGRAM_TOKEN"] == DEV_TOKEN
    assert read and all(path == env_file for path in read)
    assert run_service.PROJECT / ".env" not in read


def test_прод_берёт_токен_из_боевого_конфига(tmp_path):
    env_file = write_env(tmp_path / "svoi.env", TELEGRAM_TOKEN=PROD_TOKEN,
                         DATABASE_URL="postgresql://svoi:x@db.prod:5432/svoi")
    env = run_service.service_env("gateway", "prod", base={}, env_file=env_file)
    assert env["TELEGRAM_TOKEN"] == PROD_TOKEN
    assert env["ENV"] == "prod"


def test_прод_без_токена_отказывает(tmp_path):
    env_file = write_env(tmp_path / "svoi.env", ENV="prod")
    with pytest.raises(SystemExit) as exc:
        run_service.service_env("gateway", "prod", base={}, env_file=env_file)
    assert "TELEGRAM_TOKEN" in str(exc.value)


def test_дев_окружение_смотрит_только_в_локальную_базу():
    run_service.assert_local_db(LOCAL_DSN, "dev")
    run_service.assert_local_db("", "dev")               # пусто — локальная по умолчанию
    with pytest.raises(SystemExit) as exc:
        run_service.assert_local_db("postgresql://svoi:x@db.teatr-svoi.online:5432/svoi", "dev")
    assert "не-локальную БД" in str(exc.value)
    # прод-режим проверку не делает: база там как раз удалённая
    run_service.assert_local_db("postgresql://svoi:x@db.prod:5432/svoi", "prod")


def test_дев_с_боевой_базой_не_запускается(tmp_path):
    env_file = dev_file(tmp_path, DATABASE_URL="postgresql://svoi:x@203.0.113.10:5432/svoi")
    with pytest.raises(SystemExit) as exc:
        run_service.service_env("tool-api", "dev", base={}, env_file=env_file)
    assert "не-локальную БД" in str(exc.value)


def test_маска_токена_не_раскрывает_секрет():
    masked = run_service.mask_token(DEV_TOKEN)
    assert masked.endswith(DEV_TOKEN[-4:])
    assert DEV_TOKEN[:10] not in masked
    assert run_service.mask_token("") == "нет"


def test_строка_в_лог_говорит_среду_бота_и_базу(tmp_path):
    env_file = dev_file(tmp_path)
    env = run_service.service_env("gateway", "dev", base={}, env_file=env_file)
    line = run_service.describe("dev", env, env_file)
    assert "среда=dev" in line
    assert "127.0.0.1:5433/svoi" in line
    assert DEV_TOKEN not in line and DEV_TOKEN[-4:] in line


def test_check_печатает_среду_и_не_печатает_токен(tmp_path, monkeypatch, capsys):
    env_file = dev_file(tmp_path)
    monkeypatch.setattr(run_service.os, "chdir", lambda path: None)
    assert run_service.run("gateway", check=True, env_file=env_file) == 0
    printed = capsys.readouterr().out
    payload = json.loads(printed)
    assert payload["environment"] == "dev"
    assert payload["env_file"] == str(env_file)
    assert payload["bot"].endswith(DEV_TOKEN[-4:])
    assert DEV_TOKEN not in printed


def test_запуск_пишет_среду_в_лог_до_exec(tmp_path, monkeypatch, capsys):
    env_file = dev_file(tmp_path)
    monkeypatch.setattr(run_service.os, "chdir", lambda path: None)
    monkeypatch.setattr(run_service, "rotate_logs", lambda service, logs=None: [])
    seen: dict = {}

    def fake_exec(executable, argv, env):
        seen["executable"] = executable
        seen["argv"] = argv
        seen["env"] = env
        raise RuntimeError("exec перехвачен")

    monkeypatch.setattr(run_service.os, "execvpe", fake_exec)
    with pytest.raises(RuntimeError):
        run_service.run("gateway", env_file=env_file)
    out = capsys.readouterr().out
    assert "run_service: среда=dev" in out
    assert seen["env"]["TELEGRAM_TOKEN"] == DEV_TOKEN


def test_аргумент_env_меняет_среду(tmp_path, monkeypatch, capsys):
    env_file = dev_file(tmp_path)
    monkeypatch.setattr(run_service.os, "chdir", lambda path: None)
    monkeypatch.setenv(run_service.DEV_ENV_VAR, str(env_file))
    assert run_service.main(["tool-api", "--check"]) == 0
    assert json.loads(capsys.readouterr().out)["environment"] == "dev"


def test_неизвестный_сервис_и_среда_это_ошибка(tmp_path):
    with pytest.raises(ValueError):
        run_service.service_env("scheduler", "dev", base={}, env_file=dev_file(tmp_path))
    with pytest.raises(ValueError):
        run_service.env_file_for("staging")
