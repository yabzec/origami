from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def make_text_image(
    path: Path, text: str = "FATTURA 2026", size: tuple[int, int] = (1200, 400)
) -> Path:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.text((60, size[1] // 3), text, fill="black", font=ImageFont.truetype(FONT, 72))
    img.save(path)
    return path
