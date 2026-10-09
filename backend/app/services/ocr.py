import io
import time
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


def images_to_pdf(image_paths: list[Path]) -> bytes:
    """Embed images as PDF pages with no OCR text layer (no-OCR scans)."""
    images = [Image.open(p).convert("RGB") for p in image_paths]
    try:
        out = io.BytesIO()
        images[0].save(out, "PDF", save_all=True, append_images=images[1:])
        return out.getvalue()
    finally:
        for image in images:
            image.close()


LANGUAGE_CACHE_SECONDS = 300
_IGNORED_LANGUAGES = {"osd", "equ"}  # orientation/script detection and equations: not text languages
_language_cache: tuple[float, list[str]] | None = None


def available_languages() -> list[str]:
    """Installed Tesseract languages, sorted; cached so new packages show up within 5 minutes."""
    global _language_cache
    now = time.monotonic()
    if _language_cache is None or now - _language_cache[0] > LANGUAGE_CACHE_SECONDS:
        codes = sorted(set(pytesseract.get_languages(config="")) - _IGNORED_LANGUAGES)
        _language_cache = (now, codes)
    return list(_language_cache[1])


def reset_language_cache() -> None:
    global _language_cache
    _language_cache = None


def unknown_languages(value: str) -> list[str]:
    """Codes of a Tesseract `lang` string (`ita+eng`) that are not installed."""
    installed = set(available_languages())
    return [code for code in value.split("+") if code not in installed]
