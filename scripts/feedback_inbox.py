#!/usr/bin/env python3
"""Разбор выгрузки обращений в карты доски (сторона dev-среды).

  env -u PYTHONPATH .venv/bin/python scripts/feedback_inbox.py [--apply] [--board teatrsvoi]

Как устроен мост (решение владельца 02.11.2026):
  сервер не разбирает баги и пожелания — он выгружает их файлом в feedback/inbox/
  и помечает «передано в разработку». Этот скрипт запускается на машине разработчика:
  читает файлы, заводит карты в доске и переносит разобранное в feedback/processed/.

Повторный прогон безопасен: карта ищется по «Обращение №<id>» в заголовке.

  --apply  создавать карты и переносить файлы (без флага — только отчёт)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
INBOX = ROOT / "feedback" / "inbox"
DONE = ROOT / "feedback" / "processed"
HERMES = str(pathlib.Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes")

KIND_TITLE = {"bug": "Дефект", "wish": "Пожелание"}


def card_exists(board: str, feedback_id: int) -> bool:
    out = subprocess.run([HERMES, "kanban", "--board", board, "list"],
                         capture_output=True, text=True, check=False).stdout
    return f"Обращение №{feedback_id}" in out


def body_for(item: dict) -> str:
    kind = KIND_TITLE.get(item.get("kind"), "Обращение")
    context = item.get("context") or {}
    return "\n".join([
        "Что и зачем",
        f"{kind} из обратной связи в боте, обращение №{item['id']} от {item.get('created_at', '?')}.",
        "Исходный текст (дословно):",
        (item.get("text") or "").strip(),
        "",
        "Область",
        "Определяется при разборе: сначала воспроизвести, затем править минимальный участок.",
        "",
        "Критерии приёмки",
        "- Дефект воспроизведён фактом (или пожелание сформулировано проверяемо).",
        "- Исправление покрыто тестом, который падает без него.",
        "- Автору обращения отправлен ответ в боте с номером карты.",
        "",
        "Границы",
        "- Работу ведём только в dev-среде и репозитории; прод — по команде владельца.",
        f"- Контекст обращения: {json.dumps(context, ensure_ascii=False)[:400]}",
    ])


def main() -> int:
    p = argparse.ArgumentParser(description="Выгрузка обращений в карты доски")
    p.add_argument("--board", default="teatrsvoi")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    if not INBOX.is_dir():
        print("нет каталога feedback/inbox — мост ещё ничего не выгружал")
        return 0

    files = sorted(INBOX.glob("*.json"))
    if not files:
        print("новых выгрузок нет")
        return 0

    created, skipped, bad = [], [], []
    for path in files:
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            bad.append(path.name)
            continue
        for item in items:
            title = f"Обращение №{item['id']}. {KIND_TITLE.get(item.get('kind'), 'Обращение')}"
            if card_exists(args.board, int(item["id"])):
                skipped.append(int(item["id"]))
                continue
            if not args.apply:
                print(f"будет карта: {title}")
                continue
            packet = subprocess.run(
                [HERMES, "kanban", "--board", args.board, "create", title,
                 "--body", body_for(item), "--assignee", "worker-feature", "--priority", "4"],
                capture_output=True, text=True, check=False)
            if packet.returncode == 0:
                created.append((int(item["id"]), packet.stdout.strip()[-30:]))
            else:
                bad.append(f"{item['id']}: {packet.stderr.strip()[:120]}")

    if args.apply:
        DONE.mkdir(parents=True, exist_ok=True)
        for path in files:
            shutil.move(str(path), str(DONE / path.name))

    print(f"создано карт: {len(created)}; уже были: {len(skipped)}; ошибок: {len(bad)}")
    for fid, out in created:
        print(f"  №{fid} -> {out}")
    for item in bad:
        print(f"  ошибка: {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
