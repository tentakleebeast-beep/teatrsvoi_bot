"""Извлечение текста из документов для поиска (FR-K15, FR-K16, Ш-10).

Правила простые:
  * pdf — `pypdf` (чистый Python, без системных библиотек);
  * docx — это zip с `word/document.xml`: разбираем стандартным `zipfile` + `ElementTree`,
    чтобы не тащить lxml ради вытаскивания абзацев;
  * xlsx — `openpyxl` уже в зависимостях (им же собираются отчёты);
  * txt/md/csv — как есть.

Не смогли прочитать (скан, битый файл, шифрование) — возвращаем статус `text_unavailable`.
Это не ошибка загрузки: файл живёт, поиск по имени и тегам работает, OCR — отдельная задача.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree

log = logging.getLogger("extract")

# Больше 200 тысяч символов в поисковый индекс не кладём: это уже не документ, а склад
MAX_CHARS = 200_000
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def text_of(path: str, name: str = "") -> tuple[str, str]:
    """Вернуть (текст, статус): `ok` либо `text_unavailable`."""
    suffix = Path(name or path).suffix.lower()
    try:
        if suffix in (".txt", ".md", ".csv", ".rtf"):
            raw = Path(path).read_bytes()[: MAX_CHARS * 4]
            return raw.decode("utf-8", "replace")[:MAX_CHARS].strip(), "ok"
        if suffix == ".pdf":
            return _pdf(path), "ok"
        if suffix == ".docx":
            return _docx(path), "ok"
        if suffix == ".xlsx":
            return _xlsx(path), "ok"
    except _document_errors() as exc:
        # разборщики документов: битый/чужой формат = «текста нет», не отказ сервиса
        log.info("текст не извлечён из %s: %s", name or path, exc)
    return "", "text_unavailable"


def _document_errors() -> tuple[type[BaseException], ...]:
    """Что реально бросают разборщики на испорченном документе.

    openpyxl и pypdf импортируются по месту: они тяжелее всего модуля, а нужны
    только для своих форматов.
    """
    from openpyxl.utils.exceptions import InvalidFileException
    from pypdf.errors import PyPdfError

    return (OSError, ValueError, KeyError, zipfile.BadZipFile,
            ElementTree.ParseError, InvalidFileException, PyPdfError)


def _pdf(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    if getattr(reader, "is_encrypted", False):
        raise ValueError("pdf зашифрован")
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
        if sum(len(part) for part in parts) > MAX_CHARS:
            break
    text = "\n".join(parts).strip()
    if not text:
        # pdf без текстового слоя — это скан; честно помечаем, а не притворяемся
        raise ValueError("нет текстового слоя")
    return text[:MAX_CHARS]


def _docx(path: str) -> str:
    with zipfile.ZipFile(path) as bundle:
        xml = bundle.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{WORD_NS}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{WORD_NS}t")).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)[:MAX_CHARS]


def _xlsx(path: str) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    chunks: list[str] = []
    try:
        for sheet in workbook.worksheets:
            chunks.append(sheet.title)
            for row in sheet.iter_rows(values_only=True):
                line = " ".join(str(cell) for cell in row if cell not in (None, ""))
                if line:
                    chunks.append(line)
                if sum(len(chunk) for chunk in chunks) > MAX_CHARS:
                    return "\n".join(chunks)[:MAX_CHARS]
    finally:
        workbook.close()
    return "\n".join(chunks)[:MAX_CHARS]
