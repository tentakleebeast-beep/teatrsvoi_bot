"""Порядок треков: «02» раньше «10», и архив не мешает его сохранить (FR-M4, FR-M6).

Эти две функции решают, пойдёт ли музыка на показе в правильном порядке. Обычная
сортировка по строке поставила бы «10 — финал» вторым, а ZIP без префикса — снова
перемешал бы файлы при распаковке.
"""

from __future__ import annotations

import pytest
from app import files_store


@pytest.mark.parametrize(("names", "expected"), [
    (["10 — финал", "02 — выход", "01 — пролог"], ["01 — пролог", "02 — выход", "10 — финал"]),
    (["трек без номера", "01 — первый"], ["01 — первый", "трек без номера"]),
    (["  7 — с пробелом", "6 — раньше"], ["6 — раньше", "  7 — с пробелом"]),
    (["04-А", "04-Б"], ["04-А", "04-Б"]),
])
def test_числовой_префикс_важнее_букв(names, expected):
    assert sorted(names, key=files_store.natural_key) == expected


def test_имена_без_номеров_идут_по_алфавиту():
    names = ["шумы дождь", "Афиша", "бубен"]
    assert sorted(names, key=files_store.natural_key)[0] == "Афиша"


def test_имя_архива_повторяет_путь_папки():
    assert files_store.folder_zip_name({"path": "Спектакли/Пурга/музыка"}) == \
        "Спектакли — Пурга — музыка.zip"
    assert files_store.folder_zip_name({"name": "папка"}) == "папка.zip"


def test_имя_в_архиве_получает_номер_если_его_нет():
    assert files_store.zip_entry_name(3, "01 — пролог.mp3") == "01 — пролог.mp3"
    assert files_store.zip_entry_name(3, "пролог.mp3") == "03 — пролог.mp3"
    assert files_store.zip_entry_name(3, "итог.mp3", width=3) == "003 — итог.mp3"
