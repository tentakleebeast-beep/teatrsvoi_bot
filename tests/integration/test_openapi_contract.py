"""Контракт API: снимок OpenAPI и тест на молчаливое изменение схемы (INT-012).

Правило (docs/development/26 §4a): изменение схемы = обновление `contracts/openapi.json`
тем же коммитом и перечень изменений в отчёте карты. Тест молчит, пока снимок совпадает
с `app.openapi()`, и падает точечным диффом (путь, что было, что стало), когда разошёлся.

Здесь же проверяется сам механизм на синтетической схеме: добавление/удаление маршрута,
смена метода, переименование и удаление поля обязаны давать дифф, а возврат к исходной
схеме — пустой. И отдельно — что в снимке нет секретов и внутренних адресов.
"""

from __future__ import annotations

import copy
import json
import re

import openapi_snapshot as snap


def _base_schema() -> dict:
    """Минимальная схема с маршрутом, методом и полем ответа."""
    return {
        "paths": {
            "/tools/ping": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"ok": {"type": "boolean"}},
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }


def _with_change(mutator) -> dict:
    """Копия базовой схемы с одним изменением — чтобы фикстуры не делили состояние."""
    schema = copy.deepcopy(_base_schema())
    mutator(schema)
    return schema


def _diff_lines(old: dict, new: dict) -> list[str]:
    return snap.diff_snapshots(snap.normalise(old), snap.normalise(new))


def test_снимок_совпадает_с_фактической_схемой() -> None:
    """INT-012: `scripts/openapi_snapshot.py` без флага — «контракт совпадает», код 0."""
    current = snap.snapshot_data()
    saved = snap.load_snapshot()
    changes = snap.diff_snapshots(saved, current)
    assert not changes, (
        "контракт API изменился, а снимок нет. Точечный дифф:\n"
        + "\n".join(changes)
        + "\nЕсли изменение осознанное: scripts/openapi_snapshot.py --write"
    )


def test_снимок_совпадает_байт_в_байт() -> None:
    """INT-012: снимок — ровно то, что печатает генератор (ручная правка не проходит)."""
    saved = snap.SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert saved == snap.render(snap.snapshot_data()), (
        "contracts/openapi.json отличается от генерации форматированием или вручную: "
        "обновлять только через scripts/openapi_snapshot.py --write"
    )


def test_добавленный_маршрут_даёт_дифф() -> None:
    """INT-012: новый маршрут ломает тест и печатает путь."""
    changed = _with_change(lambda schema: schema["paths"].update({"/tools/new": {"get": {}}}))
    lines = _diff_lines(_base_schema(), changed)
    assert any("/paths/~1tools~1new/get" in line and "добавлено" in line for line in lines), lines


def test_удалённый_маршрут_даёт_дифф() -> None:
    """INT-012: удалённый маршрут ломает тест и печатает путь."""
    changed = _with_change(lambda schema: schema["paths"].pop("/tools/ping"))
    lines = _diff_lines(_base_schema(), changed)
    assert any("/paths/~1tools~1ping/get" in line and "удалено" in line for line in lines), lines


def test_смена_метода_даёт_дифф() -> None:
    """INT-012: метод — часть контракта, get -> post не проходит молча."""
    def change(schema: dict) -> None:
        item = schema["paths"]["/tools/ping"]
        item["post"] = item.pop("get")

    lines = _diff_lines(_base_schema(), _with_change(change))
    assert any(line.startswith("- /paths/~1tools~1ping/get") for line in lines), lines
    assert any(line.startswith("+ /paths/~1tools~1ping/post") for line in lines), lines


def test_переименованное_поле_даёт_дифф() -> None:
    """INT-012: переименование поля видно по обоим путям — что было и что стало."""
    def change(schema: dict) -> None:
        props = schema["paths"]["/tools/ping"]["get"]["responses"]["200"]["content"][
            "application/json"]["schema"]["properties"]
        props["success"] = props.pop("ok")

    lines = _diff_lines(_base_schema(), _with_change(change))
    joined = "\n".join(lines)
    assert "/properties/ok" in joined and "удалено" in joined, lines
    assert "/properties/success" in joined and "добавлено" in joined, lines


