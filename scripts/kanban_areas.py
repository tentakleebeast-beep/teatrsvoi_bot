#!/usr/bin/env python3
"""Конфликт карт канбана: по файлам, а не по корням каталогов.

Зачем: 31.10.2026 две webapp-карты, взятые одновременно, дважды конфликтовали с общей
оболочкой (`pages/admin.py`, `misc.py`, `person.py`, `week.py`, `webapp-shell.js`), и обе
работы пришлось переделывать. Лимит `max_in_progress` этого не ловит: карты разные,
файлы у них одни.

С 04.11.2026 (docs/development/26 §7b) пересечение считается по точным путям файлов,
а не по корням: раньше область карты выводилась из первого каталога (`scripts/`, `docs/`,
`db/`), и пять карт, писавших РАЗНЫЕ файлы отчётов в `docs/reports/`, стояли в очереди
друг за другом без причины.

Правило:

1. Из раздела «Область» берутся точные пути. Две карты конфликтуют, только если
   объявили один и тот же **существующий** файл.
2. Новые файлы (отчёт со своим номером) конфликтом не считаются: их ещё нет в репозитории.
   Одинаковый новый путь у двух карт виден в парах как предупреждение, но не блокирует.
3. Сгенерированные файлы (`docs/README.md`, `contracts/openapi.json`) не блокируют запуск:
   при пересечении план говорит «решить пересборкой», а не «ждать».
4. Область (экран, домен API, инфраструктура) остаётся ярлыком для отчёта. Если раздел
   «Область» есть, а пути область не задают, это печатается причиной, а не молчаливым `null`.

Запуск:

  env -u PYTHONPATH .venv/bin/python scripts/kanban_areas.py
  env -u PYTHONPATH .venv/bin/python scripts/kanban_areas.py --board teatrsvoi --json

  # для сторожа канбана: что держать, что отпустить, что можно запускать
  env -u PYTHONPATH .venv/bin/python scripts/kanban_areas.py --board teatrsvoi --plan

Код выхода: 0 — конфликтов по файлам нет; 1 — есть карты, которым нельзя стартовать
(в режиме `--plan` они перечислены в `wait` с именем файла).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

SCREENS = "экраны"
INFRA = "инфраструктура"
API = "API"
API_PREFIX = "API: "

LIVE = ("ready", "todo", "scheduled", "running", "review", "blocked")

# Корни, которые не определяют продуктовую область: инфраструктура репозитория.
INFRA_ROOTS = ("scripts", ".github", "deploy", "db", "config", "docs", "prototypes",
               "docker-compose", "pyproject.toml", "requirements", "Makefile")
# Пути тестов область не задают: они едут за тем, что тестируют.
IGNORED_ROOTS = ("tests",)

# Сгенерированные файлы: пересобираются генератором, а не сливаются руками.
# Пересечение по ним — не запрет запускать вместе, а пометка «решить пересборкой».
GENERATED_FILES = {
    "docs/README.md": "scripts/build_docs_index.py",
    "contracts/openapi.json": "scripts/openapi_snapshot.py",
}

API_DOMAINS = {
    "schedule": "расписание",
    "money": "деньги",
    "finance": "деньги",
    "person": "люди",
    "people": "люди",
    "students": "люди",
    "auth": "доступ",
    "webapp": "экраны",
    "webapp_api": "экраны",
    "privacy": "доступ",
    "files": "файлы",
    "file_access": "файлы",
}

PATH_RE = re.compile(r"[A-Za-z0-9_.*-]+(?:/[A-Za-z0-9_.*-]+)+")
TITLE_SCREEN_RE = re.compile(r"экран|mini[ -]?app|webapp|страниц", re.IGNORECASE)
TITLE_API_RE = re.compile(r"роутер|router|эндпоинт|endpoint|\bapi\b", re.IGNORECASE)
SECTION_STOP_RE = re.compile(
    r"^(критерии|границы|как\s|что\s|зачем|проверка|состав|определения|сделаем|источник)",
    re.IGNORECASE)
# Чем путь обрамляют в тексте карты: `x`, "x", (x), [x], «x», и знаки перечисления.
_PATH_EDGE = "`\"'()[]{}<>«»,;:"


def _strip_dot_slash(path: str) -> str:
    """Убрать ведущее `./`, не тронув точку в `.github`: `lstrip('./')` съел бы её."""
    while path.startswith("./"):
        path = path[2:]
    return path


def _clean_path(raw: str) -> str:
    """Сырой токен из тела → путь от корня репозитория без обрамления и хвостового слэша."""
    path = raw.strip().strip(_PATH_EDGE).strip()
    path = _strip_dot_slash(path)
    path = path.rstrip("/")
    return path.rstrip(".,;:")


def _area_section(body: str | None) -> list[str] | None:
    """Строки раздела «Область» (включая строку заголовка) либо None, если раздела нет."""
    if not body:
        return None
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip().lstrip("#").strip()
        if re.match(r"^область\b", stripped, re.IGNORECASE):
            start = i
            break
    if start is None:
        return None

    header = re.sub(r"^#+\s*", "", lines[start].strip())
    header = re.sub(r"^область\s*:?", "", header, flags=re.IGNORECASE).strip()
    chunk: list[str] = [header]
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped:
            break
        if SECTION_STOP_RE.match(stripped):
            break                       # раздел кончился заголовком без пустой строки
        chunk.append(stripped)
    return chunk


def _declared_paths(body: str | None) -> list[str]:
    """Сырые пути из раздела «Область»."""
    chunk = _area_section(body)
    return PATH_RE.findall("\n".join(chunk)) if chunk else []


def _declared_entries(body: str | None) -> list[tuple[str, bool]]:
    """(путь, это_каталог) из раздела «Область»."""
    entries: list[tuple[str, bool]] = []
    for raw in _declared_paths(body):
        is_dir = raw.endswith("/")
        path = _clean_path(raw)
        if path and "/" in path:
            entries.append((path, is_dir))
    return entries


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def card_files(body: str | None, repo_root: pathlib.Path | None = None) -> dict[str, list[str]]:
    """Файлы, которые карта объявила в разделе «Область».

    files     — существующие файлы, которые сливаются руками: по ним и считается конфликт;
    generated — сгенерированные файлы: пересечение решается пересборкой, а не очередью;
    new_files — объявленные пути, которых в репозитории ещё нет: конфликтом не считаются;
    dirs      — заявленные каталоги: точного файла нет, значит и конфликта по файлу нет.

    Путь ищется от корня репозитория, а не от расположения скрипта: после разреза
    `tool-api` на `app/api/` относительные пути уже один раз указывали в пустоту.
    """
    repo = pathlib.Path(repo_root) if repo_root is not None else ROOT
    files: list[str] = []
    generated: list[str] = []
    new_files: list[str] = []
    dirs: list[str] = []
    for path, declared_dir in _declared_entries(body):
        if path in GENERATED_FILES:
            generated.append(path)
            continue
        target = repo / path
        if declared_dir or target.is_dir():
            dirs.append(path)
        elif target.is_file():
            files.append(path)
        else:
            new_files.append(path)
    return {"files": _unique(files), "generated": _unique(generated),
            "new_files": _unique(new_files), "dirs": _unique(dirs)}


def _classify_path(raw: str) -> str | None:
    """Путь из тела → ярлык области. None — путь область не задаёт (тесты, мусор)."""
    path = _clean_path(raw)
    if not path or "/" not in path:
        return None

    # services/<сервис>/app/... и services/<сервис>/db/... — работаем с внутренним путём.
    match = re.match(r"services/[^/]+/(.+)$", path)
    if match:
        path = _strip_dot_slash(match.group(1))

    root = path.split("/", 1)[0]
    if root in IGNORED_ROOTS:
        return None

    if path.startswith(("app/pages", "pages/", "app/static", "static/")):
        return SCREENS

    for prefix in ("app/api/", "app/routers/", "routers/"):
        if path.startswith(prefix):
            domain = path[len(prefix):].split("/", 1)[0]
            domain = domain.removesuffix(".py").removesuffix("_api")
            if not domain or domain.startswith("_"):
                return API
            return API_PREFIX + API_DOMAINS.get(domain, domain)

    if path.startswith("app/"):
        # app/webapp.py, app/errors.py и прочие входы Mini App — это экраны/оболочка.
        name = path[len("app/"):].split("/", 1)[0]
        if name in ("webapp.py", "webapp_page.py", "pages.py", "static"):
            return SCREENS
        # Остальной серверный код — это API, а не «не определена»: так терялась карта
        # t_9ca07ae1, где `services/tool-api/app/files_store.py` не попадал ни в одну ветку.
        return API

    if root in INFRA_ROOTS:
        return INFRA
    return None


def _area_and_reason(body: str | None) -> tuple[str | None, str | None]:
    """Область по телу и причина, если её нет. Пути `tests/` разбор не обрывают."""
    paths = _declared_paths(body)
    areas = [area for area in (_classify_path(p) for p in paths) if area]
    if areas:
        for area in areas:
            if area != INFRA:
                return area, None
        return INFRA, None
    if _area_section(body) is None:
        return None, None
    if not paths:
        return None, "раздел «Область» пуст"
    return None, "раздел «Область» есть, но ни один путь не задаёт область"


def area_from_body(body: str | None) -> str | None:
    """Область по объявленному разделу «Область». Продуктовая область важнее инфраструктуры."""
    return _area_and_reason(body)[0]


def area_from_title(title: str | None) -> str | None:
    text = title or ""
    if TITLE_SCREEN_RE.search(text):
        return SCREENS
    if TITLE_API_RE.search(text):
        return API
    return None


def card_area(title: str | None, body: str | None) -> str | None:
    """Область карты: тело сильнее заголовка; None — «не определена»."""
    return area_from_body(body) or area_from_title(title)


def area_note(title: str | None, body: str | None) -> str | None:
    """Почему область не определена. None — определена (или причину не назвать)."""
    if card_area(title, body):
        return None
    return _area_and_reason(body)[1] or "ни раздела «Область», ни ключевых слов в заголовке"


def _cards(db: pathlib.Path, statuses: tuple[str, ...],
           repo_root: pathlib.Path | None = None) -> list[dict]:
    marks = ",".join("?" * len(statuses))
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute(
            f"select id, status, coalesce(title,''), coalesce(body,''), "
            f"coalesce(priority,0), coalesce(created_at,0) from tasks "
            f"where status in ({marks}) order by priority desc, created_at, id",
            statuses).fetchall()
    except sqlite3.Error as exc:
        raise SystemExit(f"не удалось прочитать {db}: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()

    cards: list[dict] = []
    for r in rows:
        declared = card_files(r[3], repo_root)
        cards.append({
            "id": r[0], "status": r[1], "title": r[2],
            "area": card_area(r[2], r[3]), "area_note": area_note(r[2], r[3]),
            "files": declared["files"], "generated": declared["generated"],
            "new_files": declared["new_files"], "dirs": declared["dirs"],
            "priority": r[4], "created_at": r[5],
        })
    return cards


def running_cards(db: pathlib.Path, repo_root: pathlib.Path | None = None) -> list[dict]:
    return _cards(db, ("running",), repo_root)


def live_cards(db: pathlib.Path, repo_root: pathlib.Path | None = None) -> list[dict]:
    return _cards(db, LIVE, repo_root)


def _shared(left: dict, right: dict, key: str) -> list[str]:
    return sorted(set(left.get(key) or []) & set(right.get(key) or []))


def card_pair(left: dict, right: dict) -> dict:
    """Вердикт по паре карт: можно вместе, конфликт по файлу, решить пересборкой."""
    files = _shared(left, right, "files")
    generated = _shared(left, right, "generated")
    new_files = _shared(left, right, "new_files")
    if files:
        verdict = "конфликт по файлу: " + ", ".join(files)
    elif generated:
        verdict = "пересобрать: " + ", ".join(generated)
    else:
        verdict = "можно вместе"
    return {"cards": [left["id"], right["id"]], "verdict": verdict,
            "files": files, "generated": generated, "new_files": new_files}


def file_conflicts(db: pathlib.Path, repo_root: pathlib.Path | None = None) -> list[dict]:
    """Пары карт в running, которые пишут один и тот же существующий файл."""
    cards = running_cards(db, repo_root)
    return [pair for pair in (card_pair(a, b) for a, b in itertools.combinations(cards, 2))
            if pair["files"]]


def generated_overlaps(cards: list[dict]) -> list[dict]:
    """Сгенерированные файлы, объявленные двумя и более картами: решается пересборкой."""
    seen: dict[str, list[str]] = {}
    for card in cards:
        for path in card.get("generated") or []:
            seen.setdefault(path, []).append(card["id"])
    return [{"file": path, "generator": GENERATED_FILES.get(path, ""), "cards": ids}
            for path, ids in sorted(seen.items()) if len(ids) > 1]


AREA_HOLD_RE = re.compile(r"ждёт:\s*(файл|област)", re.IGNORECASE)


def area_hold(db: pathlib.Path, task_id: str) -> bool:
    """Карта придержана сторожем по правилу (файл/область), а не по времени.

    Причина — единственный признак: `kanban_schedule` пишет произвольный текст, поэтому
    совпадение узкое, по формулировкам самого правила («ждёт: файл …», «ждёт: область …»).
    Одинокого «ждёт» мало: иначе сторож на каждом тике снимал бы карты, отложенные по
    времени («ждёт до 12:00 — окно релиза»).
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "select payload from task_events where task_id=? and kind='scheduled'"
            " order by id desc limit 1", (task_id,)).fetchone()
    except sqlite3.Error:
        return False                  # журнала нет — решаем по файлу придержанных
    finally:
        conn.close()
    payload = (row[0] or "") if row else ""
    return bool(AREA_HOLD_RE.search(payload))


