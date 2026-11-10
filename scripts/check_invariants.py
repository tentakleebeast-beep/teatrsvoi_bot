#!/usr/bin/env python3
"""
Проверка инвариантов из docs/architecture/11-business-rules.md.
Код выхода 1, если хотя бы один инвариант нарушен — можно вешать в cron/CI.

  python scripts/check_invariants.py
"""

from __future__ import annotations

import ast
import pathlib
import plistlib
import re
import sys

import psycopg
from _cli import dsn
from psycopg.rows import dict_row

# Строка подключения одна на все скрипты: окружение → `.env` → локальная база
# разработки (см. `scripts/_cli.dsn`). Раньше здесь лежала своя копия с паролем.
DB = dsn()

PROJECT = pathlib.Path(__file__).resolve().parents[1]

# docs/development/26 §6: прод обновляется только по команде владельца. Запрет держится
# проверкой, а не комментарием в workflow: добавленный кем-то `on: push` выкатил бы прод
# молча, поэтому триггеры `deploy.yml` разбираются здесь на уровне текста.
DEPLOY_WORKFLOW = pathlib.Path(".github/workflows/deploy.yml")
ALLOWED_TRIGGERS = {"workflow_dispatch"}
FORBIDDEN_TRIGGERS = (
    "push", "pull_request", "pull_request_target", "schedule", "release",
    "workflow_run", "repository_dispatch", "merge_group",
)
# Автодеплой не должен жить и в планировщике: cron, который ходит на сервер, — тот же
# автодеплой, только мимо workflow.
CRON_DIR = pathlib.Path("scripts/cron")
CRON_FORBIDDEN = re.compile(r"deploy|ssh|systemctl|129\.101\.126\.208", re.IGNORECASE)

# V-026 (01.11.2026): n8n выведен из контура. Публичный вебхук роутера больше не
# существует, роутер — `POST /tools/route` в tool-api. Проверка статическая: строки
# в коде вернут старую дорогу молча, а база об этом ничего не знает.
LEGACY_MARKERS = ("N8N" + "_", "/webhook/")
LEGACY_SCAN = ("services", "scripts", ".env.example")


def _legacy_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for name in LEGACY_SCAN:
        base = PROJECT / name
        if base.is_file():
            files.append(base)
        elif base.is_dir():
            files.extend(p for p in base.rglob("*") if p.is_file())
    return files


def check_no_n8n() -> int:
    """Строк legacy-контура n8n в сервисах, скриптах и `.env.example` нет."""
    offenders: list[str] = []
    for path in _legacy_files():
        if path.resolve() == pathlib.Path(__file__).resolve():
            continue                      # сам проверяющий файл содержит имена маркеров
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for marker in LEGACY_MARKERS:
                if marker in line:
                    offenders.append(f"{path.relative_to(PROJECT)}:{number}: {marker}")
    for item in offenders:
        print(f"FAIL n8n {item}")
    if offenders:
        print(f"     строк n8n-контура: {len(offenders)} — выведен из контура (V-026)")
    else:
        print(f"OK   n8n  {'legacy-строк n8n в сервисах и скриптах нет':52} "
              f"проверено файлов: {len(_legacy_files())}")
    return len(offenders)


def _on_block(text: str) -> tuple[list[str], bool]:
    """Триггеры из блока `on:` и признак того, что блок найден.

    YAML-парсер здесь не нужен: проверяем форму файла, который лежит в репозитории.
    Ищем строку `on:` на нулевом отступе, затем ключи второго уровня до следующего
    ключа верхнего уровня. `on: push` и `on: [push]` разбираются как инлайн-значение.
    """
    triggers: list[str] = []
    inside = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not inside:
            if line.startswith("on:"):
                inside = True
                inline = line[len("on:"):].strip()
                for item in re.findall(r"[A-Za-z_]+", inline):
                    triggers.append(item)
            continue
        if not raw[0].isspace():
            break                         # новый ключ верхнего уровня — блок кончился
        if raw.startswith(("  ", "\t")) and not raw.startswith("    "):
            key = line.strip()
            if key.endswith(":") and " " not in key:
                triggers.append(key[:-1])
    return triggers, inside


