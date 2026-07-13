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


import uuid


def basis_vector(index: int, dim: int = 1536) -> list[float]:
    vector = [0.0] * dim
    vector[index] = 1.0
    return vector


def seed_document(session, title: str, chunk_specs: list[dict], **doc_kwargs):
    """Insert a ready Document plus chunks. Each spec: {content, embedding?, page_number?, source?}."""
    from app.models import Chunk, ChunkSource, DocStatus, DocType, Document

    doc = Document(
        title=title,
        doc_type=doc_kwargs.pop("doc_type", DocType.text),
        status=doc_kwargs.pop("status", DocStatus.ready),
        **doc_kwargs,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    for index, spec in enumerate(chunk_specs):
        session.add(
            Chunk(
                document_id=doc.id,
                chunk_index=index,
                page_number=spec.get("page_number"),
                source=spec.get("source", ChunkSource.content),
                content=spec["content"],
                embedding=spec.get("embedding"),
            )
        )
    session.commit()
    return doc