def dispatch_plan(db: pathlib.Path, held: list[str] | None = None,
                  repo_root: pathlib.Path | None = None) -> dict:
    """Что сторожу делать на этом тике.

    start     — карты, которые можно запускать: их файлы никто в работе не пишет;
    wait      — готовые карты, чей существующий файл уже занят: поле `busy_files` называет файл;
    hold      — кого из wait надо придержать (перевести в scheduled);
    release   — кого из придержанных пора отпустить: файл освободился;
    conflicts — уже случившиеся пары в running (это дефект, его видит проверка);
    pairs     — по каждой паре «можно вместе / конфликт по файлу X / пересобрать»;
    rebuild   — сгенерированные файлы, объявленные несколько раз: решить пересборкой.
    """
    held = held or []
    running = running_cards(db, repo_root)
    by_id = {card["id"]: card for card in live_cards(db, repo_root)}
    ready = [card for card in by_id.values() if card["status"] == "ready"]

    busy: dict[str, str] = {}
    for card in running:
        for path in card["files"]:
            busy.setdefault(path, card["id"])

    start: list[dict] = []
    wait: list[dict] = []
    for card in ready:
        clash = sorted(set(card["files"]) & set(busy))
        if clash:
            wait.append({**card, "busy_files": clash,
                         "occupied_by": sorted({busy[path] for path in clash})})
            continue
        start.append(card)
        for path in card["files"]:
            busy.setdefault(path, card["id"])

    hold = [card for card in wait if card["id"] not in held]

    # Отпускаем придержанные карты, чьи файлы освободились. Карта без файлов не ждёт
    # ничего: правило к ней не применяется.
    released: list[dict] = []
    for task_id in held:
        card = by_id.get(task_id)
        if card is None or card["status"] not in ("scheduled", "todo", "ready"):
            continue                    # карту уже двинули руками — не трогаем
        if card["status"] == "scheduled" and not (set(card["files"]) & set(busy)):
            released.append(card)

    # Карту мог придержать кто-то другой, или файл придержанных потерялся: тогда она
    # висит в scheduled вечно. Отпускаем и такие — по причине в журнале, а не по файлу.
    for card in by_id.values():
        if card["status"] != "scheduled" or card["id"] in held or card in released:
            continue
        if set(card["files"]) & set(busy):
            continue
        if area_hold(db, card["id"]):
            released.append(card)

    live_for_pairs = [card for card in by_id.values() if card["status"] in ("ready", "running")]
    pairs = [card_pair(a, b) for a, b in itertools.combinations(live_for_pairs, 2)]

    return {
        "ok": not wait and not file_conflicts(db, repo_root),
        "running": running,
        "conflicts": file_conflicts(db, repo_root),
        "start": start,
        "wait": wait,
        "hold": hold,
        "release": released,
        "pairs": pairs,
        "rebuild": generated_overlaps(list(by_id.values())),
        "generated": dict(GENERATED_FILES),
        "live_ids": sorted(by_id),
    }


