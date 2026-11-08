"""Собирает docs/README.md: группы, сквозная нумерация, оглавление, архив отдельно.

Запуск: env -u PYTHONPATH .venv/bin/python scripts/build_docs_index.py
Проверка: тест tests/unit/test_docs_links.py падает, если индекс отстал от файлов.

В индексе только действующие документы: архив — отдельной строкой в конце, чтобы
новый человек (или агент) не принимал исторический документ за правило.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

GROUPS = {
    "architecture": ("Архитектура", "модель данных, правила, алгоритмы, границы систем"),
    "infrastructure": ("Инфраструктура", "Google, календари, уведомления, развёртывание"),
    "development": ("Разработка", "требования, сценарии, тесты, процессы, интерфейсы"),
    "plans": ("Планы", "роадмап, планы развития"),
    "reports": ("Отчёты", "датированные срезы: чекпойнты, аудиты, покрытие, смоук-тесты"),
}
ARCHIVE = ("Архив", "устаревшее и историческое; не правило, а запись")


def documents() -> list[pathlib.Path]:
    """Документы групп; README — не документ, а сам индекс и правила архива."""
    return [path for path in DOCS.rglob("*.md")
            if path.parent != DOCS and path.name != "README.md"]


def sort_key(path: pathlib.Path) -> tuple[int, str]:
    number = path.name.split("-", 1)[0]
    return (int(number) if number.isdigit() else 10**6, path.name)


def document_title(path: pathlib.Path) -> str:
    match = re.search(r"^#\s+(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1).strip() if match else ""


def rows(paths: list[pathlib.Path], group: str) -> list[str]:
    lines = ["| № | Документ | О чём |", "|---|---|---|"]
    for path in sorted((p for p in paths if p.parent.name == group), key=sort_key):
        number = path.name.split("-", 1)[0]
        lines.append(f"| {number} | [{path.name}]({group}/{path.name}) "
                     f"| {document_title(path)} |")
    return lines


def missing_numbers(paths: list[pathlib.Path]) -> list[int]:
    """Пропуски сквозной нумерации: чего нет в диапазоне 1..max."""
    used = {int(number) for number in (p.name.split("-", 1)[0] for p in paths)
            if number.isdigit()}
    return [number for number in range(1, max(used, default=0) + 1) if number not in used]


def gap_note(paths: list[pathlib.Path]) -> str:
    """Строка индекса про пропущенные номера; формулировка — по числу совпадений."""
    gaps = missing_numbers(paths)
    if not gaps:
        return "Все номера диапазона использованы."
    listed = ", ".join(str(number) for number in gaps)
    subject = "Номер не использован" if len(gaps) == 1 else "Номера не использованы"
    return f"{subject}: {listed}."


def build() -> str:
    files = documents()
    lines = [
        "# Документация проекта",
        "",
        "Действующие документы сгруппированы по смыслу. **Нумерация сквозная**: номер означает",
        "порядок появления документа и никогда не переиспользуется — по нему ссылаются код,",
        f"тесты и другие документы. {gap_note(files)}",
        "",
        "Устаревшее не удаляется, а лежит в [архиве](archive/) — отдельной строкой в конце",
        "индекса. Правило, что туда попадает и как на него ссылаться: `docs/archive/README.md`.",
        "",
        "| Группа | Что внутри | Документов |",
        "|---|---|---|",
    ]
    for group, (title, about) in GROUPS.items():
        count = len([p for p in files if p.parent.name == group])
        lines.append(f"| [{title}]({group}/) | {about} | {count} |")
    archived = len([p for p in files if p.parent.name == "archive"])
    lines.append(f"| [{ARCHIVE[0]}](archive/) | {ARCHIVE[1]} | {archived} |")

    for group, (title, about) in GROUPS.items():
        lines += ["", f"## {title}", "", f"*{about}*", ""] + rows(files, group)

    lines += ["", "---", "", "## Архив", "",
              f"*{ARCHIVE[1]}* · правило пополнения — `docs/archive/README.md`", ""]
    lines += rows(files, "archive")

    lines += ["", "---", "", "## Правила", "",
              "1. Новый документ получает следующий свободный номер и попадает в группу по смыслу.",
              "2. Номер не меняется при переносе между группами — меняется только путь.",
              "3. Ссылки пишутся полным путём: `docs/<группа>/<номер>-<имя>.md`.",
              "4. Проверка автоматическая: `tests/unit/test_docs_links.py`.",
              "5. Устаревший документ не удаляется, а переносится в `archive/` с пометкой"
              " «Заменён: …» в первой строке.",
              "6. Документ в архиве не действует: код и тесты на него не опираются.",
              "7. Индекс собирается скриптом `scripts/build_docs_index.py`.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    target = DOCS / "README.md"
    target.write_text(build(), encoding="utf-8")
    files = documents()
    active = len([p for p in files if p.parent.name != "archive"])
    archived = len([p for p in files if p.parent.name == "archive"])
    print(f"docs/README.md обновлён, действующих документов: {active}, в архиве: {archived}")
