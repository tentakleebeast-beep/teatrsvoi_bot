#!/usr/bin/env python3
"""Гейт «готово» одной командой: последовательный прогон всех шагов и один итог.

Зачем скрипт, а не список в docs/development/26 §4: список каждый выполняет по-своему
и по удобной части. 31.10.2026 карта C закрылась как done прогоном только `tests/unit`
(898 passed), а `pytest -q` на `main` падал (4 failed + 19 errors). Гейт, который
нельзя «набрать по частям», либо прогнал всё, либо называет упавший шаг.

  env -u PYTHONPATH .venv/bin/python scripts/check_done.py            # полный прогон
  env -u PYTHONPATH .venv/bin/python scripts/check_done.py --quick    # без браузерных
  env -u PYTHONPATH .venv/bin/python scripts/check_done.py --json     # машинный отчёт
  env -u PYTHONPATH .venv/bin/python scripts/check_done.py --board teatrsvoi

Шаги идут в фиксированном порядке, каждый — отдельная строка отчёта:

  1. ruff check .
  2. pytest -q                       — unit + integration на поднятом Postgres
  3. tests/webapp --browser chromium — только в полном прогоне (`--full` по умолчанию)
  4. scripts/check_invariants.py
  5. scripts/check_registers.py
  6. scripts/check_deadcode.py
  7. scripts/check_kanban_cards.py   — если задан HERMES_KANBAN_TASK или `--board`

Единственное разрешённое сокращение — `--quick`: он пропускает только браузерные шаги
и пишет это в итоговой строке явно. Флага «только unit» нет: integration-тесты входят
в шаг 2 всегда.

Границы: скрипт не поднимает Postgres, не накатывает миграции, не делает git-операций
и не правит файлы. Логику проверок не дублирует — только вызывает готовые скрипты
и складывает результат.

Код выхода: 0 — все шаги пройдены, 1 — первый упавший шаг (следующие не запускаются),
2 — неверные аргументы.

Тест: tests/unit/test_check_done.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAIL_LINES = 20
STEP_TIMEOUT = 3600

# Общий набор ключей pytest для всех шагов: без чужих addopts и без шума предупреждений.
PYTEST = ("-m", "pytest", "-p", "no:warnings", "-o", "addopts=", "-q")


@dataclass(frozen=True)
class Step:
    """Шаг гейта: имя для отчёта, человеческий заголовок и готовая команда."""

    key: str
    title: str
    command: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()

    def environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ if base is None else base)
        env.pop("PYTHONPATH", None)          # PYTHONPATH Hermes перебивает пакеты
        env.update(dict(self.env))
        return env


@dataclass
class StepResult:
    """Результат одного шага: то, что попадает в отчёт."""

    key: str
    title: str
    code: int
    seconds: float
    output: str = ""
    summary: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    def tail(self, lines: int = TAIL_LINES) -> list[str]:
        rows = self.output.rstrip().splitlines()
        return rows[-lines:]

    def as_dict(self) -> dict:
        return {
            "step": self.key,
            "title": self.title,
            "code": self.code,
            "seconds": round(self.seconds, 3),
            "summary": self.summary,
            "ok": self.ok,
            "tail": self.tail() if not self.ok else [],
        }


@dataclass
class Report:
    """Отчёт гейта: выполненные шаги, режим и итоговая строка."""

    steps: list[StepResult] = field(default_factory=list)
    quick: bool = False
    skipped: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    @property
    def failed(self) -> StepResult | None:
        for result in self.steps:
            if not result.ok:
                return result
        return None

    @property
    def ok(self) -> bool:
        return self.failed is None

    @property
    def seconds(self) -> float:
        return time.monotonic() - self.started

    def summary(self) -> str:
        if not self.ok:
            failed = self.failed
            assert failed is not None
            return f"ГЕЙТ КРАСНЫЙ: шаг «{failed.key}» упал, код {failed.code}"
        parts = [result.summary for result in self.steps if result.summary]
        head = "ГЕЙТ ЗЕЛЁНЫЙ (без браузерных)" if self.quick else "ГЕЙТ ЗЕЛЁНЫЙ"
        return f"{head}: {', '.join(parts)}"

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "mode": "quick" if self.quick else "full",
            "seconds": round(self.seconds, 3),
            "summary": self.summary(),
            "steps": [result.as_dict() for result in self.steps],
            "skipped": self.skipped,
        }


def python() -> str:
    """Интерпретатор гейта: venv проекта, иначе тот, которым запущен скрипт."""
    candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def gate_steps(
    *,
    quick: bool = False,
    board: str | None = None,
    python_path: str | None = None,
    project: Path | None = None,
) -> list[Step]:
    """Собрать шаги гейта в фиксированном порядке.

    `quick` убирает только браузерный шаг; `board` добавляет шаг карт канбана.
    Флага, который убирает integration-тесты, здесь нет и не появится.
    """
    exe = python_path or python()
    root = project or ROOT
    steps = [
        Step("ruff", "ruff check .", (exe, "-m", "ruff", "check", ".")),
        Step("pytest", "pytest -q (unit + integration)", (exe, *PYTEST)),
    ]
    if not quick:
        steps.append(Step(
            "webapp",
            "браузерные тесты Mini App",
            (exe, *PYTEST, "tests/webapp", "--browser", "chromium"),
            env=(("SVOI_WEBAPP_TESTS", "1"),),
        ))
    steps += [
        Step("invariants", "check_invariants.py",
             (exe, str(root / "scripts" / "check_invariants.py"))),
        Step("registers", "check_registers.py",
             (exe, str(root / "scripts" / "check_registers.py"))),
        Step("deadcode", "check_deadcode.py",
             (exe, str(root / "scripts" / "check_deadcode.py"))),
        Step("settings", "check_settings.py",
             (exe, str(root / "scripts" / "check_settings.py"))),
    ]
    if board or os.getenv("HERMES_KANBAN_TASK"):
        commands = [exe, str(root / "scripts" / "check_kanban_cards.py")]
        if board:
            commands += ["--board", board]
        steps.append(Step("kanban", "check_kanban_cards.py", tuple(commands)))
    return steps


def parse_pytest(output: str) -> tuple[int, int]:
    """Счётчики pytest из последней строки вида «1475 passed, 287 skipped in 90.1s»."""
    passed = skipped = 0
    for match in re.finditer(r"(\d+) passed", output):
        passed = int(match.group(1))
    for match in re.finditer(r"(\d+) skipped", output):
        skipped = int(match.group(1))
    return passed, skipped


def parse_deadcode(output: str) -> int | None:
    """Число новых находок мёртвого кода из строки «Находок: N (известных K, новых M)»."""
    match = re.search(r"новых (\d+)", output)
    return int(match.group(1)) if match else None


def summarize(key: str, output: str, code: int) -> str:
    """Одна короткая фраза шага для итоговой строки."""
    if code != 0:
        return ""
    if key == "ruff":
        return "ruff чисто"
    if key == "pytest":
        passed, skipped = parse_pytest(output)
        return f"pytest {passed} passed/{skipped} skipped"
    if key == "webapp":
        passed, skipped = parse_pytest(output)
        return f"браузерные {passed} passed/{skipped} skipped"
    if key == "invariants":
        return "инварианты OK"
    if key == "registers":
        return "реестры OK"
    if key == "deadcode":
        new = parse_deadcode(output)
        return "deadcode новых 0" if new is None else f"deadcode новых {new}"
    if key == "kanban":
        return "карты OK"
    return f"{key} OK"


def run_step(step: Step, root: Path | None = None, timeout: int = STEP_TIMEOUT) -> StepResult:
    """Запустить один шаг и запомнить код, время и вывод."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(step.command),
            cwd=str(root or ROOT),
            env=step.environment(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        code = proc.returncode
        output = f"{proc.stdout}\n{proc.stderr}".strip()
    except FileNotFoundError as exc:
        code, output = 127, f"не найден исполняемый файл: {exc.filename}"
    except subprocess.TimeoutExpired:
        code, output = 124, f"шаг не уложился в {timeout} с и был прерван"
    seconds = time.monotonic() - started
    return StepResult(key=step.key, title=step.title, code=code, seconds=seconds,
                      output=output, summary=summarize(step.key, output, code))


def run_gate(steps: list[Step], *, quick: bool = False,
             root: Path | None = None, runner=run_step) -> Report:
    """Пройти шаги по порядку; после первого падения следующие не запускаются."""
    report = Report(quick=quick)
    for index, step in enumerate(steps):
        result = runner(step, root) if root is not None else runner(step)
        report.steps.append(result)
        if not result.ok:
            report.skipped = [item.key for item in steps[index + 1:]]
            break
    return report


def environment_hint(result: StepResult) -> str | None:
    """Подсказка, если шаг упал из-за неподготовленного окружения."""
    text = result.output.lower()
    markers = ("could not connect", "connection refused", "does not exist",
               "нет соединения", "no such file or directory")
    if any(marker in text for marker in markers):
        return ("Похоже, окружение не подготовлено. Гейт сам не поднимает Postgres "
                "и не накатывает миграции: `docker compose up -d postgres`, затем "
                "`set -a; . ./.env; set +a` — и повторить.")
    return None


def render_text(report: Report) -> str:
    """Человеческий отчёт: строка на шаг, хвост вывода при падении, итог."""
    lines = []
    for result in report.steps:
        mark = "ok  " if result.ok else "FAIL"
        lines.append(f"[{mark}] {result.key:<11} {result.seconds:7.2f} с  {result.summary}")
    for key in report.skipped:
        lines.append(f"[ -- ] {key:<11} {'':7}    не запускался (гейт остановлен)")
    if report.quick:
        lines.append("[ -- ] webapp      "
                     "        пропущен: --quick, без браузерных")
    failed = report.failed
    if failed is not None:
        lines.append("")
        lines.append(f"Шаг «{failed.key}» ({failed.title}) упал с кодом {failed.code}. "
                     f"Последние {TAIL_LINES} строк вывода:")
        lines.extend(f"    {row}" for row in failed.tail())
        hint = environment_hint(failed)
        if hint:
            lines.append("")
            lines.append(hint)
    lines.append("")
    lines.append(report.summary())
    return "\n".join(lines)


def main(argv: list[str] | None = None, steps: list[Step] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Гейт «готово»: ruff, полный pytest, инварианты, реестры, deadcode")
    parser.add_argument("--quick", action="store_true",
                        help="пропустить только браузерные тесты Mini App")
    parser.add_argument("--full", action="store_true",
                        help="полный прогон (по умолчанию и так полный)")
    parser.add_argument("--json", action="store_true", help="машинный отчёт")
    parser.add_argument("--board", default=None,
                        help="проверить карты канбана этого борда")
    args = parser.parse_args(argv)

    gate = steps if steps is not None else gate_steps(quick=args.quick, board=args.board)
    report = run_gate(gate, quick=args.quick)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
