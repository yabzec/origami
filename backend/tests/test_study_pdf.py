from io import BytesIO

from PIL import Image
from pypdf import PdfReader

from app.services.study_pdf import A4, PageImage, build_study_pdf, cover_page, image_page


def test_pdf_has_cover_plus_one_page_per_image():
    images = [
        PageImage(Image.new("L", (600, 900), 128), "AP · 2026-08-28 · image 1/2"),
        PageImage(Image.new("RGB", (900, 600), "red"), "Lateral · 2026-08-28 · image 2/2"),
    ]
    reader = PdfReader(BytesIO(build_study_pdf("Rx gamba — 2026-08-28", [("Modality", "DX")], "note", images)))
    assert len(reader.pages) == 3
    cover = reader.pages[0].mediabox
    assert (round(float(cover.width)), round(float(cover.height))) == (595, 842)  # A4 in points
    portrait = reader.pages[1].mediabox
    assert float(portrait.height) > float(portrait.width)
    assert reader.pages[1].images[0].name.endswith(".jpg")  # JPEG-encoded, not lossless


def test_image_page_keeps_image_and_adds_caption_strip():
    page = image_page(PageImage(Image.new("L", (1200, 800), 0), "AP"))
    assert page.mode == "L"
    assert page.size[0] == 1200 and page.size[1] > 800
    assert page.getpixel((600, 400)) == 0
    assert page.getpixel((1199, page.size[1] - 1)) == 255  # white strip


def test_cover_page_survives_long_values_and_notes():
    rows = [("Study", "word " * 200), ("Series", "AP (2); Lateral (2)")]
    page = cover_page("A very long title " * 10, rows, "note line\n" * 200)
    assert page.size == A4