def check_no_autodeploy(root: pathlib.Path | None = None) -> int:
    """В `deploy.yml` единственный триггер — `workflow_dispatch` (docs/development/26 §6)."""
    root = root or PROJECT
    path = root / DEPLOY_WORKFLOW
    shown = DEPLOY_WORKFLOW.as_posix()
    if not path.is_file():
        # Прод-workflow живут только в приватном репозитории (docs/development/26 §10):
        # в публичном дереве deploy.yml нет, а без него автодеплою неоткуда взяться.
        print(f"OK   деплой  {'deploy.yml не публикуется — автодеплоя нет':52} проверено: нет файла")
        return 0
    triggers, found = _on_block(path.read_text(encoding="utf-8", errors="ignore"))
    if not found:
        print(f"FAIL деплой {shown}: нет блока on: — триггер не проверить")
        return 1
    extra = [item for item in triggers if item not in ALLOWED_TRIGGERS]
    forbidden = [item for item in extra if item in FORBIDDEN_TRIGGERS]
    if extra:
        reason = "автодеплой запрещён" if forbidden else "разрешён только workflow_dispatch"
        print(f"FAIL деплой {shown}: on: {', '.join(extra)} — {reason} (docs/development/26 §6)")
        return 1
    if not triggers:
        print(f"FAIL деплой {shown}: нет ни одного триггера, включая workflow_dispatch")
        return 1
    print(f"OK   деплой  {'только workflow_dispatch, автодеплоя нет':52} проверено: {shown}")
    return 0


def check_cron_no_deploy(root: pathlib.Path | None = None) -> int:
    """В `scripts/cron/` нет строк про выкат: деплой живёт только в ручном workflow."""
    root = root or PROJECT
    base = root / CRON_DIR
    if not base.is_dir():
        print(f"OK   cron    {'каталога scripts/cron нет':52} проверено: 0 файлов")
        return 0
    offenders: list[str] = []
    files = [p for p in base.rglob("*") if p.is_file() and p.suffix not in {".pyc", ".pyo"}]
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if CRON_FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    for item in offenders:
        print(f"FAIL cron  {item}")
    if offenders:
        print(f"     строк про выкат в scripts/cron: {len(offenders)} — деплой только вручную")
    else:
        print(f"OK   cron    {'строк про deploy/ssh/systemctl в cron нет':52} "
              f"проверено файлов: {len(files)}")
    return len(offenders)


def check_master_disciplines() -> int:
    """Мастер без дисциплины — предупреждение, а не падение (V-037, D6/D7).

    Решение владельца 08.11.2026: роль `master` выдаётся без дисциплины, мастер
    работает сразу, а недостающее называется словами на экранах. Поэтому состояние
    «мастер без дисциплины» печатается как `WARN M1` со списком и **не** останавливает
    деплой. Единственное определение нарушения остаётся прежним — view
    `v_master_without_discipline` (db/init/061).

    Отсутствие view — по-прежнему `FAIL`: это не «инвариант выполнен», а непрокатанная
    миграция; молчать значило бы показывать зелёный гейт без проверки вовсе.
    """
    try:
        with psycopg.connect(DB, row_factory=dict_row) as conn:
            rows = conn.execute("select participant_id, display_name "
                                "from v_master_without_discipline order by display_name").fetchall()
    except psycopg.Error as exc:
        print(f"FAIL M1  {'мастер без дисциплины':52} view нет: {str(exc).splitlines()[0][:60]}")
        return 1
    for row in rows:
        print(f"WARN M1  {'мастер без дисциплины':52} "
              f"№{row['participant_id']} {row['display_name']}")
    if not rows:
        print(f"OK   M1  {'у каждого мастера есть дисциплина':52} проверено: view пуст")
    return 0


