"""Тома docker-compose: первый запуск на чистой машине и сохранность данных.

Том хранилища `gardata` обязан создаваться самим compose (иначе `docker compose up`
на чистой машине падает с `external volume "s3-storage_gardata" not found`) и при
этом иметь фиксированное имя (иначе compose добавит к нему имя каталога проекта и
потеряет уже накопленные файлы при переезде или запуске из другого worktree).
Два этих требования тянут в разные стороны — тест держит оба.

Обоснование: docs/infrastructure/08, Ш-08 «клон с чистой машины», карта t_0dbe4cda.
"""

from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
# Имя тома не должно зависеть от каталога проекта: на старой машине он назывался
# s3-storage_gardata, и данные локального хранилища лежат именно там.
GARDATA_NAME = "s3-storage_gardata"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_том_хранилища_создаёт_compose_а_не_человек() -> None:
    """`external: true` запрещён: он требует, чтобы том уже существовал."""
    gardata = _compose()["volumes"]["gardata"]
    assert "external" not in gardata, (
        "том gardata объявлен внешним: на чистой машине docker compose up упадёт "
        "с external volume not found, а руками его создавать не должно быть нужно"
    )


def test_имя_тома_фиксировано_и_не_зависит_от_каталога() -> None:
    """`name:` задан явно — иначе compose подставит префикс проекта и данные «пропадут»."""
    gardata = _compose()["volumes"]["gardata"]
    assert gardata.get("name") == GARDATA_NAME


def test_сервис_garage_монтирует_именно_этот_том() -> None:
    """Том ищется по имени, а не по расположению: он должен быть смонтирован в garage."""
    garage = _compose()["services"]["garage"]
    assert "gardata:/var/lib/garage" in garage["volumes"]
