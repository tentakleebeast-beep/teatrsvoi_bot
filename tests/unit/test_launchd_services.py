"""Устойчивый запуск сервисов на маке: launchd-агенты и живая проверка (Ш-27)."""

from __future__ import annotations

import io
import json
import plistlib
import subprocess
from pathlib import Path

import pytest

from scripts import check_services, install_launchd, run_service

TEMPLATES = install_launchd.TEMPLATES
LABELS = install_launchd.LABELS


def test_шаблоны_это_plist_с_keepalive():
    for service, label in LABELS.items():
        template = TEMPLATES / f"{label}.plist.template"
        data = plistlib.loads(template.read_bytes())
        assert data["Label"] == label
        assert data["RunAtLoad"] is True
        assert data["KeepAlive"] is True
        assert data["WorkingDirectory"] == "@PROJECT@"
        assert data["EnvironmentVariables"]["PYTHONPATH"] == ""
        # процесс поднимается через скрипт, а не через sh -c: политика режет шелл
        assert data["ProgramArguments"][0] == "@PYTHON@"
        assert data["ProgramArguments"][1].endswith("/scripts/run_service.py")
        assert data["ProgramArguments"][2] == service
        # t_4aa28fe9: агент всегда поднимает dev-среду, прод-токен ему недоступен
        assert data["ProgramArguments"][3:5] == ["--env", "@ENV@"]
        assert data["EnvironmentVariables"]["SVOI_ENV"] == "@ENV@"
        assert data["StandardOutPath"].startswith("@LOG_DIR@/")


def test_скрипт_из_plist_существует(monkeypatch):
    monkeypatch.setattr(install_launchd, "TEMPLATES", TEMPLATES)
    data = plistlib.loads((TEMPLATES / "com.svoi.tool-api.plist.template").read_bytes())
    script = Path(data["ProgramArguments"][1].replace("@PROJECT@", str(run_service.PROJECT)))
    assert script.is_file()


def test_подстановка_без_заглушек_и_с_пробелом_в_пути(tmp_path):
    # путь с пробелом задаём сами: в CI проект лежит в пути без пробела,
    # и проверка «а если пробел» не должна зависеть от машины (INT-005)
    project = tmp_path / "teatrsvoi_bot Project"
    project.mkdir()
    text = install_launchd.plists(project=project, python="/usr/bin/python3")["tool-api"]
    assert "@" not in text
    data = plistlib.loads(text.encode("utf-8"))
    assert data["WorkingDirectory"] == str(project)
    assert " " in str(project)  # путь с пробелом не ломает ни XML, ни argv
    assert data["ProgramArguments"][1] == f"{project}/scripts/run_service.py"
    assert data["ProgramArguments"][0] == "/usr/bin/python3"
    # среда по умолчанию — dev: на маке агент не должен поднимать прод-поллер
    assert data["ProgramArguments"][3:5] == ["--env", "dev"]
    assert data["EnvironmentVariables"]["SVOI_ENV"] == "dev"


def test_установка_в_пустую_папку(tmp_path, monkeypatch):
    # живой pgrep и launchctl не должны влиять на тест: подменяем и то, и другое
    monkeypatch.setattr(install_launchd, "processes", lambda service: [])
    monkeypatch.setattr(install_launchd, "is_loaded", lambda service: False)
    actions = install_launchd.install(
        dry_run=True, target_dir=tmp_path, logs=tmp_path / "logs",
    )
    assert len(actions) == len(install_launchd.ALL_LABELS)
    assert not list(tmp_path.glob("*.plist")), "dry-run ничего не пишет"


def test_чужой_процесс_тормозит_установку_без_takeover(tmp_path, monkeypatch):
    monkeypatch.setattr(install_launchd, "processes", lambda service: [4242])
    monkeypatch.setattr(install_launchd, "is_loaded", lambda service: False)
    actions = install_launchd.install(
        dry_run=True, target_dir=tmp_path, logs=tmp_path / "logs",
    )
    assert any("нужен --takeover" in line and "4242" in line for line in actions)