def static_checks(root: pathlib.Path | None = None) -> int:
    """Проверки без базы: триггеры деплоя и планировщик."""
    root = root or PROJECT
    return check_no_autodeploy(root) + check_cron_no_deploy(root)


def _parse_static_args(argv: list[str]) -> tuple[bool, pathlib.Path]:
    """`--static` (без БД) и `--root <каталог>` для проверки копии репозитория."""
    static = "--static" in argv
    root = PROJECT
    if "--root" in argv:
        index = argv.index("--root")
        if index + 1 >= len(argv):
            raise SystemExit("--root требует каталог")
        root = pathlib.Path(argv[index + 1]).resolve()
    return static, root


def main(argv: list[str] | None = None) -> int:
    static, root = _parse_static_args(sys.argv[1:] if argv is None else argv)
    if static:
        print("Статические проверки (docs/development/26 §6):")
        print("-" * 78)
        failed = static_checks(root)
        if failed:
            print(f"\nНЕ ПРОЙДЕНО проверок: {failed}")
            return 1
        print("\nВсе статические проверки пройдены.")
        return 0

    with psycopg.connect(DB, row_factory=dict_row) as conn:
        rows = conn.execute("select * from v_invariant_checks").fetchall()
        file_rows = conn.execute(
            "select * from v_file_checks order by length(code), code").fetchall()
        bal = conn.execute("select * from v_balance").fetchone()
        totals = conn.execute("select * from v_period_totals order by period").fetchall()

    print("Инварианты (docs/architecture/11-business-rules.md)")
    print("-" * 78)
    failed = 0
    for r in rows:
        mark = "OK  " if r["ok"] else "FAIL"
        if not r["ok"]:
            failed += 1
        print(f"{mark} {r['code']:3} {r['name'][:52]:52} {r['detail']}")
    print("-" * 78)

    # Роль «педагог» (Э1, docs/architecture/140 §2.2): у мастера есть дисциплина.
    failed += check_master_disciplines()

    print("\nФайлы и папки (docs/development/86):")
    for r in file_rows:
        mark = "OK  " if r["ok"] else "FAIL"
        if not r["ok"]:
            failed += 1
        print(f"{mark} {r['code']:3} {r['name'][:52]:52} {r['detail']}")

    print("\nПериоды:")
    print(f"{'период':8} {'кач.':6} {'доход':>10} {'расход':>10} {'результат':>11}")
    for t in totals:
        res = float(t["income_total"]) - float(t["expense_total"])
        print(f"{t['period']:8} {t['quality']:6} {float(t['income_total']):>10.0f} "
              f"{float(t['expense_total']):>10.0f} {res:>11.0f}")

    print(f"\nБаланс студии: {float(bal['balance']):,.2f} ₽".replace(",", " "))
    print(f"  взносы {float(bal['dues_total']):,.0f} + прочие доходы {float(bal['income_tx']):,.0f} "
          f"− расходы {float(bal['expense_tx']):,.0f}".replace(",", " "))

    print("\nКонтур (V-026, docs/infrastructure/29):")
    failed += check_no_n8n()

    print("\nДеплой (docs/development/26 §6):")
    failed += static_checks(PROJECT)

    print("\nСреды (t_4aa28fe9, docs/infrastructure/08 §1a):")
    failed += check_dev_env_isolation()

    if failed:
        print(f"\nНЕ ПРОЙДЕНО инвариантов: {failed}")
        return 1
    print("\nВсе проверки пройдены.")
    return 0


