"""
Роли проекта: admin, org, troupe, master. Роль student удалена 26.10.2026 (docs/architecture/02).

Тест держит инвариант: если кто-то вернёт студента в код или манифесты
без миграции и решения — упадёт здесь. Роль `master` («педагог») добавлена картой Э1
(db/init/061, docs/architecture/140): у неё есть манифест `roles/master.md`, иначе
промпт педагога собирался бы без слоя роли.

Обоснование: FR-A1 (роли и доступ), docs/architecture/02-access-control.md,
docs/architecture/140-master-role.md.
"""

import pathlib
import sys

import gateway_modules

ROOT = pathlib.Path(__file__).resolve().parents[2]
ALLOWED = {"admin", "org", "troupe", "master"}


def gateway():
    import importlib.util
    return gateway_modules.load("texts")


def test_в_gateway_только_четыре_роли():
    assert set(gateway().ROLE_TITLES) == ALLOWED


def test_манифесты_ролей_совпадают():
    roles_dir = ROOT / "bot" / "manifests" / "roles"
    assert {p.stem for p in roles_dir.glob("*.md")} == ALLOWED


def test_нет_упоминания_студента_в_манифестах():
    for path in (ROOT / "bot" / "manifests").rglob("*.md"):
        text = path.read_text(encoding="utf-8").lower()
        assert "student" not in text, f"student остался в {path.name}"
        assert "студент курсов" not in text, f"«студент курсов» остался в {path.name}"


def test_compose_manifest_знает_четыре_роли():
    sys.path.insert(0, str(ROOT / "scripts"))
    import compose_manifest

    parser_choices = None
    for action in compose_manifest.build_parser()._actions:
        if action.dest == "role":
            parser_choices = set(action.choices)
    assert parser_choices == ALLOWED


def test_манифест_педагога_описывает_запреты():
    """Слой роли мастера говорит, чего у него нет: деньги, люди, панель (140 §3.2)."""
    text = (ROOT / "bot" / "manifests" / "roles" / "master.md").read_text(encoding="utf-8")
    assert "педагог" in text.lower()
    for forbidden in ("Бюджет", "блокир", "коды приглашений"):
        assert forbidden.lower() in text.lower(), f"нет запрета про «{forbidden}»"


def test_инвайт_и_роль_не_предлагают_студента():
    src = gateway_modules.source()
    assert '"admin", "org", "troupe", "student"' not in src
