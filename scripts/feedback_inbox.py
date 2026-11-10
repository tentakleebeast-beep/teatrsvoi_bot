#!/usr/bin/env python3
"""Разбор выгрузки обращений в карты доски (сторона dev-среды).

  env -u PYTHONPATH .venv/bin/python scripts/feedback_inbox.py [--apply] [--board teatrsvoi]

Как устроен мост (решение владельца 02.11.2026):
  сервер не разбирает баги и пожелания — он выгружает их файлом в feedback/inbox/
  и помечает «передано в разработку». Этот скрипт запускается на машине разработчика:
  читает файлы, заводит карты в доске и переносит разобранное в feedback/processed/.

Скриншоты обращения (Э6, §4.6): байты лежат на проде, поэтому перед созданием карты
скрипт качает каждый `attachments[i]` маршрутом
`GET /api/tools/feedback/<id>/attachment/<file_id>` в `feedback/inbox/<id>/` и
прикрепляет файл к карте (`hermes kanban attach`). Имя на диске — `file_id` плюс
расширение из имени: «IMG_1.png» из двух обращений не перетрёт друг друга. В тело
карты идёт строка «Скриншот: <имя>»; не скачался — «Скриншот <имя> не скачался:
<код>», и карта всё равно создаётся: текст обращения важнее одного файла.

Повторный прогон безопасен: карта ищется по «Обращение №<id>» в заголовке, а
вложение скачивается только вместе с новой картой.

  --apply  создавать карты и переносить файлы (без флага — только отчёт)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

import feedback_sync  # тот же мост: адрес прода, токен и разбор .env — один код

ROOT = pathlib.Path(__file__).resolve().parents[1]
INBOX = ROOT / "feedback" / "inbox"
DONE = ROOT / "feedback" / "processed"
HERMES = str(pathlib.Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes")

KIND_TITLE = {"bug": "Дефект", "wish": "Пожелание"}
#: Сколько раз повторяем скачивание: сеть до прода идёт через VPN и рвётся.
DOWNLOAD_RETRIES = 3
DOWNLOAD_TIMEOUT = 20
#: Маршрут байтов вложения. Записан одним литералом: по нему инвентарь действий
#: (`scripts/audit_actions.py`) видит, что маршрут не сирота.
SHOT_PATH = "/tools/feedback/{feedback_id}/attachment/{file_id}"


def card_exists(board: str, feedback_id: int) -> bool:
    out = subprocess.run([HERMES, "kanban", "--board", board, "list"],
                         capture_output=True, text=True, check=False).stdout
    return f"Обращение №{feedback_id}" in out


def _safe_suffix(name: str) -> str:
    """Расширение из имени файла: только буквы, цифры и точка, не длиннее 10 знаков."""
    suffix = pathlib.Path(name or "").suffix
    return suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,9}", suffix or "") else ""


def fetch_shot(url: str, token: str, chat: str) -> tuple[bytes | None, str]:
    """Байты скриншота: `(данные, "")` или `(None, причина)`.

    Отказ прода (404/403) не повторяем — он не изменится от паузы; сетевую ошибку
    повторяем, потому что она лечится. Причиной наружу идёт код ответа: он и попадает
    в тело карты («не скачался: 404»).
    """
    last = "нет сети"
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                                  "X-Actor-Chat-Id": chat})
        try:
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                return resp.read(), ""
        except urllib.error.HTTPError as exc:
            return None, str(exc.code)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = str(exc)
            if attempt < DOWNLOAD_RETRIES:
                time.sleep(attempt)
    return None, last


def save_shot(item: dict, shot: dict, inbox: pathlib.Path,
              base: str, token: str, chat: str) -> tuple[pathlib.Path | None, str]:
    """Скачать один скриншот в `feedback/inbox/<id>/<file_id><ext>`.

    Возвращает `(путь, "")` или `(None, причина)`. Имя не берётся из обращения: два
    файла «IMG_1.png» из разных обращений лежат в разных папках, но и в одной папке
    одинаковые имена затёрли бы друг друга — `file_id` уникален всегда.
    """
    url = "{}/api{}".format(base.rstrip("/"), SHOT_PATH.format(
        feedback_id=item["id"], file_id=shot["id"]))
    body, error = fetch_shot(url, token, chat)
    if body is None:
        return None, error
    folder = inbox / str(item["id"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{shot['id']}{_safe_suffix(shot.get('name') or '')}"
    target.write_bytes(body)
    return target, ""


def collect_shots(item: dict, inbox: pathlib.Path,
                  base: str, token: str, chat: str) -> tuple[list[pathlib.Path], list[str]]:
    """Все скриншоты обращения: `(файлы на диске, строки тела карты)`."""
    files: list[pathlib.Path] = []
    lines: list[str] = []
    for shot in item.get("attachments") or []:
        name = shot.get("name") or f"файл {shot.get('id')}"
        if not token or not base:
            lines.append(f"Скриншот {name} не скачался: нет доступа к API прода")
            continue
        path, error = save_shot(item, shot, inbox, base, token, chat)
        if path is None:
            lines.append(f"Скриншот {name} не скачался: {error}")
        else:
            files.append(path)
            lines.append(f"Скриншот: {name}")
    return files, lines


def attach_files(board: str, task_id: str, files: list[pathlib.Path]) -> list[str]:
    """Прикрепить скачанные файлы к карте. Возвращает ошибки (пусто — всё прикрепилось)."""
    errors = []
    for path in files:
        packet = subprocess.run(
            [HERMES, "kanban", "--board", board, "attach", task_id, str(path)],
            capture_output=True, text=True, check=False)
        if packet.returncode != 0:
            errors.append(f"{path.name}: {packet.stderr.strip()[:120]}")
    return errors


def created_id(text: str) -> str:
    """ID созданной карты: из `--json` ответа, иначе — из строки вывода."""
    text = (text or "").strip()
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("id", "task_id", "task"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict) and isinstance(value.get("id"), str):
                return value["id"]
    found = re.search(r"t_[0-9a-f]{8}", text)
    return found.group(0) if found else ""


def body_for(item: dict, shot_lines: list[str] | None = None) -> str:
    kind = KIND_TITLE.get(item.get("kind"), "Обращение")
    context = item.get("context") or {}
    return "\n".join([
        "Что и зачем",
        f"{kind} из обратной связи в боте, обращение №{item['id']} от {item.get('created_at', '?')}.",
        "Исходный текст (дословно):",
        (item.get("text") or "").strip(),
        *list(shot_lines or []),
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

    base, token, chat = feedback_sync.settings(feedback_sync.load_env())
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
            shots, shot_lines = collect_shots(item, INBOX, base, token, chat)
            packet = subprocess.run(
                [HERMES, "kanban", "--board", args.board, "create", title,
                 "--body", body_for(item, shot_lines), "--assignee", "worker-feature",
                 "--priority", "4", "--json"],
                capture_output=True, text=True, check=False)
            if packet.returncode != 0:
                bad.append(f"{item['id']}: {packet.stderr.strip()[:120]}")
                continue
            task_id = created_id(packet.stdout)
            attached = 0
            if task_id and shots:
                errors = attach_files(args.board, task_id, shots)
                attached = len(shots) - len(errors)
                bad.extend(f"{item['id']}: вложение {error}" for error in errors)
            created.append((int(item["id"]), f"{task_id or '?'} +{attached} файл(ов)"))

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