# t_4aa28fe9: локальный запуск не должен читать прод-токен. Проверка статическая —
# она ловит возврат к старому поведению в тот же коммит, а не через инцидент.
RUN_SERVICE = "scripts/run_service.py"
LAUNCHD_TEMPLATES = "deploy/launchd"
# Имена переменных собираем из частей: иначе проверяющий файл сам попадает под
# собственный поиск (тот же приём, что с LEGACY_MARKERS выше).
PROD_TOKEN = "TELEGRAM" + "_TOKEN"
# Прод-токен разрешён только этим функциям: они и есть ветка `--env prod`.
PROD_ALLOWED = ("prod_env",)
GITIGNORE = ".gitignore"


def _function_stack(tree: ast.AST) -> list[tuple[str, int]]:
    """Все ссылки на прод-токен в файле: (функция, строка). Функция вне def — пустая.

    Имя переменной как константа модуля (`BOT_TOKEN_VAR = "TELEGRAM_TOKEN"`) не
    считается чтением: право читать значение проверяет ветка, а не имя.
    """
    found: list[tuple[str, int]] = []

    def walk(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, [*stack, child.name])
                continue
            if isinstance(child, ast.Constant) and child.value == PROD_TOKEN:
                definition = isinstance(node, ast.Assign) and node.value is child
                if not definition:
                    found.append((".".join(stack), child.lineno))
            walk(child, stack)

    walk(tree, [])
    return found


def _main_env_reads(tree: ast.AST) -> list[int]:
    """Строки, где код берёт основной `.env` (не `.env.dev` и не боевой путь)."""
    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if "ENV_FILE" in (alias.name, alias.asname or ""):
                    offenders.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if value == ".env" or (value.endswith("/.env") and not value.startswith("/opt/")):
                offenders.append(getattr(node, "lineno", 0))
    return offenders


def check_dev_env_isolation() -> int:
    """Локальные запуски отделены от прода: файл среды, токен, launchd, `.gitignore`."""
    problems: list[str] = []

    path = PROJECT / RUN_SERVICE
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        print(f"FAIL dev-context {RUN_SERVICE} не читается: {exc}")
        return 1

    for function, lineno in _function_stack(tree):
        if function.split(".")[-1] in PROD_ALLOWED:
            continue
        problems.append(f"{RUN_SERVICE}:{lineno}: прод-токен вне prod-ветки ({function or 'модуль'})")
    for lineno in _main_env_reads(tree):
        problems.append(f"{RUN_SERVICE}:{lineno}: читает основной .env в локальном запуске")

    template_dir = PROJECT / LAUNCHD_TEMPLATES
    for template in sorted(template_dir.glob("*.plist.template")):
        try:
            data = plistlib.loads(template.read_bytes())
        except Exception as exc:                     # битый XML ловим как проблему
            problems.append(f"{template.relative_to(PROJECT)}: не читается как plist: {exc}")
            continue
        argv = [str(item) for item in data.get("ProgramArguments", [])]
        env_argument = argv[argv.index("--env") + 1] if "--env" in argv else ""
        # @ENV@ — заглушка шаблона: `install_launchd` рендерит в dev (DEFAULT_ENVIRONMENT).
        if env_argument not in {"dev", "@ENV@"}:
            problems.append(f"{template.relative_to(PROJECT)}: агент запускает сервис не в dev "
                            f"(--env {env_argument or 'нет'})")

    gitignore = (PROJECT / GITIGNORE).read_text(encoding="utf-8", errors="ignore")
    if ".env.dev" not in gitignore.splitlines():
        problems.append(f"{GITIGNORE}: .env.dev не игнорируется — dev-токен может уехать в git")
    if not (PROJECT / ".env.dev.example").is_file():
        problems.append(".env.dev.example: шаблона dev-окружения нет")

    for item in problems:
        print(f"FAIL dev-context {item}")
    if problems:
        print(f"     проблем dev-контекста: {len(problems)} — см. docs/infrastructure/08 §1a")
    else:
        print(f"OK   dev  {'локальный запуск не читает прод-токен':52} "
              f"прямых обращений к имени токена: {len(_function_stack(tree))}")
    return len(problems)



if __name__ == "__main__":
    sys.exit(main())
