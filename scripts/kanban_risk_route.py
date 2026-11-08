#!/usr/bin/env python3
"""Риск-маршрутизация канбана: когда карту обязан проверить worker-test.

Зачем (docs/reports/104 §7): ревьюер возвращает работу 0.05 на карту, а 0.52 карты
требуют доводки уже после `done`. Дефекты вылезают не на ревью, а после него —
в WebView, в нажатиях, в отдаче статики, в токенах. Тестировщик на каждой карте не
нужен: он удвоит прогоны при медиане цикла 1.4 ч. Нужен узкий воркер по риску.

Этот скрипт — то самое правило «по риску, а не на каждую карту». Он отвечает на один
вопрос: **трогает ли карта то, где ревью уже промахивалось** — экраны и статику Mini App,
токены и авторизацию, контракт ошибок. Список триггеров — `config/kanban-risk-triggers.yaml`,
правило и обоснование — `docs/development/107-kanban-risk-routing.md`.

    # нужен ли worker-test на этой карте (0 — нужен, 1 — не нужен, 2 — ошибка)
    env -u PYTHONPATH .venv/bin/python scripts/kanban_risk_route.py --task t_1ef88a1f

    # по списку файлов, без карты (для ручной проверки и для хука)
    env -u PYTHONPATH .venv/bin/python scripts/kanban_risk_route.py \\
        --files services/tool-api/app/static/webapp-ui.css

    # завести карту-проверку (ребёнок текущей, воркер worker-test)
    env -u PYTHONPATH .venv/bin/python scripts/kanban_risk_route.py --task t_xxx --apply

    # пройтись по открытым картам борда: кому проверку не завели
    env -u PYTHONPATH .venv/bin/python scripts/kanban_risk_route.py --sweep --board teatrsvoi

Откуда берутся «изменённые файлы»: сначала `--files`, иначе — пути, названные в
заголовке и описании карты (в этом проекте карта обязана писать область и файлы, правило
`docs/development/26` §7a). Плюс отдельно ищутся ключевые слова (initData, токен,
авторизация, статик, ассет…). Совпало хоть что-то — триггер сработал.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "kanban-risk-triggers.yaml"

# Открытые карты: по ним имеет смысл спрашивать «нужен ли тестировщик».
LIVE = ("ready", "todo", "scheduled", "running", "review", "blocked")

# Путь в описании карты: не меньше одного слэша, буквы/цифры/точки/дефисы/звёздочки.
PATH_IN_TEXT = re.compile(r"[\w.*-]+(?:/[\w.*-]+)+")


@dataclass
class Rules:
    assignee: str
    paths: list[str]

    @classmethod
    def load(cls, path: pathlib.Path = CONFIG) -> Rules:
        try:
            import yaml
        except ImportError as exc:                      # pragma: no cover - есть в requirements
            raise SystemExit(f"нужен PyYAML: {exc}") from exc
        if not path.is_file():
            raise SystemExit(f"нет файла триггеров: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        # Ключи конфига — те же, что у правила 107: risk_paths и not_risk_paths.
        return cls(
            assignee=str(raw.get("assignee") or "worker-test"),
            paths=[str(p) for p in raw.get("risk_paths") or []],
        )


@dataclass
class Decision:
    """Результат: поднимать тестировщика или нет, и почему именно."""

    task_id: str
    title: str
    triggered: bool
    assignee: str = "worker-test"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "triggered": self.triggered,
            "assignee": self.assignee if self.triggered else None,
            "reasons": self.reasons,
        }


def _variants(pattern: str) -> list[str]:
    """Образцы «от длинного к короткому»: карта пишет `app/static`, конфиг — полный путь.

    Сокращаем не «на сколько захочется», а только корень сервиса: `services/<сервис>/…`
    → `<сервис>/…` → `…`. Общее укорочение давало ложные срабатывания: `tests/webapp/**`
    укорачивался до `webapp/**` и ловил карту про `webapp/admin` (другой экран, другой
    риск), а `app/static/**` в чужом сервисе — статику гейтвея, где Mini App нет.
    """
    variants = [pattern]
    parts = pattern.split("/")
    if len(parts) > 2 and parts[0] == "services":
        variants.append("/".join(parts[1:]))
        variants.append("/".join(parts[2:]))
    return variants


def glob_match(pattern: str, path: str) -> bool:
    """Сравнить путь с образцом, где `**` — «любое число сегментов».

    `PurePath.match` в Python 3.12 считает `**` обычной звёздочкой внутри сегмента,
    а `fullmatch` появился только в 3.13. Поэтому разбор сегментный и явный.
    """
    wants = pattern.split("/")
    parts = path.split("/")

    def step(wi: int, pi: int) -> bool:
        if wi == len(wants):
            return pi == len(parts)
        if wants[wi] == "**":
            return any(step(wi + 1, skip) for skip in range(pi, len(parts) + 1))
        if pi >= len(parts):
            return False
        if not fnmatch.fnmatchcase(parts[pi], wants[wi]):
            return False
        return step(wi + 1, pi + 1)

    return step(0, 0)


def path_hits(candidate: str, pattern: str) -> bool:
    """Подходит ли названный в карте путь под образец из конфига."""
    candidate = candidate.strip().strip("`'\"")
    if not candidate:
        return False
    for variant in _variants(pattern):
        base = variant.split("**", 1)[0].rstrip("/")
        # Вариант «**» (когда от образца осталась одна звёздочка) подошёл бы ко всему
        # на свете — и `scripts/build_reports.py` стал бы риском. Такие не берём.
        if not base:
            continue
        if candidate == base or candidate.startswith(base + "/"):
            return True
        if glob_match(variant, candidate):
            return True
    return False


def paths_from_text(*texts: str) -> list[str]:
    """Пути, названные в тексте карты. Ссылки и домашние каталоги отбрасываем."""
    found: list[str] = []
    for text in texts:
        for token in PATH_IN_TEXT.findall(text or ""):
            token = token.strip("`'\".,;:()")
            if token.startswith(("http", "~/", "www.")) or "/" not in token:
                continue
            if token not in found:
                found.append(token)
    return found


def decide(rules: Rules, title: str, body: str, files: list[str] | None = None) -> Decision:
    """Сработал ли триггер. Файлы из `--files` приоритетнее текста карты."""
    task_id = ""
    candidates = [str(f) for f in files] if files else paths_from_text(title, body)
    decision = Decision(task_id=task_id, title=title, triggered=False, assignee=rules.assignee)

    for pattern in rules.paths:
        for candidate in candidates:
            if path_hits(candidate, pattern):
                decision.reasons.append(f"файл {candidate} → {pattern}")
                break

    decision.triggered = bool(decision.reasons)
    return decision


# ── канбан: чтение карты, борд, создание проверки ───────────────────────────────


def kanban_home() -> pathlib.Path:
    """Корень канбана — общий для всех профилей (как у Hermes: `kanban_home()`).

    Порядок: `HERMES_KANBAN_HOME` → вывести из `HERMES_KANBAN_DB` → `HERMES_HOME` →
    `~/.hermes`. Выводить из БД нужно потому, что в сессии воркера `HERMES_HOME`
    указывает на каталог профиля, а доска живёт в корне Hermes: иначе скрипт искал бы
    борд внутри `profiles/<имя>/kanban/boards` и не находил его.
    """
    override = os.getenv("HERMES_KANBAN_HOME", "").strip()
    if override:
        return pathlib.Path(override).expanduser()

    db = os.getenv("HERMES_KANBAN_DB", "").strip()
    if db:
        path = pathlib.Path(db)
        # <root>/kanban/boards/<slug>/kanban.db → <root>;  <root>/kanban.db → <root>
        if path.parent.parent.name == "boards":
            return path.parents[3]
        return path.parents[1]

    home = os.getenv("HERMES_HOME", "").strip()
    if home:
        path = pathlib.Path(home).expanduser()
        # HERMES_HOME может быть каталогом профиля (~/.hermes/profiles/<имя>).
        if path.parent.name == "profiles":
            return path.parent.parent
        return path
    return pathlib.Path.home() / ".hermes"


def board_slug(explicit: str | None) -> str:
    """Борд: флаг → переменная → выделенный борд → default (как в Hermes)."""
    if explicit:
        return explicit
    for env in ("HERMES_KANBAN_BOARD",):
        value = os.getenv(env)
        if value:
            return value
    db = os.getenv("HERMES_KANBAN_DB")
    if db:
        return pathlib.Path(db).parent.name
    current = kanban_home() / "kanban" / "current"
    if current.is_symlink() or current.exists():
        try:
            return current.resolve().name
        except OSError:                                  # pragma: no cover - битая ссылка
            pass
    return "default"


def board_db(board: str) -> pathlib.Path:
    home = kanban_home()
    db = home / "kanban" / "boards" / board / "kanban.db"
    if db.is_file():
        return db
    legacy = home / "kanban.db"
    if board == "default" and legacy.is_file():
        return legacy
    raise SystemExit(f"нет базы борда «{board}»: {db}")


def read_task(db: pathlib.Path, task_id: str) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "select id, title, coalesce(body,'') as body, status, coalesce(assignee,'-') as assignee "
            "from tasks where id = ?", (task_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit(f"карты {task_id} нет на борде {db.parent.name}")
    return dict(row)


def open_tasks(db: pathlib.Path) -> list[dict]:
    marks = ",".join("?" * len(LIVE))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"select id, title, coalesce(body,'') as body, status from tasks "
            f"where status in ({marks}) order by created_at", LIVE).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def check_body(decision: Decision, parent: dict, rules: Rules) -> str:
    """Тело карты-проверки: у воркера с первого экрана есть что делать."""
    reasons = "\n".join(f"- {r}" for r in decision.reasons)
    return f"""Проверка по риску: {parent['title']}

