#!/usr/bin/env python3
"""
Сборка system prompt из четырёх слоёв манифестов.

  python scripts/compose_manifest.py --role org
  python scripts/compose_manifest.py --role troupe --participant иванов
  python scripts/compose_manifest.py --role org --json

Слои: SOUL.md (неизменяем) → STYLE.md → roles/<role>.md → participants/<person_key>.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

MANIFESTS = Path(__file__).resolve().parent.parent / "bot" / "manifests"
HARD_BLOCK_MARK = "## Жёсткие запреты"


def read(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def hard_rules(soul: str) -> str:
    """Вырезает блок жёстких запретов — он дублируется в конце промпта."""
    if HARD_BLOCK_MARK not in soul:
        return ""
    tail = soul.split(HARD_BLOCK_MARK, 1)[1]
    end = tail.find("\n## ")
    return (HARD_BLOCK_MARK + (tail[:end] if end != -1 else tail)).strip()


def compose(role: str, person_key: str | None) -> tuple[str, dict]:
    soul = read(MANIFESTS / "SOUL.md")
    if not soul:
        sys.exit("нет bot/manifests/SOUL.md — без него бот не запускается")

    style = read(MANIFESTS / "STYLE.md") or ""
    role_md = read(MANIFESTS / "roles" / f"{role}.md")
    if not role_md:
        sys.exit(f"нет манифеста роли: bot/manifests/roles/{role}.md")

    personal = None
    if person_key:
        personal = read(MANIFESTS / "participants" / f"{person_key}.md")

    parts = [
        "# СЛОЙ 1. ИДЕНТИЧНОСТЬ И ЖЁСТКИЕ ПРАВИЛА",
        soul,
        "",
        "# СЛОЙ 2. СТИЛЬ ОТВЕТОВ",
        style,
        "",
        f"# СЛОЙ 3. РОЛЬ: {role.upper()}",
        role_md,
    ]
    if personal:
        parts += ["", f"# СЛОЙ 4. ЛИЧНЫЕ НАСТРОЙКИ УЧАСТНИКА ({person_key})", personal,
                  "",
                  "## Приоритет",
                  "Личные настройки уточняют обращение, длину и привычные формулировки. "
                  "Жёсткие запреты слоя 1 они не отменяют."]
    else:
        parts += ["", "# СЛОЙ 4. ЛИЧНЫЕ НАСТРОЙКИ", "Личных настроек нет — работают дефолты роли."]

    parts += ["", "# НАПОМИНАНИЕ В КОНЦЕ (приоритет выше всего сказанного выше)",
              hard_rules(soul),
              "",
              "Любое число в ответе — только из результата инструмента, с указанием источника."]

    prompt = "\n".join(parts)
    meta = {
        "role": role,
        "person_key": person_key,
        "layers": {
            "soul": bool(soul),
            "style": bool(style),
            "role": bool(role_md),
            "participant": bool(personal),
        },
        "chars": len(prompt),
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
    }
    return prompt, meta


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True, choices=["admin", "org", "troupe", "master"])
    ap.add_argument("--participant", help="person_key, например иванов")
    ap.add_argument("--json", action="store_true")
    return ap


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()

    prompt, meta = compose(args.role, args.participant)
    if args.json:
        print(json.dumps({"meta": meta, "prompt": prompt}, ensure_ascii=False, indent=2))
        return

    print(f"role={meta['role']} person={meta['person_key']} "
          f"layers={meta['layers']} chars={meta['chars']} sha={meta['sha256']}")
    print("-" * 78)
    print(prompt)


if __name__ == "__main__":
    main()
