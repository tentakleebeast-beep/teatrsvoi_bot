"""Извлечение текста: pdf, docx, xlsx, txt — и честное «не смог» для сканов (FR-K16, Ш-10).

Файлы собираются в тесте, без образцов в репозитории: PDF пишется руками (pypdf его читает),
docx — это zip с `word/document.xml`, xlsx — через openpyxl.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from app import extract
from openpyxl import Workbook


def _pdf_bytes(text: str) -> bytes:
    content = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = out.tell()
    out.write(b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(len(objects) + 1).encode()
              + b" /Root 1 0 R >>\nstartxref\n" + str(start).encode() + b"\n%%EOF")
    return out.getvalue()


def _docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("word/document.xml", xml)
    return buffer.getvalue()


def _xlsx_bytes(rows: list[tuple]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_pdf_читается(tmp_path):
    path = tmp_path / "пьеса.pdf"
    path.write_bytes(_pdf_bytes("antrakt v purge"))
    text, status = extract.text_of(str(path), "пьеса.pdf")
    assert status == "ok"
    assert "antrakt" in text.lower()


def test_docx_читается_без_lxml(tmp_path):
    path = tmp_path / "сцена.docx"
    path.write_bytes(_docx_bytes(["Первый абзац", "", "Второй абзац"]))
    text, status = extract.text_of(str(path), "сцена.docx")
    assert status == "ok"
    assert "Первый абзац" in text and "Второй абзац" in text


def test_xlsx_читается(tmp_path):
    path = tmp_path / "смета.xlsx"
    path.write_bytes(_xlsx_bytes([("Реквизит", 5000), ("Аренда", 100000)]))
    text, status = extract.text_of(str(path), "смета.xlsx")
    assert status == "ok"
    assert "Реквизит" in text and "100000" in text


def test_txt_читается(tmp_path):
    path = tmp_path / "заметка.md"
    path.write_text("# Заметка\nВыход Пьеро после антракта", encoding="utf-8")
    text, status = extract.text_of(str(path), "заметка.md")
    assert status == "ok" and "антракта" in text


def test_скан_помечается_как_недоступный(tmp_path):
    """PDF без текстового слоя — это скан: файл живёт, поиска по тексту нет."""
    path = tmp_path / "скан.pdf"
    path.write_bytes(b"%PDF-1.4\nnot really a pdf")
    _text, status = extract.text_of(str(path), "скан.pdf")
    assert status == "text_unavailable"


def test_неизвестный_формат_не_ломает_загрузку(tmp_path):
    path = tmp_path / "архив.bin"
    path.write_bytes(b"\x00\x01\x02")
    _text, status = extract.text_of(str(path), "архив.bin")
    assert status == "text_unavailable"


@pytest.mark.parametrize("name", ["пьеса.PDF", "смета.XLSX"])
def test_расширение_не_зависит_от_регистра(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(_pdf_bytes("abc") if name.lower().endswith(".pdf")
                     else _xlsx_bytes([("abc",)]))
    _text, status = extract.text_of(str(path), name)
    assert status == "ok"
