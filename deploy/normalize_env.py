#!/usr/bin/env python3
"""Убирает из .env инлайн-комментарии, которые ломают systemd.

`.env.example` держит пояснения прямо в строке:

    INVITE_TTL_DAYS=7                # срок жизни кода регистрации

python-dotenv читает такие строки правильно, а systemd `EnvironmentFile` — буквально:
переменная получает значение `7                # срок жизни кода регистрации`, и
`int()` в коде падает с ValueError. Поэтому при генерации серверного `.env` комментарии
переносятся в отдельную строку.

    python3 deploy/normalize_env.py /opt/svoi/.env
"""

from __future__ import annotations

import sys
from pathlib import Path


def strip_inline_comment(value: str) -> tuple[str, str]:
    """Возвращает (значение, комментарий). Кавычки сохраняют `#` внутри значения."""
    if not value:
        return "", ""
    quote = value[0] if value[0] in "\"'" else ""
    if quote:
        end = value.find(quote, 1)
        if end != -1:
            rest = value[end + 1:].strip()
            return value[: end + 1], rest.lstrip("#").strip() if rest.startswith("#") else ""
    for marker in (" #", "\t#"):
        pos = value.find(marker)
        if pos != -1:
            return value[:pos].rstrip(), value[pos:].lstrip(" \t#").strip()
    return value.rstrip(), ""


def needs_quotes(value: str) -> bool:
    return any(ch in value for ch in " \t#'\"") and not value.startswith(("'", '"'))


def normalize(path: Path) -> int:
    source = path.read_text(encoding="utf-8").splitlines()
    result: list[str] = []
    moved = 0
    for line in source:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            result.append(line.rstrip())
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value, comment = strip_inline_comment(value.strip())
        if comment:
            moved += 1
        if needs_quotes(value):
            value = '"' + value.replace('"', '\\"') + '"'
        result.append(f"{key}={value}")
        if comment:
            result.append(f"# {comment}")
    path.write_text("\n".join(result) + "\n", encoding="utf-8")
    return moved


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("использование: normalize_env.py <путь к .env>")
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"нет файла: {path}")
        return 2
    print(f"перенесено комментариев: {normalize(path)} — {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
