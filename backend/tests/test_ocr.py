import io

from pypdf import PdfReader

from app.services.ocr import images_to_searchable_pdf, ocr_image, pdf_to_searchable_pdf
from tests.helpers import make_text_image


def test_ocr_image_produces_searchable_pdf_and_text(tmp_path):
    img = make_text_image(tmp_path / "page.png", "FATTURA 2026")
    pdf_bytes, text = ocr_image(img, "ita+eng")
    assert "FATTURA" in text.upper()
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1
    assert "FATTURA" in reader.pages[0].extract_text().upper()


def test_images_to_searchable_pdf_merges_pages_in_order(tmp_path):
    p1 = make_text_image(tmp_path / "p1.png", "PAGINA UNO")
    p2 = make_text_image(tmp_path / "p2.png", "PAGINA DUE")
    pdf_bytes, pages = images_to_searchable_pdf([p1, p2], "ita+eng")
    assert [n for n, _ in pages] == [1, 2]
    assert "UNO" in pages[0][1].upper()
    assert "DUE" in pages[1][1].upper()
    assert len(PdfReader(io.BytesIO(pdf_bytes)).pages) == 2


def test_pdf_to_searchable_pdf_roundtrip(tmp_path):
    img = make_text_image(tmp_path / "scan.png", "RICEVUTA 99")
    source_pdf_bytes, _ = ocr_image(img, "ita+eng")
    source = tmp_path / "source.pdf"
    source.write_bytes(source_pdf_bytes)

    pdf_bytes, pages = pdf_to_searchable_pdf(source, "ita+eng", dpi=150)
    assert len(pages) == 1
    assert "RICEVUTA" in pages[0][1].upper()