def test_удалённое_поле_даёт_дифф() -> None:
    """INT-012: удаление поля в схеме — это изменение контракта, а не мелочь."""
    def change(schema: dict) -> None:
        props = schema["paths"]["/tools/ping"]["get"]["responses"]["200"]["content"][
            "application/json"]["schema"]["properties"]
        props.pop("ok")

    lines = _diff_lines(_base_schema(), _with_change(change))
    assert any("/properties/ok" in line and "удалено" in line for line in lines), lines


def test_возврат_к_исходной_схеме_зелёный() -> None:
    """INT-012: механизм не шумит — та же схема даёт пустой дифф."""
    assert _diff_lines(_base_schema(), _base_schema()) == []


def test_изменение_значения_показывает_было_и_стало() -> None:
    """INT-012: смена типа поля печатает путь и оба значения."""
    def change(schema: dict) -> None:
        props = schema["paths"]["/tools/ping"]["get"]["responses"]["200"]["content"][
            "application/json"]["schema"]["properties"]
        props["ok"]["type"] = "integer"

    lines = _diff_lines(_base_schema(), _with_change(change))
    assert any(
        "/properties/ok/type" in line and '"boolean" -> "integer"' in line for line in lines
    ), lines


def test_нормализация_выбрасывает_описания_и_сортирует_ключи() -> None:
    """INT-012: снимок детерминирован — справки и окружение его не двигают."""
    raw = {
        "info": {"title": "SVOI", "version": "1"},
        "servers": [{"url": "http://127.0.0.1:8000"}],
        "openapi": "3.1.0",
        "paths": {
            "/b": {"get": {"summary": "b", "operationId": "b_get", "x-note": {"title": "t"}}},
            "/a": {"get": {"description": "a", "example": {"token": "secret"}}},
        },
    }
    normalised = snap.normalise(raw, root=True)
    assert normalised == {
        "paths": {"/a": {"get": {}}, "/b": {"get": {"x-note": {}}}}
    }
    assert snap.render(normalised) == snap.render(snap.normalise(copy.deepcopy(raw), root=True))


def test_нормализация_оставляет_имена_полей_совпадающие_с_аннотациями() -> None:
    """INT-012: поле модели — часть контракта, даже если называется `title`.

    Аннотация pydantic `title` стоит рядом с `type` в подсхеме и выбрасывается; ключ
    с тем же именем внутри `properties` — это имя поля, и он остаётся.
    """
    raw = {
        "components": {
            "schemas": {
                "Thing": {
                    "title": "Thing",
                    "type": "object",
                    "properties": {
                        "title": {"title": "Title", "type": "string"},
                        "description": {"title": "Description", "type": "string"},
                        "summary": {"type": "string"},
                    },
                    "required": ["title"],
                }
            }
        }
    }
    normalised = snap.normalise(raw)
    props = normalised["components"]["schemas"]["Thing"]["properties"]
    assert sorted(props) == ["description", "summary", "title"]
    # Аннотация на уровне схемы и в подсхеме выброшена, тип — остался.
    assert "title" not in normalised["components"]["schemas"]["Thing"]
    assert props["title"] == {"type": "string"}
    assert normalised["components"]["schemas"]["Thing"]["required"] == ["title"]


def test_переименование_поля_title_даёт_дифф() -> None:
    """INT-012: поле `title` не растворяется в шуме — переименование даёт две строки."""
    def change(schema: dict) -> None:
        props = schema["paths"]["/tools/ping"]["get"]["responses"]["200"]["content"][
            "application/json"]["schema"]["properties"]
        props["title"] = {"title": "Title", "type": "string"}
        props["headline"] = props.pop("title")

    base = _with_change(lambda schema: schema["paths"]["/tools/ping"]["get"]["responses"]["200"][
        "content"]["application/json"]["schema"]["properties"].update(
            {"title": {"title": "Title", "type": "string"}}))
    lines = _diff_lines(base, _with_change(change))
    joined = "\n".join(lines)
    assert "/properties/title" in joined and "удалено" in joined, lines
    assert "/properties/headline" in joined and "добавлено" in joined, lines