def apply_plan(plan: dict, held_file: pathlib.Path, board: str,
               hermes_bin: str = "hermes", dry_run: bool = False) -> list[str]:
    """Придержать карты занятых файлов и отпустить те, чьи файлы освободились.

    Держим через `kanban schedule` (статус не диспетчеризуется), отпускаем через
    `kanban unblock`. Список придержанных живёт в файле: без него сторож на следующем
    тике не отличит свою задержку от карты, которую человек увёл руками.
    """
    actions: list[str] = []
    held = [line.strip() for line in held_file.read_text(encoding="utf-8").splitlines()
            if line.strip()] if held_file.is_file() else []

    def call(*args: str) -> bool:
        if dry_run:
            actions.append("сухо: " + " ".join(args))
            return True
        try:
            result = subprocess.run(
                [hermes_bin, "kanban", "--board", board, *args],
                capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            actions.append(f"не удалось выполнить {' '.join(args)}: {exc}")
            return False
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            actions.append(f"не удалось выполнить {' '.join(args)}: "
                           f"{tail[-1] if tail else 'без вывода'}")
            return False
        return True

    for card in plan["release"]:
        if call("unblock", card["id"], "--reason", "файл освободился — карта снова в очереди"):
            actions.append(f"отпустил {card['id']}: файлы свободны")
            held = [item for item in held if item != card["id"]]

    # Придержанные карты, которые уже закрыли или увели руками, из списка убираем:
    # иначе файл растёт и сторож вечно тащит мёртвые id.
    alive = set(plan.get("live_ids") or [])
    gone = [item for item in held if item not in alive]
    if gone:
        actions.append("забыл придержанные карты, которых больше нет в работе: " + ", ".join(gone))
        held = [item for item in held if item in alive]

    for card in plan["hold"]:
        files = card.get("busy_files") or card.get("files") or []
        occupant = None
        for running in plan["running"]:
            if set(running["files"]) & set(files):
                occupant = running
                break
        names = ", ".join(files) if files else "файлы карты"
        reason = (f"ждёт: файл «{names}» занят картой "
                  f"{occupant['id'] if occupant else 'в работе'}")
        if call("schedule", card["id"], reason):
            actions.append(f"придержал {card['id']}: {reason}")
            if card["id"] not in held:
                held.append(card["id"])

    if not dry_run:
        held_file.parent.mkdir(parents=True, exist_ok=True)
        held_file.write_text("\n".join(held) + ("\n" if held else ""), encoding="utf-8")
    return actions


def hermes_home(explicit: str | None = None) -> pathlib.Path:
    """Корень Hermes с досками.

    Ловушка: у воркера канбана HERMES_HOME указывает на каталог профиля, а доски лежат
    в `~/.hermes/kanban/boards`. Если брать переменную как есть, скрипт молча отвечает
    «доска не найдена». Поэтому профильный путь без досок игнорируется.
    """
    candidates = [explicit, os.getenv("HERMES_HOME"), str(pathlib.Path.home() / ".hermes")]
    for candidate in candidates:
        if not candidate:
            continue
        path = pathlib.Path(candidate)
        if (path / "kanban" / "boards").is_dir() or (path / "kanban.db").is_file():
            return path
    return pathlib.Path.home() / ".hermes"


def _boards(home: pathlib.Path, only: str | None) -> list[tuple[str, pathlib.Path]]:
    out: list[tuple[str, pathlib.Path]] = []
    base = home / "kanban" / "boards"
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            db = folder / "kanban.db"
            if db.is_file() and (only is None or folder.name == only):
                out.append((folder.name, db))
    legacy = home / "kanban.db"
    if legacy.is_file() and only in (None, "default"):
        out.append(("default", legacy))
    return out


def format_report(found: list[tuple[str, list[dict]]]) -> list[str]:
    lines: list[str] = []
    multiple = len(found) > 1
    for slug, cards in found:
        prefix = f"{slug}/" if multiple else ""
        if not cards:
            lines.append(f"Карты в работе: нет ({slug})." if multiple else "Карты в работе: нет.")
            continue
        lines.append(f"Карты в работе ({slug}):" if multiple else "Карты в работе:")
        for card in cards:
            area = card["area"] or "не определена"
            note = f" — {card['area_note']}" if card.get("area_note") else ""
            lines.append(f"  {prefix}{card['id']}  {area:<18} {card['title'][:56]}{note}")
            for path in card["files"]:
                lines.append(f"      пишет: {path}")
        undefined = [card for card in cards if not card["area"]]
        if undefined:
            lines.append(f"  не определена: {len(undefined)} — причина напечатана у карты, "
                         f"догадок нет")
        for pair in (card_pair(a, b) for a, b in itertools.combinations(cards, 2)):
            if pair["files"]:
                lines.append(f"  конфликт: {' и '.join(pair['cards'])} пишут "
                             f"{', '.join(pair['files'])}")
        for item in generated_overlaps(cards):
            lines.append(f"  пересобрать: {item['file']} объявлен у "
                         f"{', '.join(item['cards'])} ({item['generator']})")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Конфликты карт канбана по файлам")
    parser.add_argument("--board", default=None, help="только этот борд (по умолчанию все)")
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    parser.add_argument("--plan", action="store_true",
                        help="план для сторожа: start / wait / hold / release / pairs")
    parser.add_argument("--apply", action="store_true",
                        help="придержать карты занятых файлов и отпустить освободившиеся")
    parser.add_argument("--hermes-bin", default=os.getenv("HERMES_BIN", "hermes"),
                        help="путь к hermes CLI (для --apply)")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать, что сделал бы --apply, ничего не меняя")
    parser.add_argument("--held", default=None,
                        help="файл со списком придержанных карт (для --plan)")
    parser.add_argument("--hermes-home", default=None,
                        help="корень Hermes с досками (по умолчанию ~/.hermes)")
    parser.add_argument("--repo", default=None,
                        help="корень репозитория для проверки существования файлов")
    args = parser.parse_args()

    repo_root = pathlib.Path(args.repo) if args.repo else ROOT
    home = hermes_home(args.hermes_home)
    boards = _boards(home, args.board)
    if not boards:
        print("Доска не найдена: проверьте --board и HERMES_HOME.", file=sys.stderr)
        return 2

    held: list[str] = []
    if args.held:
        path = pathlib.Path(args.held)
        if path.is_file():
            held = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()]

    if args.apply:
        if len(boards) != 1:
            print("--apply работает с одной доской: укажите --board.", file=sys.stderr)
            return 2
        if not args.held:
            print("--apply требует --held <файл> со списком придержанных карт.",
                  file=sys.stderr)
            return 2
        slug, db = boards[0]
        plan = dispatch_plan(db, held, repo_root)
        for line in apply_plan(plan, pathlib.Path(args.held), slug,
                               hermes_bin=args.hermes_bin, dry_run=args.dry_run):
            print(line)
        return 1 if plan["conflicts"] else 0

    if args.plan:
        payload = []
        for slug, db in boards:
            plan = dispatch_plan(db, held, repo_root)
            plan["board"] = slug
            payload.append(plan)
        print(json.dumps(payload if len(payload) > 1 else payload[0],
                         ensure_ascii=False, indent=2))
        return 0 if all(item["ok"] for item in payload) else 1

    found = [(slug, running_cards(db, repo_root)) for slug, db in boards]
    if args.json:
        payload = [{"board": slug, "cards": cards} for slug, cards in found]
        print(json.dumps(payload if len(payload) > 1 else payload[0],
                         ensure_ascii=False, indent=2))
    else:
        print("\n".join(format_report(found)))
    return 1 if any(file_conflicts(db, repo_root) for _, db in boards) else 0


if __name__ == "__main__":
    sys.exit(main())
