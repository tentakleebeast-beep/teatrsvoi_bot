"""
Ш-28: `main()` гейтвея сначала берёт замок, и только потом идёт в Telegram.

Проверяется именно проводка: без неё модуль замка есть, но на живом боте ничего не
меняет. Второй экземпляр должен выйти с понятной русской ошибкой, а не с `409` в логе
через полминуты, когда апдейты уже начали теряться.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    # Окружение здесь не подменяем (R-4): токен и замок тесты передают явно —
    # `monkeypatch.setenv("SVOI_GATEWAY_LOCK", ...)` внутри самих тестов.
    sys.path.insert(0, str(GATEWAY_APP))
    spec = importlib.util.spec_from_file_location("gw_single_instance_main",
                                                  GATEWAY_APP / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gw_single_instance_main"] = module
    spec.loader.exec_module(module)
    yield module
    module.single_instance.release()


def test_main_берёт_замок(gw, tmp_path, monkeypatch):
    path = tmp_path / "own.lock"
    monkeypatch.setenv("SVOI_GATEWAY_LOCK", str(path))
    monkeypatch.setattr(gw, "TOKEN", "42:test-token")   # токен явно, а не через окружение
    gw.single_instance.release()
    # До Telegram дело не доходит: подменяем сам polling и проверяем порядок.
    seen: list[str] = []

    class FakeDispatcher:
        def __init__(self, **kwargs) -> None:
            pass

        def include_router(self, router) -> None:
            pass

        def include_routers(self, *routers) -> None:
            pass

        async def start_polling(self, bot) -> None:
            seen.append("polling")

    monkeypatch.setattr(gw, "Dispatcher", FakeDispatcher)
    bots: list = []

    class FakeBot:
        def __init__(self, token: str) -> None:
            self.session = types.SimpleNamespace(middleware=lambda *a, **k: None)
            self.menu: list = []
            bots.append(self)

        async def set_my_commands(self, commands) -> None:
            self.menu = list(commands)

    monkeypatch.setattr(gw, "Bot", FakeBot)
    asyncio.run(gw.main())
    assert seen == ["polling"]
    # Меню команд ставит код (`commands.menu_commands()`), а не человек: иначе
    # подсказка `/note` и меню расходятся (карта t_273c08e6).
    assert [item.command for item in bots[0].menu] == \
        [item.name for item in gw.commands.menu_commands()]
    assert gw.single_instance.owner_pid(path) == os.getpid()


def test_main_второй_экземпляр_выходит(gw, tmp_path, monkeypatch):
    path = tmp_path / "taken.lock"
    monkeypatch.setenv("SVOI_GATEWAY_LOCK", str(path))
    monkeypatch.setattr(gw, "TOKEN", "42:test-token")
    gw.single_instance.release()
    gw.single_instance.acquire(path)
    with pytest.raises(SystemExit) as exc:
        asyncio.run(gw.main())
    assert "уже запущен" in str(exc.value)
    gw.single_instance.release()