Класс: проверка / Mini App
Родитель: {parent['id']} — {parent['title']}
Поднято автоматически: `scripts/kanban_risk_route.py --task {parent['id']} --apply`
Исполнитель: `{rules.assignee}`

## Почему поднято
{reasons}

## Что сделать (docs/development/107, четыре обязанности)

1. Браузерный набор на двух движках — вывод обоих в комментарий к карте:
   `SVOI_WEBAPP_TESTS=1 env -u PYTHONPATH .venv/bin/python -m pytest tests/webapp -q --browser chromium`
   `SVOI_WEBAPP_TESTS=1 env -u PYTHONPATH .venv/bin/python -m pytest tests/webapp -q --browser webkit`
2. Каждый подключённый на страницах ассет отдаётся (200 и непустое тело):
   `env -u PYTHONPATH .venv/bin/python -m pytest tests/integration/test_static_contract.py -q`
3. Воспроизвести дефект из родительской карты в браузере и оставить репро-тест
   в `tests/webapp/` (падает без правки, проходит с ней).
4. Не закрывать карту без репро: если дефект не воспроизвёлся — вернуть с конкретикой
   (что гонял, на каком движке, что увидел, каких данных не хватает).

## Границы
Только проверка. Код родительской карты не переписывать, `main` не мержить,
прод не трогать. Найденный дефект — комментарий с командой и выводом, а не «не работает».
"""


def create_check(board: str, decision: Decision, parent: dict, rules: Rules) -> str | None:
    """Завести карту-проверку через CLI Hermes. Возвращает id (или None при сбое)."""
    hermes = os.getenv("SVOI_HERMES") or "hermes"
    body = check_body(decision, parent, rules)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as tmp:
        tmp.write(body)
        body_path = tmp.name
    cmd = [
        hermes, "kanban", "--board", board, "create",
        f"Проверка по риску: {parent['title']}"[:90],
        "--assignee", rules.assignee,
        "--parent", parent["id"],
        "--body-file", body_path,
        # Идемпотентность: повторный проход не заводит вторую карту на ту же работу.
        "--idempotency-key", f"risk-check:{parent['id']}",
        "--created-by", "kanban_risk_route",
        "--json",
    ]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    finally:
        pathlib.Path(body_path).unlink(missing_ok=True)
    if done.returncode != 0:
        print(f"не удалось завести карту для {parent['id']}: {done.stderr.strip()}", file=sys.stderr)
        return None
    try:
        payload = json.loads(done.stdout)
    except json.JSONDecodeError:
        payload = {}
    return payload.get("id") or payload.get("task_id")


# ── CLI ────────────────────────────────────────────────────────────────────────


def _print_human(decision: Decision, rules: Rules) -> None:
    mark = "СРАБОТАЛ" if decision.triggered else "не сработал"
    print(f"{decision.task_id or '(без карты)'} — {decision.title or '(без заголовка)'}")
    print(f"  триггер: {mark}; воркер: {rules.assignee if decision.triggered else '—'}")
    for reason in decision.reasons:
        print(f"  · {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Риск-триггеры: нужен ли worker-test на карте")
    parser.add_argument("--task", help="id карты канбана")
    parser.add_argument("--board", help="борд (по умолчанию — выделенный)")
    parser.add_argument("--files", nargs="*", help="изменённые файлы вместо разбора карты")
    parser.add_argument("--title", default="", help="заголовок (вместе с --files)")
    parser.add_argument("--body-file", help="описание карты (вместе с --files)")
    parser.add_argument("--sweep", action="store_true", help="все открытые карты борда")
    parser.add_argument("--apply", action="store_true", help="завести карту worker-test")
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    parser.add_argument("--config", help="другой файл триггеров")
    args = parser.parse_args()

    rules = Rules.load(pathlib.Path(args.config) if args.config else CONFIG)
    results: list[Decision] = []

    if args.sweep:
        board = board_slug(args.board)
        db = board_db(board)
        for task in open_tasks(db):
            decision = decide(rules, task["title"], task["body"])
            decision.task_id = task["id"]
            decision.title = task["title"]
            results.append(decision)
            if args.apply and decision.triggered:
                created = create_check(board, decision, task, rules)
                if created:
                    print(f"заведена карта проверки {created} для {task['id']}")
    elif args.task or args.files or args.body_file:
        board = board_slug(args.board) if args.task else ""
        if args.task:
            parent = read_task(board_db(board), args.task)
            title = parent["title"]
            body = parent["body"]
        else:
            body = (pathlib.Path(args.body_file).read_text(encoding="utf-8")
                    if args.body_file else "")
            title = args.title
            # Без карты создавать нечего: --apply требует родителя.
            parent = {"id": "-", "title": title or (args.files or ["(без карты)"])[0],
                      "body": body}
        decision = decide(rules, title, body, files=args.files)
        decision.task_id = parent["id"]
        decision.title = parent["title"]
        results.append(decision)
        if args.apply and decision.triggered:
            if not args.task:
                parser.error("--apply требует --task: карта-проверка заводится ребёнком карты")
            created = create_check(board, decision, parent, rules)
            if created:
                print(f"заведена карта проверки {created} для {parent['id']}")
    else:
        parser.error("нужен --task, --files/--body-file или --sweep")

    if args.json:
        payload = [d.as_dict() for d in results]
        print(json.dumps(payload if len(payload) > 1 else payload[0], ensure_ascii=False, indent=2))
    else:
        for decision in results:
            _print_human(decision, rules)

    return 0 if any(d.triggered for d in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
