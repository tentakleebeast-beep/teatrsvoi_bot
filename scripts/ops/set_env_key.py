#!/usr/bin/env python3
"""Записать одно значение в .env, не печатая его.

  set_env_key.py --env /opt/svoi/.env --name LLM_API_KEY --value-file /tmp/k

Зачем отдельный скрипт: править .env на сервере приходится через SSH из workflow, а
значение секрета не должно попадать ни в argv команды, ни в лог. Здесь значение
приходит файлом, проверяется на ASCII (заголовок Authorization в httpx не принимает
не-ASCII: 01.11.2026 в проде в LLM_API_KEY лежал комментарий и каждый запрос к
модели падал с UnicodeEncodeError), и только потом попадает в файл.

Файл переписывается атомарно: соседний временный файл + os.replace, права 600.
Строка заменяется целиком; если её не было — добавляется в конец.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile


def main() -> int:
    p = argparse.ArgumentParser(description="Записать значение в .env без вывода значения")
    p.add_argument("--env", required=True, help="путь к .env")
    p.add_argument("--name", required=True, help="имя переменной, например LLM_API_KEY")
    p.add_argument("--value-file", required=True, help="файл, где лежит значение одной строкой")
    args = p.parse_args()

    value = pathlib.Path(args.value_file).read_text(encoding="utf-8").strip()
    if not value or "\n" in value:
        print("значение пустое или содержит перевод строки — отказ", file=sys.stderr)
        return 1
    bad = [i for i, ch in enumerate(value) if ord(ch) > 127]
    if bad:
        print(f"значение содержит не-ASCII на позициях {bad[:8]} — отказ", file=sys.stderr)
        return 1

    path = pathlib.Path(args.env)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = args.name + "="
    lines = [ln for ln in lines if not ln.startswith(prefix)]
    lines.append(prefix + value)

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    print(f"{args.name}: записано, длина {len(value)}, не-ASCII 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
