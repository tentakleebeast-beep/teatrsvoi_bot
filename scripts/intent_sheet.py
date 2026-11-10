#!/usr/bin/env python3
"""Лист «Чего не умеем»: строки с прода → карточки → решение владельца (V-035).

Решение Виктора по V-035 (`docs/reports/74-questions.md`): строки листа тянутся
с прода на локальную машину так же, как обратная связь, по каждой заводится карточка
на спецификацию, и работы не начинаются, пока владелец не сказал «да». Решение
фиксируется записью `V-NNN` в файле вопросов и состоянием карточки — таблицы
`intent_decision` в Mini App нет. Маршрут чтения `GET /tools/intent/sheet` живёт
на проде (карта-сестра); здесь он только вызывается.

  env -u PYTHONPATH .venv/bin/python scripts/intent_sheet.py pull
  env -u PYTHONPATH .venv/bin/python scripts/intent_sheet.py cards --apply
  env -u PYTHONPATH .venv/bin/python scripts/intent_sheet.py status
  env -u PYTHONPATH .venv/bin/python scripts/intent_sheet.py decide --row 1a2b3c4d \
      --decision yes

Правила, которые держит скрипт:
  * снимок прода пишется атомарно (`.state/intent_sheet.json`, права 0600): внутри имена
    людей, а половина файла хуже отсутствия файла;
  * `row_id` считается из `phrase` как есть — фразу уже привёл прод (`lower`, схлопнутые
    пробелы), вторая нормализация развела бы локальный ключ с прод-ключом;
  * карточка не заводится дважды: маркер `строка <row_id>` ищется в `title`/`body`
    карт доски (`hermes kanban list --json` не отдаёт `idempotency_key`, поэтому ключ —
    только второй замок ядра);
  * работа запускается одним путём — `decide --decision yes` и только у `blocked`
    карточки: ни `assign`, ни `unblock` при другом состоянии не вызываются;
  * в прод не пишем: только `select` на стороне маршрута, статус строки в `intent_log`
    не меняется, автору не пишется (лист — не обращение).

Коды выхода: 0 — сработало; 1 — отказ по правилу (устаревший снимок, карточка не
`blocked`, вопрос не найден, решение уже записано); 2 — нет токена или `ADMIN_CHAT_ID`;
3 — прод или сеть отказали; 4 — строк нет (тишина, не ошибка).

Тест: tests/unit/test_intent_sheet.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from _questions import QUESTIONS, ROW_MARKER, find_question, questions, resolve_question, row_marker
from feedback_sync import load_env, request, settings

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".state" / "intent_sheet.json"
# `hermes` стоит в PATH не всегда: у планировщика окружение короче, чем у терминала.
HERMES_FALLBACK = Path.home() / ".hermes" / "hermes-agent" / ".hermes" / "bin" / "hermes"

DEFAULT_BOARD = "teatrsvoi"
ASSIGNEE = "specifier"
WORKER = "worker-feature"
RESOLVED = ("решено", "решён")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
TITLE_LIMIT = 60


class KanbanError(RuntimeError):
    """Доска недоступна: нет CLI, отказ команды или не JSON в ответе."""


# --- снимок ---------------------------------------------------------------------

def row_id(phrase: str) -> str:
    """Ключ строки листа: 8 hex-знаков от `phrase` как есть, без нормализации."""
    return hashlib.sha256(phrase.encode("utf-8")).hexdigest()[:8]


def write_snapshot(snapshot: dict) -> None:
    """Пишет снимок атомарно: временный файл рядом + `os.replace`, права 0600."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle, path = tempfile.mkstemp(dir=STATE_FILE.parent, prefix=".intent_sheet-",
                                    suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(snapshot, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.chmod(path, 0o600)
        os.replace(path, STATE_FILE)
    except BaseException:
        if os.path.exists(path):
            os.unlink(path)
        raise


def read_snapshot() -> dict | None:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def fetched_at(snapshot: dict) -> datetime:
    """Момент выгрузки: формат `time.strftime("%Y-%m-%dT%H:%M:%S%z")` из `pull`."""
    value = str(snapshot.get("fetched_at") or "")
    moment = datetime.strptime(value, TIME_FORMAT)
    if moment.tzinfo is None:
        raise ValueError("в снимке нет часового пояса")
    return moment


def read_questions() -> str:
    try:
        return QUESTIONS.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_questions(text: str) -> None:
    QUESTIONS.write_text(text, encoding="utf-8")


def expired_rows(snapshot: dict, text: str) -> list[tuple[str, str]]:
    """Открытые вопросы, чьей строки в снимке уже нет (журнал почистил её по retention)."""
    present = {row_id(str(row.get("phrase") or "")) for row in snapshot.get("rows") or []}
    gone = []
    for question in questions(text):
        if question.status in RESOLVED:
            continue
        match = ROW_MARKER.search(question.source)
        if match and match.group(1) not in present:
            gone.append((match.group(1), question.id))
    return gone


# --- доска ------------------------------------------------------------------------

def hermes_path() -> Path | None:
    found = shutil.which("hermes")
    if found:
        return Path(found)
    return HERMES_FALLBACK if HERMES_FALLBACK.is_file() else None


def kanban(board: str, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Вызов `hermes kanban --board <board> <args>` без shell и без `HERMES_HOME`.

    `HERMES_HOME` убираем: у воркера в нём нет досок, и команда ищет доску в чужом
    профиле (образец — `scripts/cron/arch_drift_weekly.py`).
    """
    hermes = hermes_path()
    if hermes is None:
        raise KanbanError(f"нет hermes CLI: ни в PATH, ни {HERMES_FALLBACK}")
    command = [str(hermes), "kanban", "--board", board, *args]
    env = {key: value for key, value in os.environ.items() if key != "HERMES_HOME"}
    return subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout)


def list_cards(board: str) -> list[dict]:
    """Карты доски без архивных; `--archived` не передаём намеренно."""
    result = kanban(board, ["list", "--json"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-300:]
        raise KanbanError(f"hermes kanban list отказал (код {result.returncode}): {detail}")
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise KanbanError(f"hermes kanban list вернул не JSON: {exc}") from exc
    if not isinstance(data, list):
        raise KanbanError("hermes kanban list вернул не список карт")
    return [card for card in data if isinstance(card, dict)]


def find_card(cards: list[dict], rid: str) -> dict | None:
    pattern = row_marker(rid)
    for card in cards:
        haystack = f"{card.get('title') or ''}\n{card.get('body') or ''}"
        if pattern.search(haystack):
            return card
    return None


def card_title(rid: str, phrase: str) -> str:
    short = phrase if len(phrase) <= TITLE_LIMIT else phrase[:TITLE_LIMIT].rstrip() + "…"
    return f"Строка листа «Чего не умеем» {rid}: «{short}»"


def card_body(rid: str, row: dict) -> str:
    """Тело карты: первая строка — маркер, по нему ищут `cards`, `status` и `decide`."""
    return (
        f"Источник: лист «Чего не умеем», строка {rid}; V-035\n"
        "\n"
        f"Строка листа (дословно): «{row.get('phrase') or ''}»\n"
        f"Факты: {row.get('times') or 0} раз, {row.get('people') or 0} человек "
        f"({row.get('who') or 'участник'}), первый {row.get('first_seen') or '—'}, "
        f"последний {row.get('last_seen') or '—'}.\n"
        f"Последний ответ бота: «{row.get('last_answer') or ''}». "
        f"Потрачено {row.get('tokens') or 0} токенов.\n"
        "\n"
        "Зачем: человек спросил то, чего бот не умеет; решение о работе принимает владелец.\n"
        "\n"
        "Область: (заполняет specifier точными файлами)\n"
        "Критерии приёмки: (заполняет specifier)\n"
        "Границы: без согласования Виктора работа не начинается; код — не в этой карте.\n"
    )


def create_card(board: str, title: str, body: str, rid: str) -> tuple[int, str]:
    handle, path = tempfile.mkstemp(prefix="intent-card-", suffix=".md")
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        file.write(body)
    try:
        result = kanban(board, ["create", title, "--body-file", path,
                                "--assignee", ASSIGNEE, "--priority", "4", "--triage",
                                "--idempotency-key", f"intent-sheet:{rid}", "--json"])
    finally:
        if os.path.exists(path):
            os.unlink(path)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-500:]
        return 1, f"карта не заведена (код {result.returncode}): {detail}"
    task_id = ""
    try:
        task_id = str(json.loads(result.stdout).get("id") or "")
    except (ValueError, AttributeError):
        pass
    return 0, task_id


# --- команды ----------------------------------------------------------------------

def cmd_pull(env: dict[str, str], args) -> int:
    base, token, chat = settings(env)
    if not token or not chat:
        print("нет TOOL_API_TOKEN или ADMIN_CHAT_ID в .env", file=sys.stderr)
        return 2

    url = f"{base}/api/tools/intent/sheet?days={args.days}&limit={args.limit}"
    try:
        data = request(url, token, chat)
    except SystemExit as exc:
        text = exc.code if isinstance(exc.code, str) else str(exc)
        print(text, file=sys.stderr)
        return 3

    rows = data.get("rows") or []
    snapshot = {"fetched_at": time.strftime(TIME_FORMAT), "days": args.days,
                "count": len(rows), "rows": rows}
    write_snapshot(snapshot)

    if args.json:
        print(json.dumps({"ok": True, "count": len(rows), "days": args.days,
                          "rows": rows}, ensure_ascii=False, indent=2))
        return 0 if rows else 4
    if not rows:
        print("строк в выгрузке: 0")
        return 4
    print(f"строк в выгрузке: {len(rows)}")
    for row in rows:
        print(f"  {row_id(str(row.get('phrase') or ''))} | {row.get('times') or 0} раз | "
              f"{row.get('who') or 'участник'} | «{row.get('phrase') or ''}»")
    return 0


def cmd_cards(env: dict[str, str], args) -> int:
    snapshot = read_snapshot()
    if snapshot is None:
        print("снимка нет: сначала `pull`", file=sys.stderr)
        return 1
    try:
        moment = fetched_at(snapshot)
    except ValueError:
        print("снимок битый: сначала `pull`", file=sys.stderr)
        return 1
    age_hours = (datetime.now(moment.tzinfo) - moment).total_seconds() / 3600
    if age_hours > args.max_age_hours:
        print("снимок устарел, сначала `pull`", file=sys.stderr)
        return 1

    text = read_questions()
    rows = snapshot.get("rows") or []
    cards: list[dict] = []
    if args.apply:
        # Без `--apply` доску не спрашиваем: сухой прогон не зовёт `hermes` ни разу,
        # поэтому «уже были» в нём всегда 0 — это показ «что будет заведено», а не отчёт.
        try:
            cards = list_cards(args.board)
        except KanbanError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    created = existing = closed = over_limit = 0
    new_cards: list[dict] = []
    for row in rows:
        phrase = str(row.get("phrase") or "")
        if not phrase:
            continue
        rid = row_id(phrase)
        question = find_question(text, rid)
        if question is not None and question.status in RESOLVED:
            closed += 1
            continue
        if args.apply and find_card(cards, rid):
            existing += 1
            continue
        if created >= args.max_new:
            over_limit += 1
            continue
        title = card_title(rid, phrase)
        body = card_body(rid, row)
        if not args.apply:
            print(f"будет карта: {title}")
            created += 1
            continue
        code, detail = create_card(args.board, title, body, rid)
        if code != 0:
            print(f"карточка по строке {rid} не заведена: {detail}", file=sys.stderr)
            return 1
        cards.append({"id": detail, "title": title, "body": body, "status": "triage"})
        new_cards.append({"row_id": rid, "task_id": detail})
        created += 1

    if args.json:
        print(json.dumps({"ok": True, "dry_run": not args.apply, "created": created,
                          "existing": existing, "closed": closed,
                          "over_limit": over_limit, "cards": new_cards},
                         ensure_ascii=False, indent=2))
    elif args.apply:
        print(f"карт создано {created}; уже были {existing}; закрыто решением {closed}; "
              f"осталось за лимитом {over_limit}")
    else:
        print(f"карт будет {created}; уже были {existing}; закрыто решением {closed}; "
              f"осталось за лимитом {over_limit}")
    return 0


def cmd_status(env: dict[str, str], args) -> int:
    snapshot = read_snapshot()
    if snapshot is None:
        print("снимка нет: сначала `pull`", file=sys.stderr)
        return 1
    try:
        moment = fetched_at(snapshot)
    except ValueError:
        print("снимок битый: сначала `pull`", file=sys.stderr)
        return 1
    try:
        cards = list_cards(args.board)
    except KanbanError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    text = read_questions()
    rows = snapshot.get("rows") or []
    closed = in_work = waiting = new = 0
    for row in rows:
        rid = row_id(str(row.get("phrase") or ""))
        question = find_question(text, rid)
        if question is not None and question.status in RESOLVED:
            closed += 1
            continue
        card = find_card(cards, rid)
        if card is None:
            new += 1
        elif card.get("status") == "blocked":
            waiting += 1
        else:
            in_work += 1

    gone = expired_rows(snapshot, text)
    if args.json:
        print(json.dumps({"ok": True, "rows": len(rows),
                          "fetched_at": snapshot.get("fetched_at"), "closed": closed,
                          "in_work": in_work, "waiting": waiting, "new": new,
                          "expired": [{"row_id": rid, "question_id": qid}
                                      for rid, qid in gone]},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"строк в выгрузке: {len(rows)} ({moment.strftime('%d.%m %H:%M')})")
    print(f"  закрыто решением: {closed}")
    print(f"  в работе (карточка есть, решения нет): {in_work}")
    print(f"  ждут согласования: {waiting}")
    print(f"  новых: {new}")
    for rid, qid in gone:
        print(f"источник истёк: {rid} ({qid})")
    return 0


def cmd_decide(env: dict[str, str], args) -> int:
    note = (args.note or "").strip()
    if args.decision in ("no", "later") and not note:
        print("для «нет» и «позже» нужна причина: --note", file=sys.stderr)
        return 1

    text = read_questions()
    question = find_question(text, args.row)
    if question is None:
        print("вопроса по строке нет: сначала спецификация", file=sys.stderr)
        return 1
    if question.status in RESOLVED:
        print("решение уже записано", file=sys.stderr)
        return 1
    try:
        cards = list_cards(args.board)
    except KanbanError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    card = find_card(cards, args.row)
    if card is None:
        print(f"карточки по строке {args.row} нет: сначала `cards --apply`",
              file=sys.stderr)
        return 1
    if card.get("status") != "blocked":
        print("спецификация ещё не готова: карточка не ждёт согласования",
              file=sys.stderr)
        return 1

    task = str(card.get("id") or "")
    today = datetime.now().strftime("%d.%m.%Y")
    if args.decision == "yes":
        answer = f"{today}: „да“ — работа в карте {task}"
        if note:
            answer += f". {note}"
        updated, question_id = resolve_question(text, args.row, "решено", answer)
        write_questions(updated)
        steps = [["assign", task, WORKER], ["unblock", task]]
    elif args.decision == "no":
        answer = f"{today}: „нет“ — {note}"
        updated, question_id = resolve_question(text, args.row, "решено", answer)
        write_questions(updated)
        steps = [["comment", task, f"отклонено владельцем ({question_id}): {note}"],
                 ["complete", task, "--result", f"отклонено владельцем ({question_id})"]]
    else:
        answer = "" if question.answer in ("", "—") else f"{question.answer} "
        answer += f"отложено {today}: {note}"
        updated, question_id = resolve_question(text, args.row, question.status, answer)
        write_questions(updated)
        steps = [["comment", task, f"отложено: {note}"]]

    for step in steps:
        result = kanban(args.board, step)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-300:]
            print(f"решение записано, но `{step[0]}` отказал "
                  f"(код {result.returncode}): {detail}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps({"ok": True, "row_id": args.row, "question_id": question_id,
                          "decision": args.decision, "card": task,
                          "steps": [step[0] for step in steps]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"{question_id}: {args.decision} (карта {task})")
    return 0


# --- разбор аргументов -------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Лист «Чего не умеем»: строки, карточки, решения")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pull = sub.add_parser("pull", help="выгрузить строки с прода в .state/intent_sheet.json")
    pull.add_argument("--days", type=int, default=90)
    pull.add_argument("--limit", type=int, default=200)
    pull.add_argument("--json", action="store_true", help="машинный вывод")
    pull.set_defaults(func=cmd_pull)

    cards = sub.add_parser("cards", help="завести карточки на спецификацию")
    cards.add_argument("--apply", action="store_true", help="создавать карточки (иначе показать)")
    cards.add_argument("--max-new", type=int, default=5, dest="max_new",
                       help="не больше стольких карточек за прогон")
    cards.add_argument("--max-age-hours", type=float, default=24.0, dest="max_age_hours",
                       help="снимок старше этого — отказ, сначала pull")
    cards.add_argument("--board", default=DEFAULT_BOARD)
    cards.add_argument("--json", action="store_true", help="машинный вывод")
    cards.set_defaults(func=cmd_cards)

    status = sub.add_parser("status", help="сколько строк закрыто, в работе, ждут, новых")
    status.add_argument("--board", default=DEFAULT_BOARD)
    status.add_argument("--json", action="store_true", help="машинный вывод")
    status.set_defaults(func=cmd_status)

    decide = sub.add_parser("decide", help="записать решение владельца")
    decide.add_argument("--row", required=True, help="row_id строки листа (8 hex)")
    decide.add_argument("--decision", required=True, choices=("yes", "no", "later"))
    decide.add_argument("--note", default="", help="причина для «нет» и «позже»")
    decide.add_argument("--board", default=DEFAULT_BOARD)
    decide.add_argument("--json", action="store_true", help="машинный вывод")
    decide.set_defaults(func=cmd_decide)

    args = parser.parse_args(argv)
    return args.func(load_env(), args)


if __name__ == "__main__":
    sys.exit(main())
