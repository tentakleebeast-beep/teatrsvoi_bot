"""Путь папки — ключ, а не украшение: имя чистится, лишние слэши не создают пустых уровней.

Обе функции маленькие и опасные: ошибка в чистке имени превращается в папку, которой нет
в дереве (`Спектакли/…` с чужим слэшем), а ошибка в разборе пути — в шесть запросов вместо
одного и в папку `''` посреди дерева.

Правила и требования раздела: docs/development/86-files-and-storage.md.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import gateway_modules
import pytest
from app.folders import FolderError, clean_name

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"


def _gateway():
    """Загрузить gateway-main под своим именем: `main` уже занят tool-api в этом процессе."""
    return gateway_modules.load("parsers")


def test_путь_разбирается_по_слэшам():
    gateway = _gateway()
    assert gateway.split_folder_path("Спектакли/Пурга/шумы") == ["Спектакли", "Пурга", "шумы"]
    assert gateway.split_folder_path("  Пьесы / Пурга ") == ["Пьесы", "Пурга"]


def test_двойной_слэш_не_создаёт_пустую_папку():
    gateway = _gateway()
    assert gateway.split_folder_path("А//Б") == ["А", "Б"]
    assert gateway.split_folder_path("///") == []
    assert gateway.split_folder_path("") == []


@pytest.mark.parametrize("name", ["Реквизит", "  Сцена  ", "И всё же..."])
def test_имя_приводится_к_виду(name):
    assert clean_name(name) == name.strip()


def test_слэш_в_имени_это_ошибка_а_не_новая_папка():
    with pytest.raises(FolderError) as caught:
        clean_name("Спектакли/Пурга")
    assert caught.value.code == "folder_name_bad"


def test_пустое_имя_и_точки_отбиваются():
    for bad in ("", "   ", ".", ".."):
        with pytest.raises(FolderError):
            clean_name(bad)


def test_слишком_длинное_имя_отбивается():
    with pytest.raises(FolderError):
        clean_name("я" * 101)