def test_удаление_поля_title_в_реальной_схеме_даёт_дифф() -> None:
    """INT-012: на фактической схеме удаление поля `title` тоже ловится.

    Проверка на реальной схеме, а не только на синтетике: именно здесь промах
    `DROP_KEYS` по имени ключа раньше прятал удаление четырёх полей `title`.
    """
    saved = snap.load_snapshot()
    assert "/components/schemas/Problem/properties/title" in "\n".join(
        snap.flatten(saved).keys()
    ), "поле Problem.title обязано быть в снимке: иначе его удаление не даёт диффа"

    changed = copy.deepcopy(saved)
    del changed["components"]["schemas"]["Problem"]["properties"]["title"]
    lines = snap.diff_snapshots(saved, changed)
    assert any(
        "/components/schemas/Problem/properties/title" in line and "удалено" in line
        for line in lines
    ), lines


def test_сравнение_не_создаёт_и_не_переписывает_снимок(tmp_path, monkeypatch) -> None:
    """INT-012: обновление — только явным `--write`; проверка файл не трогает."""
    target = tmp_path / "openapi.json"
    monkeypatch.setattr(snap, "SNAPSHOT_PATH", target)
    monkeypatch.setattr(snap, "snapshot_data", _base_schema)

    assert snap.main([]) == 1
    assert not target.exists()

    assert snap.main(["--write"]) == 0
    written = target.read_text(encoding="utf-8")
    assert json.loads(written) == _base_schema()

    target.write_text(written.replace('"boolean"', '"string"', 1), encoding="utf-8")
    tampered = target.read_text(encoding="utf-8")
    assert snap.main([]) == 1
    assert target.read_text(encoding="utf-8") == tampered


def test_проверка_печатает_контракт_совпадает(tmp_path, monkeypatch, capsys) -> None:
    """INT-012: успешная проверка говорит «контракт совпадает» и не молчит."""
    target = tmp_path / "openapi.json"
    monkeypatch.setattr(snap, "SNAPSHOT_PATH", target)
    monkeypatch.setattr(snap, "snapshot_data", _base_schema)
    snap.write_snapshot(_base_schema(), target)

    assert snap.main([]) == 0
    assert "контракт совпадает" in capsys.readouterr().out


# Ключ считается подозрительным, только если имя свойства совпадает целиком:
# `tokens` в IntentIn — это счётчик токенов LLM, а не учётные данные.
SUSPICIOUS_KEYS = frozenset({
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "refresh_token",
    "secret",
    "token",
})
# Значения: внутренние адреса, строки подключения, bearer-заголовки и длинные hex-строки
# (ключи, хеши паролей). В схеме им взяться неоткуда.
SUSPICIOUS_VALUES = re.compile(
    r"(127\.0\.0\.1|localhost|0\.0\.0\.0|https?://|postgresql://|Bearer\s|[0-9a-f]{32,})",
    re.IGNORECASE,
)


def _leaves(value, path: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}/{index}")
    else:
        yield path, value


def test_в_снимке_нет_секретов_и_внутренних_адресов() -> None:
    """INT-012: снимок лежит в репозитории — в нём не место токенам и адресам."""
    leaves = list(_leaves(snap.load_snapshot()))
    assert leaves, "снимок пуст — это ошибка контракта, а не чистый снимок"

    secret_keys = [
        path for path, _ in leaves
        if path.rsplit("/", 1)[-1].lower() in SUSPICIOUS_KEYS
    ]
    assert not secret_keys, f"в снимке подозрительные ключи: {secret_keys[:10]}"

    bad_values = [
        (path, value) for path, value in leaves if SUSPICIOUS_VALUES.search(str(value))
    ]
    assert not bad_values, f"в снимке секреты или внутренние адреса: {bad_values[:10]}"
