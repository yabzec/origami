"""Image PDF of one study: a cover page with the study data, then one page per image."""

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

DPI = 150
A4 = (1240, 1754)  # A4 portrait at 150 dpi
MARGIN = 100
LABEL_WIDTH = 330
JPEG_QUALITY = 92
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DISCLAIMER = "Images converted for reference. Not for diagnostic use."


@dataclass
class PageImage:
    image: Image.Image
    caption: str


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)
    except OSError:
        return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if not line or draw.textlength(candidate, font=font) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def cover_page(title: str, rows: list[tuple[str, str]], notes: str) -> Image.Image:
    """A4 page: title, `label  value` rows, notes; text past the bottom margin is dropped."""
    page = Image.new("RGB", A4, "white")
    draw = ImageDraw.Draw(page)
    width = A4[0] - 2 * MARGIN
    bottom = A4[1] - MARGIN - 60
    title_font, label_font, body_font = _font(40, bold=True), _font(24, bold=True), _font(24)
    y = MARGIN
    for line in _wrap(draw, title, title_font, width):
        draw.text((MARGIN, y), line, font=title_font, fill="black")
        y += 52
    y += 30
    for label, value in rows:
        if not value or y > bottom:
            continue
        draw.text((MARGIN, y), label, font=label_font, fill="black")
        for line in _wrap(draw, value, body_font, width - LABEL_WIDTH):
            if y > bottom:
                break
            draw.text((MARGIN + LABEL_WIDTH, y), line, font=body_font, fill="black")
            y += 34
        y += 8
    if notes.strip() and y < bottom:
        y += 30
        draw.text((MARGIN, y), "Notes", font=label_font, fill="black")
        y += 40
        for line in _wrap(draw, notes.strip(), body_font, width):
            if y > bottom:
                break
            draw.text((MARGIN, y), line, font=body_font, fill="black")
            y += 34
    draw.text((MARGIN, A4[1] - MARGIN), DISCLAIMER, font=_font(20), fill="#555555")
    return page


def image_page(item: PageImage) -> Image.Image:
    """The image at full size with a caption strip below; the page size follows the image."""
    width, height = item.image.size
    font_size = max(16, width // 60)
    strip = font_size * 2
    mode = "RGB" if item.image.mode == "RGB" else "L"
    page = Image.new(mode, (width, height + strip), "white")
    page.paste(item.image.convert(mode), (0, 0))
    ImageDraw.Draw(page).text(
        (font_size // 2, height + font_size // 2), item.caption, font=_font(font_size), fill="black"
    )
    return page


def build_study_pdf(title: str, rows: list[tuple[str, str]], notes: str, images: list[PageImage]) -> bytes:
    pages = [cover_page(title, rows, notes), *(image_page(item) for item in images)]
    out = io.BytesIO()
    # Pillow stores "L"/"RGB" pages as JPEG (DCTDecode); quality applies to every page
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=DPI, quality=JPEG_QUALITY)
    return out.getvalue()
