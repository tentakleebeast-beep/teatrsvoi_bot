"""Планы знакомства: три шага при входе и шесть за кнопкой «Знакомство».

При входе спрашиваем только то, без чего человек не человек в боте: полное имя
(если в записи одно слово), короткое имя и телефон, если его нет (§2.1).
Остальные шесть полей — по кнопке «Знакомство», и только пустые (§2.3).
Короткое имя не пропускается: без него бот не узнаёт человека в сообщениях
(`RETRY["alias"]`), и в план дозаполнения оно не входит.

Обоснование: FR-G16 (первый вход), FR-A3 (профиль), docs/development/182 §2.1,
§2.2, §2.3, §6.2 п. 2.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tg-gateway" / "app"))

import intro  # noqa: E402


def test_план_входа_ровно_три_шага() -> None:
    assert intro.ENTRY_FIELDS == ("name", "alias", "phone")
    assert intro.build_entry_plan({}) == ["name", "alias", "phone"]


def test_имя_спрашиваем_только_при_одном_слове() -> None:
    assert "name" in intro.build_entry_plan({"name": "Арина"})
    assert "name" not in intro.build_entry_plan({"name": "Фомина Арина"})
    assert "name" not in intro.build_entry_plan({"name": "  Фомина   Арина  "})


def test_телефон_спрашиваем_только_при_пустом() -> None:
    assert "phone" in intro.build_entry_plan({"phone": None})
    assert "phone" in intro.build_entry_plan({"phone": ""})
    assert "phone" not in intro.build_entry_plan({"phone": "+79170000000"})


def test_короткое_имя_спрашиваем_всегда() -> None:
    plan = intro.build_entry_plan({"name": "Фомина Арина", "phone": "+79170000000"})
    assert plan == ["alias"]


def test_план_дозаполнения_только_пустые_из_шести() -> None:
    assert intro.FILL_FIELDS == ("birthday", "travel", "plays", "roles", "trainings",
                                 "manifest")
    assert intro.build_fill_plan({}) == list(intro.FILL_FIELDS)

    filled = {"birth_date": "1990-03-12", "travel_minutes": 40, "plays": "Чайка",
              "roles_note": "Нина", "interests": "речь", "ai_manifest": "после 11"}
    assert intro.build_fill_plan(filled) == []

    plan = intro.build_fill_plan({"birth_date": "1990-03-12", "travel_minutes": 0,
                                  "plays": "   "})
    assert plan == ["travel", "plays", "roles", "trainings", "manifest"]


def test_короткое_имя_в_дозаполнение_не_входит() -> None:
    assert "alias" not in intro.FILL_FIELDS
    assert "alias" not in intro.build_fill_plan({})
    assert "alias" in intro.build_entry_plan({})


def test_короткое_имя_не_пропускается() -> None:
    """§2.2: «Пропустить» на шаге `alias` нет — у человека должна быть подпись."""
    assert "alias" in intro.RETRY
    source = (ROOT / "services" / "tg-gateway" / "app" / "routers" / "intro.py").read_text(
        encoding="utf-8")
    assert 'follow != "alias"' in source, "кнопка «Пропустить» вернулась на шаг alias"


def test_подтверждение_пишет_только_отвеченное() -> None:
    patch = intro.build_patch({"name": "Фомина Арина", "alias": "Арина",
                               "phone": None, "travel": 40})
    assert patch == {"display_name": "Фомина Арина", "alias_add": "Арина",
                     "travel_minutes": 40}