def test_установка_пишет_plist_в_папку_агентов(tmp_path, monkeypatch):
    # launchctl не трогаем: подменяем subprocess целиком (ни загрузки, ни pgrep)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(install_launchd.subprocess, "run", fake_run)
    monkeypatch.setattr(install_launchd, "processes", lambda service: [])
    monkeypatch.setattr(install_launchd, "is_loaded", lambda service: True)
    monkeypatch.setattr(install_launchd.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(install_launchd, "wait_running", lambda service, timeout=15.0: True)
    install_launchd.install(
        services=["gateway"], target_dir=tmp_path, logs=tmp_path / "logs",
    )

    written = tmp_path / "com.svoi.gateway.plist"
    assert written.is_file()
    assert plistlib.loads(written.read_bytes())["Label"] == "com.svoi.gateway"
    assert (tmp_path / "logs").is_dir()
    assert any(argv[:1] == ["launchctl"] and "bootstrap" in argv for argv in calls)


def test_env_режет_инлайн_комментарий(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TZ_OFFSET_HOURS=4    # Самара, UTC+4\n"
        "TELEGRAM_TOKEN=123:abc\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    values = run_service.load_env(env_file)
    assert values["TZ_OFFSET_HOURS"] == "4"
    assert int(values["TZ_OFFSET_HOURS"]) == 4
    assert values["TELEGRAM_TOKEN"] == "123:abc"
    assert values["EMPTY"] == ""


def test_нет_файла_env_пустой_словарь(tmp_path):
    assert run_service.load_env(tmp_path / "нет.env") == {}


def test_окружение_без_pythonpath(monkeypatch):
    monkeypatch.setattr(run_service, "load_env", lambda path: {"ENV": "dev"})
    env = run_service.build_env({"PYTHONPATH": "/hermes/lib", "PATH": "/usr/bin"})
    assert "PYTHONPATH" not in env
    assert env["ENV"] == "dev"
    assert env["PATH"] == "/usr/bin"


def test_команды_сервисов():
    api = run_service.command("tool-api", python="/py", port=8123)
    assert api[:4] == ["/py", "-m", "uvicorn", "app.main:app"]
    assert api[-2:] == ["--port", "8123"]
    gateway = run_service.command("gateway", python="/py")
    assert gateway[0] == "/py"
    assert gateway[1].endswith("services/tg-gateway/app/main.py")


def _fake_urlopen(payload: dict | None, error: Exception | None = None):
    def opener(url, timeout=0):
        if error is not None:
            raise error
        return io.BytesIO(json.dumps(payload).encode("utf-8"))
    return opener


def test_здоровье_сервиса(monkeypatch):
    """Ш-27: /health ok — проблем нет; недоступный сервис — проблема с текстом «нет ответа»."""
    monkeypatch.setattr(check_services.urllib.request, "urlopen",
                        _fake_urlopen({"ok": True, "db": "postgres"}))
    assert check_services.check_health("http://127.0.0.1:8000") == []
    monkeypatch.setattr(check_services.urllib.request, "urlopen",
                        _fake_urlopen(None, check_services.urllib.error.URLError("connection refused")))
    problems = check_services.check_health("http://127.0.0.1:8000")
    assert problems and "нет ответа" in problems[0]


@pytest.mark.parametrize("pids,owner,expected", [
    ([4242], 4242, ()),
    ([100, 200], 4242, ("экземпляров 2",)),
    ([4242], None, ("без замка",)),
    ([4242], 9999, ("9999", "4242")),
])
def test_живая_проверка_гейтвея(monkeypatch, pids, owner, expected):
    """Ш-27/Ш-28: поллер один и держит замок; дубли, отсутствие замка и чужой pid — проблемы."""
    monkeypatch.setattr(check_services.urllib.request, "urlopen", _fake_urlopen({"ok": True}))
    monkeypatch.setattr(check_services, "gateway_pids", lambda: pids)
    monkeypatch.setattr(check_services, "lock_owner", lambda: owner)
    problems = check_services.check_live()
    if not expected:
        assert problems == []
    else:
        assert len(problems) == 1, problems
        assert all(fragment in problems[0] for fragment in expected)


def test_живая_проверка_гейтвей_лежит(monkeypatch):
    monkeypatch.setattr(check_services.urllib.request, "urlopen", _fake_urlopen({"ok": True}))
    monkeypatch.setattr(check_services, "gateway_pids", lambda: [])
    problems = check_services.check_live()
    assert problems == ["tg-gateway не запущен: апдейты Telegram никто не читает"]


def test_флаг_live_включается_в_отчёт(monkeypatch, capsys):
    monkeypatch.setattr(check_services, "check_live", lambda url: ["tg-gateway не запущен"])
    monkeypatch.setattr(check_services, "check_local_agents", lambda: [])
    code = check_services.main(["--live", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "live: /health, процессы tg-gateway" in report["checked"]
    assert report["errors"] == ["tg-gateway не запущен"]


def test_загрузка_повторяется_после_сбоя_bootstrap(tmp_path, monkeypatch):
    # живой случай: bootout асинхронен, первый bootstrap отвечает ошибкой
    attempts: list[str] = []

    def fake_run(argv, **kwargs):
        if "bootstrap" in argv:
            attempts.append("bootstrap")
            code = 1 if len(attempts) == 1 else 0
            return subprocess.CompletedProcess(argv, code, stdout="", stderr="Input/output error")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(install_launchd.subprocess, "run", fake_run)
    monkeypatch.setattr(install_launchd.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(install_launchd, "wait_running", lambda service, timeout=15.0: True)
    result = install_launchd.load_agent("tool-api", tmp_path / "com.svoi.tool-api.plist")
    assert result == "tool-api: bootstrap ok"
    assert attempts == ["bootstrap", "bootstrap"]


def test_загрузка_считается_провальной_если_процесс_не_поднялся(tmp_path, monkeypatch):
    monkeypatch.setattr(install_launchd.subprocess,
                        "run", lambda argv, **kwargs: subprocess.CompletedProcess(
                            argv, 0, stdout="", stderr=""))
    monkeypatch.setattr(install_launchd.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(install_launchd, "wait_running", lambda service, timeout=15.0: False)
    result = install_launchd.load_agent("gateway", tmp_path / "com.svoi.gateway.plist")
    assert "ошибка" in result


def test_ожидание_видит_поздний_старт(monkeypatch):
    answers = iter([[], [], [777]])
    monkeypatch.setattr(install_launchd, "processes", lambda service: next(answers, [777]))
    monkeypatch.setattr(install_launchd.time, "sleep", lambda seconds: None)
    assert install_launchd.wait_running("gateway", timeout=5) is True


def test_ожидание_истекает(monkeypatch):
    monkeypatch.setattr(install_launchd, "processes", lambda service: [])
    monkeypatch.setattr(install_launchd.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(install_launchd.time, "monotonic", iter([0, 1, 2, 9]).__next__)
    assert install_launchd.wait_running("gateway", timeout=5) is False


def test_чужой_процесс_без_takeover_не_перезаписывает_plist(tmp_path, monkeypatch):
    monkeypatch.setattr(install_launchd, "is_loaded", lambda service: False)
    monkeypatch.setattr(install_launchd, "processes", lambda service: [4242])
    services = list(install_launchd.LABELS)          # периодический агент не демон
    actions = install_launchd.install(services=services, target_dir=tmp_path,
                                      logs=tmp_path / "logs")
    assert not list(tmp_path.glob("*.plist"))
    assert sum("уже запущен вне launchd" in line for line in actions) == len(services)


def _write_agents(target: Path) -> None:
    """Разложить отрендеренные plist-ы как после install_launchd."""
    target.mkdir(parents=True, exist_ok=True)
    for service, label in install_launchd.ALL_LABELS.items():
        text = install_launchd.plists()[service]
        (target / f"{label}.plist").write_text(text, encoding="utf-8")


def test_doctor_сравнивает_plist_с_шаблоном(tmp_path):
    """Ш-38: doctor молчит на совпадении, видит путь прошлого места и отсутствие агента."""
    _write_agents(tmp_path)
    assert install_launchd.doctor(target_dir=tmp_path) == []
    old = tmp_path / "com.svoi.tool-api.plist"
    data = plistlib.loads(old.read_bytes())
    data["WorkingDirectory"] = "/Users/old/Documents/teatrsvoi_bot"
    old.write_bytes(plistlib.dumps(data))
    problems = install_launchd.doctor(target_dir=tmp_path)
    assert len(problems) == 1
    assert "tool-api" in problems[0] and "Documents" in problems[0]
    empty = tmp_path / "empty"
    problems = install_launchd.doctor(target_dir=empty)
    assert len(problems) == len(install_launchd.ALL_LABELS)
    assert all("нет" in line and "--takeover" in line for line in problems)


def test_check_local_agents_добавляет_расхождение_plist(tmp_path, monkeypatch):
    """Дрейф plist-ов — свойство машины, поэтому живёт в check_local_agents (INT-005)."""
    _write_agents(tmp_path)
    monkeypatch.setattr(install_launchd, "agents_dir", lambda: tmp_path)
    monkeypatch.setattr(check_services.sys, "platform", "darwin")
    assert check_services.plist_drift() == []
    data = plistlib.loads((tmp_path / "com.svoi.gateway.plist").read_bytes())
    data["ProgramArguments"][1] = "/Users/old/Documents/teatrsvoi_bot/scripts/run_service.py"
    (tmp_path / "com.svoi.gateway.plist").write_bytes(plistlib.dumps(data))
    problems = check_services.check_local_agents()
    assert any("gateway" in line and "переустанови" in line for line in problems)


def test_check_live_не_зависит_от_ноутбука(monkeypatch):
    """Сервисная проверка не должна краснеть из-за plist-ов и логов этой машины."""
    monkeypatch.setattr(check_services, "check_health", lambda *a, **k: [])
    monkeypatch.setattr(check_services, "gateway_pids", lambda: [4242])
    monkeypatch.setattr(check_services, "lock_owner", lambda: 4242)
    monkeypatch.setattr(check_services.sys, "platform", "darwin")
    monkeypatch.setattr(check_services, "check_local_agents",
                        lambda: ["plist расходится"])
    assert check_services.check_live() == []


def test_ротация_лога_на_старте_сервиса(tmp_path):
    big = tmp_path / "gateway.err.log"
    big.write_bytes(b"x" * 2048)
    small = tmp_path / "gateway.log"
    small.write_text("мало", encoding="utf-8")
    actions = run_service.rotate_logs("gateway", logs=tmp_path, max_bytes=1024)
    assert actions == ["gateway.err.log -> gateway.err.log.1"]
    assert not big.exists() and (tmp_path / "gateway.err.log.1").is_file()
    assert small.exists()


def test_ротация_заменяет_прошлый_сдвиг(tmp_path):
    (tmp_path / "tool-api.log").write_bytes(b"y" * 2048)
    previous = tmp_path / "tool-api.log.1"
    previous.write_text("старое", encoding="utf-8")
    run_service.rotate_logs("tool-api", logs=tmp_path, max_bytes=1024)
    assert previous.read_bytes() == b"y" * 2048


def test_ротация_не_трогает_лог_другого_сервиса(tmp_path):
    other = tmp_path / "tool-api.log"
    other.write_bytes(b"z" * 2048)
    assert run_service.rotate_logs("gateway", logs=tmp_path, max_bytes=1024) == []
    assert other.exists()


def test_периодический_агент_сторожа_это_интервал_а_не_демон():
    """Сторож 409 на маке: раз в 5 минут, dev-среда, без KeepAlive (t_4aa28fe9)."""
    data = plistlib.loads(
        (TEMPLATES / "com.svoi.conflicts.plist.template").read_bytes())
    assert data["Label"] == "com.svoi.conflicts"
    assert data["StartInterval"] == 300
    assert "KeepAlive" not in data
    args = data["ProgramArguments"]
    assert args[1].endswith("/scripts/conflict_watch.py")
    assert args[-1] == "--apply"
    assert "--source" in args and "files" in args
    assert args[args.index("--env") + 1] == "@ENV@"
    assert data["EnvironmentVariables"]["SVOI_ENV"] == "@ENV@"


def test_сторож_ставится_и_загружается_без_ожидания_процесса(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(install_launchd.subprocess, "run", fake_run)
    monkeypatch.setattr(install_launchd, "processes", lambda service: [])
    monkeypatch.setattr(install_launchd, "is_loaded", lambda service: False)
    monkeypatch.setattr(install_launchd.time, "sleep", lambda seconds: None)
    # прогон сторожа живёт секунды: ждать его процесса нельзя
    monkeypatch.setattr(install_launchd, "wait_running",
                        lambda service, timeout=15.0: pytest.fail("ждёт процесс сторожа"))

    actions = install_launchd.install(services=["conflicts"], target_dir=tmp_path,
                                      logs=tmp_path / "logs")
    written = tmp_path / "com.svoi.conflicts.plist"
    assert written.is_file()
    data = plistlib.loads(written.read_bytes())
    assert data["StartInterval"] == 300 and data["ProgramArguments"][-1] == "--apply"
    assert any("bootstrap ok" in line for line in actions)
    assert any(argv[:1] == ["launchctl"] and "bootstrap" in argv for argv in calls)


def test_идущий_прогон_сторожа_не_блокирует_установку(tmp_path, monkeypatch):
    """Периодический агент в момент прогона — это норма, а не «чужой поллер»."""
    monkeypatch.setattr(install_launchd.subprocess,
                        "run", lambda argv, **kwargs: subprocess.CompletedProcess(
                            argv, 0, stdout="", stderr=""))
    monkeypatch.setattr(install_launchd, "is_loaded", lambda service: False)
    monkeypatch.setattr(install_launchd, "processes", lambda service: [4242])
    install_launchd.install(services=["conflicts"], target_dir=tmp_path,
                            logs=tmp_path / "logs")
    assert (tmp_path / "com.svoi.conflicts.plist").is_file()


def test_переустановка_загруженного_сторожа_снимает_его_по_своему_ярлыку(tmp_path, monkeypatch):
    """Регрессия: stop_agent брал ярлык из LABELS, где периодического агента нет.

    Как только `com.svoi.conflicts` загружен, install(services=["conflicts"]) падал
    с KeyError: 'conflicts' — документированный после мержа шаг
    `scripts/install_launchd.py --takeover` был невыполним, и агент оставался
    с путями внутрь worktree (t_4aa28fe9).
    """
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(install_launchd.subprocess, "run", fake_run)
    # агент уже загружен прошлой установкой — значит install обязан его снять
    monkeypatch.setattr(install_launchd, "is_loaded", lambda service: True)

    actions = install_launchd.install(services=["conflicts"], target_dir=tmp_path,
                                      logs=tmp_path / "logs")

    bootouts = [argv for argv in calls if argv[:2] == ["launchctl", "bootout"]]
    assert ["launchctl", "bootout", f"{install_launchd.domain()}/com.svoi.conflicts"] in bootouts
    assert any("bootstrap ok" in line for line in actions)
    assert (tmp_path / "com.svoi.conflicts.plist").is_file()


def test_uninstall_снимает_и_периодического_сторожа(tmp_path, monkeypatch):
    """`--uninstall` обязан снимать все агенты, включая com.svoi.conflicts."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(install_launchd.subprocess, "run", fake_run)
    (tmp_path / "com.svoi.conflicts.plist").write_text("x", encoding="utf-8")

    actions = install_launchd.uninstall(target_dir=tmp_path)

    booted = {argv[2].rsplit("/", 1)[-1] for argv in calls
              if argv[:2] == ["launchctl", "bootout"]}
    assert booted == set(install_launchd.ALL_LABELS.values())
    assert any(line.startswith("conflicts:") for line in actions)
    assert not (tmp_path / "com.svoi.conflicts.plist").exists()
