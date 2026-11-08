"""Документация: ссылки живые, нумерация сквозная, индекс полон (docs/README.md).

Обоснование (Ш-51): файл создан в 721f154 docs: нормализация документации по группам, отчёты смоука, ревью требований и кода."""

import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
GROUPS = {"architecture", "infrastructure", "development", "plans", "reports", "archive"}
LINK = re.compile(r"docs/([a-z]+)/(\d{2,3})-([a-z0-9\-]+)\.md")


def _documents() -> dict[str, pathlib.Path]:
    return {p.name: p for p in DOCS.rglob("*.md") if p.parent != DOCS}


def test_все_документы_в_группах():
    loose = [p.name for p in DOCS.glob("*.md") if p.name != "README.md"]
    assert not loose, f"документы вне групп: {loose}"


def test_ссылки_живые():
    broken = []
    for path in list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.py")):
        if ".git" in path.parts or ".venv" in path.parts or ".worktrees" in path.parts:
            continue
        for match in LINK.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            group, number, name = match.groups()
            if group not in GROUPS or not (DOCS / group / f"{number}-{name}.md").exists():
                broken.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
    assert not broken, "битые ссылки:\n" + "\n".join(broken[:10])


def test_номера_уникальны():
    """Номер — целое число до первого дефиса: сквозная нумерация дошла до 100."""
    numbers = {}
    for name in _documents():
        # номер — часть имени до первого дефиса: 10 и 100 — разные документы
        numbers.setdefault(name.split("-", 1)[0], []).append(name)
    duplicates = {number: names for number, names in numbers.items() if len(names) > 1}
    assert not duplicates, f"номера повторились: {duplicates}"


def test_индекс_перечисляет_все_документы():
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    missing = [name for name in _documents() if name not in index]
    assert not missing, f"нет в docs/README.md: {missing}"


def test_у_каждого_документа_есть_заголовок():
    without = []
    for name, path in _documents().items():
        if not re.search(r"^#\s+\S", path.read_text(encoding="utf-8"), re.MULTILINE):
            without.append(name)
    assert not without, f"без заголовка: {without}"


def _generated_index() -> str:
    """Индекс, который собирает scripts/build_docs_index.py."""
    path = ROOT / "scripts" / "build_docs_index.py"
    spec = importlib.util.spec_from_file_location("build_docs_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build()


def test_индекс_совпадает_с_генератором():
    """Ручная правка индекса ловится: docs/README.md собирается только скриптом."""
    assert (DOCS / "README.md").read_text(encoding="utf-8") == _generated_index()


# Рукописные счётчики и снятый контур: их возврат означает, что снова поверили прозе.
УСТАРЕВШИЕ_ФАКТЫ = (
    "n8n 2.44", "WF-00", "WF-01", "93 операции", "11 248", "52 документа",
    "48 готово", "421", "927", "37 файлов", "001–032", "001–038",
    "27 эндпоинт", "44 хендлер",
)
# Кодовые числа: они живут в прозе законно и держатся тестами, а не отчётами.
РАЗРЕШЁННЫЙ_КОНТЕКСТ = ("MVP на 20 человек", "TTL 7 дней", "25 с", "UTC+4", "20:00 → 08:00")


def test_нет_устаревших_фактов_в_активных_документах():
    """Активные документы описывают текущий контур, а числа берут из источников."""
    stale = []
    for name in ("ARCHITECTURE.md", "README.md"):
        for number, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
            if any(allowed in line for allowed in РАЗРЕШЁННЫЙ_КОНТЕКСТ):
                continue
            stale += [f"{name}:{number}: {fact}"
                      for fact in УСТАРЕВШИЕ_ФАКТЫ if fact in line]
    assert not stale, "устаревшие факты вернулись:\n" + "\n".join(stale)
