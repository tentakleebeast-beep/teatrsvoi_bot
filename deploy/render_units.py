#!/usr/bin/env python3
"""
Рендер systemd-юнитов из deploy/systemd в каталог системы (docs/infrastructure/75).

Зачем: юниты в репозитории написаны под умолчания (`/opt/svoi`, пользователь `svoi`).
Если сервер ставится в другой каталог или под другого пользователя, правка
unit-файлов руками расходится с git. Поэтому источник истины — файлы в
`deploy/systemd/`, а этот скрипт подставляет адрес и пользователя.

    python3 deploy/render_units.py --root /opt/svoi --user svoi --dry-run
    sudo python3 deploy/render_units.py --root /opt/svoi --user svoi
    python3 deploy/render_units.py --check --root /opt/svoi --user svoi   # только сверить

Подставляется:
    /opt/svoi        -> --root
    User=svoi        -> --user
    Group=svoi       -> --group (по умолчанию равен --user)
    EnvironmentFile=/opt/svoi/.env — через подстановку корня, отдельной правки нет.

Проверка юнитов-источников остаётся за scripts/check_services.py: он читает
deploy/systemd и не знает про рендер.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

SOURCE_ROOT = "/opt/svoi"
SOURCE_USER = "svoi"
DEFAULT_SOURCE = pathlib.Path(__file__).resolve().parent / "systemd"
DEFAULT_TARGET = pathlib.Path("/etc/systemd/system")


def render_text(text: str, root: str, user: str, group: str | None = None) -> str:
    """Подставляет адрес и пользователя в текст юнита. Чистая функция — под тестом."""
    group = group or user
    lines = []
    for line in text.splitlines():
        if line.startswith("User="):
            line = f"User={user}"
        elif line.startswith("Group="):
            line = f"Group={group}"
        line = line.replace(f"User={SOURCE_USER}", f"User={user}")
        line = line.replace(f"Group={SOURCE_USER}", f"Group={group}")
        line = line.replace(SOURCE_ROOT, root.rstrip("/"))
        lines.append(line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def render_dir(source: pathlib.Path, target: pathlib.Path, root: str, user: str,
               group: str | None = None, dry_run: bool = False, check: bool = False) -> list[str]:
    """Возвращает список сообщений; при `check` падает, если файл на месте отличается."""
    messages: list[str] = []
    problems: list[str] = []
    units = sorted(p for p in source.glob("*") if p.suffix in {".service", ".timer"})
    if not units:
        raise SystemExit(f"в {source} нет юнитов .service/.timer")

    if not dry_run and not check:
        target.mkdir(parents=True, exist_ok=True)

    for unit in units:
        rendered = render_text(unit.read_text(encoding="utf-8"), root, user, group)
        destination = target / unit.name
        if check:
            try:
                current = destination.read_text(encoding="utf-8")
            except OSError:
                problems.append(f"{unit.name}: не установлен в {target}")
                continue
            if current != rendered:
                problems.append(f"{unit.name}: установленный файл отличается от рендера")
            else:
                messages.append(f"{unit.name}: совпадает")
            continue
        if dry_run:
            messages.append(f"{destination} (будет записан)")
            continue
        destination.write_text(rendered, encoding="utf-8")
        destination.chmod(0o644)
        messages.append(f"записан {destination}")

    if problems:
        raise SystemExit("расхождения:\n" + "\n".join(f"  {p}" for p in problems))
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Рендер systemd-юнитов SVOI")
    parser.add_argument("--source", type=pathlib.Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=pathlib.Path, default=DEFAULT_TARGET)
    parser.add_argument("--root", default=SOURCE_ROOT, help="каталог проекта на сервере")
    parser.add_argument("--user", default=SOURCE_USER, help="рабочий пользователь сервисов")
    parser.add_argument("--group", default=None, help="группа (по умолчанию = --user)")
    parser.add_argument("--dry-run", action="store_true", help="показать, что будет записано")
    parser.add_argument("--check", action="store_true", help="сверить с уже установленными")
    args = parser.parse_args(argv)

    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", args.root):
        parser.error("--root должен быть абсолютным путём без пробелов")

    for message in render_dir(args.source, args.target, args.root, args.user,
                              args.group, args.dry_run, args.check):
        print(f"  {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
