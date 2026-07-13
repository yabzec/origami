from pathlib import Path

import docx
from pypdf import PdfReader

MIN_CHARS_PER_PAGE = 50


def extract_pdf_text(pdf_path: Path) -> list[tuple[int, str]]:
    reader = PdfReader(pdf_path)
    return [
        (number, (page.extract_text() or "").strip())
        for number, page in enumerate(reader.pages, start=1)
    ]


def pdf_needs_ocr(pages: list[tuple[int, str]]) -> bool:
    if not pages:
        return True
    average = sum(len(text) for _, text in pages) / len(pages)
    return average < MIN_CHARS_PER_PAGE


def extract_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def extract_docx(path: Path) -> str:
    document = docx.Document(path)
    return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())
