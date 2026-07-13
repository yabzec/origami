import io
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path
from PIL import Image
from pypdf import PdfReader, PdfWriter


def _ocr_pil_image(image: Image.Image, languages: str) -> tuple[bytes, str]:
    pdf_page = pytesseract.image_to_pdf_or_hocr(image, lang=languages, extension="pdf")
    text = pytesseract.image_to_string(image, lang=languages)
    return pdf_page, text.strip()


def ocr_image(image_path: Path, languages: str) -> tuple[bytes, str]:
    with Image.open(image_path) as image:
        return _ocr_pil_image(image, languages)


def _merge(pdf_pages: list[bytes]) -> bytes:
    writer = PdfWriter()
    for page_bytes in pdf_pages:
        writer.append(PdfReader(io.BytesIO(page_bytes)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def images_to_searchable_pdf(
    image_paths: list[Path], languages: str
) -> tuple[bytes, list[tuple[int, str]]]:
    pdf_pages: list[bytes] = []
    texts: list[tuple[int, str]] = []
    for number, path in enumerate(image_paths, start=1):
        page_pdf, text = ocr_image(path, languages)
        pdf_pages.append(page_pdf)
        texts.append((number, text))
    return _merge(pdf_pages), texts


def pdf_to_searchable_pdf(
    pdf_path: Path, languages: str, dpi: int = 300
) -> tuple[bytes, list[tuple[int, str]]]:
    images = convert_from_path(pdf_path, dpi=dpi)
    pdf_pages: list[bytes] = []
    texts: list[tuple[int, str]] = []
    for number, image in enumerate(images, start=1):
        page_pdf, text = _ocr_pil_image(image, languages)
        pdf_pages.append(page_pdf)
        texts.append((number, text))
    return _merge(pdf_pages), texts
